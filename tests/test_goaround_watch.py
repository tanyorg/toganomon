#!/usr/bin/env python3
"""Unit tests for goaround_watch.py (stdlib only, synthetic traces).

Run: python3 -m unittest discover -s tests   (or python3 tests/test_goaround_watch.py)
The RJFF example configuration is the fixture, so it is checked too.
"""
import calendar
import gzip
import importlib.util
import json
import math
import os
import sys
import tempfile
import time
import tomllib
import unittest

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("goaround_watch", os.path.join(HERE, "..", "goaround_watch.py"))
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)
RJFF_TOML = os.path.join(HERE, "..", "examples", "rjff.toml")
with open(RJFF_TOML, "rb") as _f:
    RJFF = cs.parse_config(tomllib.load(_f))
cs.configure(RJFF)

T0 = 1_800_000_000.0


def snap(now, *aircraft):
    return {"now": now, "aircraft": list(aircraft)}


class Update(unittest.TestCase):
    def test_counts_recent_and_skips_old(self):
        st = cs.update(cs.load_state("/nonexistent", T0), snap(
            T0,
            {"hex": "aaaaaa", "seen": 1, "lat": 33.5, "lon": 130.4, "seen_pos": 2},
            {"hex": "bbbbbb", "seen": 30},
            {"hex": "cccccc", "seen": 61},
        ))
        s = cs.summary(st, T0)
        self.assertEqual((s["total"], s["with_pos"]), (2, 1))
        self.assertEqual(s["started"], T0)

    def test_stale_position_is_not_counted(self):
        st = cs.update(cs.load_state("/nonexistent", T0), snap(
            T0, {"hex": "aaaaaa", "seen": 1, "lat": 33.5, "lon": 130.4, "seen_pos": 90}))
        self.assertEqual(cs.summary(st, T0)["with_pos"], 0)

    def test_same_aircraft_counted_once(self):
        st = cs.load_state("/nonexistent", T0)
        for i in range(5):
            cs.update(st, snap(T0 + 60 * i, {"hex": "aaaaaa", "seen": 0}))
        self.assertEqual(cs.summary(st, T0 + 240)["total"], 1)

    def test_prunes_after_24h(self):
        st = cs.load_state("/nonexistent", T0)
        cs.update(st, snap(T0, {"hex": "aaaaaa", "seen": 0, "lat": 1, "lon": 1, "seen_pos": 0}))
        cs.update(st, snap(T0 + cs.WINDOW_S - 1, {"hex": "bbbbbb", "seen": 0}))
        self.assertEqual(cs.summary(st, T0 + cs.WINDOW_S - 1)["total"], 2)
        cs.update(st, snap(T0 + cs.WINDOW_S + 1, {"hex": "bbbbbb", "seen": 0}))
        s = cs.summary(st, T0 + cs.WINDOW_S + 1)
        self.assertEqual((s["total"], s["with_pos"]), (1, 0))

    def test_position_seen_earlier_in_window_still_counts(self):
        st = cs.load_state("/nonexistent", T0)
        cs.update(st, snap(T0, {"hex": "aaaaaa", "seen": 0, "lat": 1, "lon": 1, "seen_pos": 0}))
        cs.update(st, snap(T0 + 3600, {"hex": "aaaaaa", "seen": 0}))
        self.assertEqual(cs.summary(st, T0 + 3600)["with_pos"], 1)

    def test_ignores_malformed_entries(self):
        st = cs.update(cs.load_state("/nonexistent", T0), snap(
            T0, {"seen": 0}, {"hex": "aaaaaa"}, {"hex": 5, "seen": 0}))
        self.assertEqual(cs.summary(st, T0)["total"], 0)


def gate_point(dt, along, cross, alt, track, rate):
    """Trace point at (along, cross) km in the runway frame (inverse of runway_frame)."""
    rw = cs.RUNWAYS[0]
    ux, uy = math.sin(math.radians(rw["true_track"])), math.cos(math.radians(rw["true_track"]))
    x, y = along * ux + cross * uy, along * uy - cross * ux
    lat = rw["lat"] + y / 111.2
    lon = rw["lon"] + x / (111.2 * math.cos(math.radians(rw["lat"])))
    return [dt, lat, lon, alt, 140.0, track, 0, rate, None, "adsb_icao", alt, rate]


def pass_trace(t0, along_from, along_to, alt_from, alt_to, track, rate, cross=0.0, n=20):
    pts = []
    for i in range(n):
        f = i / (n - 1)
        pts.append(gate_point(i * 5.0, along_from + (along_to - along_from) * f, cross,
                              round(alt_from + (alt_to - alt_from) * f), track, rate))
    return {"icao": "abcdef", "timestamp": t0, "trace": pts}


