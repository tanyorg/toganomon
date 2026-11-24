# Tuning an airport's gates from recorded traffic

> [!WARNING]
> **Experimental.** This tool is new and has been tried on three airports
> only (RJFF with one receiver, RJBB and RJTT with adsb.lol data). Its options, its output and the way it recommends a gate
> may change without notice, also in a patch release. Check every
> recommendation by hand before it goes into `airports.json`.

`tune_airport.py` replays recorded readsb traces with toganomon's own
detection and recommends the airport's `airports.json` entry: the true
track and centre of each runway gate, and its width. It is a tool for
whoever adds or checks an airport; the Docker setup does not need it.
Python 3.11 or later, standard library only.

```sh
tools/tune/tune_airport.py RJTT data/2026.11.16 data/2026.11.17 data/2026.11.19 data/2026.11.20 \
    --airports draft.json --metar metar-archive.txt --source "adsb.lol, ODbL" --out rjtt.json
```

Give it as many days as you have: each directory is read on its own (the
memory needed is that of one day), the passes of all of them make one
recommendation, and each day gets a line of its own. You do not need to
pick the days: the report says when a runway had too little traffic
(`more data needed`) and which runway ends were never seen in use; add
days until those are gone (days with the other wind direction).

| argument | what |
|---|---|
| `ICAO` | the airport; its entry comes from `airports.json` (or `--airports draft.json`, e.g. from `tools/make_airports.py`) |
| `TRACES ...` | one or more directories with `trace_full_*.json` at any depth: readsb's `traces` directory (`--write-json-globe-index`, the last 24 hours), a day of globe history (`YYYY/MM/DD/traces/`, readsb `--write-globe-history`), or a day from `extract_traces.py` |
| `--metar` | METAR archive, `<ISO time>\t<METAR>` per line (`fetch_wx.py --archive` writes it), for the QNH correction of the gate heights |
| `--answer` | a toganomon movements log of the same receiver and time, to compare the replay with |
| `--out` | write the recommended entry as JSON, with `checked` saying when, from which days and how many passes |
| `--source` | where the traces come from, for `checked` (e.g. `adsb.lol, ODbL`) |
| `--min-passes` | passes a gate needs before its track and centre are changed (30) |

The window is the traces' span less the first two hours (aircraft already
in flight when the traces start) and the last ten minutes (`trace_full`
lags the live traffic).

## What it prints

```
RJTT: 4 day(s), 5795 traces, QNH from 1117 METARs
  16L/34R: 1305 passes below 500 ft, track 150.0 (+0.0), offset -0.00 km, spread 0.01 km -> true_track 150.0, gate_cross_km 1.0
  04/22: 702 passes below 500 ft, track 35.0 (+0.1), offset +0.00 km, spread 0.00 km -> true_track 35.0, gate_cross_km 1.0
  ...
  2026-11-16: 567 arr, 552 dep, 3 go-around(s), 10 low without a movement (arr/dep: 04 2/0, 05 0/307, 16L 0/2, 16R 0/1, 34L 361/4, 34R 204/238)
  2026-11-19: 571 arr, 545 dep, 3 go-around(s), 7 low without a movement (arr/dep: 04 4/2, 16L 0/245, 16R 0/294, 22 370/3, 23 197/1)
  ...
    go-around 2026-11-16T02:47:40Z SFJ78 850fb6 RWY34L
  ...
```

With one day only (here 2026-11-16, north wind) it would also say:

```
  more data needed: 04/22 had fewer than 30 passes: add days when these runways are in use (another wind direction)
  not seen in use: 22, 23
```

- **passes / track / offset**: the aircraft low and straight along the
  gate, their median true track and how far right (+) of the gate's centre
  line they fly. Measured on the passes below 500 ft and the take-off and
  landing rolls (the runway itself: higher up, a departure may already be
  turning); with a receiver that does not hear that low, on the passes
  below 1500 ft as `toganomon.py --check-runways` does.
- **spread**: 95% of the passes are this close to the median offset. A gate
  narrower than spread + 0.2 km is widened; a much wider one is only noted
  (a group of parallel runways needs its width). Beside a parallel runway
  (within 5 degrees) passes are only looked for up to half way to it, and
  the gate is never widened beyond that.
- **->**: the recommendation, from the gates with enough passes.
- **one line per day**: arrivals and departures per runway end (arr/dep)
  with the recommended entry, the go-arounds and the low aircraft without
  a movement. Arrivals and departures should roughly balance over a day.
- **low near the airport without a movement**: aircraft within 8 km of the
  reference point and 1500 ft of its elevation that made no arrival or
  departure: helicopters, light aircraft beside the airport, or a gate
  that misses real traffic (look at these hexes when the count is large).
- **more data needed / not seen in use**: what the days did not show. Add
  days until these are gone.
- **answer**: with `--answer`, the replay against what toganomon logged
  live (same kind, runway and hex within 2 minutes).

## Caveats

- One day may use one direction only: a runway end without traffic keeps
  its entry. Use days with both wind directions.
- Offset or curved approaches pull the measured centre towards the
  approach path; check the offset against the airport's charts before
  moving a centre by much.
- Gate heights depend on how low the receiver hears; the recommendation
  covers the track, centre and width only.
