// toganomon console: the RWY/WIND window. The airport's runways where they
// are (centre, length and true track from go-arounds.json's airport block),
// north up, in a rounded square; the METAR wind (true, as the tracks) as
// small chevrons drifting downwind (turning slowly round for VRB), its
// direction and speed on one line, and the head/cross components on the
// runways in use, the same way toganomon.py computes them for a go-around. A runway in use is blue for
// arrivals, green for departures, amber for both, with a small triangle
// along the direction of use.
// Wind.mount(element) builds the whole panel and returns
// { update({ metar, airport, runway }), pulse(end, kind, text), note(kind,
// text) }: pass any of
// the three as they come; runway is { arr: [...], dep: [...] } (Stats'
// onRunway), a designator, a list of them or null. pulse: something was
// recorded on that runway end (Wind.pulse has the kinds), shown once on the
// diagram with text (Movements.pulseOf) under it;
// note: the text only (the last event when the page opens).
// Everything but Wind.draw and Wind.mount is DOM-free (tests/console_test.js).
(function (root) {
    'use strict';

    // One wind group ("29015G25KT", "VRB03KT", "05010MPS") as
    // {dir, spd, gust, unit, vrb, calm, varFrom, varTo}, or null.
    // dir is as reported (360 stays 360; null for VRB); calm when the speed is 0.
    // Also used by airband's METAR / TAF decoders (one parser for both).
    function parseWindToken(token) {
        const m = typeof token === 'string' && token.match(/^(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?(KT|MPS)$/);
        if (!m) return null;
        const spd = +m[2];
        return {
            dir: m[1] === 'VRB' ? null : +m[1],
            spd, gust: m[3] ? +m[3] : null, unit: m[4] === 'KT' ? 'kt' : 'm/s',
            vrb: m[1] === 'VRB', calm: spd === 0, varFrom: null, varTo: null,
        };
    }

    // The wind of a raw METAR (its first wind group, with the variable
    // sector after it), or null; nothing from RMK or a trend on.
    function parseWind(metar) {
        if (typeof metar !== 'string') return null;
        let w = null;
        for (const t of metar.trim().split(/\s+/)) {
            if (t === 'RMK' || t === 'NOSIG' || t === 'BECMG' || t === 'TEMPO') break;
            let m;
            if (!w) {
                w = parseWindToken(t);
            } else if ((m = t.match(/^(\d{3})V(\d{3})$/))) {
                w.varFrom = +m[1];
                w.varTo = +m[2];
            }
        }
        return w;
    }

    // [{name, track}] for both ends of every runway (ends[1] flies track + 180)
    function runwayEnds(airport) {
        const out = [];
        for (const r of (airport && airport.runways) || []) {
            if (!Array.isArray(r.ends) || typeof r.true_track !== 'number') continue;
            out.push({ name: r.ends[0], track: r.true_track });
            out.push({ name: r.ends[1], track: (r.true_track + 180) % 360 });
        }
        return out;
    }

    // Head (+) / tail (-) and cross component of the steady wind on a track
    function components(wind, track) {
        if (!wind || wind.dir === null || wind.calm) return null;
        const a = (wind.dir - track) * Math.PI / 180;
        const cross = wind.spd * Math.sin(a);
        return {
            head: Math.round(wind.spd * Math.cos(a)), cross: Math.abs(Math.round(cross)),
            from: cross > 0.5 ? 'R' : cross < -0.5 ? 'L' : null,
        };
    }

    // "RWY34 HW 8 · XW 6L" (TW for a tailwind)
    function componentText(name, c) {
        if (!c) return null;
        const hw = c.head >= 0 ? `HW ${c.head}` : `TW ${-c.head}`;
        return `RWY${name} ${hw} · XW ${c.cross}${c.from || ''}`;
    }

    // runway: { arr, dep }, a designator, a list of them, or null -> a list
    function inUse(runway) {
        if (runway && typeof runway === 'object' && !Array.isArray(runway)) {
            const list = k => (Array.isArray(runway[k]) ? runway[k] : []);
            return [...new Set([...list('arr'), ...list('dep')])];
        }
        return Array.isArray(runway) ? runway : (typeof runway === 'string' && runway ? [runway] : []);
    }

    // 'arr' / 'dep' for an end in use for arrivals / departures only, 'both'
    // for both or when the kind is not known, null when not in use
    function useOf(runway, name) {
        if (!inUse(runway).includes(name)) return null;
        if (!runway || typeof runway !== 'object' || Array.isArray(runway)) return 'both';
        const a = Array.isArray(runway.arr) && runway.arr.includes(name);
        const d = Array.isArray(runway.dep) && runway.dep.includes(name);
        return a && !d ? 'arr' : d && !a ? 'dep' : 'both';
    }

    // point at bearing deg (clockwise from north) and radius r from the centre
    function at(deg, r) {
        const a = deg * Math.PI / 180;
        return [Math.sin(a) * r, -Math.cos(a) * r];
    }

    // The runways on the diagram (half size r): where they are, scaled so
    // that the airport fits within r - LAYOUT_MARGIN of the middle, north up; through the middle at
    // their true tracks when a runway has no centre. -> { runways: [{ ends,
    // a, b }], ends: { name: { thr, far, track, label } } }: a / b the ends[0]
    // / ends[1] thresholds; per end its threshold, the far end, the track
    // flown and its name's place just beyond the threshold, names pushed
    // apart to LABEL_GAP where two meet (RJTT: 22 next to 16L).
    const LAYOUT_MARGIN = 15;
    const LABEL_OUT = 8;
    const LABEL_GAP = 13;
    function layout(airport, r) {
        const rws = ((airport && airport.runways) || [])
            .filter(x => Array.isArray(x.ends) && x.ends.length === 2 && typeof x.true_track === 'number');
        const geo = rws.length && rws.every(x => Array.isArray(x.center) && x.center.length === 2
            && x.center.every(v => typeof v === 'number'));
        const kx = geo ? 111.32 * Math.cos(rws[0].center[0] * Math.PI / 180) : 0, ky = 110.57;
        const segs = rws.map(x => {
            if (!geo) return { x, a: at(x.true_track + 180, r - 22), b: at(x.true_track, r - 22) };
            const c = [x.center[1] * kx, -x.center[0] * ky];            // km, x east, y south
            const h = (typeof x.length_m === 'number' && x.length_m > 0 ? x.length_m : 3000) / 2000;
            const [ux, uy] = at(x.true_track, h);
            return { x, a: [c[0] - ux, c[1] - uy], b: [c[0] + ux, c[1] + uy] };
        });
        if (geo) {
            const pts = segs.flatMap(g => [g.a, g.b]);
            const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
            const mx = (Math.min(...xs) + Math.max(...xs)) / 2, my = (Math.min(...ys) + Math.max(...ys)) / 2;
            const ext = Math.max(...pts.map(([x, y]) => Math.hypot(x - mx, y - my))) || 1;
            const k = (r - LAYOUT_MARGIN) / ext;
            for (const p of pts) {
                p[0] = (p[0] - mx) * k;
                p[1] = (p[1] - my) * k;
            }
        }
        const ends = {};
        for (const { x, a, b } of segs) {
            const t0 = x.true_track % 360, t1 = (x.true_track + 180) % 360;
            const [ox, oy] = at(t0, LABEL_OUT);
            ends[x.ends[0]] = { thr: a, far: b, track: t0, label: [a[0] - ox, a[1] - oy] };
            ends[x.ends[1]] = { thr: b, far: a, track: t1, label: [b[0] + ox, b[1] + oy] };
        }
        const labels = Object.values(ends).map(e => e.label);
        for (let it = 0; it < 20; it++) {
            for (let i = 0; i < labels.length; i++) {
                for (let j = i + 1; j < labels.length; j++) {
                    const p = labels[i], q = labels[j];
                    let dx = q[0] - p[0], dy = q[1] - p[1], d = Math.hypot(dx, dy);
                    if (d >= LABEL_GAP) continue;
                    if (d < 0.01) { dx = 1; dy = 0; d = 1; }
                    const m = (LABEL_GAP - d) / 2 / d;
                    p[0] -= dx * m; p[1] -= dy * m;
                    q[0] += dx * m; q[1] += dy * m;
                }
            }
        }
        return { runways: segs.map(g => ({ ends: g.x.ends, a: g.a, b: g.b })), ends };
    }

    // One cycle of the drifting chevrons across the diagram, in seconds:
    // faster in a stronger wind (14 kt about 2.2 s, 3 kt about 5.7 s)
    function flowSeconds(wind) {
        const kt = wind.unit === 'm/s' ? wind.spd * 1.944 : wind.spd;
        return 40 / (kt + 4);
    }

    // Lines under the dial: the runways in use, in their order; without one,
    // every end with a headwind (the ends the wind favours).
    function windLines(wind, airport, runway) {
        const ends = runwayEnds(airport);
        const pick = inUse(runway).map(n => ends.find(e => e.name === n)).filter(Boolean);
        const list = pick.length ? pick
            : ends.filter(e => { const c = components(wind, e.track); return c && c.head >= 0; });
        return list.map(e => componentText(e.name, components(wind, e.track))).filter(Boolean);
    }

    function dirText(w) {
        if (!w) return '---°';
        if (w.calm) return 'CALM';
        if (w.vrb) return 'VRB';
        return String(w.dir).padStart(3, '0') + '°';
    }

    function speedText(w) {
        if (!w) return '-- kt';
        if (w.calm) return `0 ${w.unit}`;
        return `${w.spd} ${w.unit}`;
    }

    function gustText(w) {
        return w && !w.calm && w.gust !== null ? `GUST ${w.gust}${w.unit}` : null;
    }

    function varText(w) {
        return w && w.varFrom !== null
            ? `${String(w.varFrom).padStart(3, '0')}V${String(w.varTo).padStart(3, '0')}` : null;
    }

    const Wind = {
        parseWindToken, parseWind, runwayEnds, components, componentText, windLines, dirText, speedText,
        gustText, varText, inUse, useOf, layout, flowSeconds,
    };
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = Wind;
        return;
    }
    root.Wind = Wind;

    // ---------- SVG diagram ----------
    const NS = 'http://www.w3.org/2000/svg';
    const R = 60;                   // the runways' scale (layout)
    const H = R + 2;                // half the rounded square
    const FLOW = 88;                // the chevrons' travel, corner to corner
    const PLANE = 'M 0 -5 L 1 -1 L 5 1 L 5 2 L 1 1 L 0.6 4 L 2 5 L -2 5 L -0.6 4 L -1 1 L -5 2 L -5 1 L -1 -1 Z';
    let clipSeq = 0;

    function el(name, attrs, text) {
        const e = document.createElementNS(NS, name);
        for (const k in attrs) e.setAttribute(k, attrs[k]);
        if (text !== undefined) e.textContent = text;
        return e;
    }

    function reducedMotion() {
        return typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
    }

    const fix = v => v.toFixed(1);

    // a clip path of the square's inside; -> 'url(#id)'
    function squareClip(svg) {
        const id = 'wind-clip-' + (++clipSeq);
        const cp = el('clipPath', { id });
        cp.appendChild(el('rect', { x: -H + 1, y: -H + 1, width: 2 * H - 2, height: 2 * H - 2, rx: 9 }));
        svg.appendChild(cp);
        return `url(#${id})`;
    }

    // Small chevrons drifting downwind, nine lanes, under the runways; still
    // (spread over the square) for reduced motion. A variable wind (VRB):
    // the whole drift turns slowly round and drifts at half the speed, so it
    // is calm to look at (none for reduced motion, as a still one would show
    // a direction).
    const VRB_TURN_S = 120;
    const VRB_SLOWER = 2;
    function drawFlow(svg, wind, clip) {
        const still = reducedMotion();
        if (wind.vrb && still) return;
        const flow = el('g', { 'clip-path': clip, class: 'wind-flow' + (wind.vrb ? ' vrb' : '') });
        const turn = el('g', {});
        if (wind.vrb) {
            turn.appendChild(el('animateTransform', {
                attributeName: 'transform', type: 'rotate', from: '0', to: '360', dur: `${VRB_TURN_S}s`, repeatCount: 'indefinite',
            }));
        }
        const g = el('g', { transform: `rotate(${wind.vrb ? 0 : wind.dir})` });
        const dur = flowSeconds(wind) * (wind.vrb ? VRB_SLOWER : 1), PER = 3;
        [-80, -60, -40, -20, 0, 20, 40, 60, 80].forEach((x, lane) => {
            for (let i = 0; i < PER; i++) {
                const c = el('path', { d: `M ${x - 4} -3 L ${x} 2 L ${x + 4} -3` });
                const phase = (i + (lane % 2) * 0.5) / PER;     // lanes out of step
                if (still) {
                    c.setAttribute('transform', `translate(0 ${fix(-FLOW + phase * 2 * FLOW)})`);
                } else {
                    c.appendChild(el('animateTransform', {
                        attributeName: 'transform', type: 'translate', from: `0 ${-FLOW}`, to: `0 ${FLOW}`,
                        dur: `${dur.toFixed(2)}s`, begin: `${(-phase * dur).toFixed(2)}s`, repeatCount: 'indefinite',
                    }));
                }
                g.appendChild(c);
            }
        });
        turn.appendChild(g);
        flow.appendChild(turn);
        svg.appendChild(flow);
    }

    // Draw into an <svg> (viewBox Wind.VIEWBOX): the square, the variable
    // sector, the wind, the runways. Returns the layout (for Wind.pulse).
    Wind.VIEWBOX = '-62.75 -62.75 125.5 125.5';
    Wind.draw = function (svg, wind, airport, runway) {
        svg.textContent = '';
        svg.appendChild(el('rect', { x: -H, y: -H, width: 2 * H, height: 2 * H, rx: 10, class: 'wind-dial' }));
        const clip = squareClip(svg);
        if (wind && wind.varFrom !== null) {
            // the variable sector: a faint wedge from the middle
            const RR = 90, [x1, y1] = at(wind.varFrom, RR), [x2, y2] = at(wind.varTo, RR);
            const sweep = ((wind.varTo - wind.varFrom + 360) % 360) > 180 ? 1 : 0;
            svg.appendChild(el('path', {
                d: `M 0 0 L ${fix(x1)} ${fix(y1)} A ${RR} ${RR} 0 ${sweep} 1 ${fix(x2)} ${fix(y2)} Z`,
                class: 'wind-var', 'clip-path': clip,
            }));
        }
        if (wind && !wind.calm && (wind.dir !== null || wind.vrb)) drawFlow(svg, wind, clip);
        const lay = layout(airport, R);
        for (const { a, b } of lay.runways) {
            svg.appendChild(el('line', { x1: fix(a[0]), y1: fix(a[1]), x2: fix(b[0]), y2: fix(b[1]), class: 'wind-rwy' }));
        }
        // a small triangle along the direction of use: an arrival's at its
        // threshold, a departure's at the far end
        for (const [name, e] of Object.entries(lay.ends)) {
            const use = useOf(runway, name);
            if (!use) continue;
            const marks = use === 'arr' ? [[e.thr, 'arr']] : use === 'dep' ? [[e.far, 'dep']]
                : [[e.thr, 'both'], [e.far, 'both']];
            for (const [p, cls] of marks) {
                svg.appendChild(el('path', {
                    d: 'M 0 -4 L 3 2 L -3 2 Z', class: 'wind-dir-mark ' + cls,
                    transform: `translate(${fix(p[0])} ${fix(p[1])}) rotate(${e.track})`,
                }));
            }
        }
        // a runway is named after the track flown from its threshold
        for (const [name, e] of Object.entries(lay.ends)) {
            const use = useOf(runway, name);
            svg.appendChild(el('text', {
                x: fix(e.label[0]), y: fix(e.label[1]),
                class: 'wind-rwy-name' + (use ? ' in-use ' + use : ''),
            }, name));
        }
        return lay;
    };

    // One recorded event on a diagram drawn with lay, shown once. kind:
    //   arr     a small aircraft comes down to the threshold (what is
    //           recorded is the gate pass, not the touchdown) - blue
    //   dep     it rolls and climbs away beyond the far end - green
    //   ga      it comes down, carries on low along the runway and climbs
    //           away beyond the far end - amber
    //   missed  given up kilometres out, off this scale: no aircraft, the
    //           final before the threshold blinks - amber
    // The runway glows; a climbing aircraft grows (nearer the viewer).
    // Reduced motion: the glow (and the final) only.
    const PULSE_S = { arr: 2.6, dep: 2.6, ga: 4.2, missed: 4.2 };
    const PULSE_PATH = {          // [units from the threshold, size] ...
        arr: [[-22, 1], [-8, 0.9], [0, 0.8]],
        dep: [[2, 0.8], [L => L * 0.7, 0.8], [L => L + 18, 1.3]],
        ga: [[-22, 1], [0, 0.8], [L => L * 0.6, 0.8], [L => L + 22, 1.5]],
    };
    const PULSE_TIMES = { arr: '0;0.6;1', dep: '0;0.6;1', ga: '0;0.3;0.6;1' };
    Wind.pulse = function (svg, lay, end, kind) {
        const e = lay && lay.ends[end];
        if (!e || !PULSE_S[kind]) return;
        const dur = PULSE_S[kind], cls = kind === 'missed' ? 'ga' : kind;
        const len = Math.hypot(e.far[0] - e.thr[0], e.far[1] - e.thr[1]) || 1;
        const u = [(e.far[0] - e.thr[0]) / len, (e.far[1] - e.thr[1]) / len];
        const along = k => [e.thr[0] + u[0] * k, e.thr[1] + u[1] * k];
        const still = reducedMotion();
        const anims = [], added = [];
        const animate = (parent, name, attrs) => {
            const a = el(name, Object.assign({ begin: 'indefinite', dur: dur + 's', fill: 'freeze' }, attrs));
            parent.appendChild(a);
            anims.push(a);
        };
        const under = svg.querySelector('.wind-dir-mark, .wind-rwy-name');
        const glow = el('line', {
            x1: fix(e.thr[0]), y1: fix(e.thr[1]), x2: fix(e.far[0]), y2: fix(e.far[1]), class: 'wind-rwy-glow ' + cls,
        });
        svg.insertBefore(glow, under);
        added.push(glow);
        animate(glow, 'animate', { attributeName: 'opacity', values: '0.9;0.9;0', keyTimes: '0;0.6;1' });
        if (kind === 'missed') {
            const out = along(-26);
            const fin = el('line', { x1: fix(out[0]), y1: fix(out[1]), x2: fix(e.thr[0]), y2: fix(e.thr[1]), class: 'wind-final' });
            svg.insertBefore(fin, under);
            added.push(fin);
            animate(fin, 'animate', still
                ? { attributeName: 'opacity', values: '0.9;0.9;0', keyTimes: '0;0.7;1' }
                : { attributeName: 'opacity', values: '0;0.9;0.2;0.9;0.2;0.9;0', keyTimes: '0;0.12;0.3;0.45;0.6;0.75;1' });
        } else if (!still) {
            const path = PULSE_PATH[kind].map(([k, z]) => [along(typeof k === 'function' ? k(len) : k), z]);
            const plane = el('g', { class: 'wind-plane ' + cls, opacity: 0 });
            const size = el('g', {});
            size.appendChild(el('path', { d: PLANE, transform: `rotate(${e.track})` }));
            plane.appendChild(size);
            svg.appendChild(plane);
            added.push(plane);
            animate(plane, 'animateTransform', {
                attributeName: 'transform', type: 'translate', keyTimes: PULSE_TIMES[kind],
                values: path.map(([p]) => `${fix(p[0])} ${fix(p[1])}`).join(';'),
            });
            animate(size, 'animateTransform', {
                attributeName: 'transform', type: 'scale', keyTimes: PULSE_TIMES[kind], values: path.map(([, z]) => z).join(';'),
            });
            animate(plane, 'animate', { attributeName: 'opacity', values: '0;1;1;0', keyTimes: '0;0.1;0.85;1' });
        }
        for (const a of anims) if (typeof a.beginElement === 'function') a.beginElement();
        setTimeout(() => added.forEach(x => x.remove()), dur * 1000 + 200);
    };

    // The RWY/WIND panel: the diagram, direction and speed, gust badge,
    // variable sector, head/cross components, and the text of the last event,
    // kept until the next one (what happened last, at a glance).
    // Styles: wind.css.
    const EVENT_KINDS = ['arr', 'dep', 'ga', 'missed'];
    Wind.mount = function (container) {
        const div = (cls) => {
            const d = document.createElement('div');
            d.className = cls;
            return d;
        };
        container.classList.add('wind-panel');
        const svg = el('svg', { class: 'wind-svg', viewBox: Wind.VIEWBOX, role: 'img', 'aria-label': 'runways and wind' });
        const head = div('wind-head'), dir = div('wind-dir'), spd = div('wind-spd');
        head.append(dir, spd);
        const gust = div('wind-gust-line'), vr = div('wind-var-text'), comp = div('wind-comp');
        // two lines kept free, so that the window does not change its size
        const ev = div('wind-event');
        ev.setAttribute('aria-live', 'polite');
        container.replaceChildren(svg, head, gust, vr, comp, ev);
        const st = { metar: null, airport: null, runway: null };
        let lay = null;

        function render() {
            const w = parseWind(st.metar);
            lay = Wind.draw(svg, w, st.airport, st.runway);
            dir.textContent = dirText(w);
            spd.textContent = speedText(w);
            gust.replaceChildren();
            const g = gustText(w);
            if (g) {
                const b = document.createElement('span');
                b.className = 'wind-gust-badge';
                b.textContent = g;
                gust.append(b);
            }
            gust.hidden = !g;
            vr.textContent = varText(w) || '';
            vr.hidden = !varText(w);
            comp.textContent = windLines(w, st.airport, st.runway).join('\n');
        }

        function note(kind, text) {
            if (!text || !EVENT_KINDS.includes(kind)) return;
            ev.textContent = text;
            ev.className = 'wind-event on ' + (kind === 'missed' ? 'ga' : kind);
        }

        render();
        return {
            pulse(end, kind, text) {
                Wind.pulse(svg, lay, end, kind);
                note(kind, text);
            },
            note,
            update(part) {
                let changed = false;
                for (const k of ['metar', 'airport', 'runway']) {
                    if (part && k in part && part[k] !== st[k]) {
                        st[k] = part[k];
                        changed = true;
                    }
                }
                if (changed) render();
            },
        };
    };
})(typeof window !== 'undefined' ? window : globalThis);
