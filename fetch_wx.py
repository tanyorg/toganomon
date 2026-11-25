#!/usr/bin/env python3
"""Fetch an airport's METAR or TAF from NOAA and save it as one line.

Usage: fetch_wx.py [--archive DIR] ICAO metar|taf OUTPUT

NOAA's station files (tgftp.nws.noaa.gov) are "YYYY/MM/DD HH:MM" on the
first line and the report (a TAF over several lines) after it. The report is
joined into one line with single spaces and written atomically to OUTPUT
(mode 0644), which is what toganomon.py reads as [paths] metar and what the
console shows. A failed fetch, or a body that is not a report for ICAO,
leaves the old file in place, logs to stderr and exits 1. Every success
logs one line to stdout.

With --archive, a report that differs from the last one archived is also
appended to DIR/<kind>-YYYY-MM.txt (UTC month of the fetch) as
"<UTC fetch time>\\t<report>", a long-term record that is never rotated.

Standard library only. Run it every 10 minutes for the METAR and every 30
minutes for the TAF; NOAA updates the files a few minutes after issue.
"""
import argparse
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request

URLS = {
    "metar": "https://tgftp.nws.noaa.gov/data/observations/metar/stations/{icao}.TXT",
    "taf": "https://tgftp.nws.noaa.gov/data/forecasts/taf/stations/{icao}.TXT",
}
USER_AGENT = "toganomon-fetch-wx/1.0"
TIMEOUT_S = 10
MAX_BYTES = 64 * 1024


def report_body(raw, icao):
    """NOAA station file text -> the report as one line, or None if it is not
    a report for icao."""
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    body = " ".join(" ".join(lines[1:]).split())
    # One report only: NOAA's file for one airport can carry the next one of
    # the same bulletin after the "=" that ends a report (RJFF.TXT on
    # 2026-11-22 and 25: "... 18010KT= TAF RJCC 250505Z ...").
    body = re.split(r"\s*=|\s(?=TAF\s)", body, maxsplit=1)[0].strip()
    # METAR: "ICAO ddhhmmZ ..."; TAF: "TAF [AMD|COR] ICAO ddhhmmZ ..."
    return body if body and icao in body.split()[:3] else None


def write_atomic(path, text, mode=0o644):
    # Temp file in the same directory + rename, so a web server never serves
    # a partial file. mkstemp creates 0600; the web server needs read access.
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), prefix=".tmp-")
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


def archive(directory, kind, body, now):
    """Append body to this month's archive unless it equals the last report
    there. Returns True if a line was added."""
    os.makedirs(directory, mode=0o755, exist_ok=True)
    path = os.path.join(directory, f"{kind}-{time.strftime('%Y-%m', time.gmtime(now))}.txt")
    last = None
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = line.rstrip("\n").split("\t", 1)[-1]
    except FileNotFoundError:
        pass
    if last == body:
        return False
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(now))}\t{body}\n")
    return True


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
        return res.read(MAX_BYTES).decode("utf-8", errors="replace")


def main(argv=None, fetcher=fetch, now=None):
    ap = argparse.ArgumentParser(description="Fetch a METAR or TAF from NOAA into one-line file.")
    ap.add_argument("--archive", metavar="DIR", help="also keep every new report in DIR/<kind>-YYYY-MM.txt")
    ap.add_argument("icao", help="ICAO location indicator, e.g. RJFF")
    ap.add_argument("kind", choices=sorted(URLS))
    ap.add_argument("output", help="file to write the report to")
    args = ap.parse_args(argv)
    icao = args.icao.upper()
    if not re.fullmatch(r"[A-Z0-9]{4}", icao):
        ap.error(f"not an ICAO location indicator: {args.icao}")
    label = args.kind.upper()
    stamp = time.strftime("%Y-%m-%d %X")
    try:
        raw = fetcher(URLS[args.kind].format(icao=icao))
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"[{stamp}] {icao} {label} fetch failed: {e}", file=sys.stderr)
        return 1
    body = report_body(raw, icao)
    if body is None:
        print(f"[{stamp}] {icao} {label} unexpected content, kept the old file: {raw[:200]!r}", file=sys.stderr)
        return 1
    try:
        write_atomic(args.output, body)
    except OSError as e:
        print(f"[{stamp}] {icao} {label} cannot write {args.output}: {e}", file=sys.stderr)
        return 1
    print(f"[{stamp}] {icao} {label} updated: {body}")
    if args.archive:
        try:
            archive(args.archive, args.kind, body, time.time() if now is None else now)
        except OSError as e:
            print(f"[{stamp}] {icao} {label} cannot archive to {args.archive}: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
