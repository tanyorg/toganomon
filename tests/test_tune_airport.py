#!/usr/bin/env python3
"""Tests for tools/tune/tune_airport.py on synthetic RJFF traces.

Run: python3 -m unittest discover -s tests
"""
import gzip
import importlib.util
import io
import json
import math
import os
import tempfile
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tt = load("tune_airport", os.path.join(HERE, "..", "tools", "tune", "tune_airport.py"))
tm = load("test_toganomon_helpers", os.path.join(HERE, "test_toganomon.py"))   # gate_point, pass_trace
T0 = tm.T0


def arrival(hx, t0, cross=0.0, skew=0.0):
    """An arrival on 16 (track 150.3, along 5 -> -1 km) cross km right of the centreline, skew deg off it."""
    doc = tm.pass_trace(t0, 5, -1, 900, 100, tm.H16, -700, cross=cross)
    for p in doc["trace"]:
        along, c = tm.cs.runway_frame(p[1], p[2])
        q = tm.gate_point(p[0], along, c + along * math.tan(math.radians(skew)), p[3], p[5], p[7])
        p[1], p[2] = q[1], q[2]
    doc["icao"] = hx
    return doc


def departure(hx, t0, cross=0.0):
    """A departure from 16 (along -1 -> 5 km, climbing)."""
    doc = tm.pass_trace(t0, -1, 5, 100, 900, tm.H16, 900, cross=cross)
    doc["icao"] = hx
    return doc


def write_traces(d, docs):
    for doc in docs:
        sub = os.path.join(d, "traces", doc["icao"][-2:])
        os.makedirs(sub, exist_ok=True)
        with open(os.path.join(sub, f"trace_full_{doc['icao']}.json"), "wb") as f:
            f.write(gzip.compress(json.dumps(doc).encode()))


def day(n=40, cross=0.0, skew=0.0, start=T0):
    """n arrivals and n departures 10 minutes apart, plus traces spanning the window."""
    docs = []
    for i in range(n):
        docs.append(arrival(f"a{i:05x}", start + 7200 + i * 600, cross, skew))
        docs.append(departure(f"d{i:05x}", start + 7500 + i * 600, cross))
    # a far aircraft at the start and the end, so the window covers all of them
    for hx, t in (("e00000", start), ("e00001", start + 7200 + n * 600 + 1200)):
        docs.append({"icao": hx, "timestamp": t, "trace": [[0, 34.5, 131.5, 30000, 450, 90, 0, 0, None]]})
    return docs


