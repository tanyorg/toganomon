#!/usr/bin/env python3
"""toganomon: airport arrivals, departures and probable go-arounds from
readsb traces.

Run every minute (cron or a systemd timer):

    toganomon.py [-c /etc/toganomon.toml]
    toganomon.py [-c ...] --check-runways   # configured runways vs real traffic

Each run reads readsb's aircraft.json, records the ICAO address (hex) of
every aircraft heard in the last SEEN_S seconds, drops addresses not heard
for WINDOW_S, and writes a small JSON summary:

    {"now": <readsb time>, "window_s": 86400, "total": N, "with_pos": M,
     "started": <first run time>, "arrivals": A, "departures": D,
     "runway": "<designator>" | null, "runway_t": <time of the last movement>,
     "go_arounds": G, "last_go_around": {"t", "flight"} | null}

"with_pos" counts aircraft that reported a position at least once in the
window; "total" also includes Mode S-only aircraft.

Departures and arrivals come from readsb's per-aircraft traces (a point every
few seconds, `--write-json-globe-index`; trace_recent of every aircraft near
and low now or within CANDIDATE_KEEP_S): a point inside a runway gate (a box
around the extended centreline, tracking along the runway) that is
descending below the arrival altitude is an arrival, climbing below the
departure altitude a departure, but only once the altitude has really risen
DEP_CONFIRM_FT within DEP_CONFIRM_S (the baro rate alone flips positive in
gusts on final). One aircraft counts at most once per kind
within DEDUP_S unless the other kind came in between (so arrival, go-around,
arrival again all count). The runway is the one the last movement used. On
the first run (no movements in the state yet) the last 24 hours are rebuilt
from trace_full_* files.

A probable go-around is an arrival followed by a departure of the same
aircraft on the same runway within the go-around window; a real turnaround
takes far longer. Arrivals slower than min_gs_kt or of excluded emitter
categories (rotorcraft, A7) are not paired. Each new go-around is appended
to the optional log as one tab-separated line (UTC time, callsign, runway,
hex). Each new movement (arrival or departure) can likewise be appended to
the optional movements log, the long-term record of how many aircraft used
which runway (the state keeps only the last 24 hours). With aircraft_db (a
tar1090-db directory) each go-around also gets the registration and type,
looked up once.

Every go-around is also kept forever in the history file and published as
the go-arounds JSON for the web view, with the METAR wind at detection time
(and its runway components), what happened next (landed / again / not
landed within OUTCOME_WAIT_S) and a thinned track in runway coordinates
(km along / across the centreline of the go-around's runway, from the
runway's centre). The track keeps growing from trace_full until the entry
is settled, which waits for trace_full to reach the end of the track (readsb
rewrites it only every several minutes).

Altitudes in ADS-B are pressure altitudes (1013.25 hPa datum). With a METAR
file configured, the gates compare them after a QNH correction, so a deep low
does not lift every approach above the gate. Without one (or with a stale
METAR) the raw pressure altitude is used. The gate and candidate altitudes
are heights above the airport elevation.

If the history exists but cannot be read, it is left untouched and neither it
nor the go-arounds JSON is written until someone fixes it; go-arounds of the
last 24 hours stay in the state and reach the history once it is readable
again.

Everything airport-specific comes from the TOML configuration (see
examples/rjff.toml); the configuration path can also be given with
TOGANOMON_CONFIG. An airport listed in airports.json needs only its ICAO
code: the reference point, elevation and runway gates come from there
unless the configuration gives them.

--check-runways measures every configured runway against the traffic in
trace_full_*: the true track and the centreline offset of the passes
through its gate, low and straight. Use it to check a new airports.json
entry or a hand-written [[runway]].
"""
import argparse
import glob
import gzip
import json
import math
import os
import sys
import calendar
import re
import tempfile
import time
import tomllib

DEFAULT_CONFIG = os.environ.get("TOGANOMON_CONFIG", "/etc/toganomon.toml")
WINDOW_S = 24 * 3600
SEEN_S = 60
# Refuse to count from an aircraft.json that readsb stopped updating.
STALE_S = 120
DEDUP_S = 20 * 60
# A go-around within this long after the previous one of the same airframe
# and callsign is its 2nd (3rd, ...) in the public history.
SAME_AIRFRAME_S = 2 * 3600
# A departure point counts only when the altitude really rises by
# DEP_CONFIRM_FT within DEP_CONFIRM_S after it: in gusts the baro rate of an
# aircraft on final flips above +rate_fpm for single points (JFS91
# 2026-11-20, +320..+832 fpm while descending to land).
DEP_CONFIRM_S = 30
DEP_CONFIRM_FT = 200
# A departure's callsign and category are taken from the first points within
# DEP_IDENT_S after the gate pass, not before it: a departing aircraft is
# often first received in the gate itself, its callsign a few seconds later,
# and one tracked since its arrival may still carry the arriving flight's
# callsign until the new one is broadcast.
DEP_IDENT_S = 60
# An aircraft that was a candidate (near and low) stays one this long, so its
# trace_recent is still read after it climbed out of the candidate box: a
# fast departure goes from the runway to above max_alt_ft between two runs.
# trace_recent covers about the last 5 minutes.
CANDIDATE_KEEP_S = 5 * 60
# History: wait this long for the aircraft to land before calling it "not
# landed"; keep the track from TRACK_BEFORE_S before the approach to
# TRACK_AFTER_S after landing (or TRACK_MAX_AFTER_S after the go-around),
# one point per TRACK_STEP_S.
OUTCOME_WAIT_S = 60 * 60
TRACK_BEFORE_S = 5 * 60
TRACK_AFTER_S = 60
TRACK_MAX_AFTER_S = 30 * 60
TRACK_STEP_S = 10
# readsb rewrites trace_full only every few minutes (often more than 10 min
# apart). A go-around is settled (its trace no longer re-read, its outcome no
# longer re-judged) SETTLE_S after the end of its track once trace_full
# reaches that end, or SETTLE_MAX_S after it if trace_full never does (the
# aircraft was lost before landing).
SETTLE_S = 10 * 60
SETTLE_MAX_S = 60 * 60
# Use the current METAR only for a go-around detected this recently.
METAR_FRESH_S = 15 * 60
# QNH correction: pressure altitude + (QNH - 1013.25) * FT_PER_HPA ~ altitude
# above mean sea level. 27 ft/hPa is the usual near-sea-level figure. Ignore
# a METAR older than QNH_MAX_AGE_S or a value outside QNH_RANGE_HPA.
STD_HPA = 1013.25
FT_PER_HPA = 27.0
QNH_MAX_AGE_S = 3 * 3600
QNH_RANGE_HPA = (900.0, 1100.0)
HPA_PER_INHG = 33.8639
KM_PER_DEG = 111.2

