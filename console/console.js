// toganomon console: header (METAR / TAF / clocks), tar1090 map centred on
// the airport, last-24-hour counts and the go-around list (GoAroundView).
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
    const STALE_S = 180;          // summary older than this: shown in red
    const MAP_ZOOM = 10;

    function mapUrl(airport, zoom) {
        const p = new URLSearchParams({ zoom: String(zoom || MAP_ZOOM) });
        if (airport && Array.isArray(airport.arp)) {
            p.set('lat', airport.arp[0].toFixed(4));
            p.set('lon', airport.arp[1].toFixed(4));
        }
        return '/tar1090/?' + p.toString();
    }

    // summary.json -> what the four tiles and the UPDATED line show
    function summaryView(s, nowSec, timeZone) {
        if (!s || typeof s.now !== 'number') return null;
        const age = nowSec - s.now;
        const t = new Intl.DateTimeFormat('en-GB', {
            timeZone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
        }).format(new Date(s.now * 1000));
        return {
            arrivals: num(s.arrivals),
            departures: num(s.departures),
            runway: s.runway || '--',
            goArounds: num(s.go_arounds),
            hot: (s.go_arounds || 0) > 0,
            updated: `UPDATED ${t}` + (age > STALE_S ? ` (${Math.round(age / 60)} MIN AGO)` : ''),
            stale: age > STALE_S,
        };
    }

    function num(v) {
        return typeof v === 'number' ? String(v) : '--';
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

    const Console = { mapUrl, summaryView, clockText, zoneLabel };
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

    // what the WIND window draws from
    const wx = { airport: null, runway: null, metar: null };
    function drawWind() {
        const w = Wind.parseWind(wx.metar);
        Wind.draw($('windDial'), w, wx.airport, wx.runway);
        $('windDir').textContent = Wind.dirText(w);
        $('windSpd').textContent = Wind.speedText(w);
        $('windVar').textContent = w && w.varFrom !== null
            ? `${String(w.varFrom).padStart(3, '0')}V${String(w.varTo).padStart(3, '0')}` : '';
        $('windComp').textContent = Wind.windLines(w, wx.airport, wx.runway).join('\n');
    }

    let mapSet = false;
    async function loadAirport() {
        try {
            const d = await getJson(DATA + 'go-arounds.json');
            const a = d && d.airport;
            if (a) {
                wx.airport = a;
                drawWind();
            }
            if (a && a.icao) {
                $('icao').textContent = a.icao;
                document.title = `${a.icao} · toganomon console`;
            }
            if (!mapSet && a && a.arp) {
                $('map').src = mapUrl(a);
                mapSet = true;
            }
        } catch (e) { /* not written yet */ }
        if (!mapSet || !wx.airport) setTimeout(loadAirport, 15000);
    }

    async function loadSummary() {
        try {
            const s = await getJson(DATA + 'summary.json');
            const v = summaryView(s, Date.now() / 1000, timeZone);
            if (s && (s.runway || null) !== wx.runway) {
                wx.runway = s.runway || null;
                drawWind();
            }
            if (v) {
                for (const k of ['arrivals', 'departures', 'runway', 'goArounds']) $(k).textContent = v[k];
                $('gaTile').classList.toggle('hot', v.hot);
                $('updated').textContent = v.updated;
                $('updated').classList.toggle('stale', v.stale);
            }
        } catch (e) {
            $('updated').textContent = 'NO DATA';
            $('updated').classList.add('stale');
        }
    }

    async function loadWx() {
        for (const kind of ['metar', 'taf']) {
            try {
                const t = await getText(DATA + kind + '.txt');
                $(kind).textContent = t || '--';
                $(kind).title = t;
                if (kind === 'metar' && t !== wx.metar) {
                    wx.metar = t;
                    drawWind();
                }
            } catch (e) {
                $(kind).textContent = 'NOT AVAILABLE';
            }
        }
    }

    $('clockLocalLabel').textContent = zoneLabel(timeZone);
    tickClocks();
    setInterval(tickClocks, 1000);
    loadAirport();
    loadSummary();
    setInterval(loadSummary, SUMMARY_MS);
    loadWx();
    setInterval(loadWx, WX_MS);

    // Floating windows: WIND then LAST 24H down the left, GO-AROUNDS top right (defaults)
    const top = () => Float.minTop() + Float.MARGIN;
    drawWind();
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
    $('gaTile').addEventListener('click', () => ga.toggle());
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
