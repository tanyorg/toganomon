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

    function clockText(date, timeZone) {
        return new Intl.DateTimeFormat('en-GB', {
            timeZone, hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
        }).format(date);
    }

    // Short label for a zone: an abbreviation such as "JST" or "CET" where a
    // locale has one (en-US alone gives "GMT+9" for Asia/Tokyo), else "UTC+9".
    const ZONE_LOCALES = ['en-US', 'en-GB', 'ja-JP', 'en-AU', 'en-IN', 'en-NZ'];
    function zoneLabel(timeZone, date) {
        let offset = null;
        for (const loc of ZONE_LOCALES) {
            try {
                const part = new Intl.DateTimeFormat(loc, { timeZone, timeZoneName: 'short' })
                    .formatToParts(date || new Date()).find(p => p.type === 'timeZoneName');
                if (!part) continue;
                if (/^[A-Z]{2,5}$/.test(part.value)) return part.value === 'GMT' ? 'UTC' : part.value;
                if (offset === null && /^GMT[+-]/.test(part.value)) offset = part.value.replace('GMT', 'UTC');
            } catch (e) { return String(timeZone).split('/').pop().toUpperCase(); }
        }
        return offset || 'UTC';
    }

    const Console = { mapUrl, clockText, zoneLabel };
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = Console;
        return;
    }
    root.Console = Console;

    // ---------- page ----------
    const $ = id => document.getElementById(id);
    const q = new URLSearchParams(location.search);
    const timeZone = q.get('tz') || Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';

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

    function tickClocks() {
        const now = new Date();
        $('clockUtc').textContent = clockText(now, 'UTC');
        $('clockLocal').textContent = clockText(now, timeZone);
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

    $('clockLocalLabel').textContent = zoneLabel(timeZone);
    tickClocks();
    setInterval(tickClocks, 1000);
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
    const gaFloat = $('gaFloat');
    // A size the user gave the window by its corner; else the CSS default,
    // wide enough for a track figure beside its legend.
    const size = Float.size('ga-size');
    if (size) {
        gaFloat.style.width = size.width + 'px';
        gaFloat.style.height = size.height + 'px';
    }
    let settle = true;      // the next size change is not the user's
    const ga = Float.make(gaFloat, {
        key: 'ga', button: $('gaBtn'),
        at: el => ({ left: window.innerWidth - el.offsetWidth - Float.MARGIN, top: top() }),
        onOpen: () => { settle = true; view.fit(); },
    });
    stats = Stats.mount($('statsBody'), {
        url: DATA + 'summary.json', historyUrl: DATA + 'go-arounds.json', timeZone, pollMs: SUMMARY_MS,
        onGa: () => ga.toggle(),
        onIncrease: () => view.refresh(),
        onRunway: rwy => wind.update({ runway: rwy }),
    });
    window.addEventListener('resize', () => { settle = true; });
    if (typeof ResizeObserver !== 'undefined') {
        let last = null;
        new ResizeObserver(() => {
            if (gaFloat.hidden) return;
            view.fit();
            const now = { width: gaFloat.offsetWidth, height: gaFloat.offsetHeight };
            if (!settle && last && (now.width !== last.width || now.height !== last.height)) Float.size('ga-size', now);
            settle = false;
            last = now;
        }).observe(gaFloat);
    }
})(typeof window !== 'undefined' ? window : globalThis);
