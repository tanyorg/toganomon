// toganomon console: the LAST 24H panel from summary.json (toganomon.py's
// [paths] summary). Shared by the docker console and the RJFF console.
//
//   const stats = Stats.mount(element, {
//       url: 'summary.json',          // polled every pollMs (60000)
//       historyUrl: 'go-arounds.json', // for "LAST GO-AROUND ..." when 24 h has none
//       timeZone: 'UTC',
//       airport: 'RJFF',              // caption over the airport's counts
//       onGa() {},                    // the G/A tile was clicked
//       onIncrease(count) {},         // go-arounds went up while open
//       onRunway(ends | null) {},     // runways in use changed: [designator] (null: none)
//   });                               // -> { refresh(), stop(), setAirport(icao) }
//
// Stats.view and Stats.increased do not touch the DOM (tests/console_test.js).
(function (root) {
    'use strict';

    const STALE_S = 180;             // toganomon runs every minute
    const RUNWAY_ACTIVE_S = 3600;    // no movement for an hour: no runway in use
    const formatters = {};

    function fmt(timeZone, opts) {
        const key = timeZone + JSON.stringify(opts);
        if (!formatters[key]) formatters[key] = new Intl.DateTimeFormat('en-GB', { timeZone, hourCycle: 'h23', ...opts });
        return formatters[key];
    }
    const hhmm = (sec, tz) => fmt(tz, { hour: '2-digit', minute: '2-digit' }).format(new Date(sec * 1000));
    const mmdd = (sec, tz) => {
        const p = fmt(tz, { month: '2-digit', day: '2-digit' }).formatToParts(new Date(sec * 1000));
        const get = type => p.find(x => x.type === type).value;
        return `${get('month')}/${get('day')}`;
    };
    const num = v => (typeof v === 'number' ? String(v) : '--');

    // The runways in use: summary.json's "runways" (arrivals and departures
    // apart, busiest first; toganomon 2026-11-25), else the last movement's
    // runway while it is at most RUNWAY_ACTIVE_S old (an older summary, or a
    // quiet half hour). -> { arr, dep, ends, text, lines } or null.
    // text: one line ("34", "34L/34R", "ARR 22/23 · DEP 16R/16L"); lines: the
    // same for a tile, [{ kind: 'ARR' | 'DEP' | null, ends: '22/23' }].
    function runwaysInUse(s) {
        const list = k => (s.runways && Array.isArray(s.runways[k])
            ? s.runways[k].filter(r => typeof r === 'string' && r) : []);
        let arr = list('arr'), dep = list('dep');
        if (!arr.length && !dep.length) {
            const active = typeof s.runway_t === 'number' && typeof s.runway === 'string' && s.runway
                && s.now - s.runway_t <= RUNWAY_ACTIVE_S;
            if (!active) return null;
            arr = [s.runway];
        }
        const ends = [...new Set([...arr, ...dep])];
        const same = !arr.length || !dep.length
            || (arr.length === dep.length && [...arr].sort().join() === [...dep].sort().join());
        const lines = same ? [{ kind: null, ends: (arr.length ? arr : dep).join('/') }]
            : [{ kind: 'ARR', ends: arr.join('/') }, { kind: 'DEP', ends: dep.join('/') }];
        const text = lines.map(l => (l.kind ? `${l.kind} ${l.ends}` : l.ends)).join(' · ');
        return { arr, dep, ends, text, lines };
    }

    // summary.json -> what the panel shows, or null if it is not a summary.
    // history: go-arounds.json's go_arounds (optional), for the last date.
    function view(s, nowSec, timeZone, history) {
        if (!s || typeof s.now !== 'number') return null;
        const tz = timeZone || 'UTC';
        const inUse = runwaysInUse(s);
        const count = typeof s.go_arounds === 'number' ? s.go_arounds : null;
        const past = (Array.isArray(history) ? history : []).filter(g => g && typeof g.t === 'number');
        const latest = past.length ? past.reduce((a, b) => (b.t > a.t ? b : a)) : null;
        const partial = typeof s.started === 'number' && typeof s.window_s === 'number' && s.now - s.started < s.window_s;
        const age = nowSec - s.now;
        const lastGa = count === 0 && latest
            ? `${mmdd(latest.t, tz)} ${hhmm(latest.t, tz)}` + (latest.flight ? ` ${latest.flight}` : '') : null;
        const since = partial ? `${mmdd(s.started, tz)} ${hhmm(s.started, tz)}` : null;
        const time = hhmm(s.now, tz);
        const stale = age > STALE_S;
        // label/value rows by group: 'airport' (under the tiles), 'receiver'
        // (every aircraft received, not only the airport's), 'status' (both).
        // sub: a part of the row above. level 'warn' / 'alert' colours a value.
        const rows = [];
        const row = (group, key, label, value, level, sub) => rows.push({ group, key, label, value, level: level || null, sub: !!sub });
        if (lastGa) row('airport', 'lastGa', 'LAST G/A', lastGa);
        if (typeof s.total === 'number') row('receiver', 'seen', 'AIRCRAFT SEEN', String(s.total));
        if (typeof s.with_pos === 'number') row('receiver', 'withPos', 'WITH POSITION', String(s.with_pos), null, true);
        // counting began less than a window ago: say since when
        if (since) row('status', 'since', 'SINCE', since, 'warn');
        row('status', 'updated', 'UPDATED', time, stale ? 'alert' : null);
        return {
            arrivals: num(s.arrivals),
            departures: num(s.departures),
            runway: inUse ? inUse.text : null,
            runways: inUse,
            goArounds: num(count),
            count,
            hot: count > 0,
            // 24 h without one: "LAST mm/dd" for compact displays
            gaLast: count === 0 && latest ? `LAST ${mmdd(latest.t, tz)}` : null,
            rows,
            time,
            stale,
            since,
        };
    }

    // a rise while the page is open (not on the first poll, not when one
    // leaves the 24-hour window)
    function increased(last, count) {
        return typeof last === 'number' && typeof count === 'number' && count > last;
    }

    const Stats = { view, increased, runwaysInUse, STALE_S, RUNWAY_ACTIVE_S };
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = Stats;
        return;
    }
    root.Stats = Stats;

    function node(tag, cls, text) {
        const e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text !== undefined) e.textContent = text;
        return e;
    }

    function tile(tag, label) {
        const t = node(tag, 'st-tile');
        const val = node('span', 'st-val', '--');
        const lab = node('span', 'st-label', label);
        t.append(val, lab);
        return { t, val, lab };
    }

    Stats.mount = function (container, options) {
        const o = Object.assign({
            url: 'summary.json', historyUrl: null, timeZone: 'UTC', pollMs: 60000, airport: null,
            onGa: null, onIncrease: null, onRunway: null,
        }, options || {});
        container.classList.add('st-panel');
        const tiles = node('div', 'st-tiles');
        const arr = tile('div', 'ARR'), dep = tile('div', 'DEP'), rwy = tile('div', 'RWY'), ga = tile('button', 'G/A');
        ga.t.classList.add('st-ga');
        ga.t.title = 'GO-AROUNDS';
        tiles.append(arr.t, dep.t, rwy.t, ga.t);
        // RJFF-style caption over the airport part, RECEIVER over the rest
        const capA = node('div', 'st-cap', o.airport || 'AIRPORT');
        const capR = node('div', 'st-cap', 'RECEIVER');
        const lists = { airport: node('dl', 'st-rows'), receiver: node('dl', 'st-rows'), status: node('dl', 'st-rows st-status') };
        container.replaceChildren(capA, tiles, lists.airport, capR, lists.receiver, lists.status);
        // a tile that opens something says so: an arrow and a tooltip
        if (o.onGa) {
            ga.lab.textContent = 'G/A ▸';
            ga.t.title = 'OPEN GO-AROUNDS';
            ga.t.setAttribute('aria-label', 'open GO-AROUNDS');
            ga.t.addEventListener('click', () => o.onGa());
        } else {
            ga.t.disabled = true;
        }

        let last = null, runwayKey, timer = null;

        function renderRows(list) {
            for (const g in lists) {
                const items = list.filter(r => r.group === g);
                lists[g].replaceChildren(...items.map(r => {
                    const cls = 'st-row st-' + r.key.replace(/[A-Z]/g, c => '-' + c.toLowerCase()) + (r.sub ? ' st-sub' : '');
                    const el = node('div', cls);
                    el.append(node('dt', '', r.label), node('dd', r.level ? 'st-' + r.level : '', r.value));
                    return el;
                }));
                lists[g].hidden = !items.length;
            }
            capR.hidden = lists.receiver.hidden;
        }

        async function getJson(url) {
            const res = await fetch(url, { cache: 'no-store' });
            if (!res.ok) throw new Error(res.status);
            return res.json();
        }

        async function refresh() {
            let s = null, history = null;
            try {
                s = await getJson(o.url);
                if (s && s.go_arounds === 0 && o.historyUrl) {
                    try { history = (await getJson(o.historyUrl)).go_arounds; } catch (e) { history = null; }
                }
            } catch (e) {
                s = null;
            }
            const v = view(s, Date.now() / 1000, o.timeZone, history);
            if (!v) {
                // keep what is shown; mark UPDATED, or say there is nothing yet
                const upd = lists.status.querySelector('.st-updated dd');
                if (upd) upd.className = 'st-alert';
                else renderRows([{ group: 'status', key: 'updated', label: 'UPDATED', value: 'NO DATA', level: 'alert' }]);
                return;
            }
            arr.val.textContent = v.arrivals;
            dep.val.textContent = v.departures;
            // one line, or ARR and DEP apart in two smaller ones
            const lines = v.runways ? v.runways.lines : [];
            if (lines.length > 1) {
                rwy.val.replaceChildren(...lines.map(l => {
                    const d = node('div', 'st-rwy-line');
                    d.append(node('span', 'st-rwy-kind', l.kind[0]), l.ends);
                    return d;
                }));
            } else {
                rwy.val.textContent = lines.length ? lines[0].ends : '--';
            }
            rwy.val.classList.toggle('st-rwy-two', lines.length > 1);
            rwy.t.title = v.runway ? 'RUNWAYS IN USE: ' + v.runway : '';
            rwy.t.classList.toggle('st-on', !!v.runway);
            ga.val.textContent = v.goArounds;
            ga.t.classList.toggle('st-hot', v.hot);
            renderRows(v.rows);
            if (increased(last, v.count)) {
                ga.t.classList.remove('st-flash');
                void ga.t.offsetWidth;          // restart the animation
                ga.t.classList.add('st-flash');
                if (o.onIncrease) o.onIncrease(v.count);
            }
            if (v.count !== null) last = v.count;
            const ends = v.runways ? v.runways.ends : null;
            const key = ends ? ends.join() : null;
            if (key !== runwayKey) {
                runwayKey = key;
                if (o.onRunway) o.onRunway(ends);
            }
        }

        refresh();
        if (o.pollMs > 0) timer = setInterval(refresh, o.pollMs);
        return {
            refresh,
            stop() { if (timer) clearInterval(timer); timer = null; },
            setAirport(icao) { capA.textContent = icao || 'AIRPORT'; },
        };
    };
})(typeof window !== 'undefined' ? window : globalThis);
