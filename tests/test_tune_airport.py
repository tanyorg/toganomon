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
        self.assertIn("80 passes, track 150.3 (+0.0)", lines[1])

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
        self.assertIn("go-arounds: 1", "\n".join(lines))

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
