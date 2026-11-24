#!/usr/bin/env python3
"""Recommend an airport's runway gates from recorded traffic.

    tools/tune/tune_airport.py ICAO TRACES [--metar ARCHIVE] [--airports FILE]
                               [--answer MOVEMENTS_LOG] [--out ENTRY.json]

TRACES is a directory with readsb trace_full_*.json files at any depth:
readsb's traces directory (--write-json-globe-index, the last 24 hours) or
a day of globe_history (YYYY/MM/DD/traces/, readsb --write-globe-history,
also how adsb.lol publishes its data).

It replays the traces with toganomon.py's own detection, so what it
recommends is what toganomon will do with it:

1. Measures every runway gate of the airport's entry (airports.json, or
   --airports with a draft from tools/make_airports.py): the passes low and
   straight along the runway, their median true track and centreline
   offset (as --check-runways), and how far the passes spread across it.
2. Recommends an entry: true track and centre from the measurement (with
   at least --min-passes passes), the gate widened when traffic spreads
   wider than it. The recommendation is written with --out.
3. Replays the window with the recommended entry: arrivals and departures
   per runway end, the go-arounds found, and the aircraft that came low near
   the airport without a movement (a sign of a gate that does not fit).
4. With --answer (a toganomon movements log of the same receiver), compares
   the replay with what toganomon recorded live.

The METAR archive (--metar, lines "<ISO time>\\t<METAR>" as fetch_wx.py
--archive writes them) gives the QNH for each trace; without it the raw
pressure altitude is used.
"""
import argparse
import bisect
import calendar
import copy
import glob
import importlib.util
import json
import math
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

MIN_PASSES = 30          # fewer: keep the entry's track and centre
WINDOW_HEAD_S = 7200     # the first traces start mid-flight: skip this much
WINDOW_TAIL_S = 600      # trace_full lags the live traffic by minutes
LOW_KM = 8.0             # "low near the airport": within this of the ARP ...
LOW_FT = 1500            # ... and this far above it
CROSS_MARGIN_KM = 0.2    # gate half-width beyond the spread of the passes
MATCH_S = 120            # a replayed movement matches a logged one within this


def load_toganomon(path=None):
    path = path or os.path.join(ROOT, "toganomon.py")
    spec = importlib.util.spec_from_file_location("toganomon", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def parse_iso(s):
    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))


# ---------- inputs ----------

def trace_files(root):
    return sorted(glob.glob(os.path.join(root, "**", "trace_full_*.json"), recursive=True))


def qnh_series(tg, path):
    """[(time, QNH hPa)] from a METAR archive, in time order."""
    out = []
    if not path:
        return out
    with open(path, encoding="utf-8") as f:
        for ln in f:
            parts = ln.rstrip("\n").split("\t", 1)
            if len(parts) != 2:
                continue
            try:
                t = parse_iso(parts[0])
            except ValueError:
                continue
            q = tg.parse_metar_qnh(parts[1], t)
            if q is not None:
                out.append((t, q))
    return sorted(out)


def qnh_at(series, t):
    i = bisect.bisect_right([s[0] for s in series], t) - 1
    return series[i][1] if i >= 0 else None


def load_docs(tg, paths):
    """[(doc, first time, last time)] of the traces with points."""
    out = []
    for p in paths:
        doc = tg.load_trace(p)
        tr = doc.get("trace") if isinstance(doc, dict) else None
        if not tr or not isinstance(doc.get("timestamp"), (int, float)) or not isinstance(doc.get("icao"), str):
            continue
        out.append((doc, doc["timestamp"] + tr[0][0], doc["timestamp"] + tr[-1][0]))
    return out


def configure(tg, icao, entry):
    tg.configure(tg.parse_config({"airport": {"icao": icao}}, airports={icao: entry}))


# ---------- 1. measure ----------

def measure(tg, docs):
    """Per configured runway: {"ends", "passes", "track", "offset_km",
    "spread_km" (95% of the passes are this close to the median offset)}."""
    out = []
    for rw in tg.RUNWAYS:
        passes = []
        for doc, _, _ in docs:
            passes.extend(tg.runway_passes(doc, rw))
        r = {"ends": list(rw["ends"]), "passes": len(passes), "track": None, "offset_km": None, "spread_km": None}
        if passes:
            ref = rw["true_track"]
            r["track"] = round((ref + statistics.median((tr - ref + 180.0) % 360.0 - 180.0
                                                        for tr, _ in passes)) % 360.0, 1)
            r["offset_km"] = round(statistics.median(c for _, c in passes), 2)
            dev = sorted(abs(c - r["offset_km"]) for _, c in passes)
            r["spread_km"] = round(dev[min(len(dev) - 1, int(0.95 * len(dev)))], 2)
        out.append(r)
    return out


