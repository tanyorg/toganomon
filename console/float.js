// toganomon console: floating windows over the map. A header button opens
// and closes each window, x closes it, its title bar drags it; open/closed
// and the position are kept in localStorage (key 'toganomon.console'), so a
// browser without storage just starts from the defaults each time.
// clampPos does not touch the DOM (tests/console_test.js).
(function (root) {
    'use strict';

    const STORE_KEY = 'toganomon.console';
    const MARGIN = 12;

    // Keep a window of w x h inside a view of viewW x viewH (top >= minTop).
    function clampPos(pos, w, h, viewW, viewH, minTop) {
        const top0 = minTop || 0;
        return {
            left: Math.max(0, Math.min(pos.left, viewW - w)),
            top: Math.max(top0, Math.min(pos.top, viewH - h)),
        };
    }

    const Float = { clampPos, STORE_KEY };
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = Float;
        return;
    }
    root.Float = Float;

    function load() {
        try {
            const s = JSON.parse(localStorage.getItem(STORE_KEY));
            return s && typeof s === 'object' ? s : {};
        } catch (e) {
            return {};
        }
    }

    function save(state) {
        try {
            localStorage.setItem(STORE_KEY, JSON.stringify(state));
        } catch (e) { /* works without storage */ }
    }

    const state = load();

    // The header's bottom edge: windows never go under it.
    function minTop() {
        const h = document.querySelector('header');
        return h ? h.getBoundingClientRect().bottom : 0;
    }

    function place(el, left, top) {
        const p = clampPos({ left, top }, el.offsetWidth, el.offsetHeight, window.innerWidth, window.innerHeight, minTop());
        el.style.left = p.left + 'px';
        el.style.top = p.top + 'px';
        el.style.right = 'auto';
    }

    // Drag by the title bar with pointer capture, so the map iframe below
    // does not take the pointer away mid-drag.
    function draggable(el, bar, onDrop) {
        let drag = null;
        bar.addEventListener('pointerdown', e => {
            if (e.button !== 0 || e.target.closest('.float-close')) return;
            const r = el.getBoundingClientRect();
            drag = { x: e.clientX, y: e.clientY, left: r.left, top: r.top };
            bar.setPointerCapture(e.pointerId);
            document.querySelectorAll('iframe').forEach(f => { f.style.pointerEvents = 'none'; });
            e.preventDefault();
        });
        bar.addEventListener('pointermove', e => {
            if (drag) place(el, drag.left + e.clientX - drag.x, drag.top + e.clientY - drag.y);
        });
        function end(e) {
            if (!drag) return;
            drag = null;
            if (bar.hasPointerCapture(e.pointerId)) bar.releasePointerCapture(e.pointerId);
            document.querySelectorAll('iframe').forEach(f => { f.style.pointerEvents = ''; });
            onDrop(parseFloat(el.style.left), parseFloat(el.style.top));
        }
        bar.addEventListener('pointerup', end);
        bar.addEventListener('pointercancel', end);
    }

    // el: the window (.float), with a .float-bar and a .float-close inside.
    // opts: key, button (element), open (default true),
    //       at(el) -> {left, top}  default position, onOpen()
    // Returns { open(), close(), toggle(), isOpen() }.
    Float.make = function (el, opts) {
        const s = (state[opts.key] = state[opts.key] || {});
        const bar = el.querySelector('.float-bar');
        const btn = opts.button;

        function setOpen(open) {
            el.hidden = !open;
            if (btn) {
                btn.classList.toggle('active', open);
                btn.setAttribute('aria-pressed', String(open));
            }
            s.open = open;
            save(state);
            if (open) {
                position();
                if (opts.onOpen) opts.onOpen();
            }
        }

        function position() {
            if (typeof s.left === 'number' && typeof s.top === 'number') {
                place(el, s.left, s.top);
            } else if (opts.at) {
                const p = opts.at(el);
                place(el, p.left, p.top);
            }
        }

        if (btn) btn.addEventListener('click', () => setOpen(el.hidden));
        el.querySelector('.float-close').addEventListener('click', () => setOpen(false));
        draggable(el, bar, (left, top) => {
            s.left = left;
            s.top = top;
            save(state);
        });
        window.addEventListener('resize', () => { if (!el.hidden) position(); });
        setOpen(typeof s.open === 'boolean' ? s.open : opts.open !== false);
        return {
            open: () => setOpen(true),
            close: () => setOpen(false),
            toggle: () => setOpen(el.hidden),
            isOpen: () => !el.hidden,
        };
    };

    // Float.size(key) -> {width, height} | null; Float.size(key, size) stores it.
    Float.size = function (key, size) {
        const s = (state[key] = state[key] || {});
        if (size) {
            s.width = Math.round(size.width);
            s.height = Math.round(size.height);
            save(state);
            return size;
        }
        return typeof s.width === 'number' && typeof s.height === 'number' ? { width: s.width, height: s.height } : null;
    };

    Float.MARGIN = MARGIN;
    Float.minTop = minTop;
})(typeof window !== 'undefined' ? window : globalThis);
