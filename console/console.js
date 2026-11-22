// toganomon console: header (METAR / TAF / clocks), tar1090 map centred on
// the airport, LAST 24H (Stats), WIND (Wind) and GO-AROUNDS (GoAroundView).
// Data, all from the same origin (docker/nginx.conf):
//   /toganomon/summary.json, /toganomon/go-arounds.json  (toganomon.py)
//   /toganomon/metar.txt, /toganomon/taf.txt              (fetch_wx.py)
//   /tar1090/                                             (ultrafeeder)
// Query: ?tz=<IANA zone> for the second clock and the list (default: browser).
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

    const Console = { mapUrl };
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

    let mapSet = false;
    async function loadAirport() {
        try {
            const d = await getJson(DATA + 'go-arounds.json');
            const a = d && d.airport;
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

    async function loadWx() {
        for (const kind of ['metar', 'taf']) {
            try {
                const t = await getText(DATA + kind + '.txt');
                $(kind).textContent = t || '--';
                $(kind).title = t;
                if (kind === 'metar') wind.update({ metar: t });
            } catch (e) {
                $(kind).textContent = 'NOT AVAILABLE';
            }
        }
    }

    Clock.start({ utc: $('clockUtc'), local: $('clockLocal'), label: $('clockLocalLabel'), timeZone });
    loadAirport();
    loadWx();
    setInterval(loadWx, WX_MS);

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
    stats = Stats.mount($('statsBody'), {
        url: DATA + 'summary.json', historyUrl: DATA + 'go-arounds.json', timeZone, pollMs: SUMMARY_MS,
        onGa: () => ga.toggle(),
        onIncrease: () => view.refresh(),
        onRunway: rwy => wind.update({ runway: rwy }),
    });
})(typeof window !== 'undefined' ? window : globalThis);
