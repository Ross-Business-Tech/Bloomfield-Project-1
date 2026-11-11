"""core-builder steps: stg.* -> core.* (everything except core.signal).

Each step is idempotent: natural keys (ccn, source_id+source_key, facility_role/loan/event source_key,
legal_case court+docket_no, metric primary key) make re-runs update in place instead of duplicating.
Records that already have a core.facility_xref row are never re-matched, so a human review decision
(review_status confirmed / rejected) survives re-runs.
"""
from __future__ import annotations

import csv
import re
import uuid
from datetime import date
from collections import Counter, defaultdict
from pathlib import Path

from rapidfuzz import fuzz, process

from src.common.bulk import bulk_upsert
from src.common.config import PROJECT_ROOT, thresholds
from src.common.normalize import entity_stem, normalize_name, zip5

from .match import (
    FacilityIndex, MatchResult, care_types_from_text, clean, hud_best, hud_care_classes, hud_name_variants,
    qualifying_prefixes, split_borrower_prefix, borrower_group_verdict, direction_conflict, court_source_id, court_state, distinctive_name, name_only_score,
    normalize_court, normalize_docket, state_code, to_int,
)
from .orgs import OrgResolver

SIZE_WARN_MB = 400
REVIEW_CSV = PROJECT_ROOT / "eval" / "reports" / "match_review.csv"
HUD_SAMPLE_CSV = PROJECT_ROOT / "eval" / "reports" / "hud_review_sample.csv"

# ASSUMPTION: HUD loans terminated before this date are only attached when they match a facility we already
# know; they never create a new facility (a 1970s termination says nothing about distress today).
HUD_TERMINATED_NEW_FACILITY_SINCE = "2016-01-01"

# ASSUMPTION: which CMS "all owners" rows become core.facility_role. Organizations only (individual people are
# not buyers/lenders Bloomfield calls, and keeping them would add ~190k rows). Direct owners >= 5%,
# organizations with operational/managerial control (management companies) and mortgage/security holders.
# Indirect owners (parent companies / PE funds) with >= 5% become role 'indirect_owner' (schema request #5).
SNF_OWNER_ROLES = {
    "5% OR GREATER INDIRECT OWNERSHIP INTEREST": ("indirect_owner", "io"),
    "INDIRECT OWNERSHIP INTEREST": ("indirect_owner", "i"),    # kept only when ownership_pct >= 5
    "5% OR GREATER DIRECT OWNERSHIP INTEREST": ("owner", "do"),
    "DIRECT OWNERSHIP INTEREST": ("owner", "d"),           # kept only when ownership_pct >= 5
    "OPERATIONAL/MANAGERIAL CONTROL": ("operator", "om"),
    "5% OR GREATER MORTGAGE INTEREST": ("lender", "mi"),
    "5% OR GREATER SECURITY INTEREST": ("lender", "si"),
}
LICENSE_SOURCE = {"MI": "mi_lara", "FL": "fl_ahca", "NC": "nc_dhsr", "TX": "tx_hhsc", "NY": "ny_doh", "IA": "ia_dial",
                  "PA": "pa_dhs"}

FAC_COLS = ["facility_id", "canonical_name", "street", "city", "state", "zip5", "phone", "care_types",
            "beds", "units", "ccn"]
XREF_COLS = ["facility_id", "source_id", "source_key", "match_method", "match_score", "review_status"]
ROLE_COLS = ["facility_id", "org_id", "role", "start_date", "end_date", "source_url", "source_key"]
EVENT_COLS = ["facility_id", "event_type", "effective_date", "seller_org_id", "buyer_org_id", "price",
              "source_url", "source_key"]


class SizeLimitReached(RuntimeError):
    pass


def db_size_mb(conn) -> int:
    return conn.execute("select pg_database_size(current_database())/1048576 as mb").fetchone()["mb"]


def guard_size(conn, step: str) -> int:
    mb = db_size_mb(conn)
    print(f"  [{step}] database size: {mb} MB")
    if mb > SIZE_WARN_MB:
        raise SizeLimitReached(f"database is {mb} MB (> {SIZE_WARN_MB} MB warning line) after step '{step}'; "
                               "stopping so the free plan limit (500 MB) is not hit")
    return mb


def _lim(limit: int | None) -> str:
    return f" limit {int(limit)}" if limit else ""


def table_columns(conn, schema: str, table: str) -> set[str]:
    rows = conn.execute("select column_name from information_schema.columns where table_schema=%s and table_name=%s",
                        (schema, table)).fetchall()
    return {r["column_name"] for r in rows}


# ======================================================================= batch writer

class Batch:
    """Collects core rows produced in Python and writes them with COPY (src.common.bulk) in FK order."""

    def __init__(self, conn, orgs: OrgResolver | None = None, flush_every: int = 1000):
        self.conn = conn
        self.orgs = orgs
        self.flush_every = flush_every
        self.facilities: list[list] = []
        self.xrefs: list[list] = []
        self.roles: list[list] = []
        self.events: list[list] = []
        self.loans: list[list] = []
        self.loan_cols: list[str] | None = None
        self.case_facility: list[list] = []
        self.pending = 0

    def tick(self):
        self.pending += 1
        if self.pending >= self.flush_every:
            self.flush()

    def flush(self):
        c = self.conn
        if self.orgs is not None:
            self.orgs.flush(c)
        bulk_upsert(c, "core.facility", FAC_COLS, self.facilities, ["facility_id"], update=False)
        # update=False: never overwrite a human review decision on an existing xref
        bulk_upsert(c, "core.facility_xref", XREF_COLS, self.xrefs, ["source_id", "source_key"], update=False)
        # Roles and events are keyed by their natural key (enrollment/owner/role, buyer/seller/date ...), so an
        # existing key means the same fact: skip it instead of rewriting identical rows on every run (each
        # rewrite leaves a dead tuple and the free plan has 500 MB).
        bulk_upsert(c, "core.facility_role", ROLE_COLS, self.roles, ["source_key"], update=False)
        bulk_upsert(c, "core.ownership_event", EVENT_COLS, self.events, ["source_key"], update=False)
        if self.loans:
            bulk_upsert(c, "core.loan", self.loan_cols, self.loans, ["source_key"])
        bulk_upsert(c, "core.case_facility", ["case_id", "facility_id", "link_method", "match_score"],
                    self.case_facility, ["case_id", "facility_id"])
        c.commit()
        for lst in (self.facilities, self.xrefs, self.roles, self.events, self.loans, self.case_facility):
            lst.clear()
        self.pending = 0


def load_index(conn) -> FacilityIndex:
    idx = FacilityIndex()
    for r in conn.execute("select facility_id::text, canonical_name as name, street, city, state, zip5, phone, "
                          "care_types from core.facility").fetchall():
        idx.add(dict(r))
    return idx


def load_xref(conn, source_ids) -> dict:
    rows = conn.execute("select source_id, source_key, facility_id::text, review_status, match_score from core.facility_xref "
                        "where source_id = any(%s)", (list(source_ids),)).fetchall()
    return {(r["source_id"], r["source_key"]): r for r in rows}


CCN_FACILITY_SQL = """(select ccn, facility_id, 0 as pref from core.facility where ccn is not null
     union all
     select source_key, facility_id, 1 from core.facility_xref where match_method = 'exact_ccn')"""


def ccn_map(conn) -> dict[str, str]:
    """CCN -> facility, including extra CCNs of merged duplicates (kept as exact_ccn xrefs)."""
    out = {}
    for r in conn.execute(f"select ccn, facility_id::text from {CCN_FACILITY_SQL} m order by pref desc").fetchall():
        out[r["ccn"]] = r["facility_id"]          # facility.ccn (pref 0) written last, so it wins
    return out


def new_facility(batch: Batch, idx: FacilityIndex, *, name, street=None, city=None, state=None, zip_=None,
                 phone=None, care_types=(), beds=None, units=None) -> str:
    fid = str(uuid.uuid4())
    row = {"facility_id": fid, "canonical_name": name.strip(), "street": clean(street), "city": clean(city),
           "state": state_code(state), "zip5": zip5(zip_), "phone": clean(phone),
           "care_types": list(care_types), "beds": beds, "units": units, "ccn": None}
    batch.facilities.append([row[c] for c in FAC_COLS])
    idx.add({"facility_id": fid, "name": row["canonical_name"], "street": row["street"], "city": row["city"],
             "state": row["state"], "zip5": row["zip5"], "phone": row["phone"], "care_types": row["care_types"]})
    return fid


