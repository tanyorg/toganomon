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
tools/tune/tune_airport.py RJFF /path/to/traces \
    --metar metar-archive.txt --answer movements.log --out rjff.json
```

| argument | what |
|---|---|
| `ICAO` | the airport; its entry comes from `airports.json` (or `--airports draft.json`, e.g. from `tools/make_airports.py`) |
| `TRACES` | a directory with `trace_full_*.json` at any depth: readsb's `traces` directory (`--write-json-globe-index`, the last 24 hours) or a day of globe history (`YYYY/MM/DD/traces/`, readsb `--write-globe-history`) |
| `--metar` | METAR archive, `<ISO time>\t<METAR>` per line (`fetch_wx.py --archive` writes it), for the QNH correction of the gate heights |
| `--answer` | a toganomon movements log of the same receiver and time, to compare the replay with |
| `--out` | write the recommended entry as JSON |
| `--min-passes` | passes a gate needs before its track and centre are changed (30) |

The window is the traces' span less the first two hours (aircraft already
in flight when the traces start) and the last ten minutes (`trace_full`
lags the live traffic).

## What it prints

```
RJFF: 917 traces, window 2026-11-23T03:09:14Z .. 2026-11-24T02:58:48Z, QNH from 190 METARs
  16/34: 215 passes below 500 ft, track 150.3 (+0.0), offset -0.12 km, spread 0.01 km -> true_track 150.3, gate_cross_km 1.1
  note: 16/34: traffic spreads 0.01 km; the 1.1 km gate could be narrower
  replay: 201 arrivals, 211 departures (arr/dep per end: 16 184/184, 34 17/27)
  go-arounds: 0
  low near the airport without a movement: 2 (71c525 8413bd)
  answer: 412 matched, 3 only in the log, 0 only in the replay
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
- **replay**: arrivals and departures per runway end with the recommended
  entry. Arrivals and departures should roughly balance over a day.
- **low near the airport without a movement**: aircraft within 8 km of the
  reference point and 1500 ft of its elevation that made no arrival or
  departure: helicopters, light aircraft beside the airport, or a gate
  that misses real traffic (look at these hexes when the count is large).
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
