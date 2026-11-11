"""Buyer diversity in the DISPLAY ranking (decision Nov 11, 2026).

    python -m src.score.diversity        # records the cap in app.lead.score_breakdown (after `python -m src.score`)

At most `per_buyer` leads of one buyer in the top `top_n` of the main list and of List A (config scoring.buyer_diversity;
List B has no buyer and List C has one row per firm, so the rule cannot bind there). Overflow leads keep their score
and move to just below rank `top_n`, in score order with everything else. Leads without a buyer (NOT FOUND) are never
capped. The same function orders the exports and the website, so all of them agree.
"""
from __future__ import annotations

import sys

NF = "NOT FOUND"


def settings() -> tuple[int, int]:
    from . import rules
    d = rules.cfg()["scoring"].get("buyer_diversity") or {}
    return int(d.get("top_n", 20)), int(d.get("per_buyer", 2))


def buyer_key(r: dict) -> str | None:
    from src.enrich.steps import party_key
    bd = r.get("score_breakdown") or {}
    name = bd.get("buyer_parent") or r.get("buyer")
    if not name or name == NF:
        return None
    return party_key(name)


def diversity_order(rows: list[dict], top_n: int | None = None, per_buyer: int | None = None) -> tuple[list[dict], set]:
    """rows in score order -> (display order, ids of capped rows). Row id = lead_id (else facility_id)."""
    if top_n is None or per_buyer is None:
        t, p = settings()
        top_n, per_buyer = top_n or t, per_buyer or p
    top, rest, seen, capped = [], [], {}, set()
    for r in rows:
        if len(top) >= top_n:
            rest.append(r)
            continue
        k = buyer_key(r)
        if k is not None and seen.get(k, 0) >= per_buyer:
            capped.add(str(r.get("lead_id") or r.get("facility_id")))
            rest.append(r)
            continue
        if k is not None:
            seen[k] = seen.get(k, 0) + 1
        top.append(r)
    return top + rest, capped        # rest stays in score order: overflow lands right below rank top_n


def in_list_a(r: dict) -> bool:
    return r.get("buyer_status") == "named" and "sale_closed_over_12m" not in (r.get("flags") or [])


def main() -> int:
    from psycopg.types.json import Jsonb

    from src.common.db import connect
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "select lead_id::text, facility_id::text, buyer, buyer_status, flags, score, score_breakdown "
            "from app.v_lead_list order by score desc nulls last").fetchall()]
        _, cap_main = diversity_order(rows)
        _, cap_a = diversity_order([r for r in rows if in_list_a(r)])
        conn.execute("update app.lead set score_breakdown = score_breakdown - 'buyer_diversity_cap' "
                     "where score_breakdown ? 'buyer_diversity_cap'")
        top_n, per = settings()
        for lid in cap_main | cap_a:
            where = [x for x, s in (("main list", cap_main), ("List A", cap_a)) if lid in s]
            note = f"more than {per} leads of this buyer in the top {top_n} of the {' and '.join(where)}: shown below rank {top_n}"
            conn.execute("update app.lead set score_breakdown = score_breakdown || %s where lead_id = %s",
                         (Jsonb({"buyer_diversity_cap": note}), lid))
        conn.commit()
    print(f"buyer diversity cap: main list {len(cap_main)}, List A {len(cap_a)} leads moved below the top {top_n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