class Movements(unittest.TestCase):
    def test_frame_round_trip(self):
        p = gate_point(0, 3.0, -0.5, 0, 0, 0)
        along, cross = cs.runway_frame(p[1], p[2])
        self.assertAlmostEqual(along, 3.0, places=6)
        self.assertAlmostEqual(cross, -0.5, places=6)

    def test_arrival_runway_16(self):
        doc = pass_trace(T0, -5.5, 0.5, 950, 0, 157.0, -700)
        ev = cs.detect_movements(doc)
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0][1:3], ("arr", "16"))

    def test_departure_runway_34(self):
        ev = cs.detect_movements(pass_trace(T0, 0.5, -5.5, 0, 950, 337.0, 2500))
        self.assertEqual([e[1:3] for e in ev], [("dep", "34")])

    def test_crosswind_crab_within_tolerance(self):
        ev = cs.detect_movements(pass_trace(T0, -5, 0, 900, 0, 157.0 + 15, -600))
        self.assertEqual([e[1:3] for e in ev], [("arr", "16")])

    def test_not_movements(self):
        cases = {
            "overflight above the gate": pass_trace(T0, -5, 5, 3000, 3000, 157.0, -500),
            "crossing the runway": pass_trace(T0, -1, 1, 800, 800, 70.0, -500),
            "parallel but 2 km off the centreline": pass_trace(T0, -5, 0, 900, 0, 157.0, -700, cross=2.0),
            "level flight": pass_trace(T0, -5, 0, 800, 800, 157.0, 0),
            "beyond the gate": pass_trace(T0, -12, -7, 900, 600, 157.0, -700),
        }
        for label, doc in cases.items():
            with self.subTest(label):
                self.assertEqual(cs.detect_movements(doc), [])

    def test_departure_gate_is_higher_than_the_arrival_gate(self):
        # departures from 16 are often first heard well above 1000 ft
        self.assertEqual(cs.classify_point(gate_point(0, 2.5, 0, 1500, 150.0, 2500)), ("dep", "16"))
        self.assertIsNone(cs.classify_point(gate_point(0, 2.5, 0, 2100, 150.0, 2500)))
        self.assertIsNone(cs.classify_point(gate_point(0, -3, 0, 1100, 157.0, -700)))
        self.assertEqual(cs.classify_point(gate_point(0, -3, 0, 1000, 157.0, -700)), ("arr", "16"))
        # the correction applies to both
        self.assertIsNone(cs.classify_point(gate_point(0, 2.5, 0, 1600, 150.0, 2500), 450.0))

    def test_ground_altitude_and_missing_rate(self):
        self.assertIsNone(cs.classify_point(gate_point(0, 0, 0, "ground", 157.0, -700)))
        p = gate_point(0, -2, 0, 500, 157.0, None)
        p[11] = -800  # geom_rate fallback
        self.assertEqual(cs.classify_point(p), ("arr", "16"))
        self.assertIsNone(cs.classify_point([0, None, None]))

    def test_go_around_counts_both(self):
        doc = pass_trace(T0, -5, -1, 900, 200, 157.0, -700)
        append_pass(doc, pass_trace(T0 + 100, -1, 4, 200, 900, 157.0, 1500))
        self.assertEqual([e[1] for e in cs.detect_movements(doc)], ["arr", "dep"])

    def test_gusty_final_is_not_a_departure(self):
        # JFS91 2026-11-20 (290/15G25KT): steady descent to land on 34, the
        # baro rate flipping to +320..+832 fpm on single points
        rates = [-640, 320, -1152, 512, -896, 832, -1856, 512, -576, -960]
        pts = [gate_point(i * 4.0, 4.0 - i * 0.4, 0, 700 - i * 30, 337.0, r) for i, r in enumerate(rates)]
        doc = {"icao": "87cde5", "timestamp": T0, "trace": pts}
        self.assertEqual([e[1:3] for e in cs.detect_movements(doc)], [("arr", "34")])

    def test_departure_needs_a_real_climb(self):
        # level at 1500 ft with a climbing rate: not a departure
        self.assertEqual(cs.detect_movements(pass_trace(T0, 0.5, 5, 1500, 1500, 157.0, 800)), [])
        # a climb at the very end of the trace is confirmed by a later run
        doc = pass_trace(T0, 0.5, 2, 300, 350, 157.0, 1500, n=3)
        self.assertEqual(cs.detect_movements(doc), [])
        append_pass(doc, pass_trace(T0 + 15, 2.5, 4, 600, 900, 157.0, 1500, n=3))
        self.assertEqual([e[1:3] for e in cs.detect_movements(doc)], [("dep", "16")])
        self.assertEqual(cs.detect_movements(doc)[0][0], T0)

    def test_record_dedup_prune_and_summary(self):
        st = cs.load_state("/nonexistent", T0)
        cs.record_movements(st, "aaaaaa", [(T0, "arr", "16")], T0)
        # same event seen again a minute later through trace_recent
        cs.record_movements(st, "aaaaaa", [(T0 + 5, "arr", "16")], T0 + 60)
        cs.record_movements(st, "aaaaaa", [(T0 + 3600, "dep", "34")], T0 + 3600)
        cs.record_movements(st, "bbbbbb", [(T0 + 60, "arr", "16")], T0 + 3600)
        s = cs.summary(st, T0 + 3600)
        self.assertEqual((s["arrivals"], s["departures"], s["runway"], s["runway_t"]), (2, 1, "34", T0 + 3600))
        cs.record_movements(st, "", [], T0 + cs.WINDOW_S + 100)
        s = cs.summary(st, T0 + cs.WINDOW_S + 100)
        self.assertEqual((s["arrivals"], s["departures"]), (0, 1))

    def test_summary_without_movements(self):
        s = cs.summary(cs.load_state("/nonexistent", T0), T0)
        self.assertEqual((s["arrivals"], s["departures"], s["runway"], s["runway_t"]), (0, 0, None, None))

    def test_backfill_then_recent(self):
        with tempfile.TemporaryDirectory() as d:
            old = cs.TRACES_DIR
            cs.TRACES_DIR = d
            try:
                os.makedirs(os.path.join(d, "ef"))
                full = pass_trace(T0, -5, 0, 900, 0, 157.0, -700)
                with open(os.path.join(d, "ef", "trace_full_abcdef.json"), "wb") as f:
                    f.write(gzip.compress(json.dumps(full).encode()))
                recent = pass_trace(T0 + 7200, 0, -5, 0, 900, 337.0, 2000)
                with open(os.path.join(d, "ef", "trace_recent_abcdef.json"), "w") as f:
                    json.dump(recent, f)
                p = gate_point(0, -1, 0, 300, 337.0, 2000)
                snapshot = {"now": T0 + 7300, "aircraft": [
                    {"hex": "abcdef", "seen": 0, "lat": p[1], "lon": p[2], "alt_baro": 300},
                    {"hex": "123456", "seen": 0, "lat": 35.0, "lon": 139.0, "alt_baro": 1000},
                ]}
                self.assertEqual(cs.candidates(snapshot), ["abcdef"])
                st = cs.update_movements(cs.load_state("/nonexistent", T0), snapshot)
                s = cs.summary(st, T0 + 7300)
                self.assertEqual((s["arrivals"], s["departures"], s["runway"]), (1, 1, "34"))
                # second run must not backfill or double count
                cs.update_movements(st, snapshot)
                self.assertEqual(len(st["movements"]), 2)
            finally:
                cs.TRACES_DIR = old