def resolve(batch: Batch, idx: FacilityIndex, existing: dict, source_id: str, source_key: str, rec: dict,
            stats: Counter, *, allow_new: bool = True, new_method: str = "fuzzy", new_kwargs=None, matcher=None):
    """Match one source record to a facility (or create one). Returns (facility_id, review_status) or
    (None, None) when the record is left unattached."""
    prev = existing.get((source_id, source_key))
    if prev is not None:
        stats["already_linked"] += 1
        if prev["review_status"] == "rejected":
            # TODO: re-match rejected links excluding the rejected facility; for now they stay unattached.
            return None, None
        return prev["facility_id"], prev["review_status"]
    res: MatchResult = matcher(rec) if matcher else idx.best(rec)
    if res.status in ("auto", "needs_review"):
        stats[res.status] += 1
        batch.xrefs.append([res.facility_id, source_id, source_key, "fuzzy", res.score, res.status])
        return res.facility_id, res.status
    if not allow_new:
        stats["unmatched_skipped"] += 1
        return None, None
    stats["new_facility"] += 1
    fid = new_facility(batch, idx, **(new_kwargs or {}))
    # A new facility is defined by this record: score NULL (no other facility scored >= 75).
    batch.xrefs.append([fid, source_id, source_key, new_method, None, "auto"])
    return fid, "auto"


# ======================================================================= step 1: facilities (CCN exact)

def step_facilities(conn, limit=None) -> dict:
    with conn.cursor() as cur:
        cur.execute(f"""
            insert into core.facility (canonical_name, street, city, state, zip5, phone, care_types, ccn)
            select coalesce(nullif(trim(provider_name),''), nullif(trim(dba_name),''), organization_name),
                   street, city, state, zip5, phone, '{{SNF}}', ccn
            from (select * from stg.cms_snf_enrollment where ccn is not null order by id{_lim(limit)}) e
            on conflict (ccn) do update set canonical_name = excluded.canonical_name, street = excluded.street,
                city = excluded.city, state = excluded.state, zip5 = excluded.zip5,
                phone = coalesce(excluded.phone, core.facility.phone)
            where (core.facility.canonical_name, core.facility.street, core.facility.city, core.facility.state,
                   core.facility.zip5, core.facility.phone)
                  is distinct from (excluded.canonical_name, excluded.street, excluded.city, excluded.state,
                   excluded.zip5, coalesce(excluded.phone, core.facility.phone))""")
        enr = cur.rowcount
        # CCNs that file cost reports but are no longer in the enrollment file (often closed / changed hands)
        cur.execute(f"""
            insert into core.facility (canonical_name, street, city, state, zip5, care_types, beds, ccn)
            select facility_name, street, city, state, zip5, '{{SNF}}', beds, ccn from (
                select distinct on (ccn) * from stg.cms_cost_report c
                where facility_name is not null
                  and not exists (select 1 from stg.cms_snf_enrollment e where e.ccn = c.ccn)
                  -- merged into a CMS-enrollment facility by step_dedupe: the CCN lives on as an xref
                  and not exists (select 1 from core.facility_xref x
                                  where x.source_id = 'cms_cost_report' and x.source_key = c.ccn)
                order by ccn, fiscal_year_end desc{_lim(limit)}) c
            on conflict (ccn) do nothing""")
        cost_new = cur.rowcount
        cur.execute("""
            update core.facility f set beds = c.beds from (
                select distinct on (ccn) ccn, beds from stg.cms_cost_report where beds is not null
                order by ccn, fiscal_year_end desc) c
            where f.ccn = c.ccn and f.beds is distinct from c.beds""")
        beds = cur.rowcount
        cur.execute("""
            insert into core.facility_xref (facility_id, source_id, source_key, match_method, match_score, review_status)
            select f.facility_id, 'cms_snf_enrollment', f.ccn, 'exact_ccn', 100, 'auto'
            from core.facility f join stg.cms_snf_enrollment e on e.ccn = f.ccn
            on conflict (source_id, source_key) do nothing""")
        x1 = cur.rowcount
        cur.execute("""
            insert into core.facility_xref (facility_id, source_id, source_key, match_method, match_score, review_status)
            select f.facility_id, 'cms_cost_report', f.ccn, 'exact_ccn', 100, 'auto'
            from core.facility f
            where f.ccn is not null and not exists (select 1 from stg.cms_snf_enrollment e where e.ccn = f.ccn)
            on conflict (source_id, source_key) do nothing""")
        x2 = cur.rowcount
    conn.commit()
    return {"enrollment_new_or_changed": enr, "cost_report_only_new": cost_new, "beds_updated": beds,
            "xref_new": x1 + x2}


# ======================================================================= step 2: organizations + CMS roles

def step_orgs(conn, limit=None) -> dict:
    orgs = OrgResolver(conn)
    batch = Batch(conn, orgs, flush_every=5000)
    stats = Counter()
    for r in conn.execute("select distinct on (chain_id) chain_id, chain_name from stg.cms_chain_perf "
                          "order by chain_id, report_date desc nulls last").fetchall():
        if orgs.get_chain(r["chain_id"], r["chain_name"]):
            stats["chains"] += 1
    rows = conn.execute(f"""
        select e.enrollment_id, e.associate_id, e.organization_name, e.affiliation_entity_id, e.source_url,
               f.facility_id::text
        from stg.cms_snf_enrollment e join core.facility f on f.ccn = e.ccn order by e.id{_lim(limit)}""").fetchall()
    for r in rows:
        oid = orgs.get(r["organization_name"], r["associate_id"], "operator_entity", chain_id=r["affiliation_entity_id"])
        if oid:
            batch.roles.append([r["facility_id"], oid, "operator", None, None, r["source_url"],
                                f"enr:{r['enrollment_id']}"])
            stats["operator_roles"] += 1
        batch.tick()
    batch.flush()

    rows = conn.execute(f"""
        select o.enrollment_id, o.owner_associate_id, o.owner_name, o.role_text, o.ownership_pct,
               o.association_date, o.source_url, o.is_private_equity, o.is_reit, o.is_holding_company,
               f.facility_id::text
        from stg.cms_snf_owner o
        join stg.cms_snf_enrollment e on e.enrollment_id = o.enrollment_id
        join core.facility f on f.ccn = e.ccn
        where o.owner_type = 'organization' and o.role_text = any(%s)
        order by o.id{_lim(limit)}""", (list(SNF_OWNER_ROLES),)).fetchall()
    for r in rows:
        role, code = SNF_OWNER_ROLES[r["role_text"]]
        if code in ("d", "i") and (r["ownership_pct"] is None or r["ownership_pct"] < 5):
            stats["owner_rows_below_5pct_skipped"] += 1
            continue
        kind = {"lender": "lender", "operator": "management_company"}.get(role, "owner")
        oid = orgs.get(r["owner_name"], r["owner_associate_id"], kind)
        if not oid:
            continue
        if r["is_private_equity"]:
            orgs.force_kind(oid, "private_equity")
        elif r["is_reit"]:
            orgs.force_kind(oid, "reit")
        batch.roles.append([r["facility_id"], oid, role, r["association_date"], None, r["source_url"],
                            f"own:{r['enrollment_id']}:{r['owner_associate_id'] or normalize_name(r['owner_name'])}:{code}"])
        stats[f"{role}_roles"] += 1
        batch.tick()
    batch.flush()
    stats["orgs_created"] = orgs.created
    return dict(stats)


# ======================================================================= step 3: CHOW -> ownership_event

