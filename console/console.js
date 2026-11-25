// toganomon console: header (METAR / TAF / clocks), tar1090 map centred on
// the airport, LAST 24H (Stats), WIND (Wind), GO-AROUNDS (GoAroundView) and
// MOVEMENTS (Movements: a row shows that flight's trace on the map).
// Data, all from the same origin (docker/nginx.conf):
//   /toganomon/summary.json, /toganomon/go-arounds.json,
//   /toganomon/movements.json                             (toganomon.py)
//   /toganomon/metar.txt, /toganomon/taf.txt              (fetch_wx.py)
//   /tar1090/                                             (ultrafeeder)
// Query: ?tz=<IANA zone> for the second clock and the list (default: browser);
//        ?chime=0: no chime for a new go-around.
// The header shows how old the METAR is (amber after 90 min, red after 180)
// and marks an expired TAF (Wx, wx.js).
// Pure helpers are in Console (module.exports for node tests).
(function (root) {
    'use strict';

    const DATA = '/toganomon/';
    const SUMMARY_MS = 30000;
    const WX_MS = 300000;
    const MAP_ZOOM = 10;

    function mapUrl(airport, zoom) {
        const p = new URLSearchParams({ zoom: String(zoom || MAP_ZOOM) });
        if (airport && Array.isArray(airport.arp)) {
            p.set('lat', airport.arp[0].toFixed(4));
            p.set('lon', airport.arp[1].toFixed(4));
        }
        return '/tar1090/?' + p.toString();
    }

    // tar1090 showing one flight (Movements.mapView params), on the live
    // map's view of the airport (without a zoom tar1090 opens a trace at
    // zoom 5, a whole region; with lat/lon it does not follow the aircraft)
    function traceUrl(params, airport, zoom) {
        const p = new URLSearchParams(params);
        p.set('zoom', String(zoom || MAP_ZOOM));
        if (airport && Array.isArray(airport.arp)) {
            p.set('lat', airport.arp[0].toFixed(4));
            p.set('lon', airport.arp[1].toFixed(4));
        }
        return '/tar1090/?' + p.toString();
    }

    const Console = { mapUrl, traceUrl };
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = Console;
        return;
    }
    root.Console = Console;

    // ---------- page ----------
    const $ = id => document.getElementById(id);
    const q = new URLSearchParams(location.search);
    const timeZone = q.get('tz') || Clock.browserZone();

    async function getJson(url) {
        const res = await fetch(url, { cache: 'no-store' });
        if (!res.ok) throw new Error(res.status);
        return res.json();
    }

    async function getText(url) {
        const res = await fetch(url, { cache: 'no-store' });
        if (!res.ok) throw new Error(res.status);
        return (await res.text()).trim();
    }


    // WIND panel (wind.js); fed by loadAirport, loadWx and Stats' runway
    const wind = Wind.mount($('windBody'));
    let stats = { setAirport() {} };     // Stats.mount below

    let mapSet = false, airport = null;
    async function loadAirport() {
        try {
            const d = await getJson(DATA + 'go-arounds.json');
            const a = d && d.airport;
            if (a) airport = a;
            if (a) wind.update({ airport: a });
            if (a && a.icao) stats.setAirport(a.icao);
            if (a && a.icao) {
                $('icao').textContent = a.icao;
                document.title = `${a.icao} · toganomon console`;
            }
            if (!mapSet && a && a.arp) {
                $('map').src = mapUrl(a);
                mapSet = true;
            }
        } catch (e) { /* not written yet */ }
        if (!mapSet) setTimeout(loadAirport, 15000);
    }

    // the raw reports as last read; their age is redrawn every AGE_MS
    const wx = { metar: null, taf: null };
    const AGE_MS = 30000;
    const LEVEL_CLASS = { warn: 'stale-warn', alert: 'stale-alert' };

    function showAge() {
        const now = new Date();
        const metarAge = Wx.minutesSince(Wx.metarTime(wx.metar, now), now);
        const ml = wx.metar ? Wx.metarLevel(metarAge) : null;
        $('metarAge').textContent = wx.metar ? Wx.formatAge(metarAge) : '';
        $('metarAge').className = 'wx-age ' + (LEVEL_CLASS[ml] || '');
        const tl = wx.taf ? Wx.tafLevel(Wx.tafValidTo(wx.taf, now), now) : null;
        $('tafAge').textContent = tl === 'alert' ? 'EXPIRED' : '';
        $('tafAge').className = 'wx-age ' + (LEVEL_CLASS[tl] || '');
    }

    async function loadWx() {
        for (const kind of ['metar', 'taf']) {
            try {
                const t = await getText(DATA + kind + '.txt');
                wx[kind] = t || null;
                $(kind).textContent = t || '--';
                $(kind).title = t;
                if (kind === 'metar') wind.update({ metar: t });
            } catch (e) {
                wx[kind] = null;
                $(kind).textContent = 'NOT AVAILABLE';
            }
        }
        showAge();
    }

    Clock.start({ utc: $('clockUtc'), local: $('clockLocal'), label: $('clockLocalLabel'), timeZone });
    loadAirport();
    loadWx();
    setInterval(loadWx, WX_MS);
    setInterval(showAge, AGE_MS);
    const chimeOn = q.get('chime') !== '0';
    if (chimeOn) document.addEventListener('pointerdown', () => GoAroundView.unlockChime(), { once: true });

    // Floating windows: WIND then LAST 24H down the left, GO-AROUNDS top right (defaults)
    const top = () => Float.minTop() + Float.MARGIN;
    const windFloat = $('windFloat');
    Float.make(windFloat, {
        key: 'wind', button: $('windBtn'),
        at: () => ({ left: Float.MARGIN, top: top() }),
    });
    Float.make($('statsFloat'), {
        key: 'stats', button: $('statsBtn'),
        at: () => ({
            left: Float.MARGIN,
            top: windFloat.hidden ? top() : windFloat.getBoundingClientRect().bottom + Float.MARGIN,
        }),
    });
    const view = GoAroundView.mount($('gaList'), { url: DATA + 'go-arounds.json', timeZone });
    const ga = Float.make($('gaFloat'), {
        key: 'ga', button: $('gaBtn'), resizable: true,
        at: el => ({ left: window.innerWidth - el.offsetWidth - Float.MARGIN, top: top() }),
        onOpen: () => view.fit(),
        onResize: () => view.fit(),
    });
    // MOVEMENTS: left of GO-AROUNDS when that is open, else at the right edge
    const gaFloat = $('gaFloat');
    Float.make($('mvFloat'), {
        key: 'mv', button: $('mvBtn'), resizable: true,
        at: el => ({
            left: window.innerWidth - el.offsetWidth - Float.MARGIN
                - (gaFloat.hidden ? 0 : gaFloat.offsetWidth + Float.MARGIN),
            top: top(),
        }),
    });
    Movements.mount($('mvBody'), {
        url: DATA + 'movements.json', timeZone, pollMs: SUMMARY_MS,
        onSelect: (m, v) => {
            if (m) $('map').src = traceUrl(v.params, airport);
            else if (airport) $('map').src = mapUrl(airport);
        },
        onGa: id => {
            ga.open();
            view.show(id);
        },
        // each new movement lands or takes off on the WIND dial, one by one
        onNew: list => list.forEach((m, i) => setTimeout(() => {
            const p = Movements.pulseOf(m, timeZone);
            wind.pulse(p.end, p.kind, p.text);
        }, i * 900)),
    });
    // A missed approach above the gate has no movements: when the go-arounds
    // go up, show the newest on RWY/WIND if it is one (once).
    const missedShown = new Set();
    async function pulseMissed() {
        try {
            const res = await fetch(DATA + 'go-arounds.json', { cache: 'no-store' });
            const g = res.ok ? ((await res.json()).go_arounds || [])[0] : null;
            const p = Movements.missedPulse(g, timeZone);
            if (p && !missedShown.has(g.id)) {
                missedShown.add(g.id);
                wind.pulse(p.end, p.kind, p.text);
            }
        } catch (e) {
            // nothing to show
        }
    }
    stats = Stats.mount($('statsBody'), {
        url: DATA + 'summary.json', historyUrl: DATA + 'go-arounds.json', timeZone, pollMs: SUMMARY_MS,
        onGa: () => ga.toggle(),
        onIncrease: () => {
            view.refresh();
            if (chimeOn) GoAroundView.chime();
            pulseMissed();
        },
        onRunway: rwy => wind.update({ runway: rwy }),
    });
})(typeof window !== 'undefined' ? window : globalThis);