class Candidates(unittest.TestCase):
    def test_fast_departure_is_read_after_leaving_the_box(self):
        # ANA260 2026-11-19: on the ground at one run, above max_alt_ft at the
        # next; the departure is only in trace_recent read after that.
        with tempfile.TemporaryDirectory() as d:
            old = cs.TRACES_DIR
            cs.TRACES_DIR = d
            try:
                os.makedirs(os.path.join(d, "7c"))
                st = cs.load_state("/nonexistent", T0)
                st["movements"] = []
                p = gate_point(0, -1, 0, 300, 337.0, 0)
                ground = {"now": T0, "aircraft": [{"hex": "86837c", "seen": 0, "lat": p[1], "lon": p[2],
                                                   "alt_baro": "ground"}]}
                cs.update_movements(st, ground)
                self.assertEqual(st["candidates"], {"86837c": T0})
                with open(os.path.join(d, "7c", "trace_recent_86837c.json"), "w") as f:
                    json.dump(pass_trace(T0 + 10, 0, -5, 300, 2000, 337.0, 3500), f)
                high = {"now": T0 + 60, "aircraft": [{"hex": "86837c", "seen": 0, "lat": p[1], "lon": p[2],
                                                      "alt_baro": 7000}]}
                self.assertEqual(cs.candidates(high), [])
                cs.update_movements(st, high)
                self.assertEqual([(m[1], m[2]) for m in st["movements"]], [("dep", "34")])
                # dropped CANDIDATE_KEEP_S after it was last a candidate
                cs.update_movements(st, dict(high, now=T0 + cs.CANDIDATE_KEEP_S + 1))
                self.assertEqual(st["candidates"], {})
            finally:
                cs.TRACES_DIR = old

    def test_departure_first_heard_in_the_climb(self):
        # 86803b 2026-11-19: first heard at 1275 ft, at 3150 ft 8.3 km out at
        # the next run; never near and low before
        p = gate_point(0, 8.3, 0, 3150, 157.0, 2400)
        snap = {"now": T0, "aircraft": [{"hex": "86803b", "seen": 0, "lat": p[1], "lon": p[2], "alt_baro": 3150}]}
        self.assertEqual(cs.candidates(snap), ["86803b"])

    def test_bad_candidates_in_state_are_reset(self):
        st = {"started": T0, "aircraft": {}, "movements": [], "candidates": ["x"]}
        cs.update_movements(st, {"now": T0, "aircraft": []})
        self.assertEqual(st["candidates"], {})


def append_pass(doc, later):
    """Append a pass that starts at later["timestamp"] to doc (dt is relative to doc)."""
    off = later["timestamp"] - doc["timestamp"]
    doc["trace"] += [[p[0] + off] + p[1:] for p in later["trace"]]
    return doc


def with_flight(doc, flight):
    doc["trace"][0][8] = {"flight": flight + "  "}
    return doc


