#!/usr/bin/env python3
"""Draft airports.json entries from OurAirports data (public domain).

    tools/make_airports.py airports.csv runways.csv RJFF [RJTT ...] > draft.json
    tools/make_airports.py airports.csv runways.csv --country JP > draft.json

The CSV files: https://ourairports.com/data/ (airports.csv, runways.csv).

For each airport: the reference point and elevation, and its runways as
gate groups. The true track comes from the two threshold positions, not
from the rounded heading column. Parallel runways whose centrelines are
less than --group-km apart share one gate: its centre is the middle of the
group, its width covers every member, and its ends drop the L/C/R suffix
(16L/34R + 16R/34L -> 16/34). Helipads, closed runways and runways without
both threshold positions are left out and reported on stderr.

The output is a draft: check it against real traffic before using it
(toganomon.py --check-runways) and edit it by hand where needed.
"""
import argparse
import csv
import json
import math
import sys

KM_PER_DEG = 111.2
PARALLEL_DEG = 3.0       # members of a group differ by at most this much in track
GATE_CROSS_KM = 1.0      # each side of the outermost centreline
FT_TO_M = 0.3048


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def bearing(lat1, lon1, lat2, lon2):
    """Initial great-circle bearing in degrees true."""
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360.0


def local_xy(lat, lon, lat0, lon0):
    """km east, km north of (lat0, lon0) (flat; fine within an airport)."""
    return (lon - lon0) * KM_PER_DEG * math.cos(math.radians(lat0)), (lat - lat0) * KM_PER_DEG


def runway(row):
    """runways.csv row -> runway dict, or (None, reason)."""
    le, he = row["le_ident"].strip(), row["he_ident"].strip()
    if row.get("closed") == "1":
        return None, "closed"
    if not le or not he or le.upper().startswith("H"):
        return None, "helipad or unnamed"
    pos = [_float(row[k]) for k in ("le_latitude_deg", "le_longitude_deg", "he_latitude_deg", "he_longitude_deg")]
    if None in pos:
        return None, "no threshold positions"
    lat1, lon1, lat2, lon2 = pos
    x, y = local_xy(lat2, lon2, lat1, lon1)
    length = _float(row.get("length_ft"))
    return {"ends": [le, he], "track": bearing(lat1, lon1, lat2, lon2),
            "mid": [(lat1 + lat2) / 2, (lon1 + lon2) / 2],
            "length_m": round(length * FT_TO_M) if length else round(math.hypot(x, y) * 1000)}, None


def _angdiff(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _base(designator):
    return designator.rstrip("LCR") or designator


def group_runways(runways, group_km):
    """Runways -> gate groups: parallel (same direction, PARALLEL_DEG) with
    centrelines less than group_km apart."""
    groups = []
    for rw in sorted(runways, key=lambda r: -r["length_m"]):
        for g in groups:
            ref = g[0]
            if _angdiff(rw["track"], ref["track"]) > PARALLEL_DEG:
                continue
            x, y = local_xy(rw["mid"][0], rw["mid"][1], ref["mid"][0], ref["mid"][1])
            ux, uy = math.sin(math.radians(ref["track"])), math.cos(math.radians(ref["track"]))
            if abs(x * uy - y * ux) < group_km:
                g.append(rw)
                break
        else:
            groups.append([rw])
    out = []
    for g in groups:
        ref = g[0]
        # mean track of the members, all taken in ref's direction
        track = ref["track"] + sum(((r["track"] - ref["track"] + 180.0) % 360.0 - 180.0) for r in g) / len(g)
        lat = sum(r["mid"][0] for r in g) / len(g)
        lon = sum(r["mid"][1] for r in g) / len(g)
        ux, uy = math.sin(math.radians(track)), math.cos(math.radians(track))
        cross = [abs(x * uy - y * ux) for x, y in (local_xy(r["mid"][0], r["mid"][1], lat, lon) for r in g)]
        ends = list(ref["ends"])
        if len(g) > 1:
            ends = [_base(e) for e in ends]
        entry = {"ends": ends, "true_track": round(track % 360.0, 1),
                 "center": [round(lat, 6), round(lon, 6)],
                 "gate_cross_km": round(GATE_CROSS_KM + max(cross), 2),
                 "length_m": max(r["length_m"] for r in g)}
        if len(g) > 1:
            entry["members"] = ["/".join(r["ends"]) for r in g]
        out.append(entry)
    seen = set()
    for e in out:
        if seen & set(e["ends"]):
            raise ValueError(f"designator used twice after grouping: {e['ends']}; edit by hand")
        seen |= set(e["ends"])
    return out


def build(airports_csv, runways_csv, idents=(), country=None, group_km=1.0, warn=None):
    """{ICAO: entry}; airports with no usable runway are left out (warn())."""
    warn = warn or (lambda msg: None)
    wanted = {}
    with open(airports_csv, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            ident = row["ident"]
            if ident in idents or (country and row["iso_country"] == country
                                   and row["scheduled_service"] == "yes"
                                   and row["type"] in ("large_airport", "medium_airport", "small_airport")):
                wanted[ident] = row
    for ident in idents:
        if ident not in wanted:
            warn(f"{ident}: not in {airports_csv}")
    found = {k: [] for k in wanted}
    with open(runways_csv, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row["airport_ident"] in found:
                rw, why = runway(row)
                if rw:
                    found[row["airport_ident"]].append(rw)
                elif why != "helipad or unnamed":
                    warn(f"{row['airport_ident']} {row['le_ident']}/{row['he_ident']}: {why}, left out")
    out = {}
    for ident in sorted(wanted):
        a = wanted[ident]
        if not found[ident]:
            warn(f"{ident}: no usable runway, left out")
            continue
        try:
            groups = group_runways(found[ident], group_km)
        except ValueError as e:
            warn(f"{ident}: {e}")
            continue
        elev = _float(a["elevation_ft"])
        out[ident] = {"name": a["name"], "arp": [round(float(a["latitude_deg"]), 6), round(float(a["longitude_deg"]), 6)],
                      "elevation_ft": round(elev) if elev is not None else 0, "runways": groups}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Draft airports.json entries from OurAirports CSV files.")
    ap.add_argument("airports_csv")
    ap.add_argument("runways_csv")
    ap.add_argument("icao", nargs="*", help="airport idents (ICAO)")
    ap.add_argument("--country", help="every scheduled-service airport of this ISO country, e.g. JP")
    ap.add_argument("--group-km", type=float, default=1.0,
                    help="parallel runways closer than this share one gate (default 1.0)")
    args = ap.parse_args(argv)
    if not args.icao and not args.country:
        ap.error("give airport idents or --country")
    out = build(args.airports_csv, args.runways_csv, set(args.icao), args.country, args.group_km,
                warn=lambda m: print(m, file=sys.stderr))
    doc = {"source": "OurAirports (public domain), drafted by tools/make_airports.py", "airports": out}
    json.dump(doc, sys.stdout, indent=1, ensure_ascii=False)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
