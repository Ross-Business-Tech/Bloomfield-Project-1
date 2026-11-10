"""Recall audit (Gate 3): where do real distress stories fall out of the funnel?

python -m eval.recall_audit            -> eval/reports/recall_audit.md + two sample CSVs for the human check

1. Holdout trace: each holdout case (H03, H05, H08, H09, H10, H12, H13, H14) through the funnel
   fetched -> keyword prefilter -> AI extraction -> facility link -> signal -> main / watch list.
2. 30 random keyword-skipped articles (eval/reports/recall_keyword_skips.csv) to check what the prefilter drops.
3. Unlinked articles: AI-extracted distress articles that never linked to a facility.
4. 30 random watch-list homes (eval/reports/recall_watch_sample.csv).
Read-only: never writes to the database.
"""
from __future__ import annotations

import csv
import json
import random
import re
from collections import Counter
from pathlib import Path

from rapidfuzz import fuzz

from src.common.db import connect
from src.common.normalize import normalize_name
from src.extract.client import DEFAULT_MODEL

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "eval" / "reports"
CASES = ("H03", "H05", "H08", "H09", "H10", "H12", "H13", "H14")
NEWS = ("shb_rss", "shn_rss", "snn_rss", "gnews_rss", "mcknights_ltc_rss", "mcknights_sl_rss", "gdelt_doc")
SEED = 7


def _url_key(u: str | None) -> str:
    u = (u or "").strip().lower()
    u = re.sub(r"^https?://(www\.)?", "", u)
    return u.split("?")[0].split("#")[0].rstrip("/")


def _latest(path: Path, prefix: str) -> Path | None:
    files = sorted(path.glob(f"{prefix}_*.csv"))
    return files[-1] if files else None


def holdout_trace(conn, main_ids: set, watch_ids: set) -> list[dict]:
    cases = {c["id"]: c for c in json.load(open(ROOT / "eval/extraction/holdout/holdout_labels.json"))["cases"]}
    docs = {_url_key(r["source_url"]): r for r in conn.execute(
        "select id, source_id, source_url, title, published_at::date pub from raw.document").fetchall()}
    facs = conn.execute("select facility_id::text, canonical_name, city, state, care_types from core.facility").fetchall()
    out = []
    for cid in CASES:
        c = cases[cid]
        lab = c.get("labels") or {}
        row = {"id": cid, "url": c["url"], "property": lab.get("property_name"), "city": lab.get("city"),
               "state": lab.get("state"), "care_type": "/".join(lab.get("care_type") or []),
               "distress": lab.get("distress_type"), "is_distress": c.get("is_distress")}
        d = docs.get(_url_key(c["url"]))
        row["fetched"] = bool(d)
        # is the building in the facility hub at all? (name + city, name-only when no city)
        name = normalize_name(lab.get("property_name"))
        cands = []
        if name and name != "not found":
            for f in facs:
                s = fuzz.token_set_ratio(name, normalize_name(f["canonical_name"]))
                if s >= 85 and (not lab.get("city") or lab["city"] in ("NOT FOUND",) or
                                (f["city"] or "").lower() == str(lab["city"]).lower()):
                    cands.append((s, f))
        cands.sort(key=lambda x: -x[0])
        row["facility_in_hub"] = f"{cands[0][1]['canonical_name']} ({cands[0][1]['city']}, {cands[0][1]['state']})" \
            if cands else "no"
        hub_fid = cands[0][1]["facility_id"] if cands else None
        if not d:
            row["stage_reached"] = "not fetched (source not covered)"
        else:
            ex = conn.execute("select model, fields from stg.extraction where document_id = %s", (d["id"],)).fetchall()
            pre = [e for e in ex if e["model"] == "prefilter"]
            ai = [e for e in ex if e["model"] == DEFAULT_MODEL]
            xr = conn.execute("select facility_id::text from core.facility_xref where source_id = %s and "
                              "(source_key = %s or source_key like %s)",
                              (d["source_id"], f"doc:{d['id']}", f"doc:{d['id']}:%")).fetchall()
            fids = {x["facility_id"] for x in xr}
            sig = conn.execute("select count(*) n from core.signal where source_url = %s", (d["source_url"],)).fetchone()["n"]
            row["ai_distress_types"] = ", ".join((ai[0]["fields"].get("distress_type") or [])) if ai else ""
            if pre and not ai:
                row["stage_reached"] = "skipped by keyword prefilter"
            elif not ai:
                row["stage_reached"] = "fetched, not extracted"
            elif not fids:
                row["stage_reached"] = "extracted, not linked to a facility"
            elif not sig:
                row["stage_reached"] = "linked, no signal"
            elif fids & main_ids:
                row["stage_reached"] = "MAIN LIST"
            elif fids & watch_ids:
                row["stage_reached"] = "watch list"
            else:
                row["stage_reached"] = "signal, but no lead"
        if hub_fid and row["stage_reached"] != "MAIN LIST":
            row["hub_home_list"] = "main" if hub_fid in main_ids else "watch" if hub_fid in watch_ids else "none"
        out.append(row)
    return out