class GoAround(unittest.TestCase):
    def go_around_doc(self, t0=T0):
        doc = pass_trace(t0, -5, -1, 900, 200, 157.0, -700)
        append_pass(doc, pass_trace(t0 + 100, -1, 4, 200, 900, 157.0, 1500))
        return with_flight(doc, "HGB308")

    def test_events_carry_the_callsign(self):
        ev = cs.detect_movements(self.go_around_doc())
        self.assertEqual([(e[1], e[3]) for e in ev], [("arr", "HGB308"), ("dep", "HGB308")])

    def test_events_carry_speed_and_category(self):
        doc = self.go_around_doc()
        doc["trace"][0][8]["category"] = "A3"
        ev = cs.detect_movements(doc)
        self.assertEqual([(e[4], e[5]) for e in ev], [(140, "A3"), (140, "A3")])
        st = cs.load_state("/nonexistent", T0)
        cs.record_movements(st, "abcdef", ev, T0 + 300)
        self.assertEqual(st["movements"][0][3:], ["abcdef", "HGB308", 140, "A3"])

    def test_slow_or_rotorcraft_arrivals_are_not_go_arounds(self):
        moves = [
            [T0, "arr", "16", "aaaaaa", "JA01", 55, None], [T0 + 90, "dep", "16", "aaaaaa", "JA01", 65, None],
            [T0, "arr", "16", "eeeeee", "AHX1", 75, None], [T0 + 90, "dep", "16", "eeeeee", "AHX1", 90, None],
            [T0, "arr", "16", "bbbbbb", "JA02", 120, "A7"], [T0 + 90, "dep", "16", "bbbbbb", "JA02", 120, "A7"],
            [T0, "arr", "16", "cccccc", "ANA1", 135, "A3"], [T0 + 90, "dep", "16", "cccccc", "ANA1", 150, None],
            [T0, "arr", "16", "dddddd", "JAL2", None, None], [T0 + 90, "dep", "16", "dddddd", "JAL2", None, None],
        ]
        got = cs.find_go_arounds(moves)
        self.assertEqual(sorted((g["hex"], g["gs"], g["category"]) for g in got),
                         [("cccccc", 135, "A3"), ("dddddd", None, None), ("eeeeee", 75, None)])

    def test_second_approach_after_go_around_counts(self):
        doc = self.go_around_doc()
        append_pass(doc, pass_trace(T0 + 700, -5, 0, 900, 0, 157.0, -700))
        self.assertEqual([e[1] for e in cs.detect_movements(doc)], ["arr", "dep", "arr"])
        # the same three seen again by a later run are not added twice
        st = cs.load_state("/nonexistent", T0)
        for _ in range(2):
            cs.record_movements(st, "abcdef", cs.detect_movements(doc), T0 + 900)
        self.assertEqual([m[1] for m in st["movements"]], ["arr", "dep", "arr"])

    def test_find_go_arounds(self):
        moves = [
            [T0, "arr", "34", "aaaaaa", "HGB308"], [T0 + 65, "dep", "34", "aaaaaa", "HGB308"],
            [T0, "arr", "16", "bbbbbb", None], [T0 + 2400, "dep", "16", "bbbbbb", None],   # turnaround
            [T0, "arr", "16", "cccccc", None], [T0 + 120, "dep", "34", "cccccc", None],    # other runway
            [T0 + 30, "dep", "16", "dddddd", None],                                       # departure only
            [T0, "arr", "16", "eeeeee"], [T0 + 200, "dep", "16", "eeeeee"],              # old 4-field rows
        ]
        got = cs.find_go_arounds(moves)
        self.assertEqual([(g["hex"], g["flight"], g["rwy"], g["t"]) for g in got],
                         [("aaaaaa", "HGB308", "34", T0 + 65), ("eeeeee", None, "16", T0 + 200)])

    def test_movement_lines(self):
        t = 1794556800.0                                  # 2026-11-13T08:00:00Z
        moves = [[t + 60, "dep", "16", "86d200", "ANA256", 150.4, "A3"],
                 [t, "arr", "16", "86d200", "ANA256", 140, "A3"],
                 [t + 30, "arr", "34", "abcdef", None, None, None],
                 [t + 90, "arr", "34", "123456"]]        # an older state: five or four fields
        known = {cs.movement_key(moves[1])}
        self.assertEqual(cs.movement_lines(moves, known), [
            "2026-11-13T08:00:30Z\tarr\t34\tabcdef\t-\t-\t-\n",
            "2026-11-13T08:01:00Z\tdep\t16\t86d200\tANA256\t150\tA3\n",
            "2026-11-13T08:01:30Z\tarr\t34\t123456\t-\t-\t-\n",
        ])
        self.assertEqual(cs.movement_lines(moves, {cs.movement_key(m) for m in moves}), [])

    def test_log_line(self):
        t = calendar.timegm((2026, 11, 14, 2, 25, 0))
        self.assertEqual(cs.log_line({"t": t, "hex": "86d200", "flight": "ANA256", "rwy": "16"}),
                         "2026-11-14T02:25:00Z\tANA256\t16\t86d200\n")
        self.assertEqual(cs.log_line({"t": t, "hex": "86d200", "flight": None, "rwy": "16"}),
                         "2026-11-14T02:25:00Z\t-\t16\t86d200\n")

    def test_logged_once_and_summarised(self):
        import io
        st = cs.load_state("/nonexistent", T0)
        cs.record_movements(st, "abcdef", cs.detect_movements(self.go_around_doc()), T0 + 300)
        log = io.StringIO()
        self.assertEqual(len(cs.update_go_arounds(st, T0 + 300, log)), 1)
        self.assertEqual(len(cs.update_go_arounds(st, T0 + 360, log)), 0)
        lines = log.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].endswith("\tHGB308\t16\tabcdef"))
        s = cs.summary(st, T0 + 360)
        self.assertEqual(s["go_arounds"], 1)
        self.assertEqual(s["last_go_around"]["flight"], "HGB308")
        self.assertEqual((s["arrivals"], s["departures"]), (1, 1))
        # gone after 24 hours
        later = T0 + cs.WINDOW_S + 400
        cs.record_movements(st, "", [], later)
        cs.update_go_arounds(st, later, log)
        self.assertEqual((cs.summary(st, later)["go_arounds"], cs.summary(st, later)["last_go_around"]), (0, None))

    def test_callsign_from_long_trace_when_missing(self):
        with tempfile.TemporaryDirectory() as d:
            old = cs.TRACES_DIR
            cs.TRACES_DIR = d
            try:
                os.makedirs(os.path.join(d, "ef"))
                with open(os.path.join(d, "ef", "trace_full_abcdef.json"), "w") as f:
                    json.dump(self.go_around_doc(), f)
                st = {"started": T0, "aircraft": {}, "movements": [
                    [T0 + 10, "arr", "16", "abcdef"], [T0 + 120, "dep", "16", "abcdef"]]}
                new = cs.update_go_arounds(st, T0 + 300)
                self.assertEqual([g["flight"] for g in new], ["HGB308"])
            finally:
                cs.TRACES_DIR = old


class Wind(unittest.TestCase):
    def test_parse_metar_wind(self):
        w = cs.parse_metar_wind("RJFF 130800Z 33018G28KT 290V360 9999 FEW030 18/10 Q1021\n")
        self.assertEqual((w["dir"], w["spd"], w["gust"], w["unit"]), (330, 18, 28, "kt"))
        self.assertTrue(w["raw"].startswith("RJFF 130800Z"))
        self.assertEqual(cs.parse_metar_wind("RJFF 130800Z VRB02KT CAVOK 18/10 Q1021")["dir"], None)
        self.assertEqual(cs.parse_metar_wind("RJFF 130800Z 00000KT CAVOK")["spd"], 0)
        self.assertIsNone(cs.parse_metar_wind("RJFF 130800Z 9999 FEW030"))
        self.assertIsNone(cs.parse_metar_wind(None))

    def test_wind_components(self):
        # 18 kt from 337 on runway 34: all headwind
        self.assertEqual(cs.wind_components({"dir": 337, "spd": 18}, "34"), {"head": 18, "cross": 0, "from": None})
        # same wind on 16: all tailwind
        self.assertEqual(cs.wind_components({"dir": 337, "spd": 18}, "16")["head"], -18)
        # 90 deg right of runway 34's heading
        self.assertEqual(cs.wind_components({"dir": 67, "spd": 10}, "34"), {"head": 0, "cross": 10, "from": "R"})
        self.assertEqual(cs.wind_components({"dir": 247, "spd": 10}, "34")["from"], "L")
        self.assertIsNone(cs.wind_components({"dir": None, "spd": 3}, "34"))
        self.assertIsNone(cs.wind_components(None, "16"))


