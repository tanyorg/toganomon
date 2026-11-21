#!/bin/sh
# toganomon container loop: toganomon.py once a minute, the METAR every
# 10 minutes and the TAF every 30 minutes (fetch_wx.py).
#
# Configuration: mount your own file at /etc/toganomon.toml, or set
# TOGANOMON_ICAO (an airport in airports.json) and a minimal one is written
# to /var/lib/toganomon/toganomon.toml with the container paths below.
#
# Environment:
#   TOGANOMON_ICAO   airport, e.g. RJFF (needed without /etc/toganomon.toml)
#   AIRCRAFT_DB      tar1090-db directory (default: ultrafeeder's copy)
#   FETCH_WX         0 = do not fetch METAR/TAF (default 1)
set -u

STATE=/var/lib/toganomon
OUT=/srv/toganomon
AIRCRAFT_DB=${AIRCRAFT_DB:-/var/globe_history/tar1090-update/git-db/db}
FETCH_WX=${FETCH_WX:-1}

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

if [ -f /etc/toganomon.toml ]; then
    CONFIG=/etc/toganomon.toml
    ICAO=$(sed -n 's/^[[:space:]]*icao[[:space:]]*=[[:space:]]*"\([A-Za-z0-9]*\)".*/\1/p' "$CONFIG" | head -n 1)
else
    ICAO=${TOGANOMON_ICAO:-}
    if [ -z "$ICAO" ]; then
        log "set TOGANOMON_ICAO or mount /etc/toganomon.toml" >&2
        exit 2
    fi
    CONFIG=$STATE/toganomon.toml
    cat > "$CONFIG" <<EOF
# Written by entrypoint.sh at container start; mount /etc/toganomon.toml to
# use your own instead.
[airport]
icao = "$ICAO"

[paths]
aircraft = "/run/readsb/aircraft.json"
traces = "/run/readsb/traces"
state = "$STATE/state.json"
history = "$STATE/go_arounds.json"
summary = "$OUT/summary.json"
go_arounds = "$OUT/go-arounds.json"
metar = "$OUT/metar.txt"
log = "$STATE/events.log"
movements_log = "$STATE/movements.log"
aircraft_db = "$AIRCRAFT_DB"
EOF
fi
log "toganomon: airport ${ICAO:-?}, config $CONFIG"

wx() {
    [ "$FETCH_WX" = 1 ] && [ -n "$ICAO" ] || return 0
    python3 /app/fetch_wx.py --archive "$STATE/wx" "$ICAO" "$1" "$OUT/$1.txt"
}

n=0
while :; do
    [ $((n % 10)) -eq 0 ] && wx metar
    [ $((n % 30)) -eq 0 ] && wx taf
    python3 /app/toganomon.py -c "$CONFIG" || log "toganomon.py exited $?" >&2
    n=$((n + 1))
    sleep 60
done
