# goaround-watch

Counts an airport's arrivals and departures and detects probable go-arounds
from the per-aircraft traces of [readsb](https://github.com/wiedehopf/readsb)
(the decoder behind tar1090), and publishes them as JSON for a web view.

A go-around is an arrival through a runway gate followed by a departure of
the same aircraft on the same runway within a few minutes. Each one is kept
with the METAR wind, what happened next (landed / went around again / did not
land) and a thinned track in runway coordinates.

Status: extracted from a home receiver setup at Fukuoka (RJFF), where it has
run since November 2026.

## Requirements

- readsb writing traces: `--write-json-globe-index` (the files
  `traces/<xx>/trace_full_<hex>.json` and `trace_recent_<hex>.json`)
- Python 3.11 or later (standard library only)
- Optional: a raw METAR text file for the airport, for the QNH correction of
  the gate altitudes and the wind at each go-around

## Setup

```sh
sudo install -m 0755 goaround_watch.py /usr/local/bin/
sudo cp examples/rjff.toml /etc/goaround-watch.toml   # then edit it for your airport
# every minute, e.g. in /etc/crontab:
# * * * * * root /usr/local/bin/goaround_watch.py >> /var/log/goaround-watch.log 2>&1
```

The configuration describes the airport reference point, one `[[runway]]`
per runway (its two designators, the true track of the first one and the
gate around its extended centreline), the gate altitudes and the file paths.
See [`examples/rjff.toml`](examples/rjff.toml).

## Output

- `summary` (JSON): aircraft received in the last 24 h, arrivals, departures,
  the runway in use, go-arounds in the window.
- `go_arounds` (JSON): every go-around, newest first, with the airport and
  runway geometry for drawing the tracks.
- `history` / `state`: internal, keep them on persistent storage.

## Web view

`web/` is a dependency-free view of the go-arounds JSON: a list where each
entry opens to a north-up track figure (approach, go-around point, what
followed, the runway) with the wind and the outcome.

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

## Tests

```sh
python3 -m unittest discover -s tests
node tests/web_test.js
```

## License

MIT
