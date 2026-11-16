#!/usr/bin/env node
// Tests for the DOM-free parts of web/goaround.js. Run: node tests/web_test.js
// TZ is irrelevant: every time is formatted with an explicit zone.
'use strict';
const assert = require('assert');
const path = require('path');
const v = require(path.join(__dirname, '..', 'web', 'goaround.js'));

let failed = 0;
function t(label, fn) {
    try {
        fn();
        console.log(`ok   ${label}`);
    } catch (e) {
        failed++;
        console.log(`FAIL ${label}: ${e.message}`);
    }
}

// 2026-11-13 04:02:52Z = 13:02:52 JST
const T = Date.UTC(2026, 10, 13, 4, 2, 52) / 1000;
const JST = 'Asia/Tokyo';
const base = {
    id: '789288-1', t: T, arr_t: T - 65, hex: '789288', flight: 'HGB308', rwy: '34', climb_after_s: 65,
    wind: null, wind_rwy: null, outcome: { status: 'pending' }, track: [], track_from: T - 365,
};
const RJFF = { icao: 'RJFF', arp: [33.585, 130.451],
    runways: [{ ends: ['16', '34'], true_track: 157, center: [33.585, 130.451], length_m: 2800 }] };

t('title: date, time, callsign, runway in the given zone', () => {
    assert.strictEqual(v.title(base, JST), '11/13 13:02  HGB308  RWY34');
    assert.strictEqual(v.title(base, 'UTC'), '11/13 04:02  HGB308  RWY34');
    assert.strictEqual(v.title({ ...base, flight: null }, JST), '11/13 13:02  ???  RWY34');
});

t('climbText', () => {
    assert.strictEqual(v.climbText(base), 'CLIMB AFTER 65 s');
    assert.strictEqual(v.climbText({ ...base, climb_after_s: null }), '');
});

t('outcomeText: every status', () => {
    assert.strictEqual(v.outcomeText({ ...base, outcome: { status: 'landed', t: T + 460, rwy: '34' } }, JST),
        'LANDED 13:10 (+8 min, RWY34)');
    assert.strictEqual(v.outcomeText({ ...base, outcome: { status: 'again', t: T + 600, rwy: '34' } }, JST),
        'GO-AROUND AGAIN 13:12 (+10 min)');
    assert.strictEqual(v.outcomeText({ ...base, outcome: { status: 'not_landed' } }, JST), 'NOT LANDED (DIVERTED?)');
    assert.strictEqual(v.outcomeText(base, JST), 'PENDING');
});

t('windText: gusts, head/tail and cross components', () => {
    assert.strictEqual(v.windText(base), 'WIND ---');
    assert.strictEqual(v.windText({ ...base, wind: { dir: 330, spd: 18, gust: 28, unit: 'kt' },
        wind_rwy: { head: 18, cross: 1, from: 'L' } }), 'WIND 330/18G28KT · HW 18 · XW 1L');
    assert.strictEqual(v.windText({ ...base, wind: { dir: 160, spd: 6, gust: null, unit: 'kt' },
        wind_rwy: { head: -6, cross: 0, from: null } }), 'WIND 160/6KT · TW 6 · XW 0');
    assert.strictEqual(v.windText({ ...base, wind: { dir: null, spd: 2, gust: null, unit: 'kt' }, wind_rwy: null }),
        'WIND VRB/2KT');
    assert.strictEqual(v.windText({ ...base, wind: { dir: 50, spd: 5, gust: null, unit: 'm/s' }, wind_rwy: null }),
        'WIND 050/5MPS');
});

t('infoLines: title first, empty parts dropped', () => {
    assert.deepStrictEqual(v.infoLines(base, JST), ['11/13 13:02  HGB308  RWY34', 'CLIMB AFTER 65 s', 'PENDING', 'WIND ---']);
    assert.deepStrictEqual(v.infoLines({ ...base, climb_after_s: null }, JST), ['11/13 13:02  HGB308  RWY34', 'PENDING', 'WIND ---']);
});

t('runwayOf: from the airport block, with fallbacks', () => {
    assert.deepStrictEqual(v.runwayOf(RJFF, '34'), { ends: ['16', '34'], trueTrack: 157, halfKm: 1.4 });
    assert.deepStrictEqual(v.runwayOf(null, '09'), { ends: ['09', ''], trueTrack: 0, halfKm: 1.5 });
    assert.strictEqual(v.runwayOf({ runways: [{ ends: ['16', '34'], true_track: 157 }] }, '16').halfKm, 1.5);
});

