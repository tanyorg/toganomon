// toganomon console: the UTC clock and a second clock in any zone, shared
// with the RJFF console.
//
//   Clock.start({
//       utc: element,            // UTC time
//       local: element,          // time in timeZone
//       label: element,          // optional: gets the zone's short name (JST, CET, UTC+9)
//       timeZone: 'Asia/Tokyo',  // default: the browser's
//       seconds: true,           // HH:MM:SS (default) or HH:MM
//   });                          // -> { stop() }
//
// Clock.format and Clock.zoneLabel do not touch the DOM (tests/console_test.js);
// the time is always formatted in an explicit zone, never the machine's.
(function (root) {
    'use strict';

    const formatters = {};
    function format(date, timeZone, seconds) {
        const key = `${timeZone}/${!!seconds}`;
        if (!formatters[key]) {
            formatters[key] = new Intl.DateTimeFormat('en-GB', {
                timeZone, hourCycle: 'h23', hour: '2-digit', minute: '2-digit',
                ...(seconds ? { second: '2-digit' } : {}),
            });
        }
        return formatters[key].format(date);
    }

    // Short name for a zone: an abbreviation such as "JST" or "CET" where a
    // locale has one (en-US alone gives "GMT+9" for Asia/Tokyo), else "UTC+9".
    const ZONE_LOCALES = ['en-US', 'en-GB', 'ja-JP', 'en-AU', 'en-IN', 'en-NZ'];
    function zoneLabel(timeZone, date) {
        let offset = null;
        for (const loc of ZONE_LOCALES) {
            try {
                const part = new Intl.DateTimeFormat(loc, { timeZone, timeZoneName: 'short' })
                    .formatToParts(date || new Date()).find(p => p.type === 'timeZoneName');
                if (!part) continue;
                if (/^[A-Z]{2,5}$/.test(part.value)) return part.value === 'GMT' ? 'UTC' : part.value;
                if (offset === null && /^GMT[+-]/.test(part.value)) offset = part.value.replace('GMT', 'UTC');
            } catch (e) { return String(timeZone).split('/').pop().toUpperCase(); }
        }
        return offset || 'UTC';
    }

    function browserZone() {
        try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch (e) { return 'UTC'; }
    }

    const Clock = { format, zoneLabel, browserZone };
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = Clock;
        return;
    }
    root.Clock = Clock;

    Clock.start = function (opts) {
        const o = Object.assign({ utc: null, local: null, label: null, timeZone: null, seconds: true }, opts || {});
        const tz = o.timeZone || browserZone();
        if (o.label) o.label.textContent = zoneLabel(tz);
        function tick() {
            const now = new Date();
            if (o.utc) o.utc.textContent = format(now, 'UTC', o.seconds);
            if (o.local) o.local.textContent = format(now, tz, o.seconds);
        }
        tick();
        const timer = setInterval(tick, 1000);
        return { stop() { clearInterval(timer); } };
    };
})(typeof window !== 'undefined' ? window : globalThis);
