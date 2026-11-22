// toganomon console: floating windows over the map, shared with the RJFF
// console. A header button opens and closes a window, x closes it, its title
// bar drags it, a resizable one keeps the size it was given. Open/closed,
// position and size are kept in localStorage under one key (default
// 'toganomon.console', Float.configure({ storeKey }) to change), so a browser
// without storage just starts from the defaults each time.
//
// Markup: <section class="float" hidden>
//           <div class="float-bar"><span>TITLE</span><button class="float-close">×</button></div>
//           <div class="float-body">...</div>
//         </section>                                     styles: float.css
//
// clampPos does not touch the DOM (tests/console_test.js).
(function (root) {
    'use strict';

    const MARGIN = 12;

    // Keep a window of w x h inside a view of viewW x viewH (top >= minTop).
    function clampPos(pos, w, h, viewW, viewH, minTop) {
        const top0 = minTop || 0;
        return {
            left: Math.max(0, Math.min(pos.left, viewW - w)),
            top: Math.max(top0, Math.min(pos.top, viewH - h)),
        };
    }

    const Float = { clampPos, MARGIN };
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = Float;
        return;
    }
    root.Float = Float;

    let storeKey = 'toganomon.console';
    let state = null;

    function load() {
        try {
            const s = JSON.parse(localStorage.getItem(storeKey));
            return s && typeof s === 'object' ? s : {};
        } catch (e) {
            return {};
        }
    }

    function all() {
        if (!state) state = load();
        return state;
    }

    // Use another localStorage key (call before the first make()).
    Float.configure = function (opts) {
        if (opts && opts.storeKey) {
            storeKey = opts.storeKey;
            state = null;
        }
    };

    // The stored object of one window, for a page to keep more in it
    // (call Float.save() after changing it).
    Float.state = function (key) {
        const s = all();
        return (s[key] = s[key] && typeof s[key] === 'object' ? s[key] : {});
    };

    Float.save = function () {
        try {
            localStorage.setItem(storeKey, JSON.stringify(all()));
        } catch (e) { /* works without storage */ }
    };

    // The header's bottom edge: windows never go under it.
    Float.minTop = function () {
        const h = document.querySelector('header');
        return h ? h.getBoundingClientRect().bottom : 0;
    };

    function place(el, left, top) {
        const p = clampPos({ left, top }, el.offsetWidth, el.offsetHeight, window.innerWidth, window.innerHeight, Float.minTop());
        el.style.left = p.left + 'px';
        el.style.top = p.top + 'px';
        el.style.right = 'auto';
        el.style.bottom = 'auto';
    }

    // Drag by the title bar with pointer capture, so a map iframe below does
    // not take the pointer away mid-drag.
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

    // el: the .float window. opts:
    //   key          name in the store
    //   button       header button that toggles it (optional)
    //   open         default when nothing is stored (true)
    //   remember     keep open/closed across reloads (true)
    //   at(el)       default position {left, top} (else where the CSS puts it)
    //   resizable    keep the size the user gives it by its corner (false)
    //   onOpen(), onClose(), onResize()
    // Returns { open(), close(), toggle(), isOpen() }.
    Float.make = function (el, opts) {
        const s = Float.state(opts.key);
        const bar = el.querySelector('.float-bar');
        const btn = opts.button;
        const remember = opts.remember !== false;

        function setOpen(open) {
            const was = !el.hidden;
            el.hidden = !open;
            if (btn) {
                btn.classList.toggle('active', open);
                btn.setAttribute('aria-pressed', String(open));
            }
            if (remember) {
                s.open = open;
                Float.save();
            }
            if (open) {
                position();
                settle = true;
                if (opts.onOpen) opts.onOpen();
            } else if (was && opts.onClose) {
                opts.onClose();
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

        // a size the user gave it earlier; size changes that are not the
        // user's (opening, a browser resize) are not stored
        let settle = true;
        if (opts.resizable) {
            if (typeof s.width === 'number' && typeof s.height === 'number') {
                el.style.width = s.width + 'px';
                el.style.height = s.height + 'px';
            }
            window.addEventListener('resize', () => { settle = true; });
            if (typeof ResizeObserver !== 'undefined') {
                let last = null;
                new ResizeObserver(() => {
                    if (el.hidden) return;
                    if (opts.onResize) opts.onResize();
                    const now = { width: el.offsetWidth, height: el.offsetHeight };
                    if (!settle && last && (now.width !== last.width || now.height !== last.height)) {
                        s.width = now.width;
                        s.height = now.height;
                        Float.save();
                    }
                    settle = false;
                    last = now;
                }).observe(el);
            }
        }

        if (btn) btn.addEventListener('click', () => setOpen(el.hidden));
        el.querySelector('.float-close').addEventListener('click', () => setOpen(false));
        draggable(el, bar, (left, top) => {
            s.left = left;
            s.top = top;
            Float.save();
        });
        window.addEventListener('resize', () => { if (!el.hidden) position(); });
        const initial = remember && typeof s.open === 'boolean' ? s.open : opts.open !== false;
        if (initial) setOpen(true);
        else {
            el.hidden = true;
            if (btn) btn.classList.remove('active');
        }
        return {
            open: () => setOpen(true),
            close: () => setOpen(false),
            toggle: () => setOpen(el.hidden),
            isOpen: () => !el.hidden,
        };
    };
})(typeof window !== 'undefined' ? window : globalThis);
