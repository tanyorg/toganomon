#!/usr/bin/env node
// Tests for the DOM-free parts of console/wind.js, console/stats.js,
// console/float.js and console/console.js. Run: node tests/console_test.js
'use strict';
const assert = require('assert');
const path = require('path');
const W = require(path.join(__dirname, '..', 'console', 'wind.js'));
const F = require(path.join(__dirname, '..', 'console', 'float.js'));
const C = require(path.join(__dirname, '..', 'console', 'console.js'));
const S = require(path.join(__dirname, '..', 'console', 'stats.js'));

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

// 2026-11-13 07:42:00Z = 16:42 JST
const NOW = Date.UTC(2026, 10, 13, 7, 42, 0) / 1000;
const SUM = { now: NOW, window_s: 86400, total: 187, with_pos: 162, started: NOW - 2 * 86400,
    arrivals: 212, departures: 193, runway: '16', runway_t: NOW - 120, go_arounds: 3 };

t('Stats.view: tiles, aircraft line, update time in the given zone', () => {
    const v = S.view(SUM, NOW + 30, 'Asia/Tokyo');
    assert.deepStrictEqual(v, {
        arrivals: '212', departures: '193', runway: '16', goArounds: '3', count: 3, hot: true, gaLast: null,
        aircraft: '187 AIRCRAFT · POS 162', time: '16:42', stale: false, since: null });
    assert.strictEqual(S.view(SUM, NOW, 'UTC').time, '07:42');
});

t('Stats.view: stale after 3 minutes', () => {
    assert.strictEqual(S.view(SUM, NOW + 180, 'UTC').stale, false);
    assert.strictEqual(S.view(SUM, NOW + 181, 'UTC').stale, true);
});

t('Stats.view: no runway in use after an hour without movements', () => {
    assert.strictEqual(S.view({ ...SUM, runway_t: NOW - 3600 }, NOW, 'UTC').runway, '16');
    assert.strictEqual(S.view({ ...SUM, runway_t: NOW - 3601 }, NOW, 'UTC').runway, null);
    assert.strictEqual(S.view({ ...SUM, runway: null, runway_t: null }, NOW, 'UTC').runway, null);
});

t('Stats.view: last go-around date only when 24 h has none', () => {
    const hist = [{ t: NOW - 3 * 86400 }, { t: NOW - 5 * 86400 }];   // 11/10 16:42 JST
    const v = S.view({ ...SUM, go_arounds: 0 }, NOW, 'Asia/Tokyo', hist);
    assert.deepStrictEqual([v.goArounds, v.hot, v.gaLast], ['0', false, 'LAST 11/10']);
    assert.strictEqual(S.view({ ...SUM, go_arounds: 0 }, NOW, 'Asia/Tokyo', [...hist].reverse()).gaLast, 'LAST 11/10');
    assert.strictEqual(S.view({ ...SUM, go_arounds: 0 }, NOW, 'Asia/Tokyo', []).gaLast, null);
    assert.strictEqual(S.view(SUM, NOW, 'Asia/Tokyo', hist).gaLast, null);
});

t('Stats.view: since when, during the first window', () => {
    assert.strictEqual(S.view({ ...SUM, started: NOW - 3600 }, NOW, 'Asia/Tokyo').since, '11/13 15:42');
});

t('Stats.view: older or partial summaries', () => {
    const v = S.view({ now: NOW, total: 5 }, NOW, 'UTC');
    assert.deepStrictEqual([v.arrivals, v.runway, v.goArounds, v.count, v.aircraft], ['--', null, '--', null, '5 AIRCRAFT']);
    assert.strictEqual(S.view(null, NOW, 'UTC'), null);
    assert.strictEqual(S.view({ total: 1 }, NOW, 'UTC'), null);
});

t('Stats.increased: only a rise after the first poll', () => {
    assert.strictEqual(S.increased(null, 3), false);
    assert.strictEqual(S.increased(2, 3), true);
    assert.strictEqual(S.increased(3, 3), false);
    assert.strictEqual(S.increased(3, 2), false);     // one left the 24 h window
    assert.strictEqual(S.increased(2, undefined), false);
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
