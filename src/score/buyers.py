"""Step 6: buyer linking (BUILD_SPEC step 6).

Buyer = court sale buyer (sale order / closed = named; stalking horse / proposed = candidate), extracted
buyer_or_new_operator from news, CHOW buyer org or new licensee within the operator-turnover window.
Nothing found → 'NOT FOUND'. A buyer name is only ever copied from a source; never invented.
CMS chain performance is attached when the buyer org (or an org with the same normalized name) has a chain_id.
"""
from __future__ import annotations

import re

from dataclasses import dataclass, field
from datetime import date

from src.common.normalize import normalize_name

from . import rules
from .signals import BuyerHint

ORIGIN_RANK = {"manual": 4, "court_sale": 3, "news": 2, "chow": 1, "license_change": 1}


@dataclass
class Buyer:
    status: str = "NOT FOUND"
    name: str | None = None
    org_id: str | None = None
    source_url: str | None = None
    origin: str | None = None
    link_status: str = "auto"
    chain: dict | None = None
    prior_hud: bool = False
    family_id: int | None = None
    alternatives: list[str] = field(default_factory=list)
    event_date: object = None          # date of the ownership change (news: latest date the article states)
    parent: str | None = None          # parent org: stated by a source, or the CMS owner behind a shell
    parent_basis: str | None = None    # 'source' (config / article) or 'cms_ownership'
    buyer_type: str | None = None      # rules.BUYER_TYPES
    type_basis: str | None = None      # 'source' / 'cms' / 'name' / 'default'
    type_source: str | None = None     # 'confirmed' (source / CMS flag) or 'inferred' (from the name)
    date_basis: str | None = None      # 'source' / 'sale_order' / 'cms_chow' / None (undated)
    entity: str | None = None          # the shell / subsidiary that actually signed, when a parent is shown


def pick(hints: list[BuyerHint]) -> BuyerHint | None:
    """named beats candidate; then confirmed (auto) links; then court > news > CHOW; then most recent."""
    if not hints:
        return None
    return max(hints, key=lambda h: (h.status == "named", h.link_status == "auto", ORIGIN_RANK.get(h.origin, 0),
                                     h.weight, h.event_date.toordinal() if h.event_date else 0))