# Defaults for every configuration key; [[runway]] has no default.
DEFAULTS = {
    "airport": {"icao": None, "arp": None, "elevation_ft": None},
    "gate": {"arr_alt_ft": 1000, "dep_alt_ft": 2000, "track_tol_deg": 20.0, "rate_fpm": 200},
    "go_around": {"window_s": 600, "min_gs_kt": 60, "exclude_categories": ["A7"]},
    # max_alt_ft well above the departure gate: a departure first heard in
    # the climb can be some 3000 ft higher at the next run.
    "candidates": {"radius_km": 15.0, "max_alt_ft": 6000},
    "paths": {
        "aircraft": "/run/readsb/aircraft.json",
        "traces": "/run/readsb/traces",
        "state": "/var/lib/toganomon/state.json",
        "history": "/var/lib/toganomon/go_arounds.json",
        "summary": "/var/www/html/toganomon/summary.json",
        "go_arounds": "/var/www/html/toganomon/go-arounds.json",
        "metar": None,
        "log": None,
        "movements_log": None,
        "aircraft_db": None,
        "airports": None,
    },
}
RUNWAY_DEFAULTS = {"gate_along_km": 6.0, "gate_cross_km": 1.0, "length_m": None}
# Where airports.json (airport data by ICAO code) is looked for when
# [paths] airports is not set: next to this script, then the shared copy.
AIRPORTS_SEARCH = [os.path.join(os.path.dirname(os.path.realpath(__file__)), "airports.json"),
                   "/usr/local/share/toganomon/airports.json"]

# Set by configure(); module-level so the detection functions stay simple.
ICAO = None
ARP_LAT = ARP_LON = None
ELEVATION_FT = 0.0
RUNWAYS = []            # [{"ends": (a, b), "true_track", "lat", "lon", "gate_along_km", "gate_cross_km", "length_m"}]
RUNWAY_HDG = {}         # designator -> true track
GATE_ARR_ALT_FT = GATE_DEP_ALT_FT = TRACK_TOL_DEG = RATE_FPM = None
GA_WINDOW_S = GA_MIN_GS_KT = None
GA_EXCLUDE_CATEGORIES = set()
CANDIDATE_KM = CANDIDATE_ALT_FT = None
AIRCRAFT_JSON = TRACES_DIR = STATE_PATH = GA_HISTORY = OUT_PATH = GA_OUT = METAR_PATH = GA_LOG = None
MOVES_LOG = AIRCRAFT_DB = None


class ConfigError(ValueError):
    pass


def _num(v, what):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ConfigError(f"{what} must be a number")
    return float(v)


def load_airports(path=None):
    """airports.json -> {ICAO: entry}. path None: the first of AIRPORTS_SEARCH
    that exists, or {} when there is none. Raises ConfigError on a file that
    is there but unreadable, or on an explicit path that is missing."""
    paths = [path] if path else [p for p in AIRPORTS_SEARCH if os.path.exists(p)][:1]
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                doc = json.load(f)
            airports = doc["airports"]
            if not isinstance(airports, dict):
                raise ValueError("airports is not an object")
            return airports
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise ConfigError(f"airports file {p}: {e}") from e
    return {}


def _runway(r, i, arp):
    unknown = set(r) - {"ends", "true_track", "center"} - set(RUNWAY_DEFAULTS)
    if unknown:
        raise ConfigError(f"unknown key(s) in [[runway]] #{i + 1}: {', '.join(sorted(unknown))}")
    ends = r.get("ends")
    if not (isinstance(ends, list) and len(ends) == 2 and all(isinstance(e, str) and e for e in ends)):
        raise ConfigError(f'[[runway]] #{i + 1}: ends = ["16", "34"] is required')
    center = r.get("center", arp)
    if not isinstance(center, list) or len(center) != 2:
        raise ConfigError(f"[[runway]] #{i + 1}: center = [lat, lon]")
    rw = dict(RUNWAY_DEFAULTS)
    rw.update({k: v for k, v in r.items() if k in RUNWAY_DEFAULTS})
    return {
        "ends": tuple(ends), "true_track": _num(r.get("true_track"), "true_track") % 360.0,
        "lat": _num(center[0], "center lat"), "lon": _num(center[1], "center lon"),
        "gate_along_km": _num(rw["gate_along_km"], "gate_along_km"),
        "gate_cross_km": _num(rw["gate_cross_km"], "gate_cross_km"),
        "length_m": None if rw["length_m"] is None else _num(rw["length_m"], "length_m"),
    }


