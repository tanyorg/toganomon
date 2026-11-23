// toganomon console: the MOVEMENTS window. The arrivals and departures of
// the last 24 hours (movements.json written by toganomon.py), newest first,
// with a filter (ALL / ARR / DEP).
//
//   const mv = Movements.mount(element, {
//       url: '/toganomon/movements.json',   // polled every pollMs (60000; 0 = no polling)
//       timeZone: 'Asia/Tokyo',             // times in the list (default UTC)
//       onSelect(m, view) {},   // a row was clicked: view = Movements.mapView(m, now);
//                               // (null, null) to go back to the live map
//       onGa(id) {},        // the G/A mark of a row was clicked (go-around id)
//   });
//   -> { refresh(), stop() }
//
// A row's flight on the tar1090 map: Movements.mapView(m, now) gives the
// tar1090 query. Once the part of the flight to show has passed, that is
// tar1090's history view (Movements.traceParams: icao, showTrace,
// startTime, endTime), which is loaded once and not updated. While it is
// still going on (a departure climbing out), it is the aircraft selected
// on the live map (icao only), whose track keeps growing. While a flight
// is shown, a line above the list says so, with a button back to the live
// map.
// The list has a line for each day (in timeZone).
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

    // "2026-11-24 TUE" in timeZone (the list's day lines)
    function dayText(t, timeZone) {
        const p = {};
        for (const x of new Intl.DateTimeFormat('en-US', {
            timeZone: timeZone || 'UTC', year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short',
        }).formatToParts(new Date(t * 1000))) p[x.type] = x.value;
        return `${p.year}-${p.month}-${p.day} ${p.weekday.toUpperCase()}`;
    }

    // The list's items, in order: {day} before the first movement of each day, then {m}.
    function withDays(rows, timeZone) {
        const out = [];
        let last = null;
        for (const m of rows) {
            const day = dayText(m.t, timeZone);
            if (day !== last) out.push({ day });
            last = day;
            out.push({ m });
        }
        return out;
    }

    // The line above the list while a flight is on the map (LIVE: updating;
    // TRACE: the history, as loaded).
    function showingText(m, timeZone, live) {
        const c = cells(m, timeZone);
        return `MAP: ${c.flight} ${m.kind === 'arr' ? 'ARR' : 'DEP'} ${c.time} · ${live ? 'LIVE' : 'TRACE'}`;
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

    // The cells of one row: time, ARR/DEP and runway, callsign (hex without
    // one), type, registration.
    function cells(m, timeZone) {
        return {
            time: hhmm(m.t, timeZone),
            dir: m.kind === 'arr' ? 'ARR' : 'DEP',
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

    // {live, params}: live (icao only) while the movement's window is not
    // over yet at now (seconds), else the history view of the window.
    function mapView(m, now) {
        const trace = traceParams(m);
        const after = m.kind === 'arr' ? TRACE_AFTER_ARR_S : TRACE_AFTER_DEP_S;
        return now < m.t + after ? { live: true, params: { icao: trace.icao } } : { live: false, params: trace };
    }

    // Same movement (to keep the selected row across polls).
    function sameMovement(a, b) {
        return !!a && !!b && a.t === b.t && a.kind === b.kind && a.hex === b.hex;
    }

    function mount(el, options) {
        const o = Object.assign({
            url: 'movements.json', pollMs: 60000, timeZone: 'UTC', onSelect: null, onGa: null,
        }, options || {});
        let doc = null, filter = 'ALL', selected = null, selectedView = null, timer = null;
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
        // shown while a flight's track is on the map
        const showing = document.createElement('div');
        showing.className = 'mv-showing';
        const showingText_ = document.createElement('span');
        const back = document.createElement('button');
        back.className = 'mv-back';
        back.textContent = '× LIVE MAP';
        back.title = 'back to the live map of all aircraft';
        back.addEventListener('click', () => select(null));
        showing.append(showingText_, back);
        const list = document.createElement('div');
        list.className = 'mv-list';
        el.append(bar, showing, list);

        function select(m) {
            selected = m;
            selectedView = m ? mapView(m, Date.now() / 1000) : null;
            if (o.onSelect) o.onSelect(m, selectedView);
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
            showing.hidden = !selected;
            if (selected) showingText_.textContent = showingText(selected, o.timeZone, selectedView.live);
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
            for (const x of withDays(v.rows, o.timeZone)) {
                if (x.m) {
                    list.appendChild(row(x.m));
                } else {
                    const d = document.createElement('div');
                    d.className = 'mv-day';
                    d.textContent = x.day;
                    list.appendChild(d);
                }
            }
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

    const Movements = { mount, view, cells, traceParams, mapView, sameMovement, hhmm, dayText, withDays, showingText };
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = Movements;
        return;
    }
    root.Movements = Movements;
})(typeof window !== 'undefined' ? window : globalThis);