class History(unittest.TestCase):
    def ga_state(self, extra_moves=()):
        moves = [[T0, "arr", "34", "789288", "HGB308"], [T0 + 65, "dep", "34", "789288", "HGB308"]]
        moves += [list(m) for m in extra_moves]
        st = {"started": T0, "aircraft": {}, "movements": moves}
        cs.update_go_arounds(st, T0 + 120)
        return st

    def doc(self, t_to=T0 + 3600):
        pts = [gate_point(dt, -5 + dt / 600, 0, 900, 337.0, -500) for dt in range(-600, int(t_to - T0), 5)]
        return {"icao": "789288", "timestamp": T0, "trace": pts}

    def test_new_entry_with_fresh_metar(self):
        st = self.ga_state()
        h = cs.update_history([], st, T0 + 120, "RJFF 130400Z 33018G28KT 9999", lambda hx: self.doc(T0 + 120))
        self.assertEqual(len(h), 1)
        e = h[0]
        self.assertEqual((e["id"], e["flight"], e["rwy"], e["climb_after_s"]), (f"789288-{int(T0 + 65)}", "HGB308", "34", 65))
        self.assertEqual(e["wind"]["gust"], 28)
        self.assertEqual(e["wind_rwy"]["head"], 18)
        self.assertEqual(e["outcome"], {"status": "pending"})
        self.assertIsNone(e["qnh"])
        self.assertFalse(e["done"])
        # track starts TRACK_BEFORE_S before the approach and is thinned
        self.assertEqual(e["track_from"], T0 - cs.TRACK_BEFORE_S)
        self.assertEqual(e["track"][0][0], 0)
        self.assertTrue(all(b[0] - a[0] >= cs.TRACK_STEP_S for a, b in zip(e["track"], e["track"][1:])))

    def test_speed_and_category_are_kept(self):
        moves = [[T0, "arr", "34", "789288", "HGB308", 132, "A3"], [T0 + 65, "dep", "34", "789288", "HGB308", 140, "A3"]]
        st = {"started": T0, "aircraft": {}, "movements": moves}
        cs.update_go_arounds(st, T0 + 120)
        h = cs.update_history([], st, T0 + 120, None, lambda hx: None)
        self.assertEqual((h[0]["gs"], h[0]["category"]), (132, "A3"))
        self.assertEqual(cs.public_history(h, T0 + 120)["go_arounds"][0]["gs"], 132)

    def test_qnh_used_by_the_gate_is_kept(self):
        h = cs.update_history([], self.ga_state(), T0 + 120, None, lambda hx: None, qnh=982.0)
        self.assertEqual(h[0]["qnh"], 982.0)
        self.assertEqual(cs.public_history(h, T0 + 120)["go_arounds"][0]["qnh"], 982.0)

    def test_done_is_public(self):
        # consumers (e.g. a follow-up post) wait for the settled outcome and track
        h = cs.update_history([], self.ga_state(), T0 + 120, None, lambda hx: None)
        self.assertIs(cs.public_history(h, T0 + 120)["go_arounds"][0]["done"], False)
        h[0]["done"] = True
        self.assertIs(cs.public_history(h, T0 + 120)["go_arounds"][0]["done"], True)

    def test_old_go_around_gets_no_metar(self):
        st = self.ga_state()
        h = cs.update_history([], st, T0 + 65 + cs.METAR_FRESH_S + 1, "RJFF 130400Z 33018KT", lambda hx: None)
        self.assertIsNone(h[0]["wind"])
        self.assertIsNone(h[0]["wind_rwy"])

    def test_landed_then_settled(self):
        st = self.ga_state([[T0 + 600, "arr", "34", "789288", "HGB308"]])
        h = cs.update_history([], st, T0 + 700, None, lambda hx: self.doc(T0 + 300))  # trace_full lags
        self.assertEqual(h[0]["outcome"], {"status": "landed", "t": T0 + 600, "rwy": "34"})
        self.assertFalse(h[0]["done"])
        n = len(h[0]["track"])
        cs.update_history(h, st, T0 + 600 + cs.TRACK_AFTER_S + cs.SETTLE_S, None, lambda hx: self.doc())
        self.assertTrue(h[0]["done"])
        self.assertGreater(len(h[0]["track"]), n)
        # settled entries are left alone
        frozen = json.dumps(h)
        cs.update_history(h, {"movements": []}, T0 + 99999, None, lambda hx: None)
        self.assertEqual(json.dumps(h), frozen)

    def test_lagging_trace_full_delays_settling(self):
        st = self.ga_state([[T0 + 600, "arr", "34", "789288", "HGB308"]])
        end = T0 + 600 + cs.TRACK_AFTER_S
        h = cs.update_history([], st, end + cs.SETTLE_S, None, lambda hx: self.doc(T0 + 300))
        self.assertFalse(h[0]["done"])                 # trace_full stops before the landing
        n = len(h[0]["track"])
        cs.update_history(h, st, end + cs.SETTLE_S + 300, None, lambda hx: self.doc())
        self.assertTrue(h[0]["done"])                  # rewritten: settled with the whole track
        self.assertTrue(h[0]["track_full"])
        self.assertGreater(len(h[0]["track"]), n)

    def test_trace_that_never_reaches_the_end_settles_late(self):
        st = self.ga_state([[T0 + 600, "arr", "34", "789288", "HGB308"]])
        end = T0 + 600 + cs.TRACK_AFTER_S
        h = cs.update_history([], st, end + cs.SETTLE_MAX_S - 1, None, lambda hx: self.doc(T0 + 300))
        self.assertFalse(h[0]["done"])
        cs.update_history(h, st, end + cs.SETTLE_MAX_S, None, lambda hx: self.doc(T0 + 300))
        self.assertTrue(h[0]["done"])
        self.assertFalse(h[0]["track_full"])

    def test_short_track_settled_by_the_old_rule_is_repaired_once(self):
        st = self.ga_state([[T0 + 600, "arr", "34", "789288", "HGB308"]])
        end = T0 + 600 + cs.TRACK_AFTER_S
        h = cs.update_history([], st, T0 + 700, None, lambda hx: self.doc(T0 + 300))
        h[0]["done"] = True                            # as settled before track_full existed
        n = len(h[0]["track"])
        cs.update_history(h, st, end + 3600, None, lambda hx: self.doc())
        self.assertGreater(len(h[0]["track"]), n)
        self.assertTrue(h[0]["track_full"])

        def no_read(hx):
            raise AssertionError("trace read again")
        cs.update_history(h, st, end + 7200, None, no_read)

    def test_old_short_track_is_left_when_the_trace_is_gone(self):
        st = self.ga_state([[T0 + 600, "arr", "34", "789288", "HGB308"]])
        h = cs.update_history([], st, T0 + 700, None, lambda hx: self.doc(T0 + 300))
        h[0]["done"] = True
        track = list(h[0]["track"])

        def no_read(hx):
            raise AssertionError("trace read after WINDOW_S")
        cs.update_history(h, st, T0 + 600 + cs.TRACK_AFTER_S + cs.WINDOW_S + 1, None, no_read)
        self.assertEqual((h[0]["track"], h[0]["track_full"]), (track, False))

    def test_second_go_around(self):
        st = self.ga_state([[T0 + 600, "arr", "34", "789288", "HGB308"], [T0 + 660, "dep", "34", "789288", "HGB308"]])
        cs.update_go_arounds(st, T0 + 700)
        h = cs.update_history([], st, T0 + 700, None, lambda hx: None)
        self.assertEqual([e["outcome"]["status"] for e in h], ["again", "pending"])

    def test_landing_that_turns_into_a_go_around_is_rejudged(self):
        st = self.ga_state([[T0 + 600, "arr", "34", "789288", "HGB308"]])
        h = cs.update_history([], st, T0 + 620, None, lambda hx: None)
        self.assertEqual(h[0]["outcome"]["status"], "landed")
        st["movements"].append([T0 + 660, "dep", "34", "789288", "HGB308"])
        cs.update_go_arounds(st, T0 + 700)
        cs.update_history(h, st, T0 + 700, None, lambda hx: None)
        self.assertEqual(h[0]["outcome"]["status"], "again")

    def test_not_landed_after_an_hour(self):
        st = self.ga_state()
        h = cs.update_history([], st, T0 + 65 + cs.OUTCOME_WAIT_S + 1, None, lambda hx: None)
        self.assertEqual(h[0]["outcome"], {"status": "not_landed"})

    def test_history_outlives_the_24h_state(self):
        st = self.ga_state()
        h = cs.update_history([], st, T0 + 120, None, lambda hx: None)
        cs.update_history(h, {"movements": [], "go_arounds": []}, T0 + 3 * 86400, None, lambda hx: None)
        self.assertEqual(len(h), 1)
        cs.update_history(h, st, T0 + 3 * 86400, None, lambda hx: None)
        self.assertEqual(len(h), 1)   # not added twice

    def test_public_history_newest_first(self):
        hist = [{"id": "a", "t": 1, "done": True, "track_full": True}, {"id": "b", "t": 2, "done": False}]
        out = cs.public_history(hist, 5)
        self.assertEqual([g["id"] for g in out["go_arounds"]], ["b", "a"])
        self.assertNotIn("track_full", out["go_arounds"][1])   # internal

    def test_second_go_around_of_the_same_airframe(self):
        hist = [{"id": "a", "hex": "aaaaaa", "t": T0}, {"id": "b", "hex": "bbbbbb", "t": T0 + 60},
                {"id": "c", "hex": "aaaaaa", "t": T0 + 1200}, {"id": "d", "hex": "aaaaaa", "t": T0 + 3000},
                {"id": "e", "hex": "aaaaaa", "t": T0 + 3000 + cs.SAME_AIRFRAME_S + 1}]
        self.assertEqual(cs.airframe_nth(hist), {"a": 1, "b": 1, "c": 2, "d": 3, "e": 1})
        out = cs.public_history(hist, T0)["go_arounds"]
        self.assertEqual([(g["id"], g["nth"]) for g in out], [("e", 1), ("d", 3), ("c", 2), ("b", 1), ("a", 1)])
        # the same airframe on another flight starts again at 1
        hist = [{"id": "a", "hex": "aaaaaa", "flight": "ANA1077", "t": T0},
                {"id": "b", "hex": "aaaaaa", "flight": "ANA1278", "t": T0 + 1800},
                {"id": "c", "hex": "aaaaaa", "flight": "ANA1278", "t": T0 + 2400}]
        self.assertEqual(cs.airframe_nth(hist), {"a": 1, "b": 1, "c": 2})

    def test_extract_track_ground_and_bounds(self):
        doc = {"timestamp": T0, "trace": [
            [0, cs.ARP_LAT, cs.ARP_LON, "ground"], [3, cs.ARP_LAT, cs.ARP_LON, 100],
            [20, cs.ARP_LAT, cs.ARP_LON, 200], [40, None, None, 300], [500, cs.ARP_LAT, cs.ARP_LON, 400]]}
        self.assertEqual(cs.extract_track(doc, T0, T0 + 100), [[0, 0.0, 0.0, 0], [20, 0.0, 0.0, 200]])
        self.assertEqual(cs.extract_track(None, T0, T0 + 1), [])


