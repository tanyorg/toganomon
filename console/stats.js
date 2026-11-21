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
//       onRunway(rwy | null) {},      // runway in use changed (null: none)
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

    // summary.json -> what the panel shows, or null if it is not a summary.
    // history: go-arounds.json's go_arounds (optional), for the last date.
    function view(s, nowSec, timeZone, history) {
        if (!s || typeof s.now !== 'number') return null;
        const tz = timeZone || 'UTC';
        const active = typeof s.runway_t === 'number' && s.runway && s.now - s.runway_t <= RUNWAY_ACTIVE_S;
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
            runway: active ? s.runway : null,
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

    const Stats = { view, increased, STALE_S, RUNWAY_ACTIVE_S };
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
        if (o.onGa) ga.t.addEventListener('click', () => o.onGa());
        else ga.t.disabled = true;

        let last = null, runway, timer = null;

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
            rwy.val.textContent = v.runway || '--';
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
            if (v.runway !== runway) {
                runway = v.runway;
                if (o.onRunway) o.onRunway(runway);
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
