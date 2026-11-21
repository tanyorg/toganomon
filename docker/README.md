# toganomon with docker

Three containers behind one web port:

| service | image | does |
|---|---|---|
| `ultrafeeder` | [sdr-enthusiasts/docker-adsb-ultrafeeder](https://github.com/sdr-enthusiasts/docker-adsb-ultrafeeder) | readsb + tar1090; takes Beast from a receiver and writes `/run/readsb` (shared, tmpfs) |
| `toganomon` | built from this repository | `toganomon.py` every minute, `fetch_wx.py` for the METAR (10 min) and TAF (30 min) |
| `web` | nginx | the console at `/`, the JSON at `/toganomon/`, tar1090 at `/tar1090/` |

## Start

```sh
cd docker
cp .env.example .env
vi .env                       # airport, receiver position, Beast source, port
docker compose up -d --build
```

Open `http://<host>:<WEB_PORT>/`. The 24-hour counts start from zero:
ultrafeeder only has the traces since it started.

The airport must be in `airports.json`; for any other airport, write a
configuration as in [INSTALL.md](../docs/INSTALL.md) and mount it at
`/etc/toganomon.toml` (see the commented line in `compose.yaml`).

## Starting with an existing history

To fill the go-around list from another toganomon installation, copy its
history (`[paths] history`, a JSON list) into the volume. The `go_arounds`
list of a published `go-arounds.json` works too:

```sh
python3 -c 'import json,sys; json.dump(json.load(open(sys.argv[1]))["go_arounds"], open("history.json","w"))' go-arounds.json
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
