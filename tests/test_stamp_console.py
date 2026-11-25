#!/usr/bin/env python3
"""tools/stamp_console.py: the ?v= in console/index.html are up to date."""
import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
spec = importlib.util.spec_from_file_location("stamp_console", os.path.join(ROOT, "tools", "stamp_console.py"))
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)


class StampConsole(unittest.TestCase):
    def test_index_is_up_to_date(self):
        # fails after a change to console/ or web/: run tools/stamp_console.py
        with open(sc.PAGE, encoding="utf-8") as f:
            _, changed = sc.stamp(f.read())
        self.assertEqual(changed, [])

    def test_stamp(self):
        text = ('<script src="wind.js"></script><script src="/web/goaround.js?v=00000000"></script>'
                '<link href="https://example.net/x.css"><iframe src="about:blank"></iframe>')
        new, changed = sc.stamp(text)
        self.assertEqual(changed, ["wind.js", "/web/goaround.js"])
        self.assertIn(f'src="wind.js?v={sc.version(os.path.join(ROOT, "console", "wind.js"))}"', new)
        self.assertIn('href="https://example.net/x.css"', new)
        self.assertEqual(sc.stamp(new)[1], [])


if __name__ == "__main__":
    unittest.main()
