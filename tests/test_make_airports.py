#!/usr/bin/env python3
"""Unit tests for tools/make_airports.py (stdlib only, made-up airports)."""
import csv
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("make_airports", os.path.join(HERE, "..", "tools", "make_airports.py"))
ma = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ma)

AIRPORT_COLS = ["id", "ident", "type", "name", "latitude_deg", "longitude_deg", "elevation_ft",
                "iso_country", "scheduled_service"]
RUNWAY_COLS = ["airport_ident", "length_ft", "closed", "le_ident", "le_latitude_deg", "le_longitude_deg",
               "he_ident", "he_latitude_deg", "he_longitude_deg"]


def rwy(apt, le, he, lat1, lon1, lat2, lon2, length=9186, closed="0"):
    return dict(zip(RUNWAY_COLS, [apt, length, closed, le, lat1, lon1, he, lat2, lon2]))


# ZZAA: two parallels 210 m apart (one gate) and a crossing runway;
# ZZBB: two parallels 2 km apart (two gates), at 2000 ft.
AIRPORTS = [
    dict(zip(AIRPORT_COLS, [1, "ZZAA", "large_airport", "Test A", 10.0, 20.0, 30, "XX", "yes"])),
    dict(zip(AIRPORT_COLS, [2, "ZZBB", "medium_airport", "Test B", 11.0, 21.0, 2000, "XX", "yes"])),
    dict(zip(AIRPORT_COLS, [3, "ZZCC", "heliport", "Test C", 12.0, 22.0, 0, "XX", "yes"])),
    dict(zip(AIRPORT_COLS, [4, "ZZDD", "small_airport", "Test D", 13.0, 23.0, 0, "XX", "yes"])),
]
D = 0.21 / 111.2          # 210 m in degrees of latitude
RUNWAYS = [
    rwy("ZZAA", "18L", "36R", 10.02, 20.0, 9.98, 20.0),
    rwy("ZZAA", "18R", "36L", 10.02, 20.0 - D, 9.98, 20.0 - D, length=8202),
    rwy("ZZAA", "09", "27", 10.0, 19.98, 10.0, 20.02, length=6000),
    rwy("ZZAA", "H1", "", 10.0, 20.0, "", ""),
    rwy("ZZBB", "18L", "36R", 11.02, 21.0, 10.98, 21.0),
    rwy("ZZBB", "18R", "36L", 11.02, 21.02, 10.98, 21.02),
    rwy("ZZDD", "05", "23", "", "", "", ""),
    rwy("ZZDD", "01", "19", 13.0, 23.0, 13.01, 23.0, closed="1"),
]


class MakeAirports(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.ap = os.path.join(self.dir.name, "airports.csv")
        self.rw = os.path.join(self.dir.name, "runways.csv")
        for path, cols, rows in ((self.ap, AIRPORT_COLS, AIRPORTS), (self.rw, RUNWAY_COLS, RUNWAYS)):
            with open(path, "w", newline="") as f:
                w = csv.DictWriter(f, cols)
                w.writeheader()
                w.writerows(rows)

    def tearDown(self):
        self.dir.cleanup()

    def test_bearing(self):
        self.assertAlmostEqual(ma.bearing(10, 20, 9, 20), 180.0)
        self.assertAlmostEqual(ma.bearing(0, 0, 0, 1), 90.0)

    def test_close_parallels_share_one_gate(self):
        warnings = []
        out = ma.build(self.ap, self.rw, country="XX", warn=warnings.append)
        a = out["ZZAA"]
        self.assertEqual((a["arp"], a["elevation_ft"]), ([10.0, 20.0], 30))
        g, x = a["runways"]
        self.assertEqual((g["ends"], g["true_track"], g["members"]), (["18", "36"], 180.0, ["18L/36R", "18R/36L"]))
        self.assertEqual(g["center"], [10.0, round(20.0 - D / 2, 6)])
        self.assertEqual((g["gate_cross_km"], g["length_m"]), (1.1, 2800))
        self.assertEqual((x["ends"], x["true_track"], x["gate_cross_km"]), (["09", "27"], 90.0, 1.0))
        self.assertNotIn("members", x)
        # far parallels: two gates, designators kept
        self.assertEqual([r["ends"] for r in out["ZZBB"]["runways"]], [["18L", "36R"], ["18R", "36L"]])
        self.assertEqual(out["ZZBB"]["elevation_ft"], 2000)
        # heliport type and an airport without a usable runway are left out, with a reason
        self.assertEqual(sorted(out), ["ZZAA", "ZZBB"])
        self.assertTrue(any("ZZDD 05/23: no threshold positions" in w for w in warnings))
        self.assertTrue(any("ZZDD 01/19: closed" in w for w in warnings))
        self.assertTrue(any("ZZDD: no usable runway" in w for w in warnings))

    def test_group_distance_is_a_parameter(self):
        out = ma.build(self.ap, self.rw, {"ZZBB"}, group_km=3.0)
        self.assertEqual([r["ends"] for r in out["ZZBB"]["runways"]], [["18", "36"]])

    def test_unknown_ident_and_main(self):
        warnings = []
        ma.build(self.ap, self.rw, {"ZZZZ"}, warn=warnings.append)
        self.assertEqual(warnings, [f"ZZZZ: not in {self.ap}"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(ma.main([self.ap, self.rw, "ZZAA"]), 0)
        doc = json.loads(buf.getvalue())
        self.assertEqual(list(doc["airports"]), ["ZZAA"])
        self.assertIn("OurAirports", doc["source"])


if __name__ == "__main__":
    unittest.main()