def parse_config(doc, airports=None):
    """TOML document (dict) -> complete configuration; raises ConfigError.

    What [airport] and [[runway]] leave out (arp, elevation_ft, the runways)
    comes from the airport's entry in airports.json: a dict {ICAO: entry},
    or None to load it from [paths] airports / AIRPORTS_SEARCH when needed.
    """
    cfg = {k: dict(v) for k, v in DEFAULTS.items()}
    for section, values in doc.items():
        if section == "runway":
            continue
        if section not in DEFAULTS or not isinstance(values, dict):
            raise ConfigError(f"unknown section [{section}]")
        unknown = set(values) - set(DEFAULTS[section])
        if unknown:
            raise ConfigError(f"unknown key(s) in [{section}]: {', '.join(sorted(unknown))}")
        cfg[section].update(values)
    a = cfg["airport"]
    if not isinstance(a["icao"], str) or not a["icao"]:
        raise ConfigError("[airport] icao is required")
    runways = doc.get("runway")
    if runways is not None and (not isinstance(runways, list) or not runways):
        raise ConfigError("[[runway]]: give at least one, or none to use airports.json")
    entry = None
    if a["arp"] is None or runways is None or a["elevation_ft"] is None:
        if airports is None:
            try:
                airports = load_airports(cfg["paths"]["airports"])
            except ConfigError:
                if a["arp"] is None or runways is None:
                    raise
                airports = {}        # only the elevation was wanted; 0 then
        entry = airports.get(a["icao"])
        if entry is not None and not isinstance(entry, dict):
            raise ConfigError(f"airports.json: {a['icao']} is not an object")
        if entry is None and (a["arp"] is None or runways is None):
            raise ConfigError(f"{a['icao']} is not in airports.json: give [airport] arp and [[runway]]")
    if a["arp"] is None:
        a["arp"] = entry.get("arp")
    if not isinstance(a["arp"], list) or len(a["arp"]) != 2:
        raise ConfigError("[airport] arp = [lat, lon] is required")
    a["arp"] = [_num(a["arp"][0], "arp lat"), _num(a["arp"][1], "arp lon")]
    if a["elevation_ft"] is None:
        a["elevation_ft"] = entry.get("elevation_ft", 0) if entry else 0
    a["elevation_ft"] = _num(a["elevation_ft"], "elevation_ft")
    if runways is None:
        runways = entry.get("runways")
        if not isinstance(runways, list) or not runways:
            raise ConfigError(f"airports.json: {a['icao']} has no runways")
        # "members" (the runways of a group) is only for people reading the file.
        runways = [{k: v for k, v in r.items() if k != "members"} if isinstance(r, dict) else r for r in runways]
    cfg["runways"] = []
    seen = set()
    for i, r in enumerate(runways):
        if not isinstance(r, dict):
            raise ConfigError(f"[[runway]] #{i + 1} is not a table")
        rw = _runway(r, i, a["arp"])
        if seen & set(rw["ends"]):
            raise ConfigError(f"[[runway]] #{i + 1}: designator used twice")
        seen |= set(rw["ends"])
        cfg["runways"].append(rw)
    return cfg


def load_config(path):
    try:
        with open(path, "rb") as f:
            return parse_config(tomllib.load(f))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"{path}: {e}") from e


def configure(cfg):
    """Install a parsed configuration as the module settings."""
    global ICAO, ARP_LAT, ARP_LON, ELEVATION_FT, RUNWAYS, RUNWAY_HDG, GATE_ARR_ALT_FT, GATE_DEP_ALT_FT, TRACK_TOL_DEG
    global RATE_FPM, GA_WINDOW_S, GA_MIN_GS_KT, GA_EXCLUDE_CATEGORIES, CANDIDATE_KM, CANDIDATE_ALT_FT
    global AIRCRAFT_JSON, TRACES_DIR, STATE_PATH, GA_HISTORY, OUT_PATH, GA_OUT, METAR_PATH, GA_LOG
    global MOVES_LOG, AIRCRAFT_DB
    ICAO = cfg["airport"]["icao"]
    ARP_LAT, ARP_LON = cfg["airport"]["arp"]
    ELEVATION_FT = cfg["airport"]["elevation_ft"]
    RUNWAYS = cfg["runways"]
    RUNWAY_HDG = {}
    for rw in RUNWAYS:
        RUNWAY_HDG[rw["ends"][0]] = rw["true_track"]
        RUNWAY_HDG[rw["ends"][1]] = (rw["true_track"] + 180.0) % 360.0
    g, ga, c, p = cfg["gate"], cfg["go_around"], cfg["candidates"], cfg["paths"]
    GATE_ARR_ALT_FT, GATE_DEP_ALT_FT = g["arr_alt_ft"], g["dep_alt_ft"]
    TRACK_TOL_DEG, RATE_FPM = g["track_tol_deg"], g["rate_fpm"]
    GA_WINDOW_S, GA_MIN_GS_KT = ga["window_s"], ga["min_gs_kt"]
    GA_EXCLUDE_CATEGORIES = set(ga["exclude_categories"])
    CANDIDATE_KM, CANDIDATE_ALT_FT = c["radius_km"], c["max_alt_ft"]
    AIRCRAFT_JSON, TRACES_DIR, STATE_PATH = p["aircraft"], p["traces"], p["state"]
    GA_HISTORY, OUT_PATH, GA_OUT = p["history"], p["summary"], p["go_arounds"]
    METAR_PATH, GA_LOG, MOVES_LOG = p["metar"], p["log"], p["movements_log"]
    AIRCRAFT_DB = p["aircraft_db"]


def write_atomic(path, text, mode=0o644):
    # Temp file in the same dir + rename, so readers never see a partial file.
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def load_state(path, now):
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
        if isinstance(state.get("aircraft"), dict) and isinstance(state.get("started"), (int, float)):
            if not isinstance(state.get("movements", []), list):
                del state["movements"]      # rebuilt from the traces on this run
            if not isinstance(state.get("go_arounds", []), list):
                del state["go_arounds"]
            return state
    except (OSError, ValueError):
        pass
    return {"started": now, "aircraft": {}}


def update(state, snapshot):
    """Merge one aircraft.json snapshot into state and prune old entries.

    state["aircraft"] maps hex -> {"t": last heard, "p": last position or None}.
    """
    now = snapshot["now"]
    ac = state["aircraft"]
    for a in snapshot.get("aircraft", []):
        hx = a.get("hex")
        seen = a.get("seen")
        if not isinstance(hx, str) or not isinstance(seen, (int, float)) or seen > SEEN_S:
            continue
        entry = ac.setdefault(hx, {"t": 0, "p": None})
        entry["t"] = max(entry["t"], now - seen)
        seen_pos = a.get("seen_pos")
        if "lat" in a and isinstance(seen_pos, (int, float)) and seen_pos <= SEEN_S:
            entry["p"] = max(entry["p"] or 0, now - seen_pos)
    cutoff = now - WINDOW_S
    for hx in [h for h, e in ac.items() if e["t"] < cutoff]:
        del ac[hx]
    return state


def runway_of(designator):
    """The configured runway with this designator at either end (or the first one)."""
    return next((rw for rw in RUNWAYS if designator in rw["ends"]), RUNWAYS[0])


def runway_frame(lat, lon, rw=None):
    """(along, cross) in km from the runway's centre (default: the first
    runway); along is positive towards the first end's true track."""
    rw = rw or RUNWAYS[0]
    x = (lon - rw["lon"]) * KM_PER_DEG * math.cos(math.radians(rw["lat"]))
    y = (lat - rw["lat"]) * KM_PER_DEG
    ux, uy = math.sin(math.radians(rw["true_track"])), math.cos(math.radians(rw["true_track"]))
    return x * ux + y * uy, x * uy - y * ux


