# toganomon

> **toganomon** — named after TOGA (Take-Off/Go-Around), the thrust setting
> a pilot selects to go around, plus *-nomon* for monitor.

Counts an airport's arrivals and departures and detects probable go-arounds
from the per-aircraft traces of [readsb](https://github.com/wiedehopf/readsb)
(the decoder behind tar1090), and publishes them as JSON for a web view.

A go-around is an arrival through a runway gate followed by a departure of
the same aircraft on the same runway within a few minutes. A departure needs
a real climb (200 ft within 30 s), not just a positive vertical rate, so a
gusty final does not fake one. Each one is kept
with the METAR wind, what happened next (landed / went around again / did not
land) and a thinned track in runway coordinates.

Status: extracted from a home receiver setup at Fukuoka (RJFF), where it has
run since November 2026.

Part of the same family as [gnomon](https://github.com/tanyorg/gnomon)
(GNSS/NMEA and PPS monitors) and [aisnomon](https://github.com/tanyorg/aisnomon)
(AIS reception monitor).

## Requirements

- readsb writing traces: `--write-json-globe-index` (the files
  `traces/<xx>/trace_full_<hex>.json` and `trace_recent_<hex>.json`)
- Python 3.11 or later (standard library only)
- Optional: a raw METAR text file for the airport, for the QNH correction of
  the gate altitudes and the wind at each go-around

## Setup

```sh
sudo install -m 0755 toganomon.py /usr/local/bin/
sudo install -D -m 0644 airports.json /usr/local/share/toganomon/airports.json
sudo cp examples/rjff.toml /etc/toganomon.toml   # then edit it for your airport
# every minute, e.g. in /etc/crontab:
# * * * * * root /usr/local/bin/toganomon.py >> /var/log/toganomon.log 2>&1
```

For an airport in [`airports.json`](airports.json) the configuration needs
only its ICAO code: the reference point, the elevation and the runway gates
come from there. For any other airport it describes the reference point,
the elevation and one `[[runway]]` per runway or group of close parallel
runways (its two designators, the true track of the first one and the gate
around its extended centreline). Either way it also holds the gate
altitudes (above the airport) and the file paths. See
[`examples/rjff.toml`](examples/rjff.toml).

### Airport data

`airports.json` holds only Fukuoka (RJFF) for now. More airports are
welcome as pull requests:

1. Draft the entry from [OurAirports](https://ourairports.com/data/)
   (public domain) with `tools/make_airports.py airports.csv runways.csv
   <ICAO>`. It takes the true track from the threshold positions and puts
   parallel runways less than 1 km apart into one gate.
2. Check it against real traffic: run `toganomon.py --check-runways` on a
   receiver that sees the airport's approaches. It measures the passes
   through each gate (low, straight, along the runway) and prints the
   measured true track and centreline offset with OK or CHECK.
3. Put the result of the check in the entry's `checked`.

[`docs/INSTALL.md`](docs/INSTALL.md) has the full walkthrough: preparing
readsb (also on a host without an SDR), every configuration key, METAR,
the aircraft database, a dedicated user, the web view and troubleshooting.

## Output

- `summary` (JSON): aircraft received in the last 24 h, arrivals, departures,
  the runway in use, go-arounds in the window.
- `go_arounds` (JSON): every go-around, newest first, with the airport and
  runway geometry for drawing the tracks.
  `done` turns true once the entry is settled: its outcome no longer changes
  (an arrival that looked like a landing can still become another go-around)
  and the track is complete. `nth` is 2 (3, ...) when the same airframe
  with the same callsign went around within the previous 2 hours. `reg` and
  `type` (ICAO type designator) come from `aircraft_db`, a tar1090-db
  directory (files of hex prefixes, as installed with tar1090 under
  `git-db/db`); without it, or for an aircraft not in it, they are null.
- `log` (optional, text): one tab-separated line per go-around: UTC time,
  callsign, runway, hex.
- `movements_log` (optional, text): one tab-separated line per arrival or
  departure: UTC time, `arr`/`dep`, runway, hex, callsign, ground speed (kt),
  emitter category (`-` when unknown). The long-term record of traffic, e.g.
  for go-arounds per 100 arrivals. It is never rewritten; after the state is
  lost the rebuilt 24 hours are appended again, so dedupe by time, kind and hex.
- `history` / `state`: internal, keep them on persistent storage.

## Web view

`web/` is a dependency-free view of the go-arounds JSON: a list where each
entry opens to a north-up track figure (approach, go-around point, what
followed, the runway) with the wind and the outcome. Where nothing was
received for more than 60 s the line is dashed: the aircraft did not fly
that straight line.

- Standalone: serve `web/` next to the go-arounds JSON and open
  `index.html` (`?url=<json>` to point elsewhere, `?tz=Asia/Tokyo` for the
  time zone; default: the browser's).
- Embedded: load `goaround.css` and `goaround.js`, then

  ```js
  const view = GoAroundView.mount(document.getElementById('list'), {
      url: 'go-arounds.json', timeZone: 'UTC',
      // flightLink(g) -> {href, label} | null, extraLinks(g, element),
      // storage: {load(), save(obj)}, pollMs
  });
  ```

## Known limitations

- Only an aircraft that came down low through the gate and then climbed is
  counted, to avoid false detections. An approach abandoned before or above
  the gate is no arrival, so it is no go-around either (it counts as one
  departure). Example: TWB207 on 2026-11-19 began its climb 8 km short of
  RWY16 at 1175 ft and never entered the gate (6 km along, 1000 ft and
  below).
- What is not received cannot be judged. Where the ground and low altitudes
  are not received, a departure may first be seen already climbing (which
  is why the candidate altitude is well above the departure gate).
- A go-around is "probable": it is inferred from ADS-B alone, without ATC
  audio or any official source.

## Tests

```sh
python3 -m unittest discover -s tests
node tests/web_test.js
```

## License

MIT