def step_chow(conn, limit=None) -> dict:
    orgs = OrgResolver(conn)
    batch = Batch(conn, orgs, flush_every=2000)
    ccns = ccn_map(conn)
    stats = Counter()
    rows = conn.execute(f"select * from stg.cms_chow order by id{_lim(limit)}").fetchall()
    for r in rows:
        fid = ccns.get(r["buyer_ccn"]) or ccns.get(r["seller_ccn"])
        if not fid:
            stats["no_facility"] += 1
            continue
        buyer = orgs.get(r["buyer_org_name"], r["buyer_associate_id"], "operator_entity")
        seller = orgs.get(r["seller_org_name"], r["seller_associate_id"], "operator_entity")
        key = f"chow:{r['buyer_enrollment_id']}:{r['seller_enrollment_id']}:{r['effective_date']}"
        batch.events.append([fid, "chow", r["effective_date"], seller, buyer, None, r["source_url"], key])
        if buyer:
            batch.roles.append([fid, buyer, "operator", r["effective_date"], None, r["source_url"], key + ":b"])
        if seller:
            batch.roles.append([fid, seller, "operator", None, r["effective_date"], r["source_url"], key + ":s"])
        stats["events"] += 1
        batch.tick()
    batch.flush()
    stats["orgs_created"] = orgs.created
    return dict(stats)


# ======================================================================= step 4: HUD loans

def _loan_cols(conn) -> list[str]:
    cols = ["facility_id", "loan_source", "lender_org_id", "amount", "origination_date", "maturity_date",
            "status", "source_url", "source_key"]
    # Schema request #6: these columns are used as soon as the orchestrator adds them.
    have = table_columns(conn, "core", "loan")
    return cols + [c for c in ("interest_rate", "current_balance", "termination_date", "termination_type") if c in have]


def _hud_names(conn):
    return conn.execute("""
        select distinct on (fha_number) fha_number, property_name, state from (
            select fha_number, property_name, state, as_of from stg.hud_active_loan
            union all select fha_number, property_name, state, as_of from stg.hud_terminated_loan) h
        order by fha_number, as_of desc""").fetchall()


def hud_prefixes(conn) -> dict[str, int]:
    """Qualifying borrower-code prefixes over all distinct HUD loans (active + terminated). Used for matching
    (scores with and without the prefix; the higher wins)."""
    return qualifying_prefixes([r["property_name"] for r in _hud_names(conn)])


def hud_borrower_groups(conn) -> tuple[dict, dict]:
    """(qualifying prefixes, {prefix: verdict}). Only verdict 'keep' prefixes become borrower_group orgs and
    are stripped from the canonical name of a facility created from the loan."""
    rows = _hud_names(conn)
    prefixes = qualifying_prefixes([r["property_name"] for r in rows])
    states = defaultdict(set)
    for r in rows:
        sp = split_borrower_prefix(r["property_name"])
        if sp and sp[0] in prefixes and r["state"]:
            states[sp[0]].add(r["state"])
    return prefixes, {p: borrower_group_verdict(p, states[p]) for p in prefixes}


def step_hud(conn, limit=None) -> dict:
    cfg = thresholds()["matching"].get("hud", {})
    orgs = OrgResolver(conn)
    batch = Batch(conn, orgs, flush_every=500)
    batch.loan_cols = cols = _loan_cols(conn)
    idx = load_index(conn)
    existing = load_xref(conn, ["hud_active", "hud_terminated"])
    prefixes, verdicts = hud_borrower_groups(conn) if cfg.get("strip_borrower_prefix", True) else ({}, {})
    real = {p for p, v in verdicts.items() if v == "keep"}
    # stop keeping generic / ambiguous prefixes as companies (their roles go with them: on delete cascade)
    real_norm = {normalize_name(p) for p in real}
    with conn.cursor() as cur:
        cur.execute("delete from core.organization where org_kind = 'borrower_group' and not (normalized_name = any(%s))",
                    (list(real_norm),))
        dropped_orgs = cur.rowcount
    conn.commit()
    orgs = OrgResolver(conn)
    batch.orgs = orgs
    out = {"qualifying_prefixes": len(prefixes), "borrower_groups_kept": len(real),
           "borrower_groups_dropped": len(prefixes) - len(real), "borrower_group_orgs_deleted": dropped_orgs}
    groups_used = set()
    for source_id, table in (("hud_active", "stg.hud_active_loan"), ("hud_terminated", "stg.hud_terminated_loan")):
        stats = Counter()
        rows = conn.execute(f"select distinct on (fha_number) * from {table} "
                            f"order by fha_number, as_of desc{_lim(limit)}").fetchall()
        if source_id == "hud_terminated":
            # records that may create a facility go first, so match-only (old) terminations of the same
            # building find it in the same run instead of only on the next run
            rows.sort(key=lambda r: not (r["termination_date"] is not None
                                         and str(r["termination_date"]) >= HUD_TERMINATED_NEW_FACILITY_SINCE))
        for r in rows:
            rec = {"name": r["property_name"], "street": r.get("street"), "city": r["city"], "state": r["state"],
                   "zip5": r["zip5"]}
            allow_new = bool(clean(r["property_name"])) and bool(state_code(r["state"]))
            if source_id == "hud_terminated":
                td = r["termination_date"]
                allow_new = allow_new and td is not None and str(td) >= HUD_TERMINATED_NEW_FACILITY_SINCE
            names, prefix = hud_name_variants(r["property_name"], prefixes)
            classes = hud_care_classes(r["soa_category"], r.get("soa_code"))
            fid, status = resolve(batch, idx, existing, source_id, r["fha_number"], rec, stats, allow_new=allow_new,
                                  matcher=lambda rec_, n=names, c=classes: hud_best(idx, rec_, n, c, cfg),
                                  new_kwargs=dict(name=names[-1] if prefix in real else names[0],
                                                  street=r.get("street"),
                                                  city=r["city"], state=r["state"], zip_=r["zip5"],
                                                  care_types=care_types_from_text(r["soa_category"]),
                                                  units=r["units"]))
            if not fid:
                continue
            lender = orgs.get(r.get("holder_name"), None, "lender") if source_id == "hud_active" else None
            if source_id == "hud_active":
                status_txt = "active"
            else:
                claim = (r.get("term_claim_type") or "").upper()
                status_txt = {"CLAIM": "terminated_claim", "NONCLAIM": "terminated_nonclaim"}.get(claim, "terminated")
            vals = {"facility_id": fid, "loan_source": "hud", "lender_org_id": lender,
                    "amount": r["original_amount"], "origination_date": r.get("endorsement_date"),
                    "maturity_date": r.get("maturity_date"), "status": status_txt, "source_url": r["source_url"],
                    "source_key": f"{source_id}:{r['fha_number']}", "interest_rate": r.get("interest_rate"),
                    "current_balance": r.get("amortized_balance"), "termination_date": r.get("termination_date"),
                    "termination_type": r.get("termination_type")}
            batch.loans.append([vals[c] for c in cols])
            stats["loans"] += 1
            if lender:
                batch.roles.append([fid, lender, "lender", r.get("endorsement_date"), None, r["source_url"],
                                    f"{source_id}:{r['fha_number']}"])
            if prefix in real:
                gid = orgs.get_kind(prefix, "borrower_group")
                if gid:
                    groups_used.add(gid)
                    batch.roles.append([fid, gid, "borrower_group", None, None, r["source_url"],
                                        f"bg:{source_id}:{r['fha_number']}"])
                    stats["borrower_group_roles"] += 1
            batch.tick()
        batch.flush()
        out[source_id] = dict(stats)
    out["borrower_groups_linked"] = len(groups_used)
    out["orgs_created"] = orgs.created
    return out


def step_hud_reset(conn, limit=None) -> dict:
    """One-off (Gate 2): drop machine-made HUD links so step_hud re-matches them with the new rules.
    Human decisions (review_status confirmed / rejected) are kept."""
    with conn.cursor() as cur:
        keep = "x.review_status in ('confirmed','rejected')"
        cur.execute(f"""delete from core.loan l where l.loan_source = 'hud' and not exists (
                          select 1 from core.facility_xref x where {keep}
                          and l.source_key = x.source_id || ':' || x.source_key)""")
        loans = cur.rowcount
        cur.execute(f"""delete from core.facility_role r where r.source_key ~ '^(bg:)?hud_(active|terminated):'
                        and not exists (select 1 from core.facility_xref x where {keep}
                          and regexp_replace(r.source_key, '^bg:', '') = x.source_id || ':' || x.source_key)""")
        roles = cur.rowcount
        # facilities that exist only because a HUD loan created them (no other source points at them)
        cur.execute(f"""
            create temp table _hud_only on commit drop as
            select f.facility_id from core.facility f
            where f.ccn is null
              and exists (select 1 from core.facility_xref x where x.facility_id = f.facility_id
                          and x.source_id in ('hud_active','hud_terminated') and x.match_score is null)
              and not exists (select 1 from core.facility_xref x where x.facility_id = f.facility_id
                              and (x.source_id not in ('hud_active','hud_terminated') or {keep}))""")
        cur.execute("""delete from core.facility_xref x where x.source_id in ('hud_active','hud_terminated')
                       and x.review_status in ('auto','needs_review')""")
        xrefs = cur.rowcount
        cur.execute("delete from core.facility f using _hud_only h where f.facility_id = h.facility_id")
        facs = cur.rowcount
    conn.commit()
    return {"loans_deleted": loans, "roles_deleted": roles, "xrefs_deleted": xrefs, "hud_only_facilities_deleted": facs}