def arp_distance_km(lat, lon):
    x = (lon - ARP_LON) * KM_PER_DEG * math.cos(math.radians(ARP_LAT))
    y = (lat - ARP_LAT) * KM_PER_DEG
    return math.hypot(x, y)


def _angdiff(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


def classify_point(p, alt_offset=0.0):
    """Trace point -> ("arr" | "dep", runway designator) inside a gate, else None.

    readsb trace point: [dt, lat, lon, alt, gs, track, flags, baro_rate,
    details, type, alt_geom, geom_rate, ...]; alt is "ground" or feet
    (pressure altitude). alt_offset (ft) is added before the gate check.
    """
    if len(p) < 8:
        return None
    lat, lon, alt, track, rate = p[1], p[2], p[3], p[5], p[7]
    if rate is None and len(p) > 11:
        rate = p[11]
    num = (int, float)
    if not all(isinstance(v, num) and not isinstance(v, bool) for v in (lat, lon, alt, track, rate)):
        return None
    alt += alt_offset
    if alt > max(GATE_ARR_ALT_FT, GATE_DEP_ALT_FT):
        return None
    for rw in RUNWAYS:
        along, cross = runway_frame(lat, lon, rw)
        if abs(along) > rw["gate_along_km"] or abs(cross) > rw["gate_cross_km"]:
            continue
        if _angdiff(track, rw["true_track"]) <= TRACK_TOL_DEG:
            rwy = rw["ends"][0]
        elif _angdiff(track, rw["true_track"] + 180.0) <= TRACK_TOL_DEG:
            rwy = rw["ends"][1]
        else:
            continue
        if rate <= -RATE_FPM and alt <= GATE_ARR_ALT_FT:
            return "arr", rwy
        if rate >= RATE_FPM and alt <= GATE_DEP_ALT_FT:
            return "dep", rwy
    return None


OTHER = {"arr": "dep", "dep": "arr"}


def _flight(p):
    d = p[8] if len(p) > 8 else None
    f = d.get("flight") if isinstance(d, dict) else None
    return (f.strip() or None) if isinstance(f, str) else None


def _category(p):
    d = p[8] if len(p) > 8 else None
    c = d.get("category") if isinstance(d, dict) else None
    return c if isinstance(c, str) and c else None


def _gs(p):
    v = p[4] if len(p) > 4 else None
    return round(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _climb_confirmed(pts, i):
    """True when a point within DEP_CONFIRM_S after pts[i] is at least
    DEP_CONFIRM_FT above it (both pressure altitudes, so no QNH needed)."""
    alt0 = pts[i][3]
    for p in pts[i + 1:]:
        if p[0] - pts[i][0] > DEP_CONFIRM_S:
            break
        alt = p[3] if len(p) > 3 else None
        if isinstance(alt, (int, float)) and not isinstance(alt, bool) and alt - alt0 >= DEP_CONFIRM_FT:
            return True
    return False


def detect_movements(doc, alt_offset=0.0, now=None):
    """readsb trace document -> [(time, kind, runway, callsign, ground speed kt,
    emitter category)]; the last three may be None.

    One event per pass: the same kind again within DEDUP_S is the same pass,
    unless the other kind came in between. With now (the per-minute run), a
    departure whose callsign has not been broadcast since the pass is held
    back until DEP_IDENT_S after it: a steep climb confirms it in seconds,
    before the new callsign, and a recorded movement is not rewritten.
    """
    t0 = doc.get("timestamp")
    out, last, flight, cat = [], {}, None, None
    if not isinstance(t0, (int, float)):
        return out
    pts = [p for p in doc.get("trace") or []
           if isinstance(p, list) and p and isinstance(p[0], (int, float))]
    for i, p in enumerate(pts):
        flight = _flight(p) or flight
        cat = _category(p) or cat
        hit = classify_point(p, alt_offset)
        if not hit:
            continue
        if hit[0] == "dep" and not _climb_confirmed(pts, i):
            continue
        t = t0 + p[0]
        if t - last.get(hit[0], -1e18) < DEDUP_S:
            continue
        ident_flight, ident_cat = flight, cat
        if hit[0] == "dep":
            ident_flight, ident_cat, fresh = _ident_after(pts, i, flight, cat)
            if not fresh and now is not None and now - t < DEP_IDENT_S:
                continue
        last[hit[0]] = t
        last.pop(OTHER[hit[0]], None)
        out.append((t, hit[0], hit[1], ident_flight, _gs(p), ident_cat))
    return out


def _ident_after(pts, i, flight, cat):
    """Callsign and category for a departure at pts[i]: the first ones
    broadcast within DEP_IDENT_S from the pass, else what was known before;
    and whether a callsign was broadcast since the pass."""
    new_flight = new_cat = None
    for p in pts[i:]:
        if p[0] - pts[i][0] > DEP_IDENT_S:
            break
        new_flight = new_flight or _flight(p)
        new_cat = new_cat or _category(p)
        if new_flight and new_cat:
            break
    return new_flight or flight, new_cat or cat, new_flight is not None


def _same_pass(moves, hx, kind, t):
    """True if an event of this aircraft and kind within DEDUP_S of t is already
    recorded with no event of the other kind in between."""
    mine = [m for m in moves if m[3] == hx]
    for m in mine:
        if m[1] != kind or abs(m[0] - t) >= DEDUP_S:
            continue
        lo, hi = min(m[0], t), max(m[0], t)
        if not any(o[1] == OTHER[kind] and lo < o[0] < hi for o in mine):
            return True
    return False


def record_movements(state, hx, events, now):
    """Add one aircraft's events, skipping ones already recorded, and prune.

    A movement is [time, kind, runway, hex, callsign, ground speed, category]
    (the last three may be None; older states have only the first five).
    """
    moves = state.setdefault("movements", [])
    for e in events:
        t, kind, rwy = e[:3]
        flight, gs, cat = (list(e[3:6]) + [None] * 3)[:3]
        if t < now - WINDOW_S or _same_pass(moves, hx, kind, t):
            continue
        moves.append([t, kind, rwy, hx, flight, gs, cat])
    cutoff = now - WINDOW_S
    moves[:] = sorted((m for m in moves if m[0] >= cutoff), key=lambda m: m[0])
    return state


def load_trace(path):
    try:
        with open(path, "rb") as f:
            raw = f.read()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return json.loads(raw)
    except (OSError, ValueError, EOFError):
        return None


# --check-runways: a pass is a run of points without a gap over
# CHECK_GAP_S, inside the gate widened to CHECK_CROSS_KM each side, below
# CHECK_ALT_FT above the airport; it counts when it has CHECK_MIN_POINTS
# points, covers CHECK_MIN_KM and runs within CHECK_TOL_DEG of the runway.
CHECK_GAP_S = 60
CHECK_CROSS_KM = 2.0
CHECK_ALT_FT = 1500
CHECK_MIN_POINTS = 5
CHECK_MIN_KM = 2.0
CHECK_TOL_DEG = 20.0
CHECK_OK_DEG = 1.0
CHECK_OK_KM = 0.2


def runway_passes(doc, rw):
    """[(true track folded to the first end, mean cross km)] of doc's passes
    through rw's gate."""
    base = doc.get("timestamp") if isinstance(doc, dict) else None
    trace = doc.get("trace") if isinstance(doc, dict) else None
    if not isinstance(base, (int, float)) or not isinstance(trace, list):
        return []
    runs, run, last_t = [], [], None
    for p in trace:
        if not (isinstance(p, list) and len(p) > 3):
            continue
        t, lat, lon, alt = p[0], p[1], p[2], p[3]
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (t, lat, lon, alt)):
            continue
        along, cross = runway_frame(lat, lon, rw)
        inside = (abs(along) <= rw["gate_along_km"] and abs(cross) <= CHECK_CROSS_KM
                  and alt - ELEVATION_FT <= CHECK_ALT_FT)
        if not inside or (last_t is not None and t - last_t > CHECK_GAP_S):
            if len(run) >= CHECK_MIN_POINTS:
                runs.append(run)
            run = []
        if inside:
            run.append((along, cross))
        last_t = t
    if len(run) >= CHECK_MIN_POINTS:
        runs.append(run)
    out = []
    for run in runs:
        (a0, c0), (a1, c1) = run[0], run[-1]
        if math.hypot(a1 - a0, c1 - c0) < CHECK_MIN_KM:
            continue
        # angle off the configured axis, + = clockwise, folded to the first end
        off = math.degrees(math.atan2(c1 - c0, a1 - a0))
        if abs(off) > 90:
            off = off - 180 if off > 0 else off + 180
        if abs(off) > CHECK_TOL_DEG:
            continue
        out.append(((rw["true_track"] + off) % 360.0, sum(c for _, c in run) / len(run)))
    return out


def check_runways(paths=None):
    """[{"ends", "true_track", "passes", "measured", "offset_km"}] per
    configured runway; measured / offset_km are medians (None: no pass)."""
    import statistics
    if paths is None:
        paths = glob.glob(os.path.join(TRACES_DIR, "*", "trace_full_*.json"))
    found = [[] for _ in RUNWAYS]
    for path in paths:
        doc = load_trace(path)
        for i, rw in enumerate(RUNWAYS):
            found[i].extend(runway_passes(doc, rw))
    out = []
    for rw, passes in zip(RUNWAYS, found):
        measured = offset = None
        if passes:
            ref = rw["true_track"]
            measured = round((ref + statistics.median(
                (tr - ref + 180.0) % 360.0 - 180.0 for tr, _ in passes)) % 360.0, 1)
            offset = round(statistics.median(c for _, c in passes), 2)
        out.append({"ends": list(rw["ends"]), "true_track": rw["true_track"], "passes": len(passes),
                    "measured": measured, "offset_km": offset})
    return out


def check_report(results):
    lines = []
    for r in results:
        name = "/".join(r["ends"])
        if not r["passes"]:
            lines.append(f"{ICAO} {name}: true_track {r['true_track']:.1f}, no pass through the gate found")
            continue
        diff = (r["measured"] - r["true_track"] + 180.0) % 360.0 - 180.0
        side = "right" if r["offset_km"] >= 0 else "left"
        ok = abs(diff) <= CHECK_OK_DEG and abs(r["offset_km"]) <= CHECK_OK_KM
        lines.append(f"{ICAO} {name}: true_track {r['true_track']:.1f}, measured {r['measured']:.1f} "
                     f"({diff:+.1f}) from {r['passes']} passes, centreline {abs(r['offset_km']):.2f} km "
                     f"{side} of the centre (looking towards {r['ends'][0]}): {'OK' if ok else 'CHECK'}")
    return lines


def trace_path(hx, kind):
    return os.path.join(TRACES_DIR, hx[-2:], f"trace_{kind}_{hx}.json")


def candidates(snapshot, alt_offset=0.0):
    """Hex of aircraft near the airport and low enough to be in a movement."""
    out = []
    for a in snapshot.get("aircraft", []):
        hx, lat, lon, alt = a.get("hex"), a.get("lat"), a.get("lon"), a.get("alt_baro")
        if not isinstance(hx, str) or not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        if not (alt == "ground" or (isinstance(alt, (int, float)) and alt + alt_offset <= CANDIDATE_ALT_FT)):
            continue
        if arp_distance_km(lat, lon) <= CANDIDATE_KM:
            out.append(hx)
    return out


def update_movements(state, snapshot, alt_offset=0.0):
    """alt_offset is applied to every trace, also when rebuilding the window
    (one QNH for the last 24 hours: good enough for a one-off backfill)."""
    now = snapshot["now"]
    if "movements" not in state:
        # First run: rebuild the window from the long traces.
        state["movements"] = []
        for path in glob.glob(os.path.join(TRACES_DIR, "*", "trace_full_*.json")):
            doc = load_trace(path)
            if doc and isinstance(doc.get("icao"), str):
                record_movements(state, doc["icao"], detect_movements(doc, alt_offset), now)
    keep = state.get("candidates")
    if not isinstance(keep, dict):
        keep = state["candidates"] = {}
    for hx in candidates(snapshot, alt_offset):
        keep[hx] = now
    for hx in [h for h, t in keep.items() if not isinstance(t, (int, float)) or now - t > CANDIDATE_KEEP_S]:
        del keep[hx]
    for hx in sorted(keep):
        doc = load_trace(trace_path(hx, "recent"))
        if doc:
            record_movements(state, hx, detect_movements(doc, alt_offset, now), now)
    return record_movements(state, "", [], now)


def _ga_capable(m):
    """False for an arrival known to be too slow or from a rotorcraft."""
    gs = m[5] if len(m) > 5 else None
    cat = m[6] if len(m) > 6 else None
    return not ((gs is not None and gs < GA_MIN_GS_KT) or cat in GA_EXCLUDE_CATEGORIES)


def find_go_arounds(moves):
    """Arrival -> departure pairs of one aircraft on one runway within GA_WINDOW_S.

    Returns [{"t": departure time, "arr_t", "hex", "flight", "rwy", "gs"
    (arrival ground speed), "category"}].
    """
    out, last_arr = [], {}
    for m in sorted(moves, key=lambda m: m[0]):
        t, kind, rwy, hx = m[:4]
        flight = m[4] if len(m) > 4 else None
        if kind == "arr":
            last_arr[hx] = m
            continue
        a = last_arr.pop(hx, None)
        if a and a[2] == rwy and 0 < t - a[0] <= GA_WINDOW_S and _ga_capable(a):
            cat = (m[6] if len(m) > 6 else None) or (a[6] if len(a) > 6 else None)
            out.append({"t": t, "arr_t": a[0], "hex": hx,
                        "flight": flight or (a[4] if len(a) > 4 else None), "rwy": rwy,
                        "gs": a[5] if len(a) > 5 else None, "category": cat})
    return out


def flight_from_trace(hx, t):
    """Last callsign broadcast up to time t, from the long trace (or None)."""
    doc = load_trace(trace_path(hx, "full"))
    if not doc or not isinstance(doc.get("timestamp"), (int, float)):
        return None
    flight = None
    for p in doc.get("trace") or []:
        if isinstance(p, list) and p and isinstance(p[0], (int, float)) and doc["timestamp"] + p[0] <= t + 60:
            flight = _flight(p) or flight
    return flight


def log_line(ga):
    """One tab-separated log line: UTC time, callsign ("-" if unknown), runway, hex."""
    when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ga["t"]))
    return f"{when}\t{ga.get('flight') or '-'}\t{ga['rwy']}\t{ga['hex']}\n"


