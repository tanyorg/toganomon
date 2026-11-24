#!/usr/bin/env python3
"""Recommend an airport's runway gates from recorded traffic.

EXPERIMENTAL: tried on RJFF, RJBB, RJTT, KLAX and KJFK only; options, output and the way it
recommends may change at any time. Check its recommendations by hand.

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
PARALLEL_DEG = 5.0       # runways this close in track are parallel ...
PASS_CROSS_KM = 2.0      # ... and a pass is looked for this far beside a centreline,
                         # or up to half way to a parallel one
LOW_PASS_FT = 500        # track and centre from the passes below this (and the rolls on the runway) ...
PASS_FT = 1500           # ... or, when those are too few, below this (as --check-runways)
MATCH_S = 120            # a replayed movement matches a logged one within this
APPROACH_KM = 15.0       # a second approach: low on a runway's extended centreline, within
                         # this of the runway's centre and inside its gate width ...
APPROACH_FT = 1500       # ... at or below this above it ...
CLIMB_BETWEEN_FT = 1000  # ... then up at least this much ...
SECOND_WITHIN_S = 3600   # ... and low again within this, never on the ground in between
KEEP_KM = 25.0           # points kept from each trace: within this of the ARP ...
KEEP_FT = 6000           # ... and this far above it (or on the ground)


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


def trim(tg, trace):
    """The points of a trace near the airport (KEEP_KM, KEEP_FT): all that
    detection, gates and the low-aircraft check look at. A point's details
    (callsign, category: index 8) are carried to the next kept point, since
    readsb sends them only now and then."""
    out, details = [], None
    for p in trace:
        if not (isinstance(p, list) and len(p) > 3):
            continue
        if len(p) > 8 and isinstance(p[8], dict):
            details = dict(details or {}, **p[8])
        lat, lon, alt = p[1], p[2], p[3]
        if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float))):
            continue
        if not (alt == "ground" or (isinstance(alt, (int, float)) and alt - tg.ELEVATION_FT <= KEEP_FT)):
            continue
        if tg.arp_distance_km(lat, lon) > KEEP_KM:
            continue
        if details is not None:
            q = list(p) + [None] * (9 - len(p))
            q[8] = dict(details, **(p[8] if len(p) > 8 and isinstance(p[8], dict) else {}))
            p, details = q, None
        out.append(p)
    return out


def load_docs(tg, paths):
    """[(doc, first time, last time)]: each trace with only its points near
    the airport (trim; a day of a busy region would not fit in memory
    otherwise), and the times of its first and last point of all."""
    out = []
    for p in paths:
        doc = tg.load_trace(p)
        tr = doc.get("trace") if isinstance(doc, dict) else None
        if not tr or not isinstance(doc.get("timestamp"), (int, float)) or not isinstance(doc.get("icao"), str):
            continue
        t0, t1 = doc["timestamp"] + tr[0][0], doc["timestamp"] + tr[-1][0]
        kept = trim(tg, tr)
        out.append(({"icao": doc["icao"], "timestamp": doc["timestamp"], "trace": kept}, t0, t1))
    return out


def configure(tg, icao, entry):
    tg.configure(tg.parse_config({"airport": {"icao": icao}}, airports={icao: entry}))


# ---------- 1. measure ----------

def parallel_km(tg, rw):
    """Distance from rw's centreline to the nearest parallel runway's, or None."""
    out = None
    for other in tg.RUNWAYS:
        if other is rw:
            continue
        d = abs((other["true_track"] - rw["true_track"] + 90.0) % 180.0 - 90.0)
        if d > PARALLEL_DEG:
            continue
        cross = abs(tg.runway_frame(other["lat"], other["lon"], rw)[1])
        out = cross if out is None else min(out, cross)
    return out


def collect_passes(tg, docs, into=None):
    """Add docs' passes to into ([{below_ft: [(track, cross)]} per configured
    runway]) and return it: the low ones (LOW_PASS_FT and the take-off and
    landing rolls) and up to PASS_FT. Beside a parallel runway only up to
    half way to it."""
    into = into or [{LOW_PASS_FT: [], PASS_FT: []} for _ in tg.RUNWAYS]
    for rw, acc in zip(tg.RUNWAYS, into):
        par = parallel_km(tg, rw)
        cross_km = PASS_CROSS_KM if par is None else min(PASS_CROSS_KM, par / 2.0)
        for below in (LOW_PASS_FT, PASS_FT):
            for doc, _, _ in docs:
                acc[below].extend(tg.runway_passes(doc, rw, below, cross_km))
    return into


def summarize(tg, collected, min_passes=MIN_PASSES):
    """Per configured runway: {"ends", "passes", "below_ft", "parallel_km",
    "track", "offset_km", "spread_km" (95% of the passes are this close to
    the median offset)}. From the low passes when there are enough: higher
    up a departure may already turn (RJBB 06L/24R, departures only, measured
    41 and 56 degrees on two days with passes up to 1500 ft; the runway is
    at 51). With a receiver that does not hear that low, up to PASS_FT."""
    out = []
    for rw, acc in zip(tg.RUNWAYS, collected):
        below = LOW_PASS_FT if len(acc[LOW_PASS_FT]) >= min_passes else PASS_FT
        passes = acc[below]
        r = {"ends": list(rw["ends"]), "passes": len(passes), "below_ft": below,
             "parallel_km": parallel_km(tg, rw), "track": None, "offset_km": None, "spread_km": None}
        if passes:
            ref = rw["true_track"]
            r["track"] = round((ref + statistics.median((tr - ref + 180.0) % 360.0 - 180.0
                                                        for tr, _ in passes)) % 360.0, 1)
            r["offset_km"] = round(statistics.median(c for _, c in passes), 2)
            dev = sorted(abs(c - r["offset_km"]) for _, c in passes)
            r["spread_km"] = round(dev[min(len(dev) - 1, int(0.95 * len(dev)))], 2)
        out.append(r)
    return out


def measure(tg, docs, min_passes=MIN_PASSES):
    """summarize(collect_passes(docs)): one set of traces."""
    return summarize(tg, collect_passes(tg, docs), min_passes)


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
        par = r.get("parallel_km")
        if par is not None and need > par / 2.0:
            notes.append(f"{name}: traffic spreads {r['spread_km']} km, but the parallel runway is "
                         f"{par:.2f} km away: gate kept at {width} km")
        elif need > width:
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


def second_approaches(tg, docs, win):
    """Aircraft that came low near the airport, climbed away and came low
    again without touching the ground in between: a go-around found another
    way, to check detection for misses. [{"hex", "flight", "t" (lowest point
    of the first approach), "min_ft" (above the airport), "again_t",
    "in_gate" (the lowest point was inside a runway gate)}]. Like
    go-arounds, not for rotorcraft or aircraft slower than min_gs_kt there
    (helicopters around a heliport go low again and again)."""
    out = []
    for doc, _, _ in docs:
        base, flight, cat = doc["timestamp"], None, None
        low = None          # the current low stretch: [t of its lowest point, its altitude, the point]
        peak = None         # highest since the last low stretch
        entered_from_above = False
        prev_alt = None
        for p in doc["trace"]:
            if len(p) > 8 and isinstance(p[8], dict):
                flight = (p[8].get("flight") or "").strip() or flight
                cat = p[8].get("category") or cat
            t = base + p[0]
            if p[3] == "ground":
                low, peak, entered_from_above, prev_alt = None, None, False, None
                continue
            if not isinstance(p[3], (int, float)):
                continue
            agl = p[3] - tg.ELEVATION_FT
            if agl <= APPROACH_FT and on_final(tg, p):
                if low is not None and peak is not None and peak - low[1] >= CLIMB_BETWEEN_FT:
                    slow = isinstance(low[2][4], (int, float)) and low[2][4] < tg.GA_MIN_GS_KT
                    if (entered_from_above and t - low[0] <= SECOND_WITHIN_S and win[0] <= low[0] <= win[1]
                            and not slow and cat not in tg.GA_EXCLUDE_CATEGORIES):
                        out.append({"hex": doc["icao"], "flight": flight, "t": low[0], "min_ft": round(low[1]),
                                    "again_t": t, "in_gate": in_any_gate(tg, low[2])})
                    low, peak = None, None
                    entered_from_above = True
                if low is None:
                    entered_from_above = entered_from_above or (prev_alt is not None and prev_alt > agl)
                    low = [t, agl, p]
                elif agl < low[1]:
                    low = [t, agl, p]
                peak = None
            elif low is not None:
                peak = agl if peak is None else max(peak, agl)
            prev_alt = agl
    return sorted(out, key=lambda x: x["t"])


def on_final(tg, p):
    """p is on a runway's extended centreline: within APPROACH_KM of its
    centre and the gate's width across it. Not a circle around the ARP: that
    takes in the traffic of airports nearby (LaGuardia is 13 km from JFK,
    Hawthorne 5 km from LAX)."""
    for rw in tg.RUNWAYS:
        along, cross = tg.runway_frame(p[1], p[2], rw)
        if abs(along) <= APPROACH_KM and abs(cross) <= rw["gate_cross_km"]:
            return True
    return False


def in_any_gate(tg, p):
    for rw in tg.RUNWAYS:
        along, cross = tg.runway_frame(p[1], p[2], rw)
        if abs(along) <= rw["gate_along_km"] and abs(cross) <= rw["gate_cross_km"]:
            return True
    return False


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

def report(icao, entry, results, new, notes, days, totals, cmp_total, n_qnh, min_passes):
    n_traces = sum(d["traces"] for d in days)
    lines = [f"{icao}: {len(days)} day(s), {n_traces} traces"
             + (f", QNH from {n_qnh} METARs" if n_qnh else ", no QNH (raw pressure altitude)")]
    for rw, r, nrw in zip(entry["runways"], results, new["runways"]):
        name = "/".join(rw["ends"])
        if not r["passes"]:
            lines.append(f"  {name}: no pass through the gate")
            continue
        diff = (r["track"] - rw["true_track"] + 180.0) % 360.0 - 180.0
        lines.append(f"  {name}: {r['passes']} passes below {r['below_ft']} ft, track {r['track']:.1f} ({diff:+.1f}), "
                     f"offset {r['offset_km']:+.2f} km, spread {r['spread_km']:.2f} km"
                     f" -> true_track {nrw['true_track']}, gate_cross_km {nrw.get('gate_cross_km', 1.0)}")
    lines += [f"  note: {n}" for n in notes]
    for d in days:
        per_end = ", ".join(f"{k} {v['arr']}/{v['dep']}" for k, v in sorted(d["counts"].items()))
        arr = sum(v["arr"] for v in d["counts"].values())
        dep = sum(v["dep"] for v in d["counts"].values())
        lines.append(f"  {d['label']}: {arr} arr, {dep} dep, {len(d['go_arounds'])} go-around(s),"
                     f" {len(d['low'])} low without a movement (arr/dep: {per_end or '-'})")
    gas = [g for d in days for g in d["go_arounds"]]
    lines += [f"    go-around {iso(g['t'])} {g.get('flight') or '-'} {g['hex']} RWY{g['rwy']}" for g in gas]
    seconds = [s for d in days for s in d["second"]]
    missed = [s for s in seconds if not s["found"]]
    lines.append(f"  second approaches (low, up {CLIMB_BETWEEN_FT} ft, low again): {len(seconds)},"
                 f" {len(seconds) - len(missed)} of them found as go-arounds")
    lines += [f"    not a go-around: {iso(s['t'])} {s['flight'] or '-'} {s['hex']} lowest {s['min_ft']} ft"
              f" {'in a gate' if s['in_gate'] else 'outside the gates'}, low again {iso(s['again_t'])[11:]}"
              for s in missed]
    # what the days did not show: so whoever adds the airport knows what to add
    unused = [e for rw in new["runways"] for e in rw["ends"] if not any(totals.get(e, {}).values())]
    thin = ["/".join(r["ends"]) for r in results if r["passes"] < min_passes]
    if thin:
        lines.append(f"  more data needed: {', '.join(thin)} had fewer than {min_passes} passes:"
                     " add days when these runways are in use (another wind direction)")
    if unused:
        lines.append(f"  not seen in use: {', '.join(unused)}")
    if cmp_total is not None:
        matched, only_log, only_rep = cmp_total
        lines.append(f"  answer: {matched} matched, {len(only_log)} only in the log, {len(only_rep)} only in the replay")
        lines += [f"    log    {iso(t)} {k} {r} {h}" for t, k, r, h in only_log[:10]]
        lines += [f"    replay {iso(t)} {k} {r} {h}" for t, k, r, h in only_rep[:10]]
    return lines


def checked_text(results, days, source=None):
    """The entry's "checked": when, from what, how many passes per runway."""
    span = f"{days[0]['label']}" if len(days) == 1 else f"{len(days)} days {days[0]['label']} .. {days[-1]['label']}"
    passes = ", ".join(f"{'/'.join(r['ends'])} {r['passes']}" for r in results)
    return (f"{time.strftime('%Y-%m-%d', time.gmtime())}: tools/tune/tune_airport.py on {span}"
            + (f" ({source})" if source else "") + f"; passes: {passes}")


