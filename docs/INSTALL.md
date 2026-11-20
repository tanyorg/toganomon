# Installing and configuring toganomon

## 1. Requirements

| What | Requirement |
|---|---|
| OS | Linux (tested on Debian 12 and Ubuntu 24.04) |
| Python | 3.11 or later, standard library only (`tomllib` sets the lower bound) |
| ADS-B decoder | [readsb](https://github.com/wiedehopf/readsb) (wiedehopf's). The receiver (SDR) need not be on the same host (net-only, below) |
| Web server | Optional, only to view the results in a browser (static files, section 8) |
| METAR | Optional: a file holding the raw METAR text. How you fetch it is up to you (section 5) |
| Aircraft database | Optional: [tar1090-db](https://github.com/wiedehopf/tar1090-db), for registration and type (installed with tar1090) |

Check:

```sh
python3 --version          # 3.11 or later
```

## 2. Preparing readsb

toganomon only reads two kinds of files that readsb writes; it never
talks to readsb.

| File | readsb option | Used for |
|---|---|---|
| `<json dir>/aircraft.json` | `--write-json <json dir>` | aircraft currently received (read on every run) |
| `<json dir>/traces/<xx>/trace_full_<hex>.json`, `trace_recent_<hex>.json` | `--write-json-globe-index` | per-aircraft tracks (arrivals, departures, go-arounds) |

wiedehopf's install script (`readsb-install.sh`) needs `curl` and `wget`. A
minimal Debian 12 has no curl, and the script then stops while installing
tar1090. Install both first:

```sh
sudo apt-get install -y curl wget
```

The readsb systemd unit already passes `--write-json /run/readsb`. Only add
`--write-json-globe-index` to `JSON_OPTIONS` in `/etc/default/readsb`:

```sh
JSON_OPTIONS="... --write-json /run/readsb --write-json-globe-index"
```

### Receiver on another host (net-only)

A host without an SDR produces the same files from another readsb's or
dump1090's Beast output (30005/tcp). The install script writes settings for
an rtlsdr device, so without one readsb keeps failing to start. Edit
`/etc/default/readsb`, then `sudo systemctl restart readsb`:

```sh
RECEIVER_OPTIONS="--device-type none --net-connector=192.0.2.10,30005,beast_in"
DECODER_OPTIONS="--max-range 450 --write-json-every 1"
NET_OPTIONS="--net --net-ri-port 0 --net-ro-port 30002 --net-sbs-port 30003 --net-bi-port 30004,30104 --net-bo-port 30005"
JSON_OPTIONS="--json-location-accuracy 2 --range-outline-hours 24 --write-json-globe-index"
```

Check:

```sh
ls -l /run/readsb/aircraft.json                       # updated every second
ls /run/readsb/traces | head                          # directories 00 .. ff
find /run/readsb/traces -name 'trace_full_*' | head
```

Note: readsb rewrites `trace_full_*` only every few minutes, sometimes more
than 10 minutes apart. This is why a go-around track starts short and grows
later (see `done` in section 7).

## 3. Installation

```sh
sudo install -m 0755 toganomon.py /usr/local/bin/
sudo install -m 0644 examples/rjff.toml /etc/toganomon.toml    # edit it as in section 4
```

The user that runs it must be able to read readsb's files (readsb writes
them world-readable) and to write the outputs. It need not be root. A
dedicated user, for example:

```sh
sudo useradd --system --no-create-home --shell /usr/sbin/nologin toganomon
sudo install -d -o toganomon -g toganomon -m 0755 /var/lib/toganomon /var/www/html/toganomon /var/log/toganomon
```

An output directory is created on the first run when its parent is
writable (as root you need not create them). The state and the history are
written 0600, the public JSON files 0644.

## 4. Configuration (`/etc/toganomon.toml`)

This file is the only thing that differs between airports. An unknown
section or key is an error, so a misspelling does not go unnoticed. Omitted
keys take the defaults shown below.

### `[airport]` (required)

```toml
[airport]
icao = "RJFF"
arp = [33.585, 130.451]     # aerodrome reference point (lat, lon), e.g. from AIP AD 2.2
```

### `[[runway]]` (one or more, required)

One per runway, or per pair of closely spaced parallel runways.

```toml
[[runway]]
ends = ["16", "34"]         # the two designators
true_track = 157.0          # TRUE track when landing on the first of ends (not magnetic)
center = [33.585, 130.451]  # default: the ARP; with several runways, each runway's centre
gate_along_km = 6.0         # from the centre, each way along the runway
gate_cross_km = 1.0         # from the extended centreline, each side
length_m = 2800             # for display only
```

- `true_track`: take the true bearing from the AIP. The magnetic heading
  (designator × 10) is off by the variation.
- The gate is a box around the extended centreline. Points inside it,
  flying along the runway and below the `[gate]` altitudes, are examined.

### `[gate]` (defaults)

```toml
[gate]
arr_alt_ft = 1000           # descending below this -> arrival
dep_alt_ft = 2000           # climbing below this -> departure
track_tol_deg = 20.0        # tolerance against the runway direction
rate_fpm = 200              # vertical rate that counts as descending / climbing
```

Altitudes are ADS-B pressure altitudes, corrected with the QNH when a METAR
is available (section 5). Review these values at a high-elevation airport.

A departure point also needs a real climb: the altitude must rise 200 ft
within the next 30 s. A single noisy vertical rate on a gusty final then
cannot fake a departure, and with it a go-around. These two values are
constants in the script, not configuration.

### `[go_around]` (defaults)

```toml
[go_around]
window_s = 600              # a departure on the same runway within this many seconds of an arrival -> go-around
min_gs_kt = 60              # slower arrivals are ignored
exclude_categories = ["A7"] # emitter categories to ignore (A7 = rotorcraft)
```

### `[candidates]` (defaults)

```toml
[candidates]
radius_km = 15.0            # only read the tracks of aircraft within this distance of the airport
max_alt_ft = 6000           # ... and at or below this altitude (and for 5 minutes after)
```

Keep `max_alt_ft` well above the departure gate altitude. A departure first
received while climbing can be some 3000 ft higher at the next run, one
minute later.

### `[paths]` (defaults)

```toml
[paths]
aircraft = "/run/readsb/aircraft.json"
traces = "/run/readsb/traces"
state = "/var/lib/toganomon/state.json"           # internal; if lost, the last 24 hours are rebuilt
history = "/var/lib/toganomon/go_arounds.json"    # every go-around, kept forever; do not delete
summary = "/var/www/html/toganomon/summary.json"
go_arounds = "/var/www/html/toganomon/go-arounds.json"
# metar = "/path/to/metar.txt"                          # section 5
# log = "/var/log/toganomon/events.log"            # one line per go-around
# movements_log = "/var/log/toganomon/movements.log" # one line per arrival / departure
# aircraft_db = "/usr/local/share/tar1090/git-db/db"     # tar1090-db: registration and type
```

Keep `state` and `history` on persistent storage (not `/run` or a tmpfs).

- `movements_log`: one tab-separated line per arrival or departure (UTC
  time, `arr`/`dep`, runway, hex, callsign, ground speed in kt, emitter
  category). It is the long-term record of traffic, e.g. for go-arounds per
  100 arrivals. It is only ever appended to; after the state is lost the
  rebuilt 24 hours are appended again, so dedupe by time, kind and hex.
- `aircraft_db`: the `db` directory of tar1090-db (gzipped JSON files named
  by hex prefix). tar1090's installer puts it at
  `/usr/local/share/tar1090/git-db/db` and keeps it up to date. Each
  go-around gets `reg` and `type` looked up once; while the directory
  cannot be read the lookup is retried on later runs. Without it they are
  null.

### Checking the configuration

```sh
sudo -u <user> /usr/local/bin/toganomon.py -c /etc/toganomon.toml; echo $?
```

| Exit code | Meaning |
|---|---|
| 0 | OK |
| 1 | `aircraft.json` unreadable or stale (not updated for 120 s or more), or the history cannot be read |
| 2 | configuration error (the message says where) |

## 5. METAR (optional)

Point `[paths] metar` at a text file holding one raw METAR. It is used for:

- QNH correction: the gate altitudes are corrected with the QNH, so that an
  approach in a deep low does not pass above the gate. A METAR more than
  3 hours old, or a QNH outside 900–1100 hPa, is not used.
- Wind: the wind at each go-around, with its headwind and crosswind
  components for that runway.

Example file (the METAR itself):

```
RJFF 190730Z 21008KT 170V250 9999 -SHRA FEW020 BKN035 10/07 Q1015
```

The source and the way to fetch it are up to you; keeping up with the
half-hourly reports is enough. Replace the file atomically (write a
temporary file, then rename it) so that a half-written file is never read.

## 6. Running it periodically

Run it once a minute. With cron and the dedicated user from section 3:

```
# /etc/cron.d/toganomon
* * * * * toganomon /usr/local/bin/toganomon.py -c /etc/toganomon.toml >> /var/log/toganomon/toganomon.log 2>&1
```

A systemd timer or anything else that runs it every minute works too.
Errors go to stderr, one line each; keep them in a log and rotate it your
own way.

## 7. First run and checks

- On the first run (no state) the arrivals and departures of the last 24
  hours are rebuilt from `trace_full_*`. If readsb does not hold 24 hours
  of traces, fewer are found.
- After a few minutes look at the summary:

  ```sh
  python3 -m json.tool /var/www/html/toganomon/summary.json
  ```

  If `arrivals` and `departures` grow and `runway` shows the runway in use,
  detection works. If they stay 0 for hours, review `true_track`, `center`,
  the gate size and the gate altitudes.
- Go-arounds are listed in `go-arounds.json`, newest first. The `outcome`
  of each (landed / went around again / did not land within 60 minutes) and
  its `track` are updated until the entry is settled (`done: true`). That
  is usually 20–30 minutes later, or 1–2 hours when it did not land.

## 8. Web view (optional)

Detection and the records need no web server; it is only for viewing the
list and the track figures in a browser. The view loads `go-arounds.json`
with `fetch()`, so opening `index.html` as a `file://` URL does not work: it
must be served over HTTP. Any static file server will do:

- With tar1090 installed, lighttpd is already running with
  `/var/www/html` as its document root; put the files below it.
- Just to try it: `cd /var/www/html/toganomon && python3 -m http.server 8080`

Put the three files of `web/` next to `go-arounds.json`:

```sh
sudo install -m 0644 web/index.html web/goaround.js web/goaround.css /var/www/html/toganomon/
```

Open `http://<host>/toganomon/`. `?tz=Asia/Tokyo` sets the display time
zone (default: the browser's), `?url=<json>` points to a JSON elsewhere.

To embed the view in your own page, see "Web view" in the README.

## 9. Upgrading and removing

- Upgrade: replace `toganomon.py`. The state and the history carry
  over.
- Remove: the cron line, `/usr/local/bin/toganomon.py`,
  `/etc/toganomon.toml` and the files in `[paths]`.

## 10. Known limitations

See "Known limitations" in the README.

## 11. Troubleshooting

| Symptom | Look at |
|---|---|
| `cannot read .../aircraft.json` | readsb's `--write-json` directory, `[paths] aircraft`, permissions |
| `... is stale, skipped` | readsb stopped, or nothing is received |
| `configuration: ...` | an error in the configuration file (the message says where) |
| arrivals and departures stay 0 | `--write-json-globe-index` missing, the `[paths] traces` location, `true_track` and the gate settings |
| `cannot read ...go_arounds.json; history not updated until it is fixed` | the history file is corrupt. It is not updated until fixed; the last 24 hours remain in the state and are added once it is fixed |