def movement_key(m):
    return (m[0], m[1], m[3])


def movement_lines(moves, known):
    """Log lines for the movements whose key is not in known, oldest first.
    One tab-separated line each: UTC time, arr/dep, runway, hex, callsign,
    ground speed (kt), emitter category; "-" for an unknown value."""
    out = []
    for m in sorted(moves, key=lambda m: m[0]):
        if movement_key(m) in known:
            continue
        t, kind, rwy, hx = m[:4]
        flight, gs, cat = (list(m[4:7]) + [None] * 3)[:3]
        when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
        out.append("\t".join([when, kind, rwy, hx, flight or "-",
                              str(round(gs)) if isinstance(gs, (int, float)) else "-", cat or "-"]) + "\n")
    return out


def update_go_arounds(state, now, log=None):
    """Record go-arounds not seen before; returns the new ones."""
    known = state.setdefault("go_arounds", [])
    new = []
    for ga in find_go_arounds(state.get("movements", [])):
        if any(k["hex"] == ga["hex"] and abs(k["t"] - ga["t"]) < 60 for k in known):
            continue
        if not ga["flight"]:
            ga["flight"] = flight_from_trace(ga["hex"], ga["t"])
        known.append(ga)
        new.append(ga)
        if log:
            log.write(log_line(ga))
    known[:] = [k for k in known if k["t"] >= now - WINDOW_S]
    return new


