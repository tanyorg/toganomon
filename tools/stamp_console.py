#!/usr/bin/env python3
"""Set ?v= on the console's own scripts and stylesheets in console/index.html.

Each local reference (src="x.js", href="x.css", and /web/... for the
go-around view parts) gets ?v=<the first 8 hex digits of the file's
sha256>, so a browser loads a part again exactly when it changed and keeps
it otherwise. Run it after changing console/ or web/ files; --check only
reports (exit 1) a reference that is not up to date, as the tests do.

    tools/stamp_console.py [--check]
"""
import argparse
import hashlib
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
PAGE = os.path.join(ROOT, "console", "index.html")
REF = re.compile(r'(?P<attr>src|href)="(?P<path>[^":?#]+\.(?:js|css))(?:\?v=[0-9a-f]*)?"')


def local_file(path):
    """The file a reference in console/index.html names, or None."""
    if path.startswith("/web/"):
        return os.path.join(ROOT, "web", path[len("/web/"):])
    if path.startswith("/") or "//" in path:
        return None
    return os.path.join(ROOT, "console", path)


def version(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:8]


def stamp(text):
    """-> (stamped text, [references that changed])."""
    changed = []

    def sub(m):
        f = local_file(m.group("path"))
        if f is None or not os.path.isfile(f):
            return m.group(0)
        new = f'{m.group("attr")}="{m.group("path")}?v={version(f)}"'
        if new != m.group(0):
            changed.append(m.group("path"))
        return new

    return REF.sub(sub, text), changed


def main(argv=None):
    ap = argparse.ArgumentParser(description="Set ?v= on the console's scripts and stylesheets.")
    ap.add_argument("--check", action="store_true", help="only report references that are not up to date")
    args = ap.parse_args(argv)
    with open(PAGE, encoding="utf-8") as f:
        text = f.read()
    new, changed = stamp(text)
    if args.check:
        for p in changed:
            print(f"console/index.html: {p} needs a new ?v= (run tools/stamp_console.py)")
        return 1 if changed else 0
    if changed:
        with open(PAGE, "w", encoding="utf-8") as f:
            f.write(new)
    print(f"console/index.html: {len(changed)} reference(s) updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
