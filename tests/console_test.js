#!/usr/bin/env node
// Tests for the DOM-free parts of console/wind.js, console/stats.js,
// console/clock.js, console/float.js, console/movements.js and console/console.js. Run: node tests/console_test.js
'use strict';
const assert = require('assert');
const path = require('path');
const W = require(path.join(__dirname, '..', 'console', 'wind.js'));
const F = require(path.join(__dirname, '..', 'console', 'float.js'));
const C = require(path.join(__dirname, '..', 'console', 'console.js'));
const S = require(path.join(__dirname, '..', 'console', 'stats.js'));
const K = require(path.join(__dirname, '..', 'console', 'clock.js'));
const M = require(path.join(__dirname, '..', 'console', 'movements.js'));

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

t('parseWindToken: one wind group, as reported', () => {
    assert.deepStrictEqual(W.parseWindToken('36011KT'),
        { dir: 360, spd: 11, gust: null, unit: 'kt', vrb: false, calm: false, varFrom: null, varTo: null });
    assert.deepStrictEqual(W.parseWindToken('270105G130KT'),
        { dir: 270, spd: 105, gust: 130, unit: 'kt', vrb: false, calm: false, varFrom: null, varTo: null });
    assert.strictEqual(W.parseWindToken('21005MPS').unit, 'm/s');
    assert.strictEqual(W.parseWindToken('VRB00KT').calm, true);
    assert.strictEqual(W.dirText(W.parseWindToken('36011KT')), '360°');
    for (const junk of ['260V330', 'RJFF', '9999', '2901KT', '', null]) assert.strictEqual(W.parseWindToken(junk), null);
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

t('dirText / speedText / gustText / varText', () => {
    assert.strictEqual(W.dirText(W.parseWind('Z 1Z 05010KT')), '050°');
    assert.strictEqual(W.dirText(W.parseWind('Z 1Z VRB03KT')), 'VRB');
    assert.strictEqual(W.dirText(W.parseWind('Z 1Z 00000KT')), 'CALM');
    assert.strictEqual(W.dirText(null), '---°');
    assert.strictEqual(W.speedText(W.parseWind('Z 1Z 29015G25KT')), '15 kt');
    assert.strictEqual(W.gustText(W.parseWind('Z 1Z 29015G25KT')), 'GUST 25kt');
    assert.strictEqual(W.gustText(W.parseWind('Z 1Z 29015KT')), null);
    assert.strictEqual(W.varText(W.parseWind('Z 1Z 29008KT 260V330')), '260V330');
    assert.strictEqual(W.varText(W.parseWind('Z 1Z 29008KT')), null);
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

t('Stats.view: tiles and label/value rows, times in the given zone', () => {
    const v = S.view(SUM, NOW + 30, 'Asia/Tokyo');
    assert.deepStrictEqual([v.arrivals, v.departures, v.runway, v.goArounds, v.count, v.hot, v.gaLast],
        ['212', '193', '16', '3', 3, true, null]);
    assert.deepStrictEqual(v.rows, [
        { group: 'receiver', key: 'seen', label: 'AIRCRAFT SEEN', value: '187', level: null, sub: false },
        { group: 'receiver', key: 'withPos', label: 'WITH POSITION', value: '162', level: null, sub: true },
        { group: 'status', key: 'updated', label: 'UPDATED', value: '16:42', level: null, sub: false }]);
    assert.strictEqual(S.view(SUM, NOW, 'UTC').time, '07:42');
});

t('Stats.view: stale after 3 minutes', () => {
    assert.strictEqual(S.view(SUM, NOW + 180, 'UTC').stale, false);
    assert.strictEqual(S.view(SUM, NOW + 181, 'UTC').stale, true);
    assert.strictEqual(S.view(SUM, NOW + 181, 'UTC').rows.find(r => r.key === 'updated').level, 'alert');
});

t('Stats.view: no runway in use after an hour without movements', () => {
    assert.strictEqual(S.view({ ...SUM, runway_t: NOW - 3600 }, NOW, 'UTC').runway, '16');
    assert.strictEqual(S.view({ ...SUM, runway_t: NOW - 3601 }, NOW, 'UTC').runway, null);
    assert.strictEqual(S.view({ ...SUM, runway: null, runway_t: null }, NOW, 'UTC').runway, null);
});

t('Stats.view: last go-around date only when 24 h has none', () => {
    const hist = [{ t: NOW - 3 * 86400, flight: 'ANA267' }, { t: NOW - 5 * 86400, flight: 'JAL329' }];   // 11/10 16:42 JST
    const v = S.view({ ...SUM, go_arounds: 0 }, NOW, 'Asia/Tokyo', hist);
    assert.deepStrictEqual([v.goArounds, v.hot, v.gaLast], ['0', false, 'LAST 11/10']);
    assert.deepStrictEqual(v.rows[0], { group: 'airport', key: 'lastGa', label: 'LAST G/A', value: '11/10 16:42 ANA267', level: null, sub: false });
    assert.strictEqual(S.view({ ...SUM, go_arounds: 0 }, NOW, 'UTC', [{ t: NOW - 600, flight: null }]).rows[0].value, '11/13 07:32');
    assert.strictEqual(S.view({ ...SUM, go_arounds: 0 }, NOW, 'Asia/Tokyo', [...hist].reverse()).gaLast, 'LAST 11/10');
    assert.strictEqual(S.view({ ...SUM, go_arounds: 0 }, NOW, 'Asia/Tokyo', []).gaLast, null);
    assert.strictEqual(S.view(SUM, NOW, 'Asia/Tokyo', hist).gaLast, null);
    assert.strictEqual(S.view(SUM, NOW, 'Asia/Tokyo', hist).rows[0].key, 'seen');
});

t('Stats.view: since when, during the first window', () => {
    const v = S.view({ ...SUM, started: NOW - 3600 }, NOW, 'Asia/Tokyo');
    assert.strictEqual(v.since, '11/13 15:42');
    assert.deepStrictEqual(v.rows.map(r => [r.group, r.label, r.level]), [
        ['receiver', 'AIRCRAFT SEEN', null], ['receiver', 'WITH POSITION', null], ['status', 'SINCE', 'warn'], ['status', 'UPDATED', null]]);
});

t('Stats.view: older or partial summaries', () => {
    const v = S.view({ now: NOW, total: 5 }, NOW, 'UTC');
    assert.deepStrictEqual([v.arrivals, v.runway, v.goArounds, v.count], ['--', null, '--', null]);
    assert.strictEqual(v.rows[0].value, '5');
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

t('Clock.format: the given zone, whatever the machine zone', () => {
    // 2026-10-31 15:30:05Z: 00:30:05 JST on 11-01
    const d = new Date('2026-10-31T15:30:05Z');
    assert.strictEqual(K.format(d, 'Asia/Tokyo', true), '00:30:05');
    assert.strictEqual(K.format(d, 'Asia/Tokyo', false), '00:30');
    assert.strictEqual(K.format(d, 'UTC', true), '15:30:05');
    assert.strictEqual(K.format(new Date('2026-11-01T00:00:00Z'), 'UTC', false), '00:00');   // h23, not 24:00
    assert.strictEqual(K.format(new Date('2026-10-28T14:59:59Z'), 'Asia/Tokyo', true), '23:59:59');
});

t('Clock.zoneLabel: abbreviation where one exists, else an offset', () => {
    assert.strictEqual(K.zoneLabel('Asia/Tokyo'), 'JST');
    assert.strictEqual(K.zoneLabel('UTC'), 'UTC');
    assert.match(K.zoneLabel('Asia/Seoul'), /^(KST|UTC\+9)$/);
});

// 2026-11-24 07:04:29 UTC = 16:04:29 JST
const T = Date.UTC(2026, 10, 24, 7, 4, 29) / 1000;
const MV = { now: T + 600, window_s: 86400, movements: [
    { t: T, kind: 'arr', rwy: '16', hex: '888165', flight: 'HVN356', gs: 155, category: 'A3', reg: 'VN-A632', type: 'A321', ga: null },
    { t: T - 60, kind: 'dep', rwy: '16', hex: '8681b7', flight: null, gs: 150, category: 'A3', reg: null, type: null, ga: 'x-1' },
    { t: T - 120, kind: 'arr', rwy: '16', hex: '8681b7', flight: 'ANA240', gs: 140, category: 'A3', reg: 'JA73NR', type: 'B738', ga: 'x-1' },
    { t: 'bad', kind: 'arr' }, null,
] };

t('Movements.view: rows for a filter and the counts', () => {
    const all = M.view(MV, 'ALL');
    assert.deepStrictEqual(all.counts, { ALL: 3, ARR: 2, DEP: 1 });
    assert.deepStrictEqual(all.rows.map(m => m.t), [T, T - 60, T - 120]);
    assert.deepStrictEqual(M.view(MV, 'DEP').rows.map(m => m.kind), ['dep']);
    assert.deepStrictEqual(M.view(null, 'ARR'), { rows: [], counts: { ALL: 0, ARR: 0, DEP: 0 } });
});

t('Movements.cells: local time, direction, hex without a callsign', () => {
    assert.deepStrictEqual(M.cells(MV.movements[0], 'Asia/Tokyo'),
        { time: '16:04', dir: '↓', rwy: '16', flight: 'HVN356', type: 'A321', reg: 'VN-A632' });
    assert.deepStrictEqual(M.cells(MV.movements[1], 'UTC'),
        { time: '07:03', dir: '↑', rwy: '16', flight: '8681B7', type: '', reg: '' });
});

t('Movements.traceParams: the approach or the climb, inside the UTC day', () => {
    assert.deepStrictEqual(M.traceParams(MV.movements[0]),
        { icao: '888165', showTrace: '2026-11-24', startTime: '06:49:29', endTime: '07:07:29' });
    assert.deepStrictEqual(M.traceParams(MV.movements[1]),
        { icao: '8681b7', showTrace: '2026-11-24', startTime: '07:00:29', endTime: '07:18:29' });
    const early = { t: Date.UTC(2026, 10, 24, 0, 5, 0) / 1000, kind: 'arr', hex: 'ABC123' };
    assert.deepStrictEqual(M.traceParams(early),
        { icao: 'abc123', showTrace: '2026-11-24', startTime: '00:00:00', endTime: '00:08:00' });
    const late = { t: Date.UTC(2026, 10, 24, 23, 55, 0) / 1000, kind: 'dep', hex: 'abc123' };
    assert.strictEqual(M.traceParams(late).endTime, '23:59:59');
});

t('Movements.sameMovement and Console.traceUrl', () => {
    assert.strictEqual(M.sameMovement(MV.movements[1], { ...MV.movements[1] }), true);
    assert.strictEqual(M.sameMovement(MV.movements[1], MV.movements[2]), false);
    assert.strictEqual(M.sameMovement(null, MV.movements[2]), false);
    assert.strictEqual(C.traceUrl(M.traceParams(MV.movements[0])),
        '/tar1090/?icao=888165&showTrace=2026-11-24&startTime=06%3A49%3A29&endTime=07%3A07%3A29');
});

if (failed) {
    console.log(`${failed} failed`);
    process.exit(1);
}
console.log('all passed');