def step_direction_reset(conn, limit=None) -> dict:
    """One-off (Gate 2 follow-up): unlink existing machine-made fuzzy links whose names carry different
    direction words, so the next run re-matches them under the direction rule. Human decisions are kept."""
    links = conn.execute("""
        select x.source_id, x.source_key, x.facility_id::text, f.canonical_name
        from core.facility_xref x join core.facility f using (facility_id)
        where x.match_method = 'fuzzy' and x.match_score is not null
          and x.review_status in ('auto','needs_review')""").fetchall()
    names = {}
    for r in conn.execute("select distinct on (fha_number) 'hud_active' s, fha_number k, property_name n "
                          "from stg.hud_active_loan order by fha_number, as_of desc").fetchall():
        names[(r["s"], r["k"])] = r["n"]
    for r in conn.execute("select distinct on (fha_number) 'hud_terminated' s, fha_number k, property_name n "
                          "from stg.hud_terminated_loan order by fha_number, as_of desc").fetchall():
        names[(r["s"], r["k"])] = r["n"]
    for r in conn.execute("select distinct on (state, license_no) state, license_no, facility_name "
                          "from stg.state_license order by state, license_no, as_of desc").fetchall():
        names[(LICENSE_SOURCE.get(r["state"]), r["license_no"])] = r["facility_name"]
    court_rows = conn.execute("select * from stg.court_case").fetchall()
    keys = _case_keys(conn, court_rows)
    for r in court_rows:
        c, d = keys[r["id"]]
        names[(court_source_id(r["source_url"]), f"{c}:{d}")] = clean(r["debtor_name"]) or clean(r["case_name"])
    for r in conn.execute("""select distinct on (x.document_id) x.document_id, x.fields, d.source_id
                             from stg.extraction x join raw.document d on d.id = x.document_id
                             where x.model <> 'prefilter' order by x.document_id, x.created_at desc""").fetchall():
        for i, f in enumerate(_fields_list(r["fields"])):
            names[(r["source_id"], f"doc:{r['document_id']}" + (f":{i}" if i else ""))] = clean(f.get("property_name"))
    bad = [r for r in links if direction_conflict(names.get((r["source_id"], r["source_key"])), r["canonical_name"])]
    stats = Counter(r["source_id"] for r in bad)
    examples = [(r["source_id"], names.get((r["source_id"], r["source_key"])), r["canonical_name"]) for r in bad[:15]]
    case_ids = {f"{r['court']}:{r['docket_no']}": r["case_id"] for r in
                conn.execute("select case_id::text, court, docket_no from core.legal_case").fetchall()}
    with conn.cursor() as cur:
        for r in bad:
            sid, key, fid = r["source_id"], r["source_key"], r["facility_id"]
            if sid in ("hud_active", "hud_terminated"):
                cur.execute("delete from core.loan where source_key = %s", (f"{sid}:{key}",))
                cur.execute("delete from core.facility_role where source_key in (%s, %s)", (f"{sid}:{key}", f"bg:{sid}:{key}"))
            elif sid in LICENSE_SOURCE.values():
                st = next(k for k, v in LICENSE_SOURCE.items() if v == sid)
                cur.execute("delete from core.facility_role where source_key like %s", (f"lic:{st}:{key}:%",))
                cur.execute("delete from core.ownership_event where source_key like %s", (f"lic:{st}:{key}:%",))
            elif key.startswith("doc:"):
                cur.execute("delete from core.ownership_event where source_key = %s", (f"{sid}:{key}",))
            elif key in case_ids:
                cur.execute("delete from core.case_facility where case_id = %s and facility_id = %s "
                            "and link_method = 'facility_name'", (case_ids[key], fid))
            cur.execute("delete from core.facility_xref where source_id = %s and source_key = %s", (sid, key))
    conn.commit()
    return {"links_unlinked": len(bad), **{f"unlinked_{k}": v for k, v in stats.items()}, "examples": examples}


# ======================================================================= step 5: state licenses

# ASSUMPTION (Wave 3.5): a licence that is missing from the newest snapshot of its state for more than this many
# days is closed; it is only attached to a facility we already have, never creates one.
LICENSE_STALE_DAYS = 90


_PARTY_NOISE = re.compile(r"\b(publicly traded|inc|incorporated|llc|l l c|lp|llp|ltd|corp|corporation|co|company|"
                          r"the|dba|pllc|pc|na)\b")


_PARTY_PLACEHOLDERS = {"na", "n a", "none", "empty", "0", "name", "self", "unknown", "tbd", "same"}


def _party_words(name) -> str:
    text = " ".join(re.findall(r"[a-z0-9]+", (name or "").lower()))
    return "" if text in _PARTY_PLACEHOLDERS else " ".join(_PARTY_NOISE.sub(" ", text).split())


def party_key(name) -> str:
    """Compact form of a licensee / manager name for "same party?" tests: lower case, legal-form words dropped,
    then every non-letter/digit removed ("4-K Housing, Inc" == "4K HOUSING INC", "P P Enterprises" == "PP")."""
    return re.sub(r"[^a-z0-9]", "", _party_words(name))


def same_party(a, b) -> bool:
    ka, kb = party_key(a), party_key(b)
    if not ka or not kb:
        return True                       # unknown on one side: never a change
    if ka == kb or (min(len(ka), len(kb)) >= 8 and (ka in kb or kb in ka)):  # "Brookdale Senior Living" vs "... Communities"
        return True
    # ASSUMPTION: >= 92 similarity on the compact form = same party with a typo ("Darsea I" vs "Darsea Holdings"
    # stays a change: 71)
    return fuzz.ratio(ka, kb) >= 92 or fuzz.token_sort_ratio(_party_words(a), _party_words(b)) >= 95  # word order


def license_changes(snaps: list[dict], field: str) -> list[tuple[dict, dict]]:
    """(last snapshot with the old party, first snapshot with the new party) for each real change of `field`.
    Empty values are skipped (never a change); spelling variants are the same party (same_party); a flip that
    returns to the previous party at the next snapshot (A -> B -> A) is treated as a data glitch and dropped."""
    named = [s for s in snaps if party_key(s.get(field))]
    runs: list[list[dict]] = []           # consecutive snapshots with the same party
    for snap in named:
        if runs and same_party(runs[-1][0][field], snap[field]):
            runs[-1].append(snap)
        else:
            runs.append([snap])
    # drop one-snapshot blips between two runs of the same party, then merge those runs
    cleaned: list[list[dict]] = []
    for run in runs:
        if (len(cleaned) >= 2 and len(cleaned[-1]) == 1
                and same_party(cleaned[-2][0][field], run[0][field])):
            blip = cleaned.pop()
            cleaned[-1].extend(blip + run)
        else:
            cleaned.append(run)
    return [(a[-1], b[0]) for a, b in zip(cleaned, cleaned[1:])]