def keyword_skips(conn, n=30) -> tuple[list[dict], int]:
    rows = conn.execute("select d.id, d.source_id, d.title, d.published_at::date pub, d.source_url, "
                        "left(d.body_text, 400) body from stg.extraction e join raw.document d on d.id = e.document_id "
                        "where e.model = 'prefilter'").fetchall()
    sample = random.Random(SEED).sample(rows, min(n, len(rows)))
    return [dict(r) for r in sample], len(rows)


def unlinked(conn) -> dict:
    rows = conn.execute("""
        select d.id, d.source_id, d.title, d.source_url, e.fields from stg.extraction e join raw.document d on d.id = e.document_id
        where e.model = %(m)s and e.fields->>'status' = 'extracted' and d.source_id = any(%(src)s)
          and not exists (select 1 from core.facility_xref x where x.source_id = d.source_id
                          and (x.source_key = 'doc:' || d.id or x.source_key like 'doc:' || d.id || ':%%'))""",
                        {"m": DEFAULT_MODEL, "src": list(NEWS)}).fetchall()
    total = conn.execute("select count(*) n from stg.extraction e join raw.document d on d.id = e.document_id "
                         "where e.model = %(m)s and e.fields->>'status' = 'extracted' and d.source_id = any(%(src)s)",
                         {"m": DEFAULT_MODEL, "src": list(NEWS)}).fetchone()["n"]
    def as_list(v):
        return [v] if isinstance(v, str) else list(v or [])
    distress = [r for r in rows if set(as_list(r["fields"].get("distress_type"))) - {"none", "sale", "NOT FOUND"}]
    why = Counter()
    for r in distress:
        f = r["fields"]
        pn = f.get("property_name")
        why["no property name" if pn in (None, "", "NOT FOUND") else
            "no state" if f.get("state") in (None, "", "NOT FOUND") else "name/address did not match a facility"] += 1
    care = Counter(c for r in distress for c in (as_list(r["fields"].get("care_type")) or ["unknown"]))
    return {"extracted": total, "unlinked": len(rows), "unlinked_distress": len(distress), "why": dict(why),
            "care_types": dict(care), "examples": [(r["title"], r["source_url"]) for r in distress[:10]]}


def watch_sample(n=30) -> list[dict]:
    p = _latest(ROOT / "output", "watch")
    rows = list(csv.DictReader(open(p))) if p else []
    return random.Random(SEED).sample(rows, min(n, len(rows)))


def main() -> None:
    lp, wp = _latest(ROOT / "output", "leads"), _latest(ROOT / "output", "watch")
    main_rows = list(csv.DictReader(open(lp))) if lp else []
    watch_rows = list(csv.DictReader(open(wp))) if wp else []
    with connect() as conn:
        by_ccn = {r["ccn"]: r["facility_id"] for r in conn.execute(
            "select ccn, facility_id::text from core.facility where ccn is not null").fetchall()}
        lid = {r["lead_id"]: r["facility_id"] for r in conn.execute(
            "select lead_id::text, facility_id::text from app.lead").fetchall()}
        main_ids = {lid.get(r["lead_id"]) for r in main_rows} - {None}
        names = {(normalize_name(r["canonical_name"]), r["state"]): r["facility_id"] for r in conn.execute(
            "select facility_id::text, canonical_name, state from core.facility").fetchall()}
        watch_ids = {by_ccn.get(r["ccn"]) or names.get((normalize_name(r["property"]), r["state"])) for r in watch_rows} - {None}
        trace = holdout_trace(conn, main_ids, watch_ids)
        skips, n_skips = keyword_skips(conn)
        un = unlinked(conn)
    ws = watch_sample()
    with open(REPORTS / "recall_keyword_skips.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "source_id", "pub", "title", "source_url", "body", "relevant"])
        w.writeheader()
        for r in skips:
            w.writerow({**r, "relevant": ""})
    with open(REPORTS / "recall_watch_sample.csv", "w", newline="", encoding="utf-8") as f:
        cols = ["property", "city", "state", "care_types", "stage", "distress_type", "score", "buyer", "buyer_status",
                "flags", "source_urls"]
        w = csv.DictWriter(f, fieldnames=cols + ["should_be_main"], extrasaction="ignore")
        w.writeheader()
        for r in ws:
            w.writerow({**r, "should_be_main": ""})
    print(json.dumps({"trace": trace, "keyword_skips_total": n_skips, "unlinked": un}, default=str, indent=1))


if __name__ == "__main__":
    main()