class Tune(unittest.TestCase):
    def run_tune(self, docs, **kw):
        with tempfile.TemporaryDirectory() as d:
            write_traces(d, docs)
            return tt.tune("RJFF", d, **kw)

    def test_aligned_traffic_keeps_the_entry(self):
        new, lines, res = self.run_tune(day())
        r, = res["results"]
        self.assertEqual((r["passes"], r["track"], r["offset_km"]), (80, 150.3, 0.0))
        self.assertEqual(new["runways"][0]["true_track"], 150.3)
        self.assertEqual(res["counts"], {"16": {"arr": 40, "dep": 40}})
        self.assertEqual(res["go_arounds"], [])
        self.assertIn("80 passes below 500 ft, track 150.3 (+0.0)", lines[1])

    def test_offset_traffic_moves_the_centre(self):
        new, _, res = self.run_tune(day(cross=0.4))
        self.assertEqual(res["results"][0]["offset_km"], 0.4)
        # measured again against the recommended entry: on the centreline
        tg = tt.load_toganomon()
        tt.configure(tg, "RJFF", new["runways"] and new)
        with tempfile.TemporaryDirectory() as d:
            write_traces(d, day(cross=0.4))
            again, = tt.measure(tg, tt.load_docs(tg, tt.trace_files(d)))
        self.assertAlmostEqual(again["offset_km"], 0.0, delta=0.02)

    def test_few_passes_keep_the_entry(self):
        new, lines, _ = self.run_tune(day(n=5, cross=0.4))
        self.assertEqual(new["runways"][0]["center"], tt.load_toganomon().load_airports()["RJFF"]["runways"][0]["center"])
        self.assertTrue(any("kept as it is" in ln for ln in lines))

    def test_wide_traffic_widens_the_gate(self):
        docs = day()
        for i, doc in enumerate(docs[:80:2]):            # arrivals spread 0 .. 1.2 km to the left
            docs[2 * i] = arrival(doc["icao"], doc["timestamp"], cross=-1.2 * (i % 2))
        new, lines, _ = self.run_tune(docs)
        self.assertGreater(new["runways"][0]["gate_cross_km"], 1.1)
        self.assertTrue(any("gate widened" in ln for ln in lines))

    def test_go_around_and_low_aircraft(self):
        docs = day(n=31)
        ga = arrival("9a0001", T0 + 10000)
        later = departure("9a0001", T0 + 10200)
        ga["trace"] += [[p[0] + 200] + p[1:] for p in later["trace"]]
        low = {"icao": "10f001", "timestamp": T0 + 20000,
               "trace": [tm.gate_point(0, 0.0, -5.0, 700, 320, 0)]}   # beside the airport, not along a runway
        _, lines, res = self.run_tune(docs + [ga, low])
        self.assertEqual([g["hex"] for g in res["go_arounds"]], ["9a0001"])
        self.assertEqual(res["low"], ["10f001"])
        self.assertIn("1 go-around(s)", "\n".join(lines))
        self.assertIn("go-around ", "\n".join(lines))

    def test_several_directories(self):
        # two days, each in its own directory: passes add up, one line per day, one recommendation
        with tempfile.TemporaryDirectory() as d1, tempfile.TemporaryDirectory() as d2:
            write_traces(d1, day(n=20))
            write_traces(d2, day(n=20, start=T0 + 86400))
            new, lines, res = tt.tune("RJFF", [d1, d2], source="test data")
        self.assertEqual(res["results"][0]["passes"], 80)
        self.assertEqual(len(res["days"]), 2)
        self.assertEqual(res["counts"], {"16": {"arr": 40, "dep": 40}})
        text = "\n".join(lines)
        self.assertIn("2 day(s)", text)
        self.assertIn("not seen in use: 34", text)
        self.assertNotIn("more data needed", text)
        self.assertIn("on 2 days", new["checked"])
        self.assertIn("(test data)", new["checked"])

    def test_too_few_passes_says_what_to_add(self):
        _, lines, _ = self.run_tune(day(n=5))
        self.assertIn("more data needed: 16/34", "\n".join(lines))

    def test_answer_and_out(self):
        docs = day()
        with tempfile.TemporaryDirectory() as d:
            write_traces(d, docs)
            arr0 = docs[0]
            t_arr = None
            # the replay's own first arrival, plus one the replay does not have
            tg = tt.load_toganomon()
            tt.configure(tg, "RJFF", tg.load_airports()["RJFF"])
            moves = tt.replay(tg, tt.load_docs(tg, tt.trace_files(d)), [])
            t_arr = next(m[0] for m in moves if m[3] == arr0["icao"])
            log = os.path.join(d, "movements.log")
            with open(log, "w") as f:
                f.write(f"{tt.iso(round(t_arr))}\tarr\t16\t{arr0['icao']}\tX\t140\tA3\n")
                f.write(f"{tt.iso(round(t_arr) + 30)}\tarr\t16\tffffff\tY\t140\tA3\n")
                f.write("not a line\n")
            out = os.path.join(d, "entry.json")
            with redirect_stdout(io.StringIO()) as buf:
                self.assertEqual(tt.main(["rjff", d, "--answer", log, "--out", out]), 0)
            self.assertIn("answer: 1 matched, 1 only in the log, 79 only in the replay", buf.getvalue())
            with open(out) as f:
                self.assertEqual(list(json.load(f)), ["RJFF"])

    def test_qnh_series(self):
        tg = tt.load_toganomon()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "metar.txt")
            with open(p, "w") as f:
                f.write("2026-11-20T04:20:02Z\tRJFF 200400Z 30013KT 9999 12/04 Q1016 NOSIG\n")
                f.write("2026-11-20T05:10:03Z\tRJFF 200500Z 29015KT 9999 11/04 Q1012 NOSIG\n")
                f.write("garbage\n")
            s = tt.qnh_series(tg, p)
        self.assertEqual([q for _, q in s], [1016, 1012])
        t = tt.parse_iso("2026-11-20T05:00:00Z")
        self.assertEqual(tt.qnh_at(s, t), 1016)
        self.assertIsNone(tt.qnh_at(s, t - 86400))

    def test_turning_departures_do_not_bend_the_track(self):
        # departures that turn 25 degrees once above 600 ft: measured on the low part
        docs = day()
        for i in range(1, 80, 2):
            doc = docs[i]
            for p in doc["trace"]:
                along, c = tm.cs.runway_frame(p[1], p[2])
                if p[3] > 600:
                    q = tm.gate_point(p[0], along, c + (along - 2.0) * math.tan(math.radians(25)), p[3], p[5], p[7])
                    p[1], p[2] = q[1], q[2]
        _, _, res = self.run_tune(docs)
        r, = res["results"]
        self.assertEqual((r["below_ft"], r["track"]), (500, 150.3))

    def test_receiver_that_does_not_hear_low_falls_back(self):
        docs = [d if not d["icao"].startswith(("a", "d")) else
                dict(d, trace=[p for p in d["trace"] if p[3] > 520]) for d in day()]
        _, lines, res = self.run_tune(docs)
        self.assertEqual(res["results"][0]["below_ft"], 1500)
        self.assertIn("below 1500 ft", lines[1])

    def test_ground_roll_is_a_pass(self):
        tg = tt.load_toganomon()
        tt.configure(tg, "RJFF", tg.load_airports()["RJFF"])
        roll = [[float(i), *tm.gate_point(0, -1.0 + 0.15 * i, 0.0, 0, tm.H16, 0)[1:3], "ground", 100.0 + i, tm.H16]
                for i in range(20)]
        taxi = [[float(i), *tm.gate_point(0, -1.0 + 0.15 * i, 0.0, 0, tm.H16, 0)[1:3], "ground", 20.0, tm.H16]
                for i in range(20)]
        self.assertEqual(len(tg.runway_passes({"timestamp": T0, "trace": roll}, tg.RUNWAYS[0], 500)), 1)
        self.assertEqual(tg.runway_passes({"timestamp": T0, "trace": taxi}, tg.RUNWAYS[0], 500), [])

    def test_parallel_runways_keep_their_own_traffic(self):
        # RJFF's gate split into two parallel runways 1.7 km apart, traffic on both
        tg = tt.load_toganomon()
        base = tg.load_airports()["RJFF"]
        rw = dict(base["runways"][0], gate_cross_km=1.0)
        entry = dict(base, runways=[dict(rw, ends=["16L", "34R"], center=tt.shifted(rw["center"], rw["true_track"], 0.85)),
                                    dict(rw, ends=["16R", "34L"], center=tt.shifted(rw["center"], rw["true_track"], -0.85))])
        tt.configure(tg, "RJFF", tg.load_airports()["RJFF"])        # the test traces are built on the one gate
        docs = [arrival(f"a{i:05x}", T0 + 600 * i, cross=0.85 if i % 2 else -0.85) for i in range(60)]
        with tempfile.TemporaryDirectory() as d:
            write_traces(d, docs)
            tt.configure(tg, "RJFF", entry)
            res = tt.measure(tg, tt.load_docs(tg, tt.trace_files(d)))
        self.assertEqual([r["passes"] for r in res], [30, 30])
        self.assertEqual([r["offset_km"] for r in res], [0.0, 0.0])
        self.assertEqual([round(r["parallel_km"], 2) for r in res], [1.7, 1.7])
        new, notes = tt.recommend(entry, [dict(r, spread_km=1.7) for r in res])
        self.assertEqual([g["gate_cross_km"] for g in new["runways"]], [1.0, 1.0])     # not widened into the other
        self.assertTrue(all("parallel runway" in n for n in notes))

    def test_trim_keeps_the_points_near_the_airport(self):
        tg = tt.load_toganomon()
        tt.configure(tg, "RJFF", tg.load_airports()["RJFF"])
        near = tm.gate_point(10, 2.0, 0.0, 800, 150, -700)
        trace = [[0, 35.5, 132.0, 30000, 450, 90, 0, 0, {"flight": "ABC123  ", "category": "A3"}],   # far, details
                 near, [20, 33.6, 130.45, 9000, 300, 90, 0, 0, None], [30, 33.59, 130.45, "ground"]]
        kept = tt.trim(tg, trace)
        self.assertEqual([p[0] for p in kept], [10, 30])
        self.assertEqual(kept[0][8], {"flight": "ABC123  ", "category": "A3"})   # carried over
        self.assertEqual(len(kept[1]), 4)

    def test_shifted_is_runway_frame_cross(self):
        tg = tt.load_toganomon()
        tt.configure(tg, "RJFF", tg.load_airports()["RJFF"])
        rw = tg.RUNWAYS[0]
        lat, lon = tt.shifted([rw["lat"], rw["lon"]], rw["true_track"], 0.5)
        along, cross = tg.runway_frame(lat, lon)
        self.assertAlmostEqual(along, 0.0, places=3)
        self.assertAlmostEqual(cross, 0.5, places=3)


if __name__ == "__main__":
    unittest.main()
