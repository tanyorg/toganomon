#!/usr/bin/env python3
"""Tests for tools/tune/extract_traces.py. Run: python3 -m unittest discover -s tests"""
import gzip
import importlib.util
import io
import json
import os
import tarfile
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("extract_traces", os.path.join(HERE, "..", "tools", "tune", "extract_traces.py"))
ex = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ex)

KIX = ("RJBB", 34.4347, 135.2440)
FUK = ("RJFF", 33.5859, 130.4510)


def tar_of(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, raw in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
    buf.seek(0)
    return buf


def trace(hx, points):
    return gzip.compress(json.dumps({"icao": hx, "timestamp": 1.0, "trace": points}).encode())


class Extract(unittest.TestCase):
    def test_near(self):
        self.assertEqual(ex.near({"trace": [[0, 34.45, 135.25, 500]]}, [KIX, FUK]), ["RJBB"])
        self.assertEqual(ex.near({"trace": [[0, 34.45, 135.25, "ground"], [1, 33.6, 130.45, 3000]]}, [KIX, FUK]),
                         ["RJBB", "RJFF"])
        self.assertEqual(ex.near({"trace": [[0, 34.45, 135.25, 35000]]}, [KIX]), [])      # overflight
        self.assertEqual(ex.near({"trace": [[0, 35.5, 135.25, 500]]}, [KIX]), [])         # > 60 km
        self.assertEqual(ex.near({"trace": "bad"}, [KIX]), [])

    def test_extract_keeps_bytes_and_counts(self):
        near = trace("0000aa", [[0, 34.45, 135.25, 500]])
        far = trace("0000bb", [[0, 10.0, 10.0, 500]])
        stream = tar_of({"./traces/aa/trace_full_0000aa.json": near, "./traces/bb/trace_full_0000bb.json": far,
                         "./traces/cc/trace_full_0000cc.json": b"\x1f\x8bnot gzip", "./heatmap/x.bin.ttf": b"x"})
        with tempfile.TemporaryDirectory() as d:
            stats = ex.extract(stream, d, [KIX, FUK])
            self.assertEqual((stats["read"], stats["kept"], stats["bad"]), (3, 1, 1))
            self.assertEqual(stats["per_point"], {"RJBB": 1, "RJFF": 0})
            with open(os.path.join(d, "traces", "aa", "trace_full_0000aa.json"), "rb") as f:
                self.assertEqual(f.read(), near)

    def test_parse_near(self):
        self.assertEqual(ex.parse_near("RJBB=34.4347,135.2440"), KIX)
        with self.assertRaises(Exception):
            ex.parse_near("RJBB")


if __name__ == "__main__":
    unittest.main()
