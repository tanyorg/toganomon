#!/usr/bin/env python3
"""Keep the traces that come near some airports, from a globe_history tar stream.

EXPERIMENTAL (see tools/tune/README.md).

    curl -sL URL.tar.aa URL.tar.ab | tools/tune/extract_traces.py OUT \\
        --near RJBB=34.4347,135.2440 --near RJTT=35.5523,139.7798

Reads a tar of one day of readsb globe history from stdin (adsb.lol
publishes one per day, split in .tar.aa, .tar.ab, ...: concatenate them)
without writing it to disk, and writes to OUT/traces/<xx>/ only the
trace_full_<hex>.json of the aircraft that had a point within --radius-km
of one of the --near points at or below --max-alt-ft (or on the ground).
A whole day of the world is some 3.5 GB; what is kept for a few airports
is a few hundred kB to some MB per airport. The files keep their bytes
(gzipped JSON as readsb writes them); tune_airport.py reads them as they
are. OUT/extract.json records what was read and kept.
"""
import argparse
import gzip
import json
import math
import os
import sys
import tarfile
import time

KM_PER_DEG = 111.2
RADIUS_KM = 60.0
MAX_ALT_FT = 10000


def parse_near(s):
    name, _, pos = s.partition("=")
    lat, _, lon = pos.partition(",")
    try:
        return name, float(lat), float(lon)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--near NAME=LAT,LON, not {s!r}")


def near(doc, points, radius_km=RADIUS_KM, max_alt_ft=MAX_ALT_FT):
    """Names of the points the trace came near (low enough), in order of points."""
    trace = doc.get("trace") if isinstance(doc, dict) else None
    if not isinstance(trace, list):
        return []
    hit = set()
    for p in trace:
        if not (isinstance(p, list) and len(p) > 3):
            continue
        lat, lon, alt = p[1], p[2], p[3]
        if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float))):
            continue
        if not (alt == "ground" or (isinstance(alt, (int, float)) and alt <= max_alt_ft)):
            continue
        for name, plat, plon in points:
            if name in hit:
                continue
            dy = (lat - plat) * KM_PER_DEG
            dx = (lon - plon) * KM_PER_DEG * math.cos(math.radians(plat))
            if math.hypot(dx, dy) <= radius_km:
                hit.add(name)
        if len(hit) == len(points):
            break
    return [n for n, _, _ in points if n in hit]


def decode(raw):
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def extract(stream, out, points, radius_km=RADIUS_KM, max_alt_ft=MAX_ALT_FT, log=None):
    stats = {"read": 0, "kept": 0, "bad": 0, "per_point": {n: 0 for n, _, _ in points}}
    started = time.time()
    with tarfile.open(fileobj=stream, mode="r|*") as tar:
        for m in tar:
            name = os.path.basename(m.name)
            if not (m.isfile() and name.startswith("trace_full_") and name.endswith(".json")):
                continue
            stats["read"] += 1
            raw = tar.extractfile(m).read()
            try:
                hits = near(decode(raw), points, radius_km, max_alt_ft)
            except (ValueError, OSError, EOFError):
                stats["bad"] += 1
                continue
            if hits:
                hx = name[len("trace_full_"):-len(".json")]
                d = os.path.join(out, "traces", hx[-2:])
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, name), "wb") as f:
                    f.write(raw)
                stats["kept"] += 1
                for h in hits:
                    stats["per_point"][h] += 1
            if log and stats["read"] % 20000 == 0:
                print(f"{stats['read']} read, {stats['kept']} kept, {time.time() - started:.0f} s", file=log, flush=True)
    stats["seconds"] = round(time.time() - started)
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(description="Keep the traces near some airports from a globe_history tar on stdin.")
    ap.add_argument("out", help="output directory (traces/<xx>/trace_full_<hex>.json)")
    ap.add_argument("--near", action="append", type=parse_near, required=True, metavar="NAME=LAT,LON")
    ap.add_argument("--radius-km", type=float, default=RADIUS_KM)
    ap.add_argument("--max-alt-ft", type=float, default=MAX_ALT_FT)
    ap.add_argument("--source", help="what the stream is (recorded in extract.json), e.g. the release name")
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    stats = extract(sys.stdin.buffer, args.out, args.near, args.radius_km, args.max_alt_ft, log=sys.stderr)
    stats.update(source=args.source, near={n: [la, lo] for n, la, lo in args.near},
                 radius_km=args.radius_km, max_alt_ft=args.max_alt_ft)
    with open(os.path.join(args.out, "extract.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=1)
        f.write("\n")
    print(json.dumps({k: stats[k] for k in ("read", "kept", "bad", "per_point", "seconds")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