def step_licenses(conn, limit=None) -> dict:
    orgs = OrgResolver(conn)
    batch = Batch(conn, orgs, flush_every=500)
    idx = load_index(conn)
    existing = load_xref(conn, list(LICENSE_SOURCE.values()))
    stats = Counter()
    latest_by_state = {r["state"]: r["d"] for r in conn.execute(
        "select state, max(as_of) d from stg.state_license group by 1").fetchall()}
    rows = conn.execute(f"select * from stg.state_license order by state, license_no, as_of{_lim(limit)}").fetchall()
    snapshots = defaultdict(list)
    for r in rows:
        snapshots[(r["state"], r["license_no"])].append(r)
    ev_cols = EVENT_COLS + ["prior_seen_date"]
    events = []
    for (state, lic), snaps in snapshots.items():
        source_id = LICENSE_SOURCE.get(state)
        if not source_id:
            stats["unknown_state_source"] += 1
            continue
        r = snaps[-1]                                   # latest snapshot describes the facility
        current = (latest_by_state[state] - r["as_of"]).days <= LICENSE_STALE_DAYS
        rec = {"name": r["facility_name"], "street": r["street"], "city": r["city"], "state": state,
               "zip5": r["zip5"]}
        fid, status = resolve(batch, idx, existing, source_id, lic, rec, stats,
                              allow_new=bool(clean(r["facility_name"])) and current, new_method="exact_license",
                              new_kwargs=dict(name=r["facility_name"] or "", street=r["street"], city=r["city"],
                                              state=state, zip_=r["zip5"],
                                              care_types=care_types_from_text(r["license_type"]) or ["AL"],
                                              beds=r["capacity"]))
        if not fid:
            continue
        if not current:
            stats["closed_licence_matched"] += 1
        lic_org = orgs.get(r["licensee"], None, "licensee")
        if lic_org:
            batch.roles.append([fid, lic_org, "licensee", None, None, r["source_url"], f"lic:{state}:{lic}:{lic_org}"])
            stats["licensee_roles"] += 1
        # A licensee (or management company) change between consecutive snapshots -> one event dated by the
        # first snapshot that shows the new party (effective_date) and the last one that showed the old party
        # (prior_seen_date): the change happened in between. These are detection dates, not legal dates.
        for field, etype, kind, tag in (("licensee", "license_change", "licensee", "lic"),
                                        ("management_co", "management_change", "manager", "mgmt")):
            for prev, cur in license_changes(snaps, field):
                seller = orgs.get(prev[field], None, kind)
                buyer = orgs.get(cur[field], None, kind)
                events.append([fid, etype, cur["as_of"], seller, buyer, None, cur["source_url"],
                               f"{tag}:{state}:{lic}:{cur['as_of']}", prev["as_of"]])
                stats[etype] += 1
        batch.tick()
    batch.flush()
    bulk_upsert(conn, "core.ownership_event", ev_cols, events, ["source_key"])
    conn.commit()
    stats["orgs_created"] = orgs.created
    return dict(stats)


def step_closures(conn, limit=None) -> dict:
    """State closure lists (Texas ALF closures) -> facility link. A closure in the last 24 months may create a
    facility (the building still exists and may be sold); older closures only attach to facilities we have."""
    orgs = OrgResolver(conn)
    batch = Batch(conn, orgs, flush_every=500)
    idx = load_index(conn)
    existing = load_xref(conn, list(LICENSE_SOURCE.values()))
    stats = Counter()
    rows = conn.execute("select distinct on (state, facility_key) * from stg.state_closure "
                        f"order by state, facility_key, closure_date desc nulls last{_lim(limit)}").fetchall()
    for r in rows:
        source_id = LICENSE_SOURCE.get(r["state"])
        recent = r["closure_date"] is not None and (date.today() - r["closure_date"]).days <= 730
        rec = {"name": r["facility_name"], "street": r["street"], "city": r["city"], "state": r["state"],
               "zip5": r["zip5"]}
        # same key as the licence roster: a closed facility we already loaded from a snapshot is found directly
        fid, _ = resolve(batch, idx, existing, source_id, r["facility_key"], rec, stats,
                         allow_new=recent and bool(clean(r["facility_name"])), new_method="exact_license",
                         new_kwargs=dict(name=r["facility_name"] or "", street=r["street"], city=r["city"],
                                         state=r["state"], zip_=r["zip5"], care_types=["AL"]))
        if fid:
            existing[(source_id, r["facility_key"])] = {"facility_id": fid, "review_status": "auto"}
        batch.tick()
    batch.flush()
    return dict(stats)


# ======================================================================= step 6: court cases

# ASSUMPTION: court data carries no address, so a court case is linked to a facility by name alone only when
# the distinctive-name score is >= 90 (needs_review); 'auto' additionally needs >= 95, a single clear winner and
# the facility in the court's state. Court cases never create facilities.
COURT_LINK_MIN = 90
COURT_AUTO_MIN = 95


def _court_candidates(names: list[str], raw_names: list[str], debtor: str, cutoff: float = COURT_LINK_MIN):
    """Name-only match of a debtor name against every facility's distinctive name."""
    query = distinctive_name(debtor)
    if not query:
        return []
    hits = process.extract(query, names, scorer=fuzz.token_set_ratio, score_cutoff=cutoff, limit=50)
    out = []
    for _, _, i in hits:
        if direction_conflict(debtor, raw_names[i]):
            continue
        s = name_only_score({"name": debtor}, {"name": raw_names[i]})
        if s >= cutoff:
            out.append((s, i))
    out.sort(reverse=True)
    return out


def _case_keys(conn, rows) -> dict[int, tuple]:
    """stg.court_case id -> (court, docket) after reconciliation. Epiq pages give no court ('UNKNOWN'); when
    exactly one known-court case (in this batch or already in core) has the same docket number and a similar
    debtor name, the UNKNOWN row is folded into it."""
    keys = {r["id"]: (normalize_court(r["court"]), normalize_docket(r["docket_no"])) for r in rows}
    known = defaultdict(list)
    for r in rows:
        c, d = keys[r["id"]]
        if c != "UNKNOWN":
            known[d].append((c, r["debtor_name"] or r["case_name"]))
    for r in conn.execute("select court, docket_no, debtor_name from core.legal_case where court <> 'UNKNOWN'").fetchall():
        known[r["docket_no"]].append((r["court"], r["debtor_name"]))
    for r in rows:
        c, d = keys[r["id"]]
        if c != "UNKNOWN":
            continue
        name = normalize_name(r["debtor_name"] or r["case_name"])
        courts = {kc for kc, kn in known.get(d, []) if fuzz.token_set_ratio(name, normalize_name(kn)) >= 85}
        if len(courts) == 1:
            keys[r["id"]] = (courts.pop(), d)
    return keys