# ---------- 2. recommend ----------

def shifted(center, track, cross_km):
    """center moved cross_km to the right of track (runway_frame's cross)."""
    lat, lon = center
    h = math.radians(track)
    dy, dx = -math.sin(h) * cross_km, math.cos(h) * cross_km
    return [lat + dy / 111.2, lon + dx / (111.2 * math.cos(math.radians(lat)))]


def recommend(entry, results, min_passes=MIN_PASSES):
    """(new entry, notes): track and centre from runways with enough passes;
    a gate narrower than its traffic is widened (never narrowed)."""
    new = copy.deepcopy(entry)
    notes = []
    for rw, r in zip(new["runways"], results):
        name = "/".join(rw["ends"])
        if r["passes"] < min_passes:
            notes.append(f"{name}: {r['passes']} passes (< {min_passes}): kept as it is")
            continue
        old_track = rw["true_track"]
        rw["true_track"] = r["track"]
        rw["center"] = [round(v, 6) for v in shifted(rw["center"], old_track, r["offset_km"])]
        need = round(math.ceil((r["spread_km"] + CROSS_MARGIN_KM) * 10) / 10, 1)
        width = rw.get("gate_cross_km", 1.0)
        if need > width:
            rw["gate_cross_km"] = need
            notes.append(f"{name}: gate widened {width} -> {need} km (spread {r['spread_km']} km)")
        elif need + 0.3 <= width:
            notes.append(f"{name}: traffic spreads {r['spread_km']} km; the {width} km gate could be narrower")
    return new, notes


# ---------- 3. replay ----------

def window(docs):
    lo = min(d[1] for d in docs)
    hi = max(d[2] for d in docs)
    return lo + WINDOW_HEAD_S, hi - WINDOW_TAIL_S


def replay(tg, docs, series):
    """Movements of all traces, as toganomon records them (QNH from series
    at the middle of each trace)."""
    state = {"movements": []}
    for doc, t0, t1 in docs:
        q = qnh_at(series, (t0 + t1) / 2) if series else None
        off = tg.qnh_offset_ft(q) - tg.ELEVATION_FT
        events = tg.detect_movements(doc, off)
        moves = state.setdefault("movements", [])
        for e in events:
            if not tg._same_pass(moves, doc["icao"], e[1], e[0]):
                moves.append([e[0], e[1], e[2], doc["icao"]] + list(e[3:6]))
    state["movements"].sort(key=lambda m: m[0])
    return state["movements"]


def low_without_movement(tg, docs, moves, win):
    """Hexes that came within LOW_KM of the ARP below LOW_FT in the window
    and have no movement there."""
    moved = {m[3] for m in moves if win[0] <= m[0] <= win[1]}
    out = []
    for doc, _, _ in docs:
        if doc["icao"] in moved:
            continue
        for p in doc["trace"]:
            if not (isinstance(p, list) and len(p) > 3 and all(isinstance(v, (int, float)) for v in p[:4])):
                continue
            t = doc["timestamp"] + p[0]
            if win[0] <= t <= win[1] and p[3] - tg.ELEVATION_FT <= LOW_FT and tg.arp_distance_km(p[1], p[2]) <= LOW_KM:
                out.append(doc["icao"])
                break
    return sorted(out)


# ---------- 4. answer ----------

def read_log(path, win):
    out = []
    with open(path, encoding="utf-8") as f:
        for ln in f:
            p = ln.rstrip("\n").split("\t")
            if len(p) < 4:
                continue
            try:
                t = parse_iso(p[0])
            except ValueError:
                continue
            if win[0] <= t <= win[1]:
                out.append((t, p[1], p[2], p[3]))
    return out


def compare(moves, logged, win):
    """(matched, only in the log, only in the replay) by kind, runway and hex within MATCH_S."""
    rep = [(m[0], m[1], m[2], m[3]) for m in moves if win[0] <= m[0] <= win[1]]
    used, only_log = set(), []
    for t, kind, rwy, hx in logged:
        hit = next((i for i, r in enumerate(rep)
                    if i not in used and r[1] == kind and r[2] == rwy and r[3] == hx and abs(r[0] - t) <= MATCH_S), None)
        if hit is None:
            only_log.append((t, kind, rwy, hx))
        else:
            used.add(hit)
    only_rep = [r for i, r in enumerate(rep) if i not in used]
    return len(used), only_log, only_rep


