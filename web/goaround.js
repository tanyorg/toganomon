// goaround-watch web view: the go-around history (go-arounds.json written by
// goaround_watch.py) as a list; each entry opens to a north-up track figure.
// Plain script, no build step: defines GoAroundView (window or module.exports).
//
//   const view = GoAroundView.mount(document.getElementById('list'), {
//       url: 'go-arounds.json',     // polled every pollMs
//       timeZone: 'UTC',            // any IANA zone, e.g. 'Asia/Tokyo'
//   });
//
// Options (all optional):
//   url, pollMs (60000; 0 = no polling), timeZone,
//   flightLink(g) -> { href, label } | null   (default: FlightAware for a callsign)
//   extraLinks(g, element)    add more links to an open entry (may be async)
//   storage: { load() -> object | null, save(object) }   which entries are open
//            (default: localStorage key 'goaround-watch')
// Returns { refresh(), stop(), fit() }; fit() re-lays out the open figures
// after the container was resized.
//
// The functions in GoAroundView other than mount() do not touch the DOM
// (tests/web_test.js).
(function (root) {
    'use strict';

    const DEFAULT_RUNWAY_HALF_KM = 1.5;
    // Two track points further apart than this are joined by a dashed line:
    // nothing was received in between (the track holds a point per 10 s), so
    // the straight line is not where the aircraft flew.
    const GAP_S = 60;
    const PLOT_MIN_W = 120;
    const SVG_NS = 'http://www.w3.org/2000/svg';
    const formatters = {};

    function fmt(timeZone, opts) {
        const key = timeZone + JSON.stringify(opts);
        if (!formatters[key]) formatters[key] = new Intl.DateTimeFormat('en-GB', { timeZone, ...opts });
        return formatters[key];
    }

    // "HH:MM", or "MM/DD HH:MM" with withDate
    function clock(sec, timeZone, withDate) {
        const d = new Date(sec * 1000);
        const hm = fmt(timeZone, { hourCycle: 'h23', hour: '2-digit', minute: '2-digit' }).format(d);
        if (!withDate) return hm;
        const [dd, mm] = fmt(timeZone, { month: '2-digit', day: '2-digit' }).format(d).split('/');
        return `${mm}/${dd} ${hm}`;
    }

    function title(g, timeZone) {
        return `${clock(g.t, timeZone, true)}  ${g.flight || '???'}  RWY${g.rwy}`;
    }

    function climbText(g) {
        return typeof g.climb_after_s === 'number' ? `CLIMB AFTER ${g.climb_after_s} s` : '';
    }

    function outcomeText(g, timeZone) {
        const o = g.outcome || {};
        const after = t => `+${Math.round((t - g.t) / 60)} min`;
        switch (o.status) {
            case 'landed': return `LANDED ${clock(o.t, timeZone)} (${after(o.t)}, RWY${o.rwy})`;
            case 'again': return `GO-AROUND AGAIN ${clock(o.t, timeZone)} (${after(o.t)})`;
            case 'not_landed': return 'NOT LANDED (DIVERTED?)';
            default: return 'PENDING';
        }
    }

    function windText(g) {
        const w = g.wind;
        if (!w) return 'WIND ---';
        const dir = w.dir === null ? 'VRB' : String(w.dir).padStart(3, '0');
        const unit = w.unit === 'kt' ? 'KT' : 'MPS';
        let s = `WIND ${dir}/${w.spd}${w.gust !== null ? 'G' + w.gust : ''}${unit}`;
        const c = g.wind_rwy;
        if (c) {
            s += c.head >= 0 ? ` · HW ${c.head}` : ` · TW ${-c.head}`;
            s += ` · XW ${c.cross}${c.from || ''}`;
        }
        return s;
    }

    function infoLines(g, timeZone) {
        return [title(g, timeZone), climbText(g), outcomeText(g, timeZone), windText(g)].filter(Boolean);
    }

    function legend() {
        return [
            { kind: 'line', cls: 'ga-key-rwy', label: 'RWY' },
            { kind: 'line', cls: 'ga-key-before', label: 'APPROACH' },
            { kind: 'line', cls: 'ga-key-after', label: 'AFTER GO-AROUND' },
            { kind: 'line', cls: 'ga-key-gap', label: 'NOT RECEIVED' },
            { kind: 'dot', cls: 'ga-key-turn', label: 'GO-AROUND' },
            { kind: 'dot', cls: 'ga-key-end', label: 'END OF TRACK' },
        ];
    }

    // The runway a go-around used, from the "airport" block of go-arounds.json
    // (ends, true_track, length_m). Without it: north-south, unnamed far end.
    function runwayOf(airport, rwy) {
        const list = airport && Array.isArray(airport.runways) ? airport.runways : [];
        const r = list.find(x => Array.isArray(x.ends) && x.ends.includes(rwy));
        if (!r) return { ends: [rwy, ''], trueTrack: 0, halfKm: DEFAULT_RUNWAY_HALF_KM };
        return {
            ends: r.ends,
            trueTrack: typeof r.true_track === 'number' ? r.true_track : 0,
            halfKm: typeof r.length_m === 'number' && r.length_m > 0 ? r.length_m / 2000 : DEFAULT_RUNWAY_HALF_KM,
        };
    }

    // Track [s, along km, cross km, alt] in the runway frame -> north-up SVG
    // coordinates (km, y positive to the south), split at the go-around.
    function trackGeometry(g, airport) {
        const rw = runwayOf(airport, g.rwy);
        const r = rw.trueTrack * Math.PI / 180;
        const ux = Math.sin(r), uy = Math.cos(r);
        const xy = (along, cross) => [along * ux + cross * uy, -(along * uy - cross * ux)];
        const track = Array.isArray(g.track) ? g.track : [];
        const gaAt = typeof g.track_from === 'number' ? g.t - g.track_from : Infinity;
        const before = [], after = [], beforeT = [], afterT = [];
        for (const p of track) {
            const part = p[0] <= gaAt;
            (part ? before : after).push(xy(p[1], p[2]));
            (part ? beforeT : afterT).push(p[0]);
        }
        // keep the line unbroken: the second part starts where the first ends
        if (before.length && after.length) {
            after.unshift(before[before.length - 1]);
            afterT.unshift(beforeT[beforeT.length - 1]);
        }
        // the first end's threshold is where its true track starts (along < 0)
        const end1 = xy(-rw.halfKm, 0), end2 = xy(rw.halfKm, 0);
        const all = before.concat(after, [end1, end2]);
        const pad = 1;
        const minX = Math.min(...all.map(p => p[0])) - pad, maxX = Math.max(...all.map(p => p[0])) + pad;
        const minY = Math.min(...all.map(p => p[1])) - pad, maxY = Math.max(...all.map(p => p[1])) + pad;
        const pt = p => `${p[0].toFixed(2)} ${p[1].toFixed(2)}`;
        const path = pts => pts.map((p, i) => `${i ? 'L' : 'M'}${pt(p)}`).join(' ');
        // solid where received, a move (M) across a gap; the gaps on their own
        const solid = (pts, ts) => pts.map((p, i) => `${i && ts[i] - ts[i - 1] <= GAP_S ? 'L' : 'M'}${pt(p)}`).join(' ');
        const gaps = (pts, ts) => pts.slice(1).map((p, i) => ts[i + 1] - ts[i] > GAP_S ? `M${pt(pts[i])} L${pt(p)}` : '')
            .filter(Boolean).join(' ');
        return {
            viewBox: `${minX.toFixed(2)} ${minY.toFixed(2)} ${(maxX - minX).toFixed(2)} ${(maxY - minY).toFixed(2)}`,
            runway: path([end1, end2]),
            ends: [{ at: end1, name: rw.ends[0] }, { at: end2, name: rw.ends[1] }],
            before: solid(before, beforeT),
            after: solid(after, afterT),
            beforeGaps: gaps(before, beforeT),
            afterGaps: gaps(after, afterT),
            start: before[0] || after[0] || null,
            // the go-around point only once the track goes past it: right after
            // a go-around trace_full often still ends on the approach
            turn: before.length && after.length ? before[before.length - 1] : null,
            end: after.length ? after[after.length - 1] : before.length ? before[before.length - 1] : null,
            scale: Math.max(maxX - minX, maxY - minY),
        };
    }

    // Which entries are open (ids, newest first). saved is the previous
    // { open, seen }. First time (no seen): only the newest. Afterwards: what
    // was open plus every go-around not seen before. Vanished ids are dropped.
    function openIds(items, saved) {
        const ids = items.map(g => g.id);
        if (!saved || !Array.isArray(saved.seen)) return { open: ids.slice(0, 1), seen: ids };
        const seen = new Set(saved.seen);
        const open = new Set((Array.isArray(saved.open) ? saved.open : []).filter(id => ids.includes(id)));
        for (const id of ids) if (!seen.has(id)) open.add(id);
        return { open: ids.filter(id => open.has(id)), seen: ids };
    }

    // Left margin that puts the track to the right of the overlay (width
    // overlayW); 0 (overlap) when what is left would be too narrow.
    function plotInset(figW, overlayW) {
        const inset = Math.ceil(overlayW) + 8;
        return figW - inset >= PLOT_MIN_W ? inset : 0;
    }

    function flightAware(g) {
        return g.flight
            ? { href: 'https://www.flightaware.com/live/flight/' + encodeURIComponent(g.flight), label: 'FLIGHTAWARE ↗' }
            : null;
    }

    function localStore(key) {
        return {
            load() {
                try { return JSON.parse(localStorage.getItem(key)); } catch (e) { return null; }
            },
            save(v) {
                try { localStorage.setItem(key, JSON.stringify(v)); } catch (e) { /* private mode */ }
            },
        };
    }

    // ---------- DOM ----------

    function svg(g, airport, timeZone) {
        const geo = trackGeometry(g, airport);
        const el = document.createElementNS(SVG_NS, 'svg');
        el.setAttribute('viewBox', geo.viewBox);
        el.setAttribute('class', 'ga-track');
        el.setAttribute('role', 'img');
        el.setAttribute('aria-label', `${title(g, timeZone)} track`);
        const sw = geo.scale / 160;     // line widths follow the figure's size
        const add = (tag, attrs) => {
            const e = document.createElementNS(SVG_NS, tag);
            for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
            el.appendChild(e);
            return e;
        };
        add('path', { d: geo.runway, class: 'ga-rwy', 'stroke-width': sw * 4 });
        for (const e of geo.ends) {
            if (!e.name) continue;
            add('text', { x: e.at[0], y: e.at[1], class: 'ga-rwy-label', 'font-size': sw * 9, dx: sw * 4 })
                .textContent = e.name;
        }
        if (geo.before) add('path', { d: geo.before, class: 'ga-before', 'stroke-width': sw * 1.6 });
        if (geo.after) add('path', { d: geo.after, class: 'ga-after', 'stroke-width': sw * 1.6 });
        const dash = `${(sw * 4).toFixed(3)} ${(sw * 3).toFixed(3)}`;
        if (geo.beforeGaps) add('path', { d: geo.beforeGaps, class: 'ga-before', 'stroke-width': sw * 1.6, 'stroke-dasharray': dash });
        if (geo.afterGaps) add('path', { d: geo.afterGaps, class: 'ga-after', 'stroke-width': sw * 1.6, 'stroke-dasharray': dash });
        if (geo.turn) add('circle', { cx: geo.turn[0], cy: geo.turn[1], r: sw * 3.5, class: 'ga-turn' });
        if (geo.end) add('circle', { cx: geo.end[0], cy: geo.end[1], r: sw * 2.5, class: 'ga-end' });
        return el;
    }

    // The figure with the description, links and legend laid over it in
    // HTML (so their size does not follow the SVG's scale).
    function figure(g, airport, o) {
        const fig = document.createElement('div');
        fig.className = 'ga-fig';
        fig.appendChild(svg(g, airport, o.timeZone));
        const overlay = document.createElement('div');
        overlay.className = 'ga-overlay';
        const info = document.createElement('div');
        info.className = 'ga-info';
        infoLines(g, o.timeZone).forEach((text, i) => {
            const line = document.createElement('div');
            line.className = i === 0 ? 'ga-info-title' : 'ga-info-line';
            line.textContent = text;
            info.appendChild(line);
        });
        const link = o.flightLink ? o.flightLink(g) : null;
        if (link) {
            const a = document.createElement('a');
            a.className = 'ga-link';
            a.href = link.href;
            a.target = '_blank';
            a.rel = 'noopener noreferrer';
            a.textContent = link.label;
            info.appendChild(a);
        }
        if (o.extraLinks) {
            const extra = document.createElement('div');
            extra.className = 'ga-extra';
            info.appendChild(extra);
            o.extraLinks(g, extra);
        }
        overlay.appendChild(info);
        const lg = document.createElement('div');
        lg.className = 'ga-legend';
        for (const k of legend()) {
            const row = document.createElement('div');
            const mark = document.createElement('span');
            mark.className = `ga-key ga-key-${k.kind} ${k.cls}`;
            row.appendChild(mark);
            row.appendChild(document.createTextNode(k.label));
            lg.appendChild(row);
        }
        overlay.appendChild(lg);
        fig.appendChild(overlay);
        return fig;
    }

    function fitPlot(fig) {
        const s = fig.querySelector('svg');
        const overlay = fig.querySelector('.ga-overlay');
        if (!s || !overlay) return;
        const inset = plotInset(fig.clientWidth, overlay.offsetWidth);
        s.style.marginLeft = inset ? inset + 'px' : '';
        s.style.width = inset ? `calc(100% - ${inset}px)` : '';
    }

    function mount(list, options) {
        const o = Object.assign({
            url: 'go-arounds.json', pollMs: 60000, timeZone: 'UTC', flightLink: flightAware,
            extraLinks: null, storage: null,
        }, options || {});
        const store = o.storage || localStore('goaround-watch');
        list.classList.add('ga-view');
        let items = [], airport = null, open = new Set(), timer = null;

        function save(seen) {
            store.save({ open: [...open], seen });
        }

        function fit() {
            list.querySelectorAll('.ga-fig').forEach(fitPlot);
        }

        function render() {
            list.textContent = '';
            if (!items.length) {
                const empty = document.createElement('div');
                empty.className = 'ga-empty';
                empty.textContent = 'NO GO-AROUNDS YET';
                list.appendChild(empty);
                return;
            }
            for (const g of items) {
                const isOpen = open.has(g.id);
                const item = document.createElement('div');
                item.className = 'ga-item' + (isOpen ? ' open' : '');
                const head = document.createElement('button');
                head.className = 'ga-head';
                head.textContent = `${isOpen ? '▾' : '▸'} ${title(g, o.timeZone)}`;
                head.setAttribute('aria-expanded', String(isOpen));
                head.addEventListener('click', () => {
                    if (open.has(g.id)) open.delete(g.id); else open.add(g.id);
                    save(items.map(x => x.id));
                    render();
                });
                item.appendChild(head);
                if (isOpen) item.appendChild(figure(g, airport, o));
                list.appendChild(item);
            }
            fit();
        }

        async function refresh() {
            try {
                const res = await fetch(o.url, { cache: 'no-store' });
                if (!res.ok) return;
                const data = await res.json();
                if (Array.isArray(data.go_arounds)) {
                    items = data.go_arounds;
                    airport = data.airport || null;
                    const sel = openIds(items, store.load());
                    open = new Set(sel.open);
                    save(sel.seen);
                }
                render();
            } catch (e) {
                // try again on the next poll
            }
        }

        function stop() {
            clearInterval(timer);
            timer = null;
        }

        refresh();
        if (o.pollMs > 0) timer = setInterval(refresh, o.pollMs);
        return { refresh, stop, fit };
    }

    const api = {
        mount, clock, title, climbText, outcomeText, windText, infoLines, legend,
        runwayOf, trackGeometry, openIds, plotInset, flightAware,
    };
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.GoAroundView = api;
})(typeof window !== 'undefined' ? window : globalThis);