def write_db(d, name, doc, gz=True):
    raw = json.dumps(doc).encode()
    with open(os.path.join(d, name + ".js"), "wb") as f:
        f.write(gzip.compress(raw) if gz else raw)


class AircraftDb(unittest.TestCase):
    def db(self, d):
        # tar1090-db layout: "8" splits "87" off into a file of its own
        write_db(d, "8", {"children": ["87"], "681B7": ["JA73NR", "B738", "00", "BOEING 737-800"]})
        write_db(d, "87", {"CDE5": ["58-1219", "KC2", "10", "KAWASAKI C-2"], "0001": ["", None]}, gz=False)
        return d

    def test_lookup(self):
        with tempfile.TemporaryDirectory() as d:
            self.db(d)
            self.assertEqual(cs.aircraft_info("8681b7", d), {"reg": "JA73NR", "type": "B738"})
            self.assertEqual(cs.aircraft_info("87cde5", d), {"reg": "58-1219", "type": "KC2"})
            self.assertEqual(cs.aircraft_info("870001", d), {"reg": None, "type": None})
            self.assertEqual(cs.aircraft_info("8affff", d), {"reg": None, "type": None})
            self.assertIsNone(cs.aircraft_info("abcdef", d))   # no "A" file

    def test_added_once_and_kept_public(self):
        with tempfile.TemporaryDirectory() as d:
            hist = [{"id": "x", "hex": "8681b7", "t": T0}, {"id": "y", "hex": "abcdef", "t": T0 + 1}]
            cs.add_aircraft(hist, d)                      # empty directory: retried later
            self.assertNotIn("reg", hist[0])
            self.db(d)
            cs.add_aircraft(hist, d)
            self.assertEqual((hist[0]["reg"], hist[0]["type"]), ("JA73NR", "B738"))
            self.assertNotIn("reg", hist[1])
            hist[0]["reg"] = "kept"
            cs.add_aircraft(hist, d)                      # looked up once
            self.assertEqual(hist[0]["reg"], "kept")
            out = cs.public_history(hist, T0)["go_arounds"]
            self.assertEqual([(g["reg"], g["type"]) for g in out], [(None, None), ("kept", "B738")])

    def test_config_key(self):
        doc = tomllib.load(open(RJFF_TOML, "rb"))
        doc.setdefault("paths", {})["aircraft_db"] = "/usr/local/share/tar1090/git-db/db"
        self.assertEqual(cs.parse_config(doc)["paths"]["aircraft_db"], "/usr/local/share/tar1090/git-db/db")