t('trackGeometry: north up, split at the go-around, runway ends named', () => {
    // along is towards 157 deg (south-south-east); runway 16's threshold is the north end
    const g = { ...base, track: [[0, -3, 0, 900], [300, 1, 0, 100], [365, 2, 0, 300], [400, 3, 0, 800]] };
    const geo = v.trackGeometry(g, RJFF);
    const [n, s] = geo.ends;
    assert.deepStrictEqual([n.name, s.name], ['16', '34']);
    assert.ok(n.at[1] < s.at[1], '16 (north end) is above');
    assert.ok(geo.start[1] < geo.end[1], 'flew from north to south');
    assert.strictEqual(geo.before.split('L').length, 3);       // 3 points up to the go-around
    assert.strictEqual(geo.after.split('L').length, 2);        // joint point + 1
    assert.strictEqual(geo.after.split(' L')[0], `M${geo.turn[0].toFixed(2)} ${geo.turn[1].toFixed(2)}`);
    const [x, y, w, h] = geo.viewBox.split(' ').map(Number);
    for (const p of [geo.start, geo.end, n.at, s.at]) {
        assert.ok(p[0] >= x && p[0] <= x + w && p[1] >= y && p[1] <= y + h, 'inside the viewBox');
    }
});

t('trackGeometry: another runway direction (09/27: east-west)', () => {
    const ap = { runways: [{ ends: ['09', '27'], true_track: 90, length_m: 2000 }] };
    const geo = v.trackGeometry({ ...base, rwy: '27', track: [[0, 2, 0, 500]] }, ap);
    const [w, e] = geo.ends;
    assert.deepStrictEqual([w.name, e.name], ['09', '27']);
    assert.ok(w.at[0] < e.at[0], '09 threshold is the west end');
    assert.ok(Math.abs(w.at[1]) < 1e-9 && Math.abs(geo.start[0] - 2) < 1e-9, 'along = east');
});

t('trackGeometry: empty track still draws the runway', () => {
    const geo = v.trackGeometry(base, RJFF);
    assert.strictEqual(geo.before, '');
    assert.strictEqual(geo.after, '');
    assert.strictEqual(geo.start, null);
    assert.ok(geo.runway.startsWith('M'));
});

t('openIds: first time only the newest is open', () => {
    const items = [{ id: 'c' }, { id: 'b' }, { id: 'a' }];
    assert.deepStrictEqual(v.openIds(items, null), { open: ['c'], seen: ['c', 'b', 'a'] });
    assert.deepStrictEqual(v.openIds([], null), { open: [], seen: [] });
});

t('openIds: keeps what was open, opens new ones, drops vanished ids', () => {
    const items = [{ id: 'd' }, { id: 'c' }, { id: 'b' }, { id: 'a' }];
    const saved = { open: ['b', 'a', 'x'], seen: ['c', 'b', 'a', 'x'] };
    assert.deepStrictEqual(v.openIds(items, saved), { open: ['d', 'b', 'a'], seen: ['d', 'c', 'b', 'a'] });
});

t('openIds: everything closed stays closed until a new one comes', () => {
    const items = [{ id: 'b' }, { id: 'a' }];
    assert.deepStrictEqual(v.openIds(items, { open: [], seen: ['b', 'a'] }).open, []);
    assert.deepStrictEqual(v.openIds([{ id: 'c' }, ...items], { open: [], seen: ['b', 'a'] }).open, ['c']);
    assert.deepStrictEqual(v.openIds(items, { seen: ['b', 'a'] }).open, []);
});

t('plotInset: track to the right of the overlay unless too narrow', () => {
    assert.strictEqual(v.plotInset(340, 150.4), 159);
    assert.strictEqual(v.plotInset(280, 152), 160);
    assert.strictEqual(v.plotInset(279, 152), 0);
});

t('legend and flight link', () => {
    assert.deepStrictEqual(v.legend().map(k => k.label), ['RWY', 'APPROACH', 'AFTER GO-AROUND', 'GO-AROUND', 'END OF TRACK']);
    assert.deepStrictEqual(v.flightAware(base), { href: 'https://www.flightaware.com/live/flight/HGB308', label: 'FLIGHTAWARE ↗' });
    assert.strictEqual(v.flightAware({ ...base, flight: null }), null);
});

if (failed) {
    console.log(`${failed} failed`);
    process.exit(1);
}
console.log('all passed');