def parse_metar_wind(text):
    """METAR text -> {"raw", "dir" (true deg or None for VRB), "spd", "gust", "unit"} or None."""
    if not isinstance(text, str):
        return None
    raw = " ".join(text.split())
    for tok in raw.split():
        if tok.endswith(("KT", "MPS")) and len(tok) >= 7:
            unit = "kt" if tok.endswith("KT") else "m/s"
            body = tok[:-2] if unit == "kt" else tok[:-3]
            d, rest = body[:3], body[3:]
            spd, _, gust = rest.partition("G")
            if not (d == "VRB" or d.isdigit()) or not spd.isdigit() or (gust and not gust.isdigit()):
                continue
            return {"raw": raw, "dir": None if d == "VRB" else int(d), "spd": int(spd),
                    "gust": int(gust) if gust else None, "unit": unit}
    return None


def metar_obs_time(text, now):
    """Epoch of the METAR's ddhhmmZ group, in the month closest to now (or None)."""
    m = re.search(r"\b(\d{2})(\d{2})(\d{2})Z\b", text or "")
    if not m:
        return None
    day, hour, minute = (int(g) for g in m.groups())
    y, mon = time.gmtime(now)[:2]
    best = None
    for off in (-1, 0, 1):
        yy, mm = y + (mon - 1 + off) // 12, (mon - 1 + off) % 12 + 1
        if not 1 <= day <= calendar.monthrange(yy, mm)[1] or hour > 23 or minute > 59:
            continue
        t = calendar.timegm((yy, mm, day, hour, minute, 0))
        if best is None or abs(t - now) < abs(best - now):
            best = t
    return best


def parse_metar_qnh(text, now):
    """QNH in hPa from Qnnnn or Annnn (inHg), or None if absent, stale or implausible."""
    if not isinstance(text, str):
        return None
    obs = metar_obs_time(text, now)
    if obs is None or abs(now - obs) > QNH_MAX_AGE_S:
        return None
    m = re.search(r"\b([QA])(\d{4})\b", text)
    if not m:
        return None
    qnh = float(m.group(2)) if m.group(1) == "Q" else round(int(m.group(2)) / 100 * HPA_PER_INHG, 1)
    lo, hi = QNH_RANGE_HPA
    return qnh if lo <= qnh <= hi else None


def qnh_offset_ft(qnh):
    """Feet to add to a pressure altitude to get altitude on QNH (0 without QNH)."""
    return 0.0 if qnh is None else (qnh - STD_HPA) * FT_PER_HPA


def wind_components(wind, rwy):
    """Head (+) / tail (-) and cross components of the steady wind on a runway.

    METAR wind is true, like RUNWAY_HDG. Returns None for VRB or no wind.
    """
    if not wind or wind.get("dir") is None or rwy not in RUNWAY_HDG:
        return None
    a = math.radians(wind["dir"] - RUNWAY_HDG[rwy])
    cross = wind["spd"] * math.sin(a)
    return {"head": round(wind["spd"] * math.cos(a)), "cross": abs(round(cross)),
            "from": "R" if cross > 0.5 else "L" if cross < -0.5 else None}


