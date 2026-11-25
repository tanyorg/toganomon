#!/usr/bin/env python3
"""Unit tests for fetch_wx.py (stdlib only, no network)."""
import importlib.util
import io
import os
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("fetch_wx", os.path.join(HERE, "..", "fetch_wx.py"))
fw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fw)

METAR = "2026/11/21 04:30\nZZZZ 210430Z 22006KT 9999 FEW010 09/04 Q1023\n"
TAF = "2026/11/21 05:00\nTAF ZZZZ 210500Z 2106/2212 27006KT 9999\n      FEW020 TEMPO 2106/2109 4000 SHRA\n"
NOW = 1795237200.0  # 2026-11-21 05:00 UTC


class FetchWx(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.dir.name, "metar.txt")

    def tearDown(self):
        self.dir.cleanup()

    def run_main(self, argv, body=None, error=None):
        urls = []

        def fetcher(url):
            urls.append(url)
            if error:
                raise error
            return body

        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = fw.main(argv, fetcher=fetcher, now=NOW)
        return rc, urls, out.getvalue(), err.getvalue()

    def test_report_body(self):
        self.assertEqual(fw.report_body(METAR, "ZZZZ"), "ZZZZ 210430Z 22006KT 9999 FEW010 09/04 Q1023")
        self.assertEqual(fw.report_body(TAF, "ZZZZ"),
                         "TAF ZZZZ 210500Z 2106/2212 27006KT 9999 FEW020 TEMPO 2106/2109 4000 SHRA")
        self.assertIsNone(fw.report_body(METAR, "ZZYY"))
        self.assertIsNone(fw.report_body("2026/11/21 04:30\n", "ZZZZ"))
        self.assertIsNone(fw.report_body("<html>Not Found</html>", "ZZZZ"))

    def test_one_report_only(self):
        # NOAA's RJFF.TXT on 2026-11-25: RJCC's TAF after RJFF's "="
        raw = ("2026/11/25 06:00\nTAF RJFF 250505Z 2506/2612 27006KT CAVOK \n"
               "      BECMG 2506/2508 18010KT= TAF RJCC 250505Z 2506/2612 18006KT 9999 FEW020 BKN040 \n"
               "      BECMG 2600/2603 27014KT \n      BECMG 2606/2609 20008KT\n")
        self.assertEqual(fw.report_body(raw, "RJFF"), "TAF RJFF 250505Z 2506/2612 27006KT CAVOK BECMG 2506/2508 18010KT")
        # the next report without an "=" before it
        raw = "2026/11/25 06:00\nTAF RJFF 250505Z 2506/2612 27006KT CAVOK TAF RJCC 250505Z 2506/2612 18006KT\n"
        self.assertEqual(fw.report_body(raw, "RJFF"), "TAF RJFF 250505Z 2506/2612 27006KT CAVOK")
        # an amended TAF keeps its AMD; a METAR ending in "=" loses only that
        self.assertEqual(fw.report_body("x\nTAF AMD RJFF 251030Z 2511/2612 18010KT=\n", "RJFF"),
                         "TAF AMD RJFF 251030Z 2511/2612 18010KT")
        self.assertEqual(fw.report_body("x\nRJFF 251030Z 18010KT 9999 Q1020=\n", "RJFF"),
                         "RJFF 251030Z 18010KT 9999 Q1020")

    def test_writes_and_archives(self):
        arc = os.path.join(self.dir.name, "wx")
        rc, urls, out, _ = self.run_main(["--archive", arc, "zzzz", "metar", self.out], METAR)
        self.assertEqual(rc, 0)
        self.assertEqual(urls, ["https://tgftp.nws.noaa.gov/data/observations/metar/stations/ZZZZ.TXT"])
        with open(self.out) as f:
            self.assertEqual(f.read(), "ZZZZ 210430Z 22006KT 9999 FEW010 09/04 Q1023")
        self.assertEqual(os.stat(self.out).st_mode & 0o777, 0o644)
        self.assertIn("ZZZZ METAR updated", out)
        # the same report again is not archived twice
        self.run_main(["--archive", arc, "ZZZZ", "metar", self.out], METAR)
        with open(os.path.join(arc, "metar-2026-11.txt")) as f:
            self.assertEqual(f.read().splitlines(), ["2026-11-21T05:00:00Z\tZZZZ 210430Z 22006KT 9999 FEW010 09/04 Q1023"])

    def test_failures_keep_the_old_file(self):
        with open(self.out, "w") as f:
            f.write("old")
        rc, _, _, err = self.run_main(["ZZZZ", "metar", self.out], error=urllib.error.URLError("down"))
        self.assertEqual((rc, "fetch failed" in err), (1, True))
        rc, _, _, err = self.run_main(["ZZZZ", "metar", self.out], "<html>oops</html>")
        self.assertEqual((rc, "unexpected content" in err), (1, True))
        with open(self.out) as f:
            self.assertEqual(f.read(), "old")
        self.assertEqual(os.listdir(self.dir.name), ["metar.txt"])  # no temp file left

    def test_bad_icao(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            fw.main(["RJ/F", "metar", self.out], fetcher=lambda u: METAR)


if __name__ == "__main__":
    unittest.main()
