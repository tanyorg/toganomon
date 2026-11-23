# toganomon with docker: details

Start, configuration and updating are in the [README](../README.md#quick-start-docker).
This page has what you need less often.

| file | what |
|---|---|
| `compose.yaml` | the three services; the project name is fixed to `toganomon` |
| `.env.example` | copy to `.env` (not committed) |
| `Dockerfile`, `entrypoint.sh` | the worker: writes a minimal configuration from `TOGANOMON_ICAO` unless `/etc/toganomon.toml` is mounted, then runs `toganomon.py` every minute and `fetch_wx.py` for the METAR and TAF |
| `nginx.conf` | one origin for the console (`../console`), the go-around view parts (`../web`), the worker's output and ultrafeeder's tar1090 |

The worker takes `FETCH_WX=0` to skip NOAA (then there is no wind and no
QNH correction of the gate altitudes) and `AIRCRAFT_DB` for another
tar1090-db directory.

## Starting with an existing history

To fill the go-around list from another toganomon installation, copy its
history (`[paths] history`, a JSON list) into the volume. The `go_arounds`
list of a published `go-arounds.json` works too, put back in time order
(the history is oldest first, the published list newest first):

```sh
python3 -c 'import json,sys; json.dump(sorted(json.load(open(sys.argv[1]))["go_arounds"], key=lambda g: g["t"]), open("history.json","w"))' go-arounds.json
docker compose cp history.json toganomon:/var/lib/toganomon/go_arounds.json
docker compose exec -u root toganomon chown toganomon:toganomon /var/lib/toganomon/go_arounds.json
```

The entries appear at the next run (within a minute).

## Data

| volume | holds |
|---|---|
| `toganomon-state` | state, the go-around history (keep it), movements and event logs, METAR/TAF archive (`wx/`) |
| `toganomon-web` | what the console reads: `summary.json`, `go-arounds.json`, `metar.txt`, `taf.txt` |
| `globe-history` | ultrafeeder's state, heatmap and tar1090-db |
| `readsb-run` | readsb's live JSON and traces (tmpfs, lost on restart) |

Remove everything, history included: `docker compose down -v`.