def extract_track(doc, t_from, t_to, rwy=None):
    """Thinned track [[s from t_from, along km, cross km, alt ft]] from a trace
    doc, in the frame of the runway with designator rwy (default: the first)."""
    rw = runway_of(rwy)
    out, last = [], None
    t0 = doc.get("timestamp") if doc else None
    if not isinstance(t0, (int, float)):
        return out
    for p in doc.get("trace") or []:
        if not isinstance(p, list) or len(p) < 4 or not isinstance(p[0], (int, float)):
            continue
        t = t0 + p[0]
        if t < t_from or t > t_to or not isinstance(p[1], (int, float)) or not isinstance(p[2], (int, float)):
            continue
        if last is not None and t - last < TRACK_STEP_S:
            continue
        last = t
        alt = 0 if p[3] == "ground" else p[3] if isinstance(p[3], (int, float)) else None
        along, cross = runway_frame(p[1], p[2], rw)
        out.append([round(t - t_from), round(along, 2), round(cross, 2), alt])
    return out


def trace_end(doc):
    """Time of the last point of a trace doc, or None."""
    t0 = doc.get("timestamp") if doc else None
    pts = [p for p in (doc.get("trace") or []) if isinstance(p, list) and p and isinstance(p[0], (int, float))] \
        if isinstance(t0, (int, float)) else []
    return t0 + pts[-1][0] if pts else None


def track_span(h):
    """(start, end) of the track kept for a history entry."""
    start = (h.get("arr_t") or h["t"]) - TRACK_BEFORE_S
    end = h["outcome"]["t"] + TRACK_AFTER_S if h["outcome"].get("t") else h["t"] + TRACK_MAX_AFTER_S
    return start, end


def load_history(path):
    """The history list; [] if the file does not exist yet, None if it exists
    but cannot be used (the caller must then not overwrite it)."""
    try:
        with open(path, encoding="utf-8") as f:
            h = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError):
        return None
    return h if isinstance(h, list) else None


def read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def update_history(history, state, now, metar_text=None, trace_loader=None, qnh=None):
    """Add new go-arounds to the history, settle outcomes and grow tracks.

    qnh is the value the gate was corrected with (kept for later checks)."""
    trace_loader = trace_loader or (lambda hx: load_trace(trace_path(hx, "full")))
    ids = {h["id"] for h in history}
    for ga in state.get("go_arounds", []):
        gid = f"{ga['hex']}-{int(ga['t'])}"
        if gid in ids:
            continue
        wind = parse_metar_wind(metar_text) if now - ga["t"] <= METAR_FRESH_S else None
        history.append({
            "id": gid, "t": ga["t"], "arr_t": ga.get("arr_t"), "hex": ga["hex"],
            "flight": ga.get("flight"), "rwy": ga["rwy"],
            "climb_after_s": round(ga["t"] - ga["arr_t"]) if ga.get("arr_t") else None,
            "wind": wind, "wind_rwy": wind_components(wind, ga["rwy"]), "qnh": qnh,
            "gs": ga.get("gs"), "category": ga.get("category"),
            "outcome": {"status": "pending"}, "track": [], "track_from": None, "done": False,
        })
        ids.add(gid)
    moves = state.get("movements", [])
    for h in history:
        if h.get("done"):
            # Settled before trace_full reached the end of the track (before
            # 2026-11-16; "track_full" absent): one more try while readsb
            # still keeps the trace (WINDOW_S).
            if "track_full" not in h:
                start, end = track_span(h)
                doc = trace_loader(h["hex"]) if now - end <= WINDOW_S else None
                full = (trace_end(doc) or 0) >= end
                if full:
                    track = extract_track(doc, start, end, h["rwy"])
                    if len(track) > len(h.get("track") or []):
                        h["track"], h["track_from"] = track, start
                h["track_full"] = full
            continue
        # Re-judged every run until settled: an arrival that looked like a
        # landing can still turn into a second go-around.
        later = sorted(m for m in moves if m[3] == h["hex"] and m[1] == "arr" and h["t"] < m[0] <= h["t"] + OUTCOME_WAIT_S)
        if later:
            again = any(o["hex"] == h["hex"] and o.get("arr_t") == later[0][0] for o in history)
            h["outcome"] = {"status": "again" if again else "landed", "t": later[0][0], "rwy": later[0][2]}
        elif now - h["t"] > OUTCOME_WAIT_S:
            h["outcome"] = {"status": "not_landed"}
        else:
            h["outcome"] = {"status": "pending"}
        start, end = track_span(h)
        doc = trace_loader(h["hex"])
        track = extract_track(doc, start, end, h["rwy"]) if doc else []
        if len(track) >= len(h.get("track") or []):
            h["track"], h["track_from"] = track, start
        full = (trace_end(doc) or 0) >= end
        if h["outcome"]["status"] != "pending" and now >= end + (SETTLE_S if full else SETTLE_MAX_S):
            h["done"], h["track_full"] = True, full
    history.sort(key=lambda h: h["t"])
    return history


def _db_file(db_dir, name, cache):
    if name not in cache:
        try:
            with open(os.path.join(db_dir, name + ".js"), "rb") as f:
                raw = f.read()
            if raw[:2] == b"\x1f\x8b":
                raw = gzip.decompress(raw)
            doc = json.loads(raw)
            cache[name] = doc if isinstance(doc, dict) else None
        except (OSError, ValueError, EOFError):
            cache[name] = None
    return cache[name]


def aircraft_info(hx, db_dir, cache=None):
    """hex -> {"reg", "type"} from a tar1090-db directory (files named by a hex
    prefix, gzipped JSON {rest of hex: [reg, type, flags, ...]}, a file's
    "children" naming the longer prefixes split off into files of their own);
    None when the directory cannot be read, {"reg": None, "type": None} when
    the aircraft is not in it."""
    cache = {} if cache is None else cache
    hx = hx.upper()
    level = 1
    doc = _db_file(db_dir, hx[:1], cache)
    while doc is not None and hx[:level + 1] in (doc.get("children") or []) and level < len(hx):
        level += 1
        doc = _db_file(db_dir, hx[:level], cache)
    if doc is None:
        return None
    e = doc.get(hx[level:])
    str_or_none = lambda v: v if isinstance(v, str) and v else None
    if not isinstance(e, list):
        return {"reg": None, "type": None}
    return {"reg": str_or_none(e[0] if e else None), "type": str_or_none(e[1] if len(e) > 1 else None)}