def step_courts(conn, limit=None) -> dict:
    orgs = OrgResolver(conn)
    batch = Batch(conn, orgs, flush_every=200)
    stats = Counter()
    rows = conn.execute(f"select * from stg.court_case order by id{_lim(limit)}").fetchall()

    # 1) core.legal_case, one row per (court, docket) after reconciling court names / docket formats
    merged: dict[tuple, dict] = {}
    pref = {"elevenflo": 0, "stretto": 1, "epiq": 1, "courtlistener": 2}
    case_key = _case_keys(conn, rows)
    for r in sorted(rows, key=lambda r: pref.get(court_source_id(r["source_url"]), 0)):
        key = case_key[r["id"]]
        m = merged.setdefault(key, {})
        for k, v in (("chapter", r["chapter"]), ("filed_date", r["filed_date"]),
                     ("debtor_name", r["debtor_name"] or r["case_name"]),
                     ("status", "closed" if r["terminated_date"] else None),
                     ("docket_url", r["source_url"]), ("claims_agent_url", r["claims_agent_url"])):
            if v is not None:
                m[k] = v               # later (preferred) sources win, nulls never erase a value
    lc_cols = ["court", "docket_no", "chapter", "filed_date", "debtor_name", "status", "docket_url", "claims_agent_url"]
    lc_rows = [[k[0], k[1]] + [m.get(c) for c in lc_cols[2:]] for k, m in merged.items()]
    stats["legal_cases_upserted"] = bulk_upsert(conn, "core.legal_case", lc_cols, lc_rows, ["court", "docket_no"])
    conn.commit()
    case_ids = {(r["court"], r["docket_no"]): r["case_id"] for r in
                conn.execute("select case_id::text, court, docket_no from core.legal_case").fetchall()}

    # 2) link cases to facilities: direct facility-name match (xref) + debtor = known owner/operator org
    facs = conn.execute("select facility_id::text, canonical_name, state from core.facility").fetchall()
    raw_names = [f["canonical_name"] for f in facs]
    names = [distinctive_name(n) for n in raw_names]
    existing = load_xref(conn, ["courtlistener", "epiq", "stretto", "elevenflo", "kroll"])
    org_facs = defaultdict(set)
    for r in conn.execute("select org_id::text, facility_id::text from core.facility_role "
                          "where role in ('owner','operator','licensee','indirect_owner')").fetchall():
        org_facs[r["org_id"]].add(r["facility_id"])
    stem_orgs = defaultdict(set)              # entity stem -> org ids (operator / licensee / owner orgs only)
    for r in conn.execute("select distinct o.org_id::text, o.name from core.organization o join core.facility_role r "
                          "using (org_id) where r.role in ('owner','operator','licensee')").fetchall():
        stem_orgs[entity_stem(r["name"])].add(r["org_id"])
    seen_keys = set()
    for r in rows:
        court, docket = case_key[r["id"]]
        case_id = case_ids.get((court, docket))
        source_id = court_source_id(r["source_url"])
        source_key = f"{court}:{docket}"
        if not case_id or (source_id, source_key) in seen_keys:
            continue
        seen_keys.add((source_id, source_key))
        debtor = clean(r["debtor_name"]) or clean(r["case_name"])
        norm = normalize_name(debtor)
        if not norm or len(norm.split()) < 2 or (r["chapter"] or "") == "13":
            stats["not_matchable"] += 1      # individuals (ch. 13) / one-word names are too ambiguous
            continue
        name_link: tuple | None = None          # (facility_id, score) from a facility-name match
        prev = existing.get((source_id, source_key))
        if prev is not None:
            stats["already_linked"] += 1
            if prev["review_status"] != "rejected":
                name_link = (prev["facility_id"], prev["match_score"])
        else:
            cands = _court_candidates(names, raw_names, debtor)
            if cands:
                top = cands[0][0]
                tops = [i for s, i in cands if s >= top - 2]
                cst = court_state(court)
                if len(tops) > 1 and cst:
                    in_state = [i for i in tops if facs[i]["state"] == cst]
                    tops = in_state or tops
                if len(tops) == 1:
                    fac = facs[tops[0]]
                    # auto only for a single clear winner; ties never auto-link
                    clear_winner = len(cands) == 1 or cands[1][0] < top - 2
                    status = "auto" if (top >= COURT_AUTO_MIN and clear_winner and cst
                                        and fac["state"] == cst) else "needs_review"
                    batch.xrefs.append([fac["facility_id"], source_id, source_key, "fuzzy", top, status])
                    stats[status] += 1
                    name_link = (fac["facility_id"], top)
                else:
                    stats["ambiguous_skipped"] += 1
            else:
                stats["no_facility_match"] += 1
        # debtor is a CMS/licence org we already know (exact normalized name) -> all its facilities
        links = {}
        oid = orgs.by_name.get(norm)
        if oid and org_facs.get(oid):
            links = {fid: ["debtor_org", None] for fid in org_facs[oid]}
            stats["linked_via_org"] += 1
        elif len(entity_stem(debtor).split()) >= 3:
            # same owner entity under its trust / master-tenant name ('... of X DST' debtor, '... of X MT, LLC'
            # licence holder): every org with that stem; the stem must be specific (3+ words)
            fs = {fid for o in stem_orgs.get(entity_stem(debtor), ()) for fid in org_facs.get(o, ())}
            if fs:
                links = {fid: ["debtor_org", None] for fid in fs}
                stats["linked_via_org_stem"] += 1
        if name_link:
            links[name_link[0]] = ["facility_name", name_link[1]]   # a direct name match is the stronger link
        if links:
            orgs.get(debtor, None, "debtor")
        for fid, (method, score) in links.items():
            batch.case_facility.append([case_id, fid, method, score])
        batch.tick()
    batch.flush()
    stats["case_facility_rows"] = conn.execute("select count(*) n from core.case_facility").fetchone()["n"]
    return dict(stats)


# ======================================================================= step 6b: hand-entered facilities

def step_manual(conn, limit=None) -> dict:
    """Facilities that no loader has (e.g. a CCRC with no CMS number) but a hand check tied to a bankruptcy case:
    config/court_facts.yaml `facilities`. Each gets a confirmed 'manual' court xref and a case_facility link, so the
    scorer treats the court link as confirmed by a person. Idempotent: keyed on the court xref."""
    import yaml
    path = PROJECT_ROOT / "config" / "court_facts.yaml"
    items = (yaml.safe_load(path.read_text()) or {}).get("facilities") or [] if path.exists() else []
    stats = Counter()
    for f in items:
        if not str(f.get("source_url", "")).startswith("http"):
            raise ValueError(f"court_facts.yaml facilities: source_url missing for {f.get('name')}")
        court, docket = f["case"].split(":", 1)
        case = conn.execute("select case_id::text from core.legal_case where court = %s and docket_no = %s",
                            (court, docket)).fetchone()
        if not case:
            stats["case_not_found"] += 1
            continue
        sid = f.get("source_id", "elevenflo")
        # one case can cover many homes: the key is case + home name (older single-home entries used the case alone)
        key = f"{f['case']}#{normalize_name(f['name'])}"
        x = conn.execute("select facility_id::text from core.facility_xref where source_id = %s and source_key = any(%s)",
                         (sid, [key, f["case"]] if not f.get("multi") else [key])).fetchone()
        vals = (f["name"].upper(), (f.get("city") or "").upper() or None, f["state"], f.get("units"), f.get("beds"),
                f.get("care_types") or [])
        if not x:
            # the building may already exist (another loader created it): same city and state, and the same name
            # (exact, or the loader name score >= 90 with one clear winner) -> reuse it
            from src.discover.licensed.loans import _name_score
            # no city in the source: the whole state, so the name must then be the only >= 90 match there
            cands = conn.execute("select facility_id::text, canonical_name from core.facility where state = %s "
                                 "and (%s::text is null or upper(coalesce(city, '')) = %s)",
                                 (vals[2], vals[1], vals[1])).fetchall()
            scored = sorted(((_name_score(f["name"], c["canonical_name"]), c["facility_id"]) for c in cands), reverse=True)
            if scored and scored[0][0] >= 90 and (len(scored) == 1 or scored[1][0] < 90):
                x = {"facility_id": scored[0][1]}
                stats["matched_existing"] += 1
            elif scored and scored[0][0] >= 90:
                stats["ambiguous_skipped"] += 1
                continue
            if x:
                conn.execute("insert into core.facility_xref (facility_id, source_id, source_key, match_method, "
                             "match_score, review_status) values (%s,%s,%s,'manual',null,'confirmed') "
                             "on conflict do nothing", (x["facility_id"], sid, key))
        if x:
            fid = x["facility_id"]
            # keep the existing name / size unless the entry states them (a public source)
            conn.execute("update core.facility set city=coalesce(%s, city), state=%s, units=coalesce(%s, units), "
                         "beds=coalesce(%s, beds), care_types=case when cardinality(%s::text[]) > 0 then %s::text[] "
                         "else care_types end, state_source=%s where facility_id = %s",
                         (vals[1], vals[2], vals[3], vals[4], vals[5], vals[5], f.get("state_source"), fid))
            stats["facilities_updated"] += 1
        else:
            fid = conn.execute("insert into core.facility (canonical_name, city, state, units, beds, care_types, "
                               "state_source) values (%s,%s,%s,%s,%s,%s,%s) returning facility_id::text",
                               (*vals, f.get("state_source"))).fetchone()["facility_id"]
            conn.execute("insert into core.facility_xref (facility_id, source_id, source_key, match_method, "
                         "match_score, review_status) values (%s,%s,%s,'manual',null,'confirmed')", (fid, sid, key))
            stats["facilities_created"] += 1
        conn.execute("insert into core.case_facility (case_id, facility_id, link_method, match_score) "
                     "values (%s,%s,'facility_name',null) on conflict (case_id, facility_id) do nothing",
                     (case["case_id"], fid))
    conn.commit()
    return dict(stats)


# ======================================================================= step 7: news / court extractions

_PARTY_FIELDS = ("buyer_or_new_operator", "debtor_or_owner", "care_type")


def _fields_list(fields) -> list[dict]:
    """The main property plus any `other_properties` (which inherit the parties of the main event)."""
    if isinstance(fields, list):
        return [f for f in fields if isinstance(f, dict)]
    if isinstance(fields, dict):
        if fields.get("status") not in (None, "extracted"):
            return []                      # prefilter placeholders etc.
        out = [fields]
        for extra in fields.get("other_properties") or []:
            if isinstance(extra, dict):
                out.append({**{k: fields.get(k) for k in _PARTY_FIELDS}, **extra})
        return out
    return []


