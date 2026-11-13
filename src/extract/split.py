"""Split multi-deal roundup articles ("Senior Living Dealbook", "Transactions and Financings", ...) into one child
document per deal, so each deal gets its own extraction (the reader keeps one main deal per document).

    python -m src.extract.split [--dry-run] [--limit N]

A child is a raw.document with the same source and date, the deal's heading as its title, the deal's paragraphs as
its text, and the article link plus '#deal-<n>' as its source_url (the link still opens the article). The parent
article is kept as it is. Re-runnable: children are keyed by their URL (insert-or-skip).

Heading rule (deterministic, no AI): a paragraph of 3-16 words and <= 110 characters that does not end like a
sentence, followed by at least one body paragraph. One- or two-word section labels ("Transactions", "Management")
start no deal. An article splits only when it yields 2+ deals of >= 150 characters each.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass

ROUNDUP_TITLE = re.compile(r"\b(dealbook|deal sheet|transactions and financings|transactions roundup|deal roundup)\b", re.I)
SPLIT_SOURCES = ("shn_rss", "snn_rss", "shb_rss", "mcknights_sl_rss", "mcknights_ltc_rss")
MIN_ITEM_CHARS = 150
# Multi-property article (Nov 12, 2026 rule): a buyer read from one is shown as 'Unverified'. Wider than ROUNDUP_TITLE
# (which decides what gets split) so that deal digests that are not split still count.
ROUNDUP_WORDS = re.compile(r"\b(dealbook|deal sheet|roundup|round-up|deals? of the week|transactions and financings|"
                           r"deal digest|deal recap)\b", re.I)


def is_roundup(source_url: str | None, title: str | None = None) -> bool:
    """A split deal child ('#deal-N') or a roundup title / URL slug. Body sections alone are NOT used: long feature
    articles have headings too (Nov 12 dry run: 2 of 5 body-only hits were features, not roundups)."""
    if source_url and re.search(r"#deal-\d+$", source_url):
        return True
    slug = re.sub(r"[-_/]+", " ", source_url or "")
    return bool(ROUNDUP_WORDS.search(title or "") or ROUNDUP_WORDS.search(slug))


@dataclass
class Deal:
    n: int
    heading: str
    text: str


def _is_heading(p: str) -> bool:
    words = p.split()
    return (3 <= len(words) <= 16 and len(p) <= 110 and not re.search(r"[.!?:;,\"”]$", p)
            and not p.lower().startswith(("the ", "according", "source:", "photo", "read more")))


def split_deals(body: str | None) -> list[Deal]:
    paras = [p.strip() for p in re.split(r"\n\s*\n", body or "") if p.strip()]
    deals, cur = [], None
    for p in paras:
        if _is_heading(p):
            if cur and cur["body"]:
                deals.append(cur)
            cur = {"heading": p, "body": []}
        elif cur is not None:
            cur["body"].append(p)
    if cur and cur["body"]:
        deals.append(cur)
    out = [Deal(i + 1, d["heading"], "\n\n".join(d["body"])) for i, d in enumerate(deals)
           if len("\n\n".join(d["body"])) >= MIN_ITEM_CHARS]
    return out if len(out) >= 2 else []


def run(conn, dry_run: bool = False, limit: int | None = None, log=print) -> dict:
    from src.common.ingest_run import ingest_run
    from src.common.raw_writer import write_document
    docs = conn.execute(
        "select id, source_id, source_url, title, body_text, published_at from raw.document "
        "where source_id = any(%s) and source_url !~ '#deal-[0-9]+$' order by id", (list(SPLIT_SOURCES),)).fetchall()
    docs = [d for d in docs if ROUNDUP_TITLE.search(d["title"] or "")][: limit or None]
    stats = {"roundups": len(docs), "split": 0, "deals": 0, "new_children": 0}
    by_source: dict[str, list] = {}
    for d in docs:
        deals = split_deals(d["body_text"])
        if not deals:
            continue
        stats["split"] += 1
        stats["deals"] += len(deals)
        by_source.setdefault(d["source_id"], []).append((d, deals))
    if dry_run:
        for sid, items in by_source.items():
            for d, deals in items[:2]:
                log(f"  {d['title'][:70]} -> {len(deals)} deals: " + " | ".join(x.heading[:40] for x in deals[:6]))
        return stats
    for sid, items in by_source.items():
        with ingest_run(conn, sid) as run_:
            for d, deals in items:
                for x in deals:
                    if write_document(conn, sid, run_.run_id, f"{d['source_url']}#deal-{x.n}", title=x.heading,
                                      body_text=x.text, published_at=d["published_at"]):
                        stats["new_children"] += 1
            run_.rows_in = sum(len(deals) for _, deals in items)
    conn.commit()
    return stats


def main(argv=None) -> int:
    from src.common.db import connect
    ap = argparse.ArgumentParser(prog="python -m src.extract.split")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args(argv)
    with connect() as conn:
        print(run(conn, a.dry_run, a.limit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
