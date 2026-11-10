"""Static checks on supabase/migrations: valid Postgres syntax and the hard rules from BUILD_SPEC."""
import re
from pathlib import Path

import pglast
import pytest

MIGRATIONS = sorted((Path(__file__).resolve().parents[2] / "supabase" / "migrations").glob("*.sql"))
ALL_SQL = "\n".join(p.read_text() for p in MIGRATIONS)


@pytest.mark.parametrize("path", MIGRATIONS, ids=lambda p: p.name)
def test_migration_parses(path):
    pglast.parse_sql(path.read_text())


def _table_body(name):
    m = re.search(rf"create table {re.escape(name)} \((.*?)\n\);", ALL_SQL, re.S)
    assert m, f"{name} not found"
    return m.group(1)


@pytest.mark.parametrize("table", [
    "core.facility_role", "core.ownership_event", "core.loan", "core.metric", "core.signal",
])
def test_evidence_tables_require_source_url(table):
    assert re.search(r"source_url\s+text not null", _table_body(table))


def test_only_the_lead_view_is_granted_to_anon():
    grants = re.findall(r"grant select on ([\w.]+) to anon", ALL_SQL)
    assert set(grants) == {"app.v_lead_list"}   # re-granted when the view is recreated (Wave 3.5)


def test_every_schema_has_rls_loop():
    assert "enable row level security" in ALL_SQL
    assert "schemaname in ('raw','stg','core','app')" in ALL_SQL


def test_no_confidential_or_secret_strings():
    lowered = ALL_SQL.lower()
    for needle in ("past_deals", "service_role_key", "sk-ant-", "eyj"):
        assert needle not in lowered
