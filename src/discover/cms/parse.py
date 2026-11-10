"""Map raw CMS API rows to stg column dicts. Unknown / blank -> None (never guess). IDs stay text."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from src.common.normalize import zip5


def s(v) -> str | None:
    """Trimmed text or None when blank."""
    if v is None:
        return None
    v = re.sub(r"\s+", " ", str(v)).strip()
    return v or None


def num(v) -> float | None:
    v = s(v)
    if v is None:
        return None
    try:
        return float(Decimal(v.replace(",", "").replace("$", "")))
    except InvalidOperation:
        return None


def integer(v) -> int | None:
    n = num(v)
    return int(n) if n is not None and n == int(n) else None


def dt(v) -> date | None:
    v = s(v)
    if v is None:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(v[:10] if fmt == "%Y-%m-%d" else v, fmt).date()
        except ValueError:
            continue
    return None


def yn(v) -> bool | None:
    v = (s(v) or "").upper()
    return True if v == "Y" else False if v == "N" else None


def join(*parts) -> str | None:
    return " ".join(p for p in (s(x) for x in parts) if p) or None


# ── change of ownership ─────────────────────────────────────────────────────────
def map_chow(r: dict, source_url: str) -> dict:
    return {
        "source_url": source_url,
        "effective_date": dt(r.get("EFFECTIVE DATE")),
        "chow_type": s(r.get("CHOW TYPE TEXT")),
        "state": s(r.get("ENROLLMENT STATE - BUYER")) or s(r.get("ENROLLMENT STATE - SELLER")),
        "buyer_enrollment_id": s(r.get("ENROLLMENT ID - BUYER")),
        "buyer_ccn": s(r.get("CCN - BUYER")),
        "buyer_associate_id": s(r.get("ASSOCIATE ID - BUYER")),
        "buyer_org_name": s(r.get("ORGANIZATION NAME - BUYER")),
        "buyer_dba_name": s(r.get("DOING BUSINESS AS NAME - BUYER")),
        "seller_enrollment_id": s(r.get("ENROLLMENT ID - SELLER")),
        "seller_ccn": s(r.get("CCN - SELLER")),
        "seller_associate_id": s(r.get("ASSOCIATE ID - SELLER")),
        "seller_org_name": s(r.get("ORGANIZATION NAME - SELLER")),
        "seller_dba_name": s(r.get("DOING BUSINESS AS NAME - SELLER")),
    }


def chow_external_id(r: dict) -> str:
    return "|".join(str(r.get(k) or "") for k in ("ENROLLMENT ID - BUYER", "ENROLLMENT ID - SELLER", "EFFECTIVE DATE"))


# ── owners (CHOW owner info and All Owners share one layout) ────────────────────
def _owner_common(r: dict, source_url: str) -> dict:
    kind = (s(r.get("TYPE - OWNER")) or "").upper()
    if kind == "I":
        name = join(r.get("FIRST NAME - OWNER"), r.get("MIDDLE NAME - OWNER"), r.get("LAST NAME - OWNER"))
    else:
        name = s(r.get("ORGANIZATION NAME - OWNER"))
    return {
        "source_url": source_url,
        "enrollment_id": s(r.get("ENROLLMENT ID")),
        "associate_id": s(r.get("ASSOCIATE ID")),
        "organization_name": s(r.get("ORGANIZATION NAME")),
        "owner_associate_id": s(r.get("ASSOCIATE ID - OWNER")),
        "owner_type": {"I": "individual", "O": "organization"}.get(kind),
        "owner_name": name,
        "role_text": s(r.get("ROLE TEXT - OWNER")),
        "ownership_pct": num(r.get("PERCENTAGE OWNERSHIP")),
        "association_date": dt(r.get("ASSOCIATION DATE - OWNER")),
    }


def map_chow_owner(r: dict, source_url: str) -> dict:
    # The dataset has no buyer/seller column; chow_side stays NULL (core-builder can join on enrollment_id to cms_chow).
    return {**_owner_common(r, source_url), "chow_side": None}


def map_snf_owner(r: dict, source_url: str) -> dict:
    return {
        **_owner_common(r, source_url),
        "is_private_equity": yn(r.get("PRIVATE EQUITY COMPANY - OWNER")),
        "is_reit": yn(r.get("REIT - OWNER")),
        "is_holding_company": yn(r.get("HOLDING COMPANY - OWNER")),
    }


def owner_external_id(r: dict) -> str:
    return "|".join(str(r.get(k) or "") for k in ("ENROLLMENT ID", "ASSOCIATE ID - OWNER", "ROLE CODE - OWNER"))


# ── enrollments ─────────────────────────────────────────────────────────────────
def map_enrollment(r: dict, source_url: str) -> dict:
    return {
        "source_url": source_url,
        "enrollment_id": s(r.get("ENROLLMENT ID")),
        "ccn": s(r.get("CCN")),
        "npi": s(r.get("NPI")),
        "associate_id": s(r.get("ASSOCIATE ID")),
        "organization_name": s(r.get("ORGANIZATION NAME")),
        "dba_name": s(r.get("DOING BUSINESS AS NAME")),
        "street": join(r.get("ADDRESS LINE 1"), r.get("ADDRESS LINE 2")),
        "city": s(r.get("CITY")),
        "state": s(r.get("STATE")),
        "zip5": zip5(r.get("ZIP CODE")),
        "phone": None,   # not in this dataset
        "organization_type": s(r.get("ORGANIZATION TYPE STRUCTURE")),
        "proprietary_nonprofit": s(r.get("PROPRIETARY_NONPROFIT")),
        "provider_name": s(r.get("NURSING HOME PROVIDER NAME")),
        "affiliation_entity_name": s(r.get("AFFILIATION ENTITY NAME")),
        "affiliation_entity_id": s(r.get("AFFILIATION ENTITY ID")),
    }


# ── cost report ─────────────────────────────────────────────────────────────────
def map_cost_report(r: dict, source_url: str) -> dict:
    days, bed_days = num(r.get("Total Days Total")), num(r.get("Total Bed Days Available"))
    occ = round(days / bed_days, 4) if days is not None and bed_days else None
    return {
        "source_url": source_url,
        "ccn": s(r.get("Provider CCN")),
        "fiscal_year_end": dt(r.get("Fiscal Year End Date")),
        "facility_name": s(r.get("Facility Name")),
        "street": s(r.get("Street Address")),
        "city": s(r.get("City")),
        "state": s(r.get("State Code")),
        "zip5": zip5(r.get("Zip Code")),
        "beds": integer(r.get("Number of Beds")),
        "total_days": days,
        "total_bed_days_available": bed_days,
        "occupancy_proxy": occ,
        "net_income": num(r.get("Net Income")),
        "total_revenue": num(r.get("Net Patient Revenue")),   # ASSUMPTION: net patient revenue = revenue
    }


# ── chain performance ───────────────────────────────────────────────────────────
_CHAIN_MAIN = {
    "Chain", "Chain ID", "Number of facilities", "Number of states and territories with operations",
    "Average overall 5-star rating", "Total amount of fines in dollars",
}


def map_chain_perf(r: dict, source_url: str, report_date: date | None) -> dict:
    n, sff = num(r.get("Number of facilities")), num(r.get("Number of Special Focus Facilities (SFF)"))
    return {
        "source_url": source_url,
        "chain_id": s(r.get("Chain ID")),
        "chain_name": s(r.get("Chain")),
        "report_date": report_date,
        "num_facilities": integer(r.get("Number of facilities")),
        "num_states": integer(r.get("Number of states and territories with operations")),
        "avg_overall_rating": num(r.get("Average overall 5-star rating")),
        "pct_sff": round(100 * sff / n, 2) if n and sff is not None else None,   # derived: SFF count / facilities
        "total_fines": num(r.get("Total amount of fines in dollars")),
        "measures": {k: v for k, v in r.items() if k not in _CHAIN_MAIN and s(v) is not None},
    }


# ── payroll based journal: daily -> one row per CCN per quarter ─────────────────
_NURSE_HOURS = ("Hrs_RNDON", "Hrs_RNadmin", "Hrs_RN", "Hrs_LPNadmin", "Hrs_LPN", "Hrs_CNA", "Hrs_NAtrn", "Hrs_MedAide")
_RN_HOURS = ("Hrs_RNDON", "Hrs_RNadmin", "Hrs_RN")


def quarter_end(q: str) -> date | None:
    m = re.fullmatch(r"(\d{4})Q([1-4])", q or "")
    if not m:
        return None
    y, k = int(m.group(1)), int(m.group(2))
    return date(y, k * 3, 30 if k in (2, 3) else 31)


class PbjAggregator:
    """Sums daily rows per (CCN, quarter). HPRD = total hours / total resident-days (not an average of daily ratios).

    ASSUMPTION: total nurse hours = RN (incl. DON + admin) + LPN (incl. admin) + CNA + nurse-aide trainee + med aide.
    Days with a blank or zero MDS census are not counted (HPRD is undefined for them).
    """

    def __init__(self):
        self.acc: dict[tuple[str, str], dict] = {}
        self.daily_rows = 0

    def add(self, r: dict):
        self.daily_rows += 1
        ccn, q = s(r.get("PROVNUM")), s(r.get("CY_Qtr"))
        census = num(r.get("MDScensus"))
        if not ccn or not q or not census or census <= 0:
            return
        a = self.acc.setdefault((ccn, q), {"days": 0, "census": 0.0, "nurse": 0.0, "rn": 0.0, "name": s(r.get("PROVNAME")), "state": s(r.get("STATE"))})
        a["days"] += 1
        a["census"] += census
        a["nurse"] += sum(num(r.get(k)) or 0.0 for k in _NURSE_HOURS)
        a["rn"] += sum(num(r.get(k)) or 0.0 for k in _RN_HOURS)

    def rows(self, source_url: str):
        """Yield (external_id, stg_row, raw_payload)."""
        for (ccn, q), a in sorted(self.acc.items()):
            stg = {
                "source_url": source_url, "ccn": ccn, "quarter": q, "quarter_end": quarter_end(q),
                "days_reported": a["days"],
                "avg_census": round(a["census"] / a["days"], 2),
                "total_nurse_hprd": round(a["nurse"] / a["census"], 3),
                "rn_hprd": round(a["rn"] / a["census"], 3),
            }
            payload = {**{k: (v.isoformat() if isinstance(v, date) else v) for k, v in stg.items()},
                       "provider_name": a["name"], "state": a["state"]}
            yield f"{ccn}|{q}", stg, payload