class BuyerResolver:
    def __init__(self, conn):
        self.by_name = {}
        for r in conn.execute("select org_id::text, normalized_name, chain_id from core.organization").fetchall():
            cur = self.by_name.get(r["normalized_name"])
            if cur is None or (r["chain_id"] and not cur[1]):
                self.by_name[r["normalized_name"]] = (r["org_id"], r["chain_id"])
        self.org_chain = {r["org_id"]: r["chain_id"] for r in conn.execute(
            "select org_id::text, chain_id from core.organization where chain_id is not null").fetchall()}
        self.chains = {r["chain_id"]: dict(r) for r in conn.execute(
            "select distinct on (chain_id) chain_id, chain_name, report_date, num_facilities, num_states, "
            "avg_overall_rating, pct_sff, total_fines, source_url from stg.cms_chain_perf "
            "order by chain_id, report_date desc").fetchall()}
        # facilities each org is tied to, and which facilities have (had) a HUD Section 232 loan
        self.hud_facs = {r["facility_id"] for r in conn.execute(
            "select distinct facility_id::text from core.loan where loan_source = 'hud'").fetchall()}
        self.org_facs: dict[str, set] = {}
        for r in conn.execute("select org_id::text, facility_id::text from core.facility_role "
                              "where role in ('owner','operator','licensee','indirect_owner','borrower_group')").fetchall():
            self.org_facs.setdefault(r["org_id"], set()).add(r["facility_id"])
        # facility -> its CMS owner orgs (to find the real buyer behind an OpCo / PropCo shell)
        self.fac_owners: dict[str, list] = {}
        for r in conn.execute("select r.facility_id::text, r.org_id::text, o.name from core.facility_role r "
                              "join core.organization o using (org_id) where r.role in ('owner','indirect_owner')").fetchall():
            self.fac_owners.setdefault(r["facility_id"], []).append((r["org_id"], r["name"]))
        # CMS-flagged org kinds (SNF owner file: private equity / REIT; HUD: lender) -> confirmed buyer types
        self.org_kind = {}
        for r in conn.execute("select normalized_name, org_kind from core.organization "
                              "where org_kind in ('private_equity','reit','lender')").fetchall():
            self.org_kind[r["normalized_name"]] = r["org_kind"]
        # latest CMS change of ownership per facility (dates an undated buyer)
        # (facility, normalized buyer name) -> latest CMS change of ownership TO that buyer (dates an undated buyer)
        self.last_chow = {}
        for r in conn.execute("select e.facility_id::text, o.name, max(e.effective_date) d from core.ownership_event e "
                              "join core.organization o on o.org_id = e.buyer_org_id where e.event_type in "
                              "('chow','license_change') and e.effective_date is not null group by 1, 2").fetchall():
            self.last_chow[(r["facility_id"], normalize_name(r["name"]))] = r["d"]
        self.known = {}
        for x in rules.buyer_orgs():
            for n in [x["name"]] + list(x.get("aliases") or []):
                self.known[normalize_name(n)] = x
        # parent-brand index (Wave 4): operators / managers / licensees that run 2+ homes, by their first brand word.
        # Sources: CMS roles and the latest state licence rosters (management company / licensee).
        self.fac_city = {r["f"]: (r["city"] or "", r["state"] or "") for r in conn.execute(
            "select facility_id::text f, city, state from core.facility").fetchall()}
        homes: dict[str, set] = {}
        for r in conn.execute("select o.name, r.facility_id::text f from core.facility_role r join core.organization o "
                              "using (org_id) where r.role in ('operator','licensee','owner','indirect_owner')").fetchall():
            homes.setdefault(r["name"], set()).add(r["f"])
        # every roster snapshot counts (an operator that has since handed homes on is still that brand's operator)
        for r in conn.execute("select distinct state, license_no, licensee, management_co from stg.state_license "
                              "where licensee is not null or management_co is not null").fetchall():
            for n in (r["management_co"], r["licensee"]):
                if n:
                    homes.setdefault(n, set()).add(f"{r['state']}:{r['license_no']}")
        self.brand_index: dict[str, list] = {}
        for name, fs in homes.items():
            words = rules.brand_words(name)
            if words and len(fs) >= 2 and not rules.is_shell_buyer(name, None) and not re.match(r"\s*\d", name):
                self.brand_index.setdefault(words[0], []).append((name, words, len(fs)))

    def brand_parent(self, facility_id: str, buyer: str) -> str | None:
        """'Acme Springfield OpCo LLC' -> 'Acme Senior Living, LLC': the one multi-home operator whose brand words
        are all in the shell's name once legal / generic words and the home's own city and state are removed, and
        whose first brand word is the shell's first. Several different candidate operators = ambiguous = None."""
        from src.enrich.steps import party_key
        # only explicit OpCo / PropCo legal shells; never an address-named one ("3920 Rosewood Way OpCo") and never a
        # company named after the home itself (its first word is usually just a common place word)
        if not rules._SHELL.search(buyer or "") or re.match(r"\s*\d", buyer or ""):
            return None
        city, state = self.fac_city.get(facility_id, ("", ""))
        words = rules.brand_words(buyer, extra_generic=rules.brand_words(f"{city} {rules.state_name(state)}", raw=True))
        if not words:
            return None
        cands = {}
        for name, pw, n in self.brand_index.get(words[0], []):
            if set(pw) <= set(words) and normalize_name(name) != normalize_name(buyer):
                cands.setdefault(party_key(name), (n, name))
        if len(cands) != 1:
            return None
        return next(iter(cands.values()))[1]

    def org_for(self, name: str | None, org_id: str | None):
        if org_id:
            return org_id, self.org_chain.get(org_id)
        hit = self.by_name.get(normalize_name(name)) if name else None
        return hit if hit else (None, None)

    def cms_parent(self, facility_id: str, buyer: str) -> str | None:
        """The CMS owner org behind a shell buyer: among this facility's owners (not shells themselves, not the buyer),
        the one that owns the most facilities, if it owns at least 2."""
        best = None
        for org_id, name in self.fac_owners.get(facility_id, []):
            if rules.is_shell_buyer(name, None) or normalize_name(name) == normalize_name(buyer):
                continue
            n = len(self.org_facs.get(org_id, ()))
            if n >= 2 and (best is None or (n, name) > best):
                best = (n, name)
        return best[1] if best else None

    def _chow_date(self, facility_id: str, *names):
        """Latest CMS change of ownership of this facility to this buyer (or its parent); None if CMS shows none."""
        ds = [self.last_chow.get((facility_id, normalize_name(n))) for n in names if n]
        ds = [d for d in ds if d]
        return max(ds) if ds else None

    def resolve(self, facility_id: str, hints: list[BuyerHint], facility_name: str | None = None,
                today: date | None = None) -> Buyer:
        h = pick(hints)
        if h is None:
            return Buyer()
        org_id, chain_id = self.org_for(h.name, h.org_id)
        chain = self.chains.get(chain_id) if chain_id else None
        prior_hud = bool(org_id and (self.org_facs.get(org_id, set()) - {facility_id}) & self.hud_facs)
        alts = sorted({f"{x.name} ({x.status}, {x.origin})" for x in hints if x is not h and x.name != h.name})[:5]
        b = Buyer(h.status, h.name, org_id, h.source_url, h.origin, h.link_status,
                  {k: (float(v) if hasattr(v, "is_finite") else (str(v) if k == "report_date" else v))
                   for k, v in chain.items()} if chain else None,
                  prior_hud, h.family_id, alts, h.event_date, h.parent, "source" if h.parent else None)
        first = h.name.split(";")[0].strip()          # joint buyers "A; B": type / parent from the first named
        known = self.known.get(normalize_name(first))
        if not b.parent and known and known.get("parent"):
            b.parent, b.parent_basis = known["parent"], "source"
        # Gate 3: an OpCo / PropCo or facility-named LLC is a shell; show the CMS owner behind it, else 'shell'
        if b.status == "named" and not b.parent and rules.is_shell_buyer(first, facility_name):
            parent = self.cms_parent(facility_id, first)
            if parent:
                b.parent, b.parent_basis = parent, "cms_ownership"
            elif (brand := self.brand_parent(facility_id, first)):
                b.parent, b.parent_basis = brand, "brand_match"      # same brand, multi-home operator (inferred)
            else:
                b.status = "shell"
        if b.parent:
            b.entity = h.name
        b.buyer_type, b.type_basis = rules.buyer_type(b.parent or first, self.known)
        kind = self.org_kind.get(normalize_name(b.parent or first))
        if b.type_basis != "source" and kind:
            b.buyer_type = {"private_equity": "PE/investor", "reit": "REIT", "lender": "lender"}[kind]
            b.type_basis = "cms"
        b.type_source = "confirmed" if b.type_basis in ("source", "cms") else "inferred"
        # Gate 3: date the ownership change: the hint's own date (article / sale order / CHOW), else the facility's
        # latest CMS change of ownership; with neither, the buyer is 'named (undated)' at lower weight
        if b.event_date:
            b.date_basis = {"court_sale": "sale_order", "chow": "cms_chow", "license_change": "cms_chow"}.get(
                h.origin, "source")
        elif b.status == "named" and self._chow_date(facility_id, first, b.parent):
            b.event_date, b.date_basis = self._chow_date(facility_id, first, b.parent), "cms_chow"
        elif b.status == "named":
            b.status = "named (undated)"
        # Gate 3: a buyer counts as named only if the ownership change is <= buyer_named_max_months old
        if b.status == "named" and b.event_date and today and \
                rules.months_between(b.event_date, today) > rules.cfg()["scoring"].get("buyer_named_max_months", 24):
            b.status = "stale"
        return b
