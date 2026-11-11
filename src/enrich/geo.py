"""Map coordinates for buildings: core.facility_geo (lat, lon, coord_source, source_url).

    python -m src.enrich.geo --gazetteer path/to/2024_Gaz_zcta_national.txt [--cms-csv path/to/NH_ProviderInfo.csv]

1. CMS Provider Information latitude / longitude by CCN (coord_source 'cms'), when a CMS file is given
   (download it by hand from data.cms.gov; its API refuses automated clients).
2. Otherwise the US Census ZCTA Gazetteer internal point of the building's ZIP (coord_source 'zip_centroid').
   Public domain: https://www.census.gov/geographies/reference-files/time-series/geo/gazetteer-files.html
Buildings with neither stay without a row (the website shows NOT FOUND). Re-running replaces the rows.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

GAZ_URL = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_zcta_national.zip"
CMS_URL = "https://data.cms.gov/provider-data/dataset/4pq5-n9py"


def read_gazetteer(path: Path) -> dict[str, tuple[float, float]]:
    out = {}
    with open(path, encoding="utf-8") as f:
        rows = csv.reader(f, delimiter="\t")
        head = [h.strip() for h in next(rows)]
        i, la, lo = head.index("GEOID"), head.index("INTPTLAT"), head.index("INTPTLONG")
        for r in rows:
            out[r[i].strip().zfill(5)] = (float(r[la]), float(r[lo]))
    return out


def read_cms(path: Path) -> dict[str, tuple[float, float]]:
    out = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            ccn = (r.get("CMS Certification Number (CCN)") or r.get("Federal Provider Number") or "").strip().zfill(6)
            try:
                lat, lon = float(r.get("Latitude") or ""), float(r.get("Longitude") or "")
            except ValueError:
                continue
            if ccn.strip("0") and -90 <= lat <= 90 and -180 <= lon <= 180:
                out[ccn] = (lat, lon)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m src.enrich.geo", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gazetteer", type=Path, required=True)
    ap.add_argument("--cms-csv", type=Path)
    a = ap.parse_args(argv)
    gaz = read_gazetteer(a.gazetteer)
    cms = read_cms(a.cms_csv) if a.cms_csv else {}
    from src.common.db import connect
    with connect() as conn:
        facs = conn.execute("select facility_id::text f, regexp_replace(coalesce(ccn, ''), '[A-Z]+$', '') ccn, zip5 "
                            "from core.facility").fetchall()
        rows = []
        for r in facs:
            if r["ccn"] and r["ccn"] in cms:
                rows.append((r["f"], *cms[r["ccn"]], "cms", CMS_URL))
            elif r["zip5"] and r["zip5"] in gaz:
                rows.append((r["f"], *gaz[r["zip5"]], "zip_centroid", GAZ_URL))
        conn.execute("delete from core.facility_geo")
        with conn.cursor().copy("copy core.facility_geo (facility_id, lat, lon, coord_source, source_url) from stdin") as cp:
            for x in rows:
                cp.write_row(x)
        conn.commit()
    n_cms = sum(1 for x in rows if x[3] == "cms")
    print(f"facility_geo: {len(rows)} of {len(facs)} buildings (cms {n_cms}, zip_centroid {len(rows) - n_cms})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
