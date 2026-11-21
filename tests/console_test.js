#!/usr/bin/env node
// Tests for the DOM-free parts of console/wind.js, console/float.js and
// console/console.js. Run: node tests/console_test.js
'use strict';
const assert = require('assert');
const path = require('path');
const W = require(path.join(__dirname, '..', 'console', 'wind.js'));
const F = require(path.join(__dirname, '..', 'console', 'float.js'));
const C = require(path.join(__dirname, '..', 'console', 'console.js'));

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

const AP = { icao: 'ZZZZ', arp: [10.5, 20.25],
    runways: [{ ends: ['16', '34'], true_track: 150.3 }, { ends: ['09', '27'], true_track: 90 }] };

t('parseWind: direction, speed, gust, unit, variable sector', () => {
    assert.deepStrictEqual(W.parseWind('ZZZZ 212230Z 29008KT 260V330 9999 FEW030 Q1029 NOSIG'),
        { dir: 290, spd: 8, gust: null, unit: 'kt', vrb: false, calm: false, varFrom: 260, varTo: 330 });
    assert.deepStrictEqual(W.parseWind('ZZZZ 200524Z 29015G25KT 9999'),
        { dir: 290, spd: 15, gust: 25, unit: 'kt', vrb: false, calm: false, varFrom: null, varTo: null });
    const mps = W.parseWind('ZZZZ 200524Z 05010MPS 9999');
    assert.strictEqual(mps.unit, 'm/s');
    assert.strictEqual(W.parseWind('ZZZZ 200524Z 00000KT CAVOK').calm, true);
    assert.strictEqual(W.parseWind('ZZZZ 200524Z VRB03KT 9999').dir, null);
});

t('parseWind: nothing after RMK or a trend, nothing from junk', () => {
    assert.strictEqual(W.parseWind('ZZZZ 200524Z 9999 RMK 09012KT'), null);
    assert.strictEqual(W.parseWind('ZZZZ 200524Z 9999 BECMG 27006KT'), null);
    assert.strictEqual(W.parseWind(''), null);
    assert.strictEqual(W.parseWind(null), null);
});

t('runwayEnds: both ends of every runway', () => {
    assert.deepStrictEqual(W.runwayEnds(AP).map(e => [e.name, Math.round(e.track * 10) / 10]),
        [['16', 150.3], ['34', 330.3], ['09', 90], ['27', 270]]);
    assert.deepStrictEqual(W.runwayEnds(null), []);
});

t('components: as toganomon.py wind_components (ESR647, ANA267 cases)', () => {
    // 2026-11-21 RJFF: 220/6 on 16 -> HW 2 XW 6R; 290/10 on 34 -> HW 8 XW 6L
    assert.deepStrictEqual(W.components(W.parseWind('ZZZZ 1Z 22006KT'), 150.3), { head: 2, cross: 6, from: 'R' });
    assert.deepStrictEqual(W.components(W.parseWind('ZZZZ 1Z 29010KT'), 330.3), { head: 8, cross: 6, from: 'L' });
    assert.deepStrictEqual(W.components(W.parseWind('ZZZZ 1Z 33010KT'), 330), { head: 10, cross: 0, from: null });
    assert.strictEqual(W.components(W.parseWind('ZZZZ 1Z VRB03KT'), 150), null);
    assert.strictEqual(W.components(W.parseWind('ZZZZ 1Z 00000KT'), 150), null);
});

t('windLines: the runway in use, else the ends with a headwind', () => {
    const w = W.parseWind('ZZZZ 1Z 22006KT');
    assert.deepStrictEqual(W.windLines(w, AP, '34'), ['RWY34 TW 2 · XW 6L']);
    assert.deepStrictEqual(W.windLines(w, AP, null), ['RWY16 HW 2 · XW 6R', 'RWY27 HW 4 · XW 5L']);
    assert.deepStrictEqual(W.windLines(null, AP, '34'), []);
});

t('dirText / speedText', () => {
    assert.strictEqual(W.dirText(W.parseWind('Z 1Z 05010KT')), '050°');
    assert.strictEqual(W.dirText(W.parseWind('Z 1Z VRB03KT')), 'VRB');
    assert.strictEqual(W.dirText(W.parseWind('Z 1Z 00000KT')), 'CALM');
    assert.strictEqual(W.dirText(null), '---°');
    assert.strictEqual(W.speedText(W.parseWind('Z 1Z 29015G25KT')), '15G25 kt');
    assert.strictEqual(W.speedText(null), '-- kt');
});

t('clampPos: inside the view and below the header', () => {
    assert.deepStrictEqual(F.clampPos({ left: 2000, top: -5 }, 400, 600, 1400, 900, 50), { left: 1000, top: 50 });
    assert.deepStrictEqual(F.clampPos({ left: 10, top: 100 }, 400, 600, 1400, 900, 50), { left: 10, top: 100 });
});

t('mapUrl: tar1090 centred on the ARP', () => {
    assert.strictEqual(C.mapUrl(AP), '/tar1090/?zoom=10&lat=10.5000&lon=20.2500');
    assert.strictEqual(C.mapUrl(null, 9), '/tar1090/?zoom=9');
});

t('summaryView: tiles and a stale mark after 3 minutes', () => {
    const now = Date.UTC(2026, 10, 22, 0, 0, 0) / 1000;
    const s = { now, arrivals: 5, departures: 3, runway: '34', go_arounds: 1 };
    assert.deepStrictEqual(C.summaryView(s, now + 60, 'Asia/Tokyo'),
        { arrivals: '5', departures: '3', runway: '34', goArounds: '1', hot: true, updated: 'UPDATED 09:00', stale: false });
    const v = C.summaryView({ ...s, go_arounds: 0, runway: null }, now + 400, 'UTC');
    assert.deepStrictEqual([v.runway, v.hot, v.updated, v.stale], ['--', false, 'UPDATED 00:00 (7 MIN AGO)', true]);
    assert.strictEqual(C.summaryView({}, now, 'UTC'), null);
});

t('zoneLabel: abbreviation where one exists, else an offset', () => {
    assert.strictEqual(C.zoneLabel('Asia/Tokyo'), 'JST');
    assert.strictEqual(C.zoneLabel('UTC'), 'UTC');
    assert.match(C.zoneLabel('Asia/Seoul'), /^(KST|UTC\+9)$/);
});

if (failed) {
    console.log(`${failed} failed`);
    process.exit(1);
}
console.log('all passed');
