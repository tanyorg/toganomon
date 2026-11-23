// toganomon console: the WIND window. The METAR wind over a compass with the
// airport's runways (true tracks from go-arounds.json's airport block; METAR
// wind is true as well), and the head/cross components on the runway in use,
// the same way toganomon.py computes them for a go-around.
// Wind.mount(element) builds the whole panel (dial and text) and returns
// { update({ metar, airport, runway }) }: pass any of the three as they come.
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

    // Lines under the dial: runway in use first; without one, every end
    // with a headwind (the ends the wind favours).
    function windLines(wind, airport, runway) {
        const ends = runwayEnds(airport);
        const pick = ends.filter(e => e.name === runway);
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

    const Wind = { parseWindToken, parseWind, runwayEnds, components, componentText, windLines, dirText, speedText, gustText, varText };
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = Wind;
        return;
    }
    root.Wind = Wind;

    // ---------- SVG dial ----------
    const NS = 'http://www.w3.org/2000/svg';
    const R = 60;

    function el(name, attrs, text) {
        const e = document.createElementNS(NS, name);
        for (const k in attrs) e.setAttribute(k, attrs[k]);
        if (text !== undefined) e.textContent = text;
        return e;
    }

    // point at bearing deg (clockwise from north) and radius r from the centre
    function at(deg, r) {
        const a = deg * Math.PI / 180;
        return [Math.sin(a) * r, -Math.cos(a) * r];
    }

    // Draw into an <svg> (viewBox -70 -70 140 140): dial, runways, arrow.
    Wind.draw = function (svg, wind, airport, runway) {
        svg.textContent = '';
        svg.appendChild(el('circle', { r: R, class: 'wind-dial' }));
        for (const [d, l] of [[0, 'N'], [90, 'E'], [180, 'S'], [270, 'W']]) {
            const [x, y] = at(d, R - 9);
            svg.appendChild(el('text', { x, y, class: 'wind-cardinal' }, l));
        }
        for (const r of (airport && airport.runways) || []) {
            if (typeof r.true_track !== 'number' || !Array.isArray(r.ends)) continue;
            const [x1, y1] = at(r.true_track + 180, R - 22), [x2, y2] = at(r.true_track, R - 22);
            svg.appendChild(el('line', { x1, y1, x2, y2, class: 'wind-rwy' }));
            // a runway is named after the track flown from its threshold
            const [ax, ay] = at(r.true_track + 180, R - 14), [bx, by] = at(r.true_track, R - 14);
            svg.appendChild(el('text', { x: ax, y: ay, class: 'wind-rwy-name' + (r.ends[0] === runway ? ' in-use' : '') }, r.ends[0]));
            svg.appendChild(el('text', { x: bx, y: by, class: 'wind-rwy-name' + (r.ends[1] === runway ? ' in-use' : '') }, r.ends[1]));
        }
        if (wind && wind.varFrom !== null) {
            // variable sector, on the rim
            const [x1, y1] = at(wind.varFrom, R), [x2, y2] = at(wind.varTo, R);
            const sweep = ((wind.varTo - wind.varFrom + 360) % 360) > 180 ? 1 : 0;
            svg.appendChild(el('path', { d: `M ${x1} ${y1} A ${R} ${R} 0 ${sweep} 1 ${x2} ${y2}`, class: 'wind-var' }));
        }
        if (wind && wind.dir !== null && !wind.calm) {
            // the arrow blows from the rim at wind.dir towards the centre and on
            const g = el('g', { transform: `rotate(${wind.dir})`, class: 'wind-arrow' });
            g.appendChild(el('line', { x1: 0, y1: -R + 4, x2: 0, y2: 22 }));
            g.appendChild(el('path', { d: 'M -8 14 L 0 30 L 8 14 Z' }));
            svg.appendChild(g);
        }
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

        function render() {
            const w = parseWind(st.metar);
            Wind.draw(svg, w, st.airport, st.runway);
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