# ---------- report ----------

def report(icao, entry, results, new, notes, counts, gas, low, cmp_result, win, n_traces, n_qnh):
    lines = [f"{icao}: {n_traces} traces, window {iso(win[0])} .. {iso(win[1])}"
             + (f", QNH from {n_qnh} METARs" if n_qnh else ", no QNH (raw pressure altitude)")]
    for rw, r, nrw in zip(entry["runways"], results, new["runways"]):
        name = "/".join(rw["ends"])
        if not r["passes"]:
            lines.append(f"  {name}: no pass through the gate")
            continue
        diff = (r["track"] - rw["true_track"] + 180.0) % 360.0 - 180.0
        lines.append(f"  {name}: {r['passes']} passes, track {r['track']:.1f} ({diff:+.1f}), "
                     f"offset {r['offset_km']:+.2f} km, spread {r['spread_km']:.2f} km"
                     f" -> true_track {nrw['true_track']}, gate_cross_km {nrw.get('gate_cross_km', 1.0)}")
    lines += [f"  note: {n}" for n in notes]
    per_end = ", ".join(f"{k} {v['arr']}/{v['dep']}" for k, v in sorted(counts.items()))
    total_arr = sum(v["arr"] for v in counts.values())
    total_dep = sum(v["dep"] for v in counts.values())
    lines.append(f"  replay: {total_arr} arrivals, {total_dep} departures (arr/dep per end: {per_end or '-'})")
    lines.append(f"  go-arounds: {len(gas)}" + "".join(
        f"\n    {iso(g['t'])} {g.get('flight') or '-'} {g['hex']} RWY{g['rwy']}" for g in gas))
    lines.append(f"  low near the airport without a movement: {len(low)}"
                 + (f" ({' '.join(low[:8])}{' ...' if len(low) > 8 else ''})" if low else ""))
    if cmp_result is not None:
        matched, only_log, only_rep = cmp_result
        lines.append(f"  answer: {matched} matched, {len(only_log)} only in the log, {len(only_rep)} only in the replay")
        lines += [f"    log    {iso(t)} {k} {r} {h}" for t, k, r, h in only_log[:10]]
        lines += [f"    replay {iso(t)} {k} {r} {h}" for t, k, r, h in only_rep[:10]]
    return lines


def tune(icao, traces, metar=None, airports=None, answer=None, min_passes=MIN_PASSES, tg=None):
    tg = tg or load_toganomon()
    table = tg.load_airports(airports)
    entry = table.get(icao)
    if entry is None:
        raise SystemExit(f"{icao} is not in {airports or 'airports.json'}")
    configure(tg, icao, entry)
    docs = load_docs(tg, trace_files(traces))
    if not docs:
        raise SystemExit(f"no trace_full_*.json with points under {traces}")
    series = qnh_series(tg, metar)
    win = window(docs)
    results = measure(tg, docs)
    new, notes = recommend(entry, results, min_passes)
    configure(tg, icao, new)
    moves = replay(tg, docs, series)
    inwin = [m for m in moves if win[0] <= m[0] <= win[1]]
    counts = {}
    for m in inwin:
        c = counts.setdefault(m[2], {"arr": 0, "dep": 0})
        c[m[1]] += 1
    gas = [g for g in tg.find_go_arounds(moves) if win[0] <= g["t"] <= win[1]]
    low = low_without_movement(tg, docs, moves, win)
    cmp_result = compare(moves, read_log(answer, win), win) if answer else None
    lines = report(icao, entry, results, new, notes, counts, gas, low, cmp_result, win, len(docs), len(series))
    return new, lines, {"results": results, "counts": counts, "go_arounds": gas, "low": low, "window": win}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Recommend an airport's runway gates from recorded traffic.")
    ap.add_argument("icao")
    ap.add_argument("traces", help="directory with trace_full_*.json at any depth")
    ap.add_argument("--metar", help='METAR archive ("<ISO time>\\t<METAR>" lines) for the QNH')
    ap.add_argument("--airports", help="airports.json (or a draft from make_airports.py); default: toganomon's")
    ap.add_argument("--answer", help="toganomon movements log to compare the replay with")
    ap.add_argument("--min-passes", type=int, default=MIN_PASSES)
    ap.add_argument("--out", help="write the recommended entry here (JSON)")
    args = ap.parse_args(argv)
    new, lines, _ = tune(args.icao.upper(), args.traces, args.metar, args.airports, args.answer, args.min_passes)
    print("\n".join(lines))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({args.icao.upper(): new}, f, indent=2, ensure_ascii=False)
            f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