def metar_at(t, rest):
    """METAR text observed at epoch t."""
    return time.strftime("RJFF %d%H%MZ ", time.gmtime(t)) + rest


class Qnh(unittest.TestCase):
    def test_parse_q_and_a(self):
        self.assertEqual(cs.parse_metar_qnh(metar_at(T0, "14013KT 9999 17/13 Q1016"), T0 + 600), 1016.0)
        self.assertEqual(cs.parse_metar_qnh(metar_at(T0, "14013KT 9999 17/13 A2992"), T0), 1013.2)
        self.assertEqual(cs.parse_metar_qnh(metar_at(T0, "14013KT 9999 Q0968 TEMPO 4000"), T0), 968.0)

    def test_unusable(self):
        cases = {
            "no metar": None,
            "no QNH group": metar_at(T0, "14013KT 9999 17/13"),
            "no observation time": "RJFF 14013KT 9999 Q1016",
            "too old": metar_at(T0, "14013KT Q1016"),
            "implausible": metar_at(T0, "14013KT Q0850"),
        }
        for label, text in cases.items():
            with self.subTest(label):
                now = T0 + cs.QNH_MAX_AGE_S + 1 if label == "too old" else T0
                self.assertIsNone(cs.parse_metar_qnh(text, now))

    def test_observation_time_across_a_month_end(self):
        # 31st 23:50Z read just after midnight on the 1st (2027-02-01)
        now = calendar.timegm((2027, 2, 1, 0, 10, 0))
        self.assertEqual(cs.metar_obs_time("RJFF 312350Z 00000KT", now), now - 20 * 60)

    def test_offset(self):
        self.assertEqual(cs.qnh_offset_ft(None), 0.0)
        self.assertAlmostEqual(cs.qnh_offset_ft(980.0), -897.75)
        self.assertAlmostEqual(cs.qnh_offset_ft(1030.0), 452.25)

    def test_deep_low_needs_the_correction(self):
        # 980 hPa: on short final at 300-500 ft the aircraft reports ~1200-1400 ft
        doc = pass_trace(T0, -5, 0, 1400, 1150, 157.0, -700)
        self.assertEqual(cs.detect_movements(doc), [])
        ev = cs.detect_movements(doc, cs.qnh_offset_ft(980.0))
        self.assertEqual([e[1:3] for e in ev], [("arr", "16")])

    def test_candidates_use_the_offset(self):
        # 500 ft above the limit by pressure altitude, below it after the correction (-891 ft)
        s = snap(T0, {"hex": "abcdef", "lat": cs.ARP_LAT, "lon": cs.ARP_LON, "alt_baro": cs.CANDIDATE_ALT_FT + 500})
        self.assertEqual(cs.candidates(s), [])
        self.assertEqual(cs.candidates(s, cs.qnh_offset_ft(980.0)), ["abcdef"])


class HistoryFile(unittest.TestCase):
    def test_missing_is_empty_corrupt_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "go_arounds.json")
            self.assertEqual(cs.load_history(p), [])
            for bad in ("{not json", '{"a": 1}'):
                with open(p, "w") as f:
                    f.write(bad)
                self.assertIsNone(cs.load_history(p))

    def run_main(self, d, history_text):
        paths = {
            "AIRCRAFT_JSON": os.path.join(d, "aircraft.json"),
            "STATE_PATH": os.path.join(d, "state.json"),
            "OUT_PATH": os.path.join(d, "summary.json"),
            "TRACES_DIR": os.path.join(d, "traces"),
            "GA_LOG": os.path.join(d, "ga.log"),
            "MOVES_LOG": os.path.join(d, "movements.log"),
            "GA_HISTORY": os.path.join(d, "go_arounds.json"),
            "GA_OUT": os.path.join(d, "go-arounds.json"),
            "METAR_PATH": os.path.join(d, "metar.txt"),
        }
        now = time.time()
        with open(paths["AIRCRAFT_JSON"], "w") as f:
            json.dump(snap(now, {"hex": "aaaaaa", "seen": 0}), f)
        with open(paths["GA_HISTORY"], "w") as f:
            f.write(history_text)
        keys = {"AIRCRAFT_JSON": "aircraft", "STATE_PATH": "state", "OUT_PATH": "summary", "TRACES_DIR": "traces",
                "GA_LOG": "log", "MOVES_LOG": "movements_log", "GA_HISTORY": "history", "GA_OUT": "go_arounds", "METAR_PATH": "metar"}
        with open(RJFF_TOML) as f:
            text = f.read().split("[paths]")[0]
        cfg = os.path.join(d, "test.toml")
        with open(cfg, "w") as f:
            f.write(text + "[paths]\n" + "".join(f'{keys[k]} = "{v}"\n' for k, v in paths.items()))
        err = sys.stderr
        try:
            sys.stderr = open(os.devnull, "w")
            rc = cs.main(["-c", cfg])
        finally:
            sys.stderr.close()
            sys.stderr = err
            cs.configure(RJFF)
        return rc, paths

    def test_main_keeps_a_corrupt_history(self):
        with tempfile.TemporaryDirectory() as d:
            rc, paths = self.run_main(d, "[{truncated")
            self.assertEqual(rc, 1)
            with open(paths["GA_HISTORY"]) as f:
                self.assertEqual(f.read(), "[{truncated")
            self.assertFalse(os.path.exists(paths["GA_OUT"]))
            with open(paths["OUT_PATH"]) as f:
                self.assertEqual(json.load(f)["total"], 1)   # the rest still runs

    def test_main_with_a_good_history(self):
        with tempfile.TemporaryDirectory() as d:
            rc, paths = self.run_main(d, "[]")
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(paths["MOVES_LOG"]))   # opened, nothing moved
            with open(paths["GA_OUT"]) as f:
                self.assertEqual(json.load(f)["go_arounds"], [])


