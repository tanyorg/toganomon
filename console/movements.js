// toganomon console: the MOVEMENTS window. The arrivals and departures of
// the last 24 hours (movements.json written by toganomon.py), newest first,
// with a filter (ALL / ARR / DEP).
//
//   const mv = Movements.mount(element, {
//       url: '/toganomon/movements.json',   // polled every pollMs (60000; 0 = no polling)
//       timeZone: 'Asia/Tokyo',             // times in the list (default UTC)
//       onSelect(m) {},     // a row was clicked (m: one movement); null for LIVE
//       onGa(id) {},        // the G/A mark of a row was clicked (go-around id)
//   });
//   -> { refresh(), stop() }
//
// A row's flight on the tar1090 map: Movements.traceParams(m) gives the
// query (icao, showTrace, startTime, endTime) for tar1090's history view
// of that aircraft around the movement.
// Everything but mount() is DOM-free (tests/console_test.js).
(function (root) {
    'use strict';

    const FILTERS = ['ALL', 'ARR', 'DEP'];
    // The part of the flight a row shows: the approach before an arrival,
    // the climb after a departure (seconds).
    const TRACE_BEFORE_ARR_S = 15 * 60;
    const TRACE_AFTER_ARR_S = 3 * 60;
    const TRACE_BEFORE_DEP_S = 3 * 60;
    const TRACE_AFTER_DEP_S = 15 * 60;

    const pad = n => String(n).padStart(2, '0');

    function hhmm(t, timeZone) {
        return new Intl.DateTimeFormat('en-GB', {
            timeZone: timeZone || 'UTC', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
        }).format(new Date(t * 1000));
    }

    // Movements to list for a filter, and the count for each filter.
    function view(doc, filter) {
        const all = (doc && Array.isArray(doc.movements) ? doc.movements : [])
            .filter(m => m && typeof m.t === 'number' && (m.kind === 'arr' || m.kind === 'dep'));
        const counts = {
            ALL: all.length,
            ARR: all.filter(m => m.kind === 'arr').length,
            DEP: all.filter(m => m.kind === 'dep').length,
        };
        const kind = { ARR: 'arr', DEP: 'dep' }[filter];
        return { rows: kind ? all.filter(m => m.kind === kind) : all, counts };
    }

    // The cells of one row: time, ↓/↑ and runway, callsign (hex without one),
    // type, registration.
    function cells(m, timeZone) {
        return {
            time: hhmm(m.t, timeZone),
            dir: m.kind === 'arr' ? '↓' : '↑',
            rwy: m.rwy || '--',
            flight: m.flight || (m.hex ? m.hex.toUpperCase() : '--'),
            type: m.type || '',
            reg: m.reg || '',
        };
    }

    // tar1090 query for the movement's flight: its trace of that UTC day,
    // limited to the approach or the climb (kept inside the day).
    function traceParams(m) {
        const before = m.kind === 'arr' ? TRACE_BEFORE_ARR_S : TRACE_BEFORE_DEP_S;
        const after = m.kind === 'arr' ? TRACE_AFTER_ARR_S : TRACE_AFTER_DEP_S;
        const t = new Date(m.t * 1000);
        const day = Date.UTC(t.getUTCFullYear(), t.getUTCMonth(), t.getUTCDate()) / 1000;
        const clock = s => {
            const d = new Date(s * 1000);
            return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
        };
        const start = Math.max(day, Math.floor(m.t - before));
        const end = Math.min(day + 86399, Math.ceil(m.t + after));
        return {
            icao: String(m.hex || '').toLowerCase(),
            showTrace: `${t.getUTCFullYear()}-${pad(t.getUTCMonth() + 1)}-${pad(t.getUTCDate())}`,
            startTime: clock(start),
            endTime: clock(end),
        };
    }

    // Same movement (to keep the selected row across polls).
    function sameMovement(a, b) {
        return !!a && !!b && a.t === b.t && a.kind === b.kind && a.hex === b.hex;
    }

    function mount(el, options) {
        const o = Object.assign({
            url: 'movements.json', pollMs: 60000, timeZone: 'UTC', onSelect: null, onGa: null,
        }, options || {});
        let doc = null, filter = 'ALL', selected = null, timer = null;
        el.classList.add('mv-view');
        el.textContent = '';
        const bar = document.createElement('div');
        bar.className = 'mv-bar';
        const tabs = {};
        for (const f of FILTERS) {
            const b = document.createElement('button');
            b.className = 'mv-tab';
            b.addEventListener('click', () => { filter = f; render(); });
            tabs[f] = b;
            bar.appendChild(b);
        }
        const live = document.createElement('button');
        live.className = 'mv-live';
        live.textContent = 'LIVE';
        live.title = 'back to the live map';
        live.addEventListener('click', () => select(null));
        bar.appendChild(live);
        const list = document.createElement('div');
        list.className = 'mv-list';
        el.append(bar, list);

        function select(m) {
            selected = m;
            if (o.onSelect) o.onSelect(m);
            render();
        }

        function row(m) {
            const c = cells(m, o.timeZone);
            const r = document.createElement('div');
            r.className = `mv-row mv-${m.kind}` + (sameMovement(m, selected) ? ' selected' : '');
            r.title = [m.hex && m.hex.toUpperCase(), m.gs != null && `${m.gs} kt`, m.category]
                .filter(Boolean).join(' · ');
            for (const k of ['time', 'dir', 'rwy', 'flight', 'type', 'reg']) {
                const s = document.createElement('span');
                s.className = 'mv-' + k;
                s.textContent = c[k];
                r.appendChild(s);
            }
            const ga = document.createElement('span');
            ga.className = 'mv-ga';
            if (m.ga) {
                const b = document.createElement('button');
                b.textContent = 'G/A';
                b.title = 'open this go-around';
                b.addEventListener('click', e => {
                    e.stopPropagation();
                    if (o.onGa) o.onGa(m.ga);
                });
                ga.appendChild(b);
            }
            r.appendChild(ga);
            r.addEventListener('click', () => select(sameMovement(m, selected) ? null : m));
            return r;
        }

        function render() {
            const v = view(doc, filter);
            for (const f of FILTERS) {
                tabs[f].textContent = `${f} ${v.counts[f]}`;
                tabs[f].classList.toggle('active', f === filter);
            }
            live.hidden = !selected;
            const scroll = list.scrollTop;      // kept across polls
            list.textContent = '';
            if (!doc) {
                list.textContent = 'LOADING…';
                return;
            }
            if (!v.rows.length) {
                const empty = document.createElement('div');
                empty.className = 'mv-empty';
                empty.textContent = 'NO MOVEMENTS YET';
                list.appendChild(empty);
                return;
            }
            for (const m of v.rows) list.appendChild(row(m));
            list.scrollTop = scroll;
        }

        async function refresh() {
            try {
                const res = await fetch(o.url, { cache: 'no-store' });
                if (!res.ok) return;
                doc = await res.json();
                render();
            } catch (e) {
                // try again on the next poll
            }
        }

        function stop() {
            clearInterval(timer);
            timer = null;
        }

        render();
        refresh();
        if (o.pollMs > 0) timer = setInterval(refresh, o.pollMs);
        return { refresh, stop };
    }

    const Movements = { mount, view, cells, traceParams, sameMovement, hhmm };
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = Movements;
        return;
    }
    root.Movements = Movements;
})(typeof window !== 'undefined' ? window : globalThis);