def tune(icao, traces, metar=None, airports=None, answer=None, min_passes=MIN_PASSES, tg=None, source=None):
    """traces: one directory or a list of them (e.g. one per day). Each is
    read on its own, twice (measure, then replay with the recommendation),
    so memory stays that of one directory however many are given."""
    tg = tg or load_toganomon()
    dirs = [traces] if isinstance(traces, str) else list(traces)
    table = tg.load_airports(airports)
    entry = table.get(icao)
    if entry is None:
        raise SystemExit(f"{icao} is not in {airports or 'airports.json'}")
    series = qnh_series(tg, metar)

    configure(tg, icao, entry)
    collected, used = None, []
    for d in dirs:
        docs = load_docs(tg, trace_files(d))
        if not docs:
            print(f"{d}: no trace_full_*.json with points, skipped", file=sys.stderr)
            continue
        collected = collect_passes(tg, docs, collected)
        used.append(d)
        del docs
    if not used:
        raise SystemExit("no traces in " + ", ".join(dirs))
    results = summarize(tg, collected, min_passes)
    new, notes = recommend(entry, results, min_passes)

    configure(tg, icao, new)
    days, totals = [], {}
    cmp_total = [0, [], []] if answer else None
    for d in used:
        docs = load_docs(tg, trace_files(d))
        win = window(docs)
        moves = replay(tg, docs, series)
        counts = {}
        for m in moves:
            if win[0] <= m[0] <= win[1]:
                for c in (counts.setdefault(m[2], {"arr": 0, "dep": 0}), totals.setdefault(m[2], {"arr": 0, "dep": 0})):
                    c[m[1]] += 1
        day = {"dir": d, "label": iso(win[0])[:10], "window": win, "traces": len(docs), "counts": counts,
               "go_arounds": [g for g in tg.find_go_arounds(moves) if win[0] <= g["t"] <= win[1]],
               "low": low_without_movement(tg, docs, moves, win)}
        # a second approach is "found" when a go-around of the same aircraft is within 10 minutes of it
        day["second"] = [dict(s, found=any(g["hex"] == s["hex"] and abs(g["t"] - s["t"]) <= 600
                                           for g in day["go_arounds"]))
                         for s in second_approaches(tg, docs, win)]
        if answer:
            m, ol, orr = compare(moves, read_log(answer, win), win)
            cmp_total[0] += m
            cmp_total[1] += ol
            cmp_total[2] += orr
        days.append(day)
        del docs, moves
    days.sort(key=lambda x: x["window"][0])
    new["checked"] = checked_text(results, days, source)
    lines = report(icao, entry, results, new, notes, days, totals, cmp_total, len(series), min_passes)
    gas = [g for d in days for g in d["go_arounds"]]
    low = sorted({h for d in days for h in d["low"]})
    second = [s for d in days for s in d["second"]]
    return new, lines, {"results": results, "counts": totals, "go_arounds": gas, "low": low, "days": days,
                        "second": second,
                        "window": (days[0]["window"][0], days[-1]["window"][1])}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Recommend an airport's runway gates from recorded traffic.")
    ap.add_argument("icao")
    ap.add_argument("traces", nargs="+",
                    help="directories with trace_full_*.json at any depth, e.g. one per day (as many as you have)")
    ap.add_argument("--metar", help='METAR archive ("<ISO time>\\t<METAR>" lines) for the QNH')
    ap.add_argument("--airports", help="airports.json (or a draft from make_airports.py); default: toganomon's")
    ap.add_argument("--answer", help="toganomon movements log to compare the replay with")
    ap.add_argument("--min-passes", type=int, default=MIN_PASSES)
    ap.add_argument("--out", help="write the recommended entry here (JSON)")
    ap.add_argument("--source", help='where the traces come from, for the entry\'s "checked" (e.g. "adsb.lol, ODbL")')
    args = ap.parse_args(argv)
    new, lines, _ = tune(args.icao.upper(), args.traces, args.metar, args.airports, args.answer, args.min_passes,
                         source=args.source)
    print("\n".join(lines))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({args.icao.upper(): new}, f, indent=2, ensure_ascii=False)
            f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