class StateFile(unittest.TestCase):
    def test_corrupt_state_starts_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "state.json")
            with open(p, "w") as f:
                f.write("{not json")
            self.assertEqual(cs.load_state(p, T0), {"started": T0, "aircraft": {}})

    def test_round_trip_keeps_started(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sub", "state.json")
            st = cs.update(cs.load_state(p, T0), snap(T0, {"hex": "aaaaaa", "seen": 0}))
            cs.write_atomic(p, json.dumps(st), 0o600)
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
            again = cs.load_state(p, T0 + 600)
            self.assertEqual(again["started"], T0)
            self.assertIn("aaaaaa", again["aircraft"])
            self.assertEqual([f for f in os.listdir(os.path.dirname(p))], ["state.json"])


class Config(unittest.TestCase):
    BASE = {"airport": {"icao": "XXXX", "arp": [10.0, 20.0]}, "runway": [{"ends": ["09", "27"], "true_track": 90}]}

    def parse(self, **changes):
        doc = json.loads(json.dumps(self.BASE))
        doc.update(changes)
        return cs.parse_config(doc)

    def test_defaults_and_runway_centre(self):
        c = self.parse()
        self.assertEqual(c["gate"]["arr_alt_ft"], 1000)
        self.assertIsNone(c["paths"]["metar"])
        rw = c["runways"][0]
        self.assertEqual((rw["ends"], rw["true_track"], rw["lat"], rw["lon"], rw["gate_along_km"]),
                         (("09", "27"), 90.0, 10.0, 20.0, 6.0))

    def test_rjff_example(self):
        self.assertEqual(RJFF["airport"]["icao"], "RJFF")
        self.assertEqual([r["ends"] for r in RJFF["runways"]], [("16", "34")])
        self.assertEqual(cs.RUNWAY_HDG, {"16": 157.0, "34": 337.0})

    def test_errors(self):
        bad = [
            {"airport": {"arp": [1, 2]}},
            {"airport": {"icao": "X", "arp": [1]}},
            {"runway": []},
            {"runway": [{"ends": ["09"], "true_track": 90}]},
            {"runway": [{"ends": ["09", "27"]}]},
            {"runway": [{"ends": ["09", "27"], "true_track": 90}, {"ends": ["27", "09"], "true_track": 270}]},
            {"runway": [{"ends": ["09", "27"], "true_track": 90, "width": 45}]},
            {"gate": {"arr_alt": 900}},
            {"weather": {}},
        ]
        for b in bad:
            with self.subTest(b), self.assertRaises(cs.ConfigError):
                self.parse(**b)

    def test_load_config_reports_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.toml")
            with open(p, "w") as f:
                f.write("[airport\n")
            with self.assertRaises(cs.ConfigError) as e:
                cs.load_config(p)
            self.assertIn(p, str(e.exception))
            with self.assertRaises(cs.ConfigError):
                cs.load_config(os.path.join(d, "missing.toml"))


class TwoRunways(unittest.TestCase):
    """A crossing runway 09/27 1.5 km north of the ARP, next to RJFF's 16/34."""

    def setUp(self):
        doc = {"airport": {"icao": "TEST", "arp": list(RJFF["airport"]["arp"])},
               "runway": [{"ends": ["16", "34"], "true_track": 157},
                          {"ends": ["09", "27"], "true_track": 90,
                           "center": [RJFF["airport"]["arp"][0] + 1.5 / 111.2, RJFF["airport"]["arp"][1]]}]}
        cs.configure(cs.parse_config(doc))

    def tearDown(self):
        cs.configure(RJFF)

    def point(self, rw, along, cross, alt, track, rate):
        ux, uy = math.sin(math.radians(rw["true_track"])), math.cos(math.radians(rw["true_track"]))
        x, y = along * ux + cross * uy, along * uy - cross * ux
        lat = rw["lat"] + y / 111.2
        lon = rw["lon"] + x / (111.2 * math.cos(math.radians(rw["lat"])))
        return [0, lat, lon, alt, 140, track, 0, rate]

    def test_each_runway_has_its_own_gate_and_designators(self):
        r1, r2 = cs.RUNWAYS
        self.assertEqual(cs.classify_point(self.point(r2, 3, 0, 800, 90, -700)), ("arr", "09"))
        self.assertEqual(cs.classify_point(self.point(r2, -3, 0, 800, 270, 900)), ("dep", "27"))
        self.assertEqual(cs.classify_point(self.point(r1, 3, 0, 800, 337, 900)), ("dep", "34"))
        # on 09/27's centreline but flying 16's track: neither
        self.assertIsNone(cs.classify_point(self.point(r2, 4, 0, 800, 157, -700)))
        self.assertEqual(cs.RUNWAY_HDG["27"], 270.0)

    def test_track_and_metadata_use_the_go_around_runway(self):
        r2 = cs.RUNWAYS[1]
        doc = {"timestamp": T0, "trace": [self.point(r2, 2, 0.5, 600, 90, -700)]}
        (_, along, cross, _), = cs.extract_track(doc, T0 - 1, T0 + 1, "27")
        self.assertEqual((along, cross), (2.0, 0.5))
        info = cs.public_history([], T0)["airport"]
        self.assertEqual([r["ends"] for r in info["runways"]], [["16", "34"], ["09", "27"]])
        self.assertAlmostEqual(info["runways"][1]["center"][0], r2["lat"])


if __name__ == "__main__":
    unittest.main(verbosity=1)
