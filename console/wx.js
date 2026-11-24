// toganomon console: how old the METAR is and whether the TAF still holds,
// from the raw text (metar.txt / taf.txt). DOM-free (tests/console_test.js);
// also used by airband's METAR / TAF decoders.
//
//   Wx.metarTime(raw, now)      observation time (Date) of a METAR, or null
//   Wx.tafValidTo(raw, now)     end of a TAF's validity (Date), or null
//   Wx.minutesSince(date, now)  whole minutes, or null without a date
//   Wx.formatAge(minutes)       "12m", "1h05m", "?"
//   Wx.metarLevel(minutes, warn, alert)  'ok' | 'warn' | 'alert'
//   Wx.tafLevel(validTo, now)            'ok' | 'alert' (expired or unknown)
(function (root) {
    'use strict';

    // Defaults for a METAR every hour (half-hourly stations may pass 60 / 120)
    const METAR_WARN_MIN = 90;
    const METAR_ALERT_MIN = 180;

    // DDHHMM (no month or year) put in the month that brings it nearest to
    // now, in UTC: a report from the end of last month read at the start of
    // this one, and a TAF's "24" hour, come out right.
    function resolveDayTime(day, hour, minute, now) {
        let best = null;
        for (const offset of [-1, 0, 1]) {
            const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + offset, day, hour, minute));
            // a day the month does not have (31 April) rolls over: not this month
            if (d.getUTCDate() !== day && !(hour === 24 && d.getUTCHours() === 0)) continue;
            if (!best || Math.abs(d - now) < Math.abs(best - now)) best = d;
        }
        return best;
    }

    function tokens(raw) {
        return typeof raw === 'string' ? raw.trim().split(/\s+/).filter(Boolean) : [];
    }

    function metarTime(raw, now = new Date()) {
        for (const t of tokens(raw)) {
            const m = t.match(/^(\d{2})(\d{2})(\d{2})Z$/);
            if (m) return resolveDayTime(+m[1], +m[2], +m[3], now);
        }
        return null;
    }

    // The first DDHH/DDHH (the validity; change groups come after it)
    function tafValidTo(raw, now = new Date()) {
        for (const t of tokens(raw)) {
            const m = t.match(/^(\d{2})(\d{2})\/(\d{2})(\d{2})$/);
            if (m) return resolveDayTime(+m[3], +m[4], 0, now);
        }
        return null;
    }

    function minutesSince(date, now = new Date()) {
        return date ? Math.floor((now - date) / 60000) : null;
    }

    function formatAge(minutes) {
        if (minutes === null || minutes === undefined) return '?';
        if (minutes < 60) return `${Math.max(minutes, 0)}m`;
        return `${Math.floor(minutes / 60)}h${String(minutes % 60).padStart(2, '0')}m`;
    }

    function metarLevel(minutes, warn = METAR_WARN_MIN, alert = METAR_ALERT_MIN) {
        if (minutes === null || minutes === undefined || minutes >= alert) return 'alert';
        return minutes >= warn ? 'warn' : 'ok';
    }

    function tafLevel(validTo, now = new Date()) {
        return !validTo || now >= validTo ? 'alert' : 'ok';
    }

    const Wx = { resolveDayTime, metarTime, tafValidTo, minutesSince, formatAge, metarLevel, tafLevel };
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = Wx;
        return;
    }
    root.Wx = Wx;
})(typeof window !== 'undefined' ? window : globalThis);
