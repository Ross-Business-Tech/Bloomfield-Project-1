"""Hub 2: core.organization resolver.

Merge rule: CMS associate ID (PAC ID) first, then normalized name.
  * associate ID known and already in core -> that org
  * associate ID new, but an org with the same normalized name and NO associate ID exists -> merge into it
    (it gains the associate ID)
  * otherwise a new org. Two CMS orgs with the same name but different associate IDs stay separate.
  * no associate ID (HUD lenders, licensees, court debtors, news buyers) -> first org with that
    normalized name, else a new org.
New orgs get a Python-side uuid so callers can reference them before the batch is flushed.
"""
from __future__ import annotations

import uuid

from src.common.bulk import bulk_upsert
from src.common.normalize import normalize_name

COLS = ["org_id", "name", "normalized_name", "cms_associate_id", "chain_id", "org_kind"]


class OrgResolver:
    def __init__(self, conn=None):
        self.by_assoc: dict[str, str] = {}
        self.by_name: dict[str, str] = {}
        self.by_chain: dict[str, str] = {}
        self.by_kind_name: dict[tuple, list] = {}
        self.rows: dict[str, dict] = {}
        self.dirty: set[str] = set()
        self.created = 0
        if conn is not None:
            with conn.cursor() as cur:
                cur.execute("select org_id::text, name, normalized_name, cms_associate_id, chain_id, org_kind "
                            "from core.organization")
                for r in cur.fetchall():
                    self._index(dict(r))

    def _index(self, row: dict) -> None:
        oid = row["org_id"]
        self.rows[oid] = row
        if row.get("cms_associate_id"):
            self.by_assoc[row["cms_associate_id"]] = oid
        self.by_name.setdefault(row["normalized_name"], oid)
        if row.get("org_kind") == "borrower_group":
            self.by_kind_name.setdefault(("borrower_group", row["normalized_name"]), []).append(oid)
        if row.get("chain_id") and row.get("org_kind") == "chain":
            self.by_chain.setdefault(row["chain_id"], oid)

    def _new(self, name: str, norm: str, assoc: str | None, kind: str | None, chain_id: str | None) -> str:
        row = {"org_id": str(uuid.uuid4()), "name": name.strip(), "normalized_name": norm,
               "cms_associate_id": assoc, "chain_id": chain_id, "org_kind": kind}
        self._index(row)
        self.dirty.add(row["org_id"])
        self.created += 1
        return row["org_id"]

    def _set(self, oid: str, **values) -> None:
        row = self.rows[oid]
        changed = False
        for k, v in values.items():
            if v and not row.get(k):
                row[k] = v
                changed = True
                if k == "cms_associate_id":
                    self.by_assoc[v] = oid
        if changed:
            self.dirty.add(oid)

    def get(self, name: str | None, assoc_id: str | None = None, kind: str | None = None,
            chain_id: str | None = None) -> str | None:
        assoc = (assoc_id or "").strip() or None
        norm = normalize_name(name)
        if assoc and assoc in self.by_assoc:
            oid = self.by_assoc[assoc]
            self._set(oid, chain_id=chain_id, org_kind=kind)
            return oid
        if not norm:
            return None
        oid = self.by_name.get(norm)
        if oid is not None:
            row = self.rows[oid]
            if assoc is None or not row.get("cms_associate_id"):
                self._set(oid, cms_associate_id=assoc, chain_id=chain_id, org_kind=kind)
                return oid
        return self._new(name, norm, assoc, kind, chain_id)

    def get_kind(self, name: str | None, kind: str) -> str | None:
        """An org of exactly this kind (e.g. 'borrower_group'), matched by normalized name within the kind only,
        so a HUD borrower code never merges into an unrelated CMS org of the same name."""
        norm = normalize_name(name)
        if not norm:
            return None
        for oid in self.by_kind_name.get((kind, norm), []):
            return oid
        oid = str(uuid.uuid4())
        row = {"org_id": oid, "name": name.strip(), "normalized_name": norm, "cms_associate_id": None,
               "chain_id": None, "org_kind": kind}
        self.rows[oid] = row
        self.by_kind_name[(kind, norm)] = [oid]
        self.by_name.setdefault(norm, oid)
        self.dirty.add(oid)
        self.created += 1
        return oid

    def force_kind(self, oid: str | None, kind: str) -> None:
        """PE / REIT flags from CMS outrank whatever kind the org was first created with."""
        if oid and self.rows[oid].get("org_kind") != kind:
            self.rows[oid]["org_kind"] = kind
            self.dirty.add(oid)

    def get_chain(self, chain_id: str, name: str | None) -> str | None:
        if chain_id in self.by_chain:
            return self.by_chain[chain_id]
        norm = normalize_name(name)
        if not norm:
            return None
        oid = self.by_name.get(norm)
        if oid is not None and self.rows[oid].get("chain_id") in (None, chain_id):
            self._set(oid, chain_id=chain_id)
            self.by_chain[chain_id] = oid
            return oid
        oid = self._new(name, norm, None, "chain", chain_id)
        return oid

    def flush(self, conn) -> int:
        if not self.dirty:
            return 0
        rows = [[self.rows[o][c] for c in COLS] for o in self.dirty]
        n = bulk_upsert(conn, "core.organization", COLS, rows, ["org_id"])
        self.dirty.clear()
        return n
