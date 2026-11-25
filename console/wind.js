// toganomon console: the WIND window. The METAR wind over a compass with the
// airport's runways where they are (centre, length and true track from
// go-arounds.json's airport block; METAR wind is true as well), small
// chevrons drifting downwind, and the head/cross components on the runways
// in use, the same way toganomon.py computes them for a go-around. A runway
// in use is blue for arrivals, green for departures, amber for both, with a
// small triangle along the direction of use.
// Wind.mount(element) builds the whole panel (dial and text) and returns
// { update({ metar, airport, runway }), pulse(end, kind) }: pass any of the
// three as they come; runway is { arr: [...], dep: [...] } (Stats'
// onRunway), a designator, a list of them or null. pulse: a movement was
// recorded ('arr' or 'dep' on that runway end); a small aircraft lands or
// takes off there once.
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

    // The runways on the dial (radius r): where they are, scaled so that the
    // airport fits within r - LAYOUT_MARGIN, north up; through the middle at
    // their true tracks when a runway has no centre. -> { runways: [{ ends,
    // a, b }], ends: { name: { thr, far, track, label } } }: a / b the ends[0]
    // / ends[1] thresholds; per end its threshold, the far end, the track
    // flown and its name's place just beyond the threshold, names pushed
    // apart to LABEL_GAP where two meet (RJTT: 22 next to 16L).
    const LAYOUT_MARGIN = 26;
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

    // One cycle of the drifting chevrons across the dial, in seconds:
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

    // ---------- SVG dial ----------
    const NS = 'http://www.w3.org/2000/svg';
    const R = 60;
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

    // Small chevrons drifting downwind, five lanes, under the runways; still
    // (spread over the dial) for reduced motion.
    function drawFlow(svg, wind) {
        const id = 'wind-clip-' + (++clipSeq);
        const cp = el('clipPath', { id });
        cp.appendChild(el('circle', { r: R - 2 }));
        svg.appendChild(cp);
        const flow = el('g', { 'clip-path': `url(#${id})`, class: 'wind-flow' });
        const g = el('g', { transform: `rotate(${wind.dir})` });
        const dur = flowSeconds(wind), still = reducedMotion(), PER = 3;
        [-40, -20, 0, 20, 40].forEach((x, lane) => {
            for (let i = 0; i < PER; i++) {
                const c = el('path', { d: `M ${x - 4} -3 L ${x} 2 L ${x + 4} -3` });
                const phase = (i + (lane % 2) * 0.5) / PER;     // lanes out of step
                if (still) {
                    c.setAttribute('transform', `translate(0 ${fix(-R + phase * 2 * R)})`);
                } else {
                    c.appendChild(el('animateTransform', {
                        attributeName: 'transform', type: 'translate', from: `0 ${-R}`, to: `0 ${R}`,
                        dur: `${dur.toFixed(2)}s`, begin: `${(-phase * dur).toFixed(2)}s`, repeatCount: 'indefinite',
                    }));
                }
                g.appendChild(c);
            }
        });
        flow.appendChild(g);
        svg.appendChild(flow);
    }

    // Draw into an <svg> (viewBox -70 -70 140 140): dial, wind, runways.
    // Returns the layout (Wind.mount's pulse uses it).
    Wind.draw = function (svg, wind, airport, runway) {
        svg.textContent = '';
        svg.appendChild(el('circle', { r: R, class: 'wind-dial' }));
        for (const [d, l] of [[0, 'N'], [90, 'E'], [180, 'S'], [270, 'W']]) {
            const [x, y] = at(d, R - 9);
            svg.appendChild(el('text', { x, y, class: 'wind-cardinal' }, l));
        }
        if (wind && wind.varFrom !== null) {
            // variable sector, on the rim
            const [x1, y1] = at(wind.varFrom, R), [x2, y2] = at(wind.varTo, R);
            const sweep = ((wind.varTo - wind.varFrom + 360) % 360) > 180 ? 1 : 0;
            svg.appendChild(el('path', { d: `M ${x1} ${y1} A ${R} ${R} 0 ${sweep} 1 ${x2} ${y2}`, class: 'wind-var' }));
        }
        if (wind && wind.dir !== null && !wind.calm) drawFlow(svg, wind);
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

    // One recorded movement on the dial drawn with lay: the runway glows, a
    // small aircraft comes in over the threshold and slows down (arr) or
    // rolls and climbs away beyond the far end (dep); the glow only for
    // reduced motion.
    const PULSE_S = 2.6;
    Wind.pulse = function (svg, lay, end, kind) {
        const e = lay && lay.ends[end];
        if (!e || (kind !== 'arr' && kind !== 'dep')) return;
        const len = Math.hypot(e.far[0] - e.thr[0], e.far[1] - e.thr[1]) || 1;
        const u = [(e.far[0] - e.thr[0]) / len, (e.far[1] - e.thr[1]) / len];
        const along = (p, k) => [p[0] + u[0] * k, p[1] + u[1] * k];
        const anims = [];
        const animate = (parent, name, attrs) => {
            const a = el(name, Object.assign({ begin: 'indefinite', dur: PULSE_S + 's', fill: 'freeze' }, attrs));
            parent.appendChild(a);
            anims.push(a);
        };
        const glow = el('line', {
            x1: fix(e.thr[0]), y1: fix(e.thr[1]), x2: fix(e.far[0]), y2: fix(e.far[1]), class: 'wind-rwy-glow ' + kind,
        });
        svg.insertBefore(glow, svg.querySelector('.wind-dir-mark, .wind-rwy-name'));
        animate(glow, 'animate', { attributeName: 'opacity', values: '0.9;0.9;0', keyTimes: '0;0.5;1' });
        let plane = null;
        if (!reducedMotion()) {
            const [from, to, spline] = kind === 'arr'
                ? [along(e.thr, -18), along(e.thr, len * 0.45), '0 0 0.4 1']
                : [along(e.thr, 2), along(e.far, 16), '0.6 0 1 1'];
            plane = el('g', { class: 'wind-plane ' + kind, opacity: 0 });
            plane.appendChild(el('path', { d: PLANE, transform: `rotate(${e.track})` }));
            svg.appendChild(plane);
            animate(plane, 'animateTransform', {
                attributeName: 'transform', type: 'translate', keyTimes: '0;1', calcMode: 'spline', keySplines: spline,
                values: `${fix(from[0])} ${fix(from[1])};${fix(to[0])} ${fix(to[1])}`,
            });
            animate(plane, 'animate', { attributeName: 'opacity', values: '0;1;1;0', keyTimes: '0;0.12;0.8;1' });
        }
        for (const a of anims) if (typeof a.beginElement === 'function') a.beginElement();
        setTimeout(() => {
            glow.remove();
            if (plane) plane.remove();
        }, PULSE_S * 1000 + 200);
    };

    // The WIND panel: dial, direction, speed, gust badge, variable sector,
    // head/cross components. Styles: wind.css.
    Wind.mount = function (container) {
        const div = (cls) => {
            const d = document.createElement('div');
            d.className = cls;
            return d;
        };
        container.classList.add('wind-panel');
        const svg = el('svg', { class: 'wind-svg', viewBox: '-70 -70 140 140', role: 'img', 'aria-label': 'wind and runways' });
        const dir = div('wind-dir'), spd = div('wind-spd'), gust = div('wind-gust-line'), vr = div('wind-var-text'), comp = div('wind-comp');
        container.replaceChildren(svg, dir, spd, gust, vr, comp);
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

        render();
        return {
            pulse(end, kind) { Wind.pulse(svg, lay, end, kind); },
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