def add_aircraft(history, db_dir):
    """Registration and type for entries that have none yet (looked up once;
    an entry stays without them while the database cannot be read)."""
    cache = {}
    for h in history:
        if "reg" not in h:
            info = aircraft_info(h["hex"], db_dir, cache)
            if info is not None:
                h.update(info)


def airframe_nth(history):
    """id -> 1 for a flight's first go-around, 2 for one of the same airframe
    and callsign within SAME_AIRFRAME_S after its previous go-around, and so
    on (the same airframe on its next flight starts again at 1)."""
    out, last = {}, {}
    for h in sorted(history, key=lambda h: h["t"]):
        key = (h.get("hex"), h.get("flight"))
        prev = last.get(key) if h.get("hex") else None
        n = prev[1] + 1 if prev and h["t"] - prev[0] <= SAME_AIRFRAME_S else 1
        last[key] = (h["t"], n)
        out[h.get("id")] = n
    return out


def airport_info():
    """The airport and runways, for drawing tracks (runway frame) in the web view."""
    return {"icao": ICAO, "arp": [ARP_LAT, ARP_LON], "runways": [
        {"ends": list(rw["ends"]), "true_track": rw["true_track"], "center": [rw["lat"], rw["lon"]],
         "length_m": rw["length_m"]} for rw in RUNWAYS]}


def public_history(history, now):
    """GA_OUT payload: newest first, without internal fields."""
    keep = ("id", "t", "arr_t", "hex", "flight", "reg", "type", "rwy", "climb_after_s", "wind",
            "wind_rwy", "qnh", "gs", "category", "outcome", "track", "track_from", "done")
    nth = airframe_nth(history)
    return {"now": now, "airport": airport_info(),
            "go_arounds": [dict({k: h.get(k) for k in keep}, nth=nth[h.get("id")]) for h in reversed(history)]}


def summary(state, now):
    cutoff = now - WINDOW_S
    ac = state["aircraft"].values()
    moves = [m for m in state.get("movements", []) if m[0] >= cutoff]
    gas = sorted((g for g in state.get("go_arounds", []) if g["t"] >= cutoff), key=lambda g: g["t"])
    return {
        "now": now,
        "window_s": WINDOW_S,
        "total": len(ac),
        "with_pos": sum(1 for e in ac if e["p"] is not None and e["p"] >= cutoff),
        "started": state["started"],
        "arrivals": sum(1 for m in moves if m[1] == "arr"),
        "departures": sum(1 for m in moves if m[1] == "dep"),
        "runway": moves[-1][2] if moves else None,
        "runway_t": moves[-1][0] if moves else None,
        "go_arounds": len(gas),
        "last_go_around": {"t": gas[-1]["t"], "flight": gas[-1]["flight"]} if gas else None,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Airport movements and probable go-arounds from readsb traces.")
    ap.add_argument("-c", "--config", default=DEFAULT_CONFIG, help=f"TOML configuration (default {DEFAULT_CONFIG})")
    ap.add_argument("--check-runways", action="store_true",
                    help="compare the configured runways with the traffic in trace_full_* and exit")
    args = ap.parse_args(argv)
    try:
        configure(load_config(args.config))
    except ConfigError as e:
        print(f"[{time.strftime('%Y-%m-%d %X')}] configuration: {e}", file=sys.stderr)
        return 2
    if args.check_runways:
        results = check_runways()
        print("\n".join(check_report(results)))
        return 0 if all(r["passes"] for r in results) else 1
    try:
        with open(AIRCRAFT_JSON, encoding="utf-8") as f:
            snapshot = json.load(f)
        now = snapshot["now"]
    except (OSError, ValueError, KeyError) as e:
        print(f"[{time.strftime('%Y-%m-%d %X')}] cannot read {AIRCRAFT_JSON}: {e}", file=sys.stderr)
        return 1
    if time.time() - now > STALE_S:
        # Leave the old summary in place; a reader can tell it is stale by its "now".
        print(f"[{time.strftime('%Y-%m-%d %X')}] {AIRCRAFT_JSON} is stale, skipped", file=sys.stderr)
        return 1
    stamp = time.strftime('%Y-%m-%d %X')
    metar = read_text(METAR_PATH) if METAR_PATH else None
    qnh = parse_metar_qnh(metar, now)
    state = update(load_state(STATE_PATH, now), snapshot)
    # A rebuilt state (first run, or the state was lost) logs the rebuilt
    # 24 hours again: a reader may see a movement twice after that.
    known = {movement_key(m) for m in state.get("movements", []) if isinstance(m, list) and len(m) >= 4}
    # The gates and the candidate altitude are heights above the airport.
    update_movements(state, snapshot, qnh_offset_ft(qnh) - ELEVATION_FT)
    if MOVES_LOG:
        try:
            with open(MOVES_LOG, "a", encoding="utf-8") as log:
                log.writelines(movement_lines(state["movements"], known))
        except OSError as e:
            print(f"[{stamp}] cannot write {MOVES_LOG}: {e}", file=sys.stderr)
    try:
        if GA_LOG:
            with open(GA_LOG, "a", encoding="utf-8") as log:
                update_go_arounds(state, now, log)
        else:
            update_go_arounds(state, now)
    except OSError as e:
        print(f"[{time.strftime('%Y-%m-%d %X')}] cannot write {GA_LOG}: {e}", file=sys.stderr)
        update_go_arounds(state, now)
    history = load_history(GA_HISTORY)
    rc = 0
    if history is None:
        # Never replace a history we could not read: it is the only long-term record.
        print(f"[{stamp}] cannot read {GA_HISTORY}; history not updated until it is fixed", file=sys.stderr)
        rc = 1
    else:
        history = update_history(history, state, now, metar, qnh=qnh)
        if AIRCRAFT_DB:
            add_aircraft(history, AIRCRAFT_DB)
    write_atomic(STATE_PATH, json.dumps(state, separators=(",", ":")), 0o600)
    if history is not None:
        write_atomic(GA_HISTORY, json.dumps(history, separators=(",", ":"), ensure_ascii=False), 0o600)
        write_atomic(GA_OUT, json.dumps(public_history(history, now), separators=(",", ":"), ensure_ascii=False))
    write_atomic(OUT_PATH, json.dumps(summary(state, now)))
    return rc


if __name__ == "__main__":
    sys.exit(main())