def step_extraction(conn, limit=None) -> dict:
    """Match facilities named in stg.extraction (latest extraction per document). Safe on an empty table."""
    orgs = OrgResolver(conn)
    batch = Batch(conn, orgs, flush_every=300)
    stats = Counter()
    rows = conn.execute(f"""
        select distinct on (x.document_id) x.id, x.document_id, x.fields, d.source_id, d.source_url
        from stg.extraction x join raw.document d on d.id = x.document_id
        where x.model <> 'prefilter' and coalesce(x.fields->>'status', 'extracted') = 'extracted'
        order by x.document_id, x.created_at desc{_lim(limit)}""").fetchall()
    if not rows:
        return {"extractions": 0}
    idx = load_index(conn)
    existing = load_xref(conn, {r["source_id"] for r in rows})
    # exact name + city + state already in the hub -> that building (an article with no street address otherwise
    # scores too low and creates a duplicate; e.g. a roundup and its split-out deal item). Ambiguous keys are skipped.
    exact: dict[tuple, set] = {}
    for f_ in conn.execute("select facility_id::text, canonical_name, city, state from core.facility "
                           "where city is not null and state is not null").fetchall():
        exact.setdefault((normalize_name(f_["canonical_name"]), normalize_name(f_["city"]), f_["state"]), set()).add(
            f_["facility_id"])
    for r in rows:
        for i, f in enumerate(_fields_list(r["fields"])):
            name, state = clean(f.get("property_name")), state_code(f.get("state"))
            if not name or not state:
                stats["missing_name_or_state"] += 1
                continue
            street, city = clean(f.get("street")), clean(f.get("city"))
            if city and normalize_name(name) == normalize_name(city):
                stats["name_is_only_the_city"] += 1      # a city name ("Springfield") is a place, not a building: never matched
                continue
            rec = {"name": name, "street": street, "city": city, "state": state, "zip5": clean(f.get("zip5"))}
            key = f"doc:{r['document_id']}" + (f":{i}" if i else "")
            hit = exact.get((normalize_name(name), normalize_name(city or ""), state), set()) if city else set()
            if len(hit) == 1 and (r["source_id"], key) not in existing:
                batch.xrefs.append([next(iter(hit)), r["source_id"], key, "fuzzy", 100, "auto"])   # exact name + city + state
                existing[(r["source_id"], key)] = {"facility_id": next(iter(hit)), "review_status": "auto"}
                stats["exact_name_city"] += 1
            fid, status = resolve(batch, idx, existing, r["source_id"], key, rec, stats,
                                  allow_new=bool(street or city),
                                  new_kwargs=dict(name=name, street=street, city=city, state=state,
                                                  zip_=f.get("zip5"), care_types=care_types_from_text(f.get("care_type")),
                                                  beds=to_int(f.get("beds")), units=to_int(f.get("units"))))
            if not fid:
                continue
            buyer = orgs.get(clean(f.get("buyer_or_new_operator")), None, "buyer")
            seller = orgs.get(clean(f.get("debtor_or_owner")), None, "owner")
            if buyer:
                # effective_date left NULL: extracted event dates are free text (never guessed)
                batch.events.append([fid, "news_reported_sale", None, seller, buyer, None, r["source_url"],
                                     f"{r['source_id']}:{key}"])
                stats["news_reported_sale"] += 1
            batch.tick()
    batch.flush()
    stats["orgs_created"] = orgs.created
    return dict(stats)


# ======================================================================= step 7b: duplicate merge (Gate 2)

def plan_merges(facs: list[dict]) -> list[dict]:
    """Facilities added beyond the CMS enrollment list that share street number + zip5 with a CMS-enrollment
    facility. Each extra goes to one survivor: the CMS facility with the most similar name when several share
    the address. Returns [{extra, survivor, extra_name, survivor_name, name_ratio, n_candidates}]."""
    import re

    def street_number(street):
        # digits only (Gate 2 rule): '801W 190TH ST' and '801 WEST 190TH ST' share number 801
        m = re.match(r"\s*(\d+)", street or "")
        return m.group(1) if m else None

    cms = defaultdict(list)
    for f in facs:
        if f["is_enrollment"]:
            num, z = street_number(f["street"]), zip5(f["zip5"])
            if num and z:
                cms[(num, z)].append(f)
    plan = []
    for f in facs:
        if f["is_enrollment"]:
            continue
        num, z = street_number(f["street"]), zip5(f["zip5"])
        cands = cms.get((num, z)) if num and z else None
        if not cands:
            continue
        scored = sorted(((fuzz.token_set_ratio(normalize_name(f["name"]), normalize_name(c["name"])), c["facility_id"], c)
                         for c in cands), key=lambda t: (-t[0], t[1]))
        ratio, _, best = scored[0]
        plan.append({"extra": f["facility_id"], "survivor": best["facility_id"], "extra_name": f["name"],
                     "survivor_name": best["name"], "name_ratio": round(ratio, 1), "n_candidates": len(cands)})
    return plan


def step_dedupe(conn, limit=None) -> dict:
    facs = [dict(r) for r in conn.execute("""
        select f.facility_id::text, f.canonical_name as name, f.street, f.zip5,
               exists (select 1 from core.facility_xref x where x.facility_id = f.facility_id
                       and x.source_id = 'cms_snf_enrollment') as is_enrollment
        from core.facility f""").fetchall()]
    plan = plan_merges(facs)
    # never cascade-delete Wave 3 rows: an extra that already has signals / a lead is left for the orchestrator
    blocked = {r["facility_id"] for r in conn.execute(
        "select facility_id::text from core.signal union select facility_id::text from app.lead").fetchall()}
    todo = [m for m in plan if m["extra"] not in blocked]
    absorb = Counter(m["survivor"] for m in todo)
    out = {"merged": 0, "blocked_by_signal_or_lead": len(plan) - len(todo),
           "dissimilar_names": [(m["extra_name"], m["survivor_name"], m["name_ratio"]) for m in todo if m["name_ratio"] < 60],
           "multi_absorb": [(next(m["survivor_name"] for m in todo if m["survivor"] == sid),
                             [m["extra_name"] for m in todo if m["survivor"] == sid]) for sid, n in absorb.items() if n >= 2],
           "extra_with_several_cms_candidates": [(m["extra_name"], m["survivor_name"], m["n_candidates"])
                                                 for m in todo if m["n_candidates"] > 1]}
    if not todo:
        return out
    with conn.cursor() as cur:
        cur.execute("create temp table _merge (extra uuid primary key, survivor uuid not null) on commit drop")
        with cur.copy("copy _merge (extra, survivor) from stdin") as cp:
            for m in todo:
                cp.write_row([m["extra"], m["survivor"]])
        for t in ("facility_xref", "facility_role", "ownership_event", "loan"):
            cur.execute(f"update core.{t} t set facility_id = m.survivor from _merge m where t.facility_id = m.extra")
        cur.execute("""insert into core.metric (facility_id, metric, period_end, value, source_id, source_url)
                       select m.survivor, x.metric, x.period_end, x.value, x.source_id, x.source_url
                       from core.metric x join _merge m on x.facility_id = m.extra
                       on conflict do nothing""")
        cur.execute("""insert into core.case_facility (case_id, facility_id, link_method, match_score)
                       select x.case_id, m.survivor, x.link_method, x.match_score
                       from core.case_facility x join _merge m on x.facility_id = m.extra
                       on conflict do nothing""")
        cur.execute("""update core.facility f set care_types = (
                           select coalesce(array_agg(distinct t order by t), '{}') from unnest(f.care_types || e.care_types) t),
                           beds = coalesce(f.beds, e.beds), units = coalesce(f.units, e.units)
                       from _merge m join core.facility e on e.facility_id = m.extra
                       where f.facility_id = m.survivor""")
        cur.execute("delete from core.facility f using _merge m where f.facility_id = m.extra")
        out["merged"] = cur.rowcount
    conn.commit()
    return out


# ======================================================================= step 8: metrics

_METRIC_SQL = [
    ("occupancy_proxy", "cms_cost_report", "stg.cms_cost_report", "fiscal_year_end", "occupancy_proxy"),
    ("net_income", "cms_cost_report", "stg.cms_cost_report", "fiscal_year_end", "net_income"),
    ("total_revenue", "cms_cost_report", "stg.cms_cost_report", "fiscal_year_end", "total_revenue"),
    ("nurse_hprd", "cms_pbj", "stg.cms_pbj_quarter", "quarter_end", "total_nurse_hprd"),
    ("rn_hprd", "cms_pbj", "stg.cms_pbj_quarter", "quarter_end", "rn_hprd"),
    ("avg_census", "cms_pbj", "stg.cms_pbj_quarter", "quarter_end", "avg_census"),
    # SFF list: 2 = Special Focus Facility, 1 = candidate (value per monthly list)
    ("sff_status", "cms_sff", "stg.cms_sff", "report_month", "case when status = 'sff' then 2 else 1 end"),
    ("sff_months_on_list", "cms_sff", "stg.cms_sff", "report_month", "months_on_list"),
]


def step_metrics(conn, limit=None) -> dict:
    out = {}
    with conn.cursor() as cur:
        for metric, source_id, table, period, expr in _METRIC_SQL:
            cur.execute(f"""
                insert into core.metric (facility_id, metric, period_end, value, source_id, source_url)
                select distinct on (m.facility_id, s.{period}) m.facility_id, %s, s.{period}, {expr}, %s, s.source_url
                from (select * from {table} order by id{_lim(limit)}) s join {CCN_FACILITY_SQL} m on m.ccn = s.ccn
                where ({expr}) is not null and s.source_url ~ '^https?://'
                -- a merged facility has two CCNs: the facility's own CCN wins for the same period
                order by m.facility_id, s.{period}, m.pref
                on conflict (facility_id, metric, period_end, source_id)
                do update set value = excluded.value, source_url = excluded.source_url
                where core.metric.value is distinct from excluded.value
                   or core.metric.source_url is distinct from excluded.source_url""", (metric, source_id))
            out[metric] = cur.rowcount
            conn.commit()
    return out


# ======================================================================= step 9: Gate 2 review export

def _review_source(conn, source_id: str, source_key: str, courts: dict) -> tuple:
    """(name, address, url) of the source record behind a needs_review xref."""
    if source_id in ("hud_active", "hud_terminated"):
        table = "stg.hud_active_loan" if source_id == "hud_active" else "stg.hud_terminated_loan"
        r = conn.execute(f"select property_name, city, state, zip5, source_url from {table} "
                         "where fha_number = %s order by as_of desc limit 1", (source_key,)).fetchone()
        if r:
            return r["property_name"], ", ".join(x for x in (r["city"], r["state"], r["zip5"]) if x), r["source_url"]
    elif source_id in LICENSE_SOURCE.values():
        r = conn.execute("select facility_name, street, city, state, zip5, source_url from stg.state_license "
                         "where license_no = %s order by as_of desc limit 1", (source_key,)).fetchone()
        if r:
            return (r["facility_name"], ", ".join(x for x in (r["street"], r["city"], r["state"], r["zip5"]) if x),
                    r["source_url"])
    elif source_key.startswith("doc:"):
        doc_id = int(source_key.split(":")[1])
        r = conn.execute("select x.fields, d.source_url from stg.extraction x join raw.document d on d.id = x.document_id "
                         "where x.document_id = %s order by x.created_at desc limit 1", (doc_id,)).fetchone()
        if r:
            parts = _fields_list(r["fields"])
            i = int(source_key.split(":")[2]) if source_key.count(":") > 1 else 0
            f = parts[i] if i < len(parts) else {}
            addr = ", ".join(x for x in (clean(f.get("street")), clean(f.get("city")), clean(f.get("state"))) if x)
            return clean(f.get("property_name")), addr, r["source_url"]
    elif source_key in courts:
        r = courts[source_key]
        return r["debtor_name"] or r["case_name"], f"court: {r['court']} (no address in court data)", r["source_url"]
    return None, None, None


def step_review(conn, limit=None, path: Path = REVIEW_CSV) -> dict:
    """Export the lowest-scoring needs_review matches for the Gate 2 human review."""
    limit = 20                                # Gate 2 always reviews the 20 lowest-scoring matches
    rows = conn.execute("""
        select x.source_id, x.source_key, x.match_score, f.canonical_name,
               concat_ws(', ', f.street, f.city, f.state, f.zip5) as fac_addr
        from core.facility_xref x join core.facility f using (facility_id)
        where x.review_status = 'needs_review'
        order by x.match_score asc, x.source_id, x.source_key limit %s""", (limit or 20,)).fetchall()
    courts = {}
    if any(r["source_id"] in ("courtlistener", "epiq", "stretto", "elevenflo") for r in rows):
        for c in conn.execute("select * from stg.court_case").fetchall():
            courts.setdefault(f"{normalize_court(c['court'])}:{normalize_docket(c['docket_no'])}", c)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["source", "source_key", "source_name", "source_address", "matched_facility_name",
                    "matched_facility_address", "score", "source_url", "decision (same/different)"])
        for r in rows:
            name, addr, url = _review_source(conn, r["source_id"], r["source_key"], courts)
            w.writerow([r["source_id"], r["source_key"], name, addr, r["canonical_name"], r["fac_addr"],
                        r["match_score"], url, ""])
    return {"rows_exported": len(rows), "file": str(path.relative_to(PROJECT_ROOT))}


def step_hud_sample(conn, limit=None, path: Path = HUD_SAMPLE_CSV) -> dict:
    """20 random HUD links scoring 75-89 (fixed seed, reproducible) for a second human look at HUD matching."""
    prefixes = hud_prefixes(conn)
    with conn.cursor() as cur:
        cur.execute("select setseed(0.20261110)")
        cur.execute("""
            select * from (
                select x.source_id, x.source_key, x.match_score, x.review_status, f.canonical_name,
                       concat_ws(', ', f.street, f.city, f.state, f.zip5) as fac_addr, f.care_types,
                       coalesce(a.property_name, t.property_name) as hud_name, coalesce(a.city, t.city) as city,
                       coalesce(a.state, t.state) as state, coalesce(a.zip5, t.zip5) as zip5,
                       coalesce(a.soa_category, t.soa_category) as category,
                       coalesce(a.source_url, t.source_url) as source_url
                from core.facility_xref x join core.facility f using (facility_id)
                left join lateral (select * from stg.hud_active_loan h where x.source_id = 'hud_active'
                                   and h.fha_number = x.source_key order by as_of desc limit 1) a on true
                left join lateral (select * from stg.hud_terminated_loan h where x.source_id = 'hud_terminated'
                                   and h.fha_number = x.source_key order by as_of desc limit 1) t on true
                where x.source_id in ('hud_active','hud_terminated') and x.match_score >= 75 and x.match_score < 90
                order by x.source_id, x.source_key) s
            order by random() limit 20""")
        rows = cur.fetchall()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["source", "fha_number", "hud_name_raw", "hud_name_stripped", "hud_city", "hud_state", "hud_zip5",
                    "hud_category", "matched_facility_name", "matched_facility_address", "facility_care_types",
                    "score", "review_status", "source_url", "decision (same/different)"])
        for r in rows:
            names, prefix = hud_name_variants(r["hud_name"], prefixes)
            w.writerow([r["source_id"], r["source_key"], r["hud_name"], names[-1] if prefix else "", r["city"],
                        r["state"], r["zip5"], r["category"], r["canonical_name"], r["fac_addr"],
                        "/".join(r["care_types"] or []), r["match_score"], r["review_status"], r["source_url"], ""])
    return {"rows_exported": len(rows), "file": str(path.relative_to(PROJECT_ROOT))}


STEPS = {
    "facilities": step_facilities,
    "orgs": step_orgs,
    "chow": step_chow,
    "hud": step_hud,
    "licenses": step_licenses,
    "closures": step_closures,
    "courts": step_courts,
    "manual": step_manual,
    "extraction": step_extraction,
    "dedupe": step_dedupe,
    "hud_reset": step_hud_reset,
    "direction_reset": step_direction_reset,
    "hud_sample": step_hud_sample,
    "metrics": step_metrics,
    "review": step_review,
}
