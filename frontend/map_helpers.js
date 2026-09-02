// How a moment, a value and a band are printed, and the legend. Nothing here knows about shapes.

const TZ = "Europe/Brussels";

/** Formatters for a page whose times are whole seconds after `epoch`. */
export function timeline(epoch) {
    const at = (offset) => new Date((epoch + offset) * 1000);
    return {
        at,
        clock: (offset) =>
            at(offset).toLocaleString("en-GB", {
                timeZone: TZ, weekday: "short", day: "2-digit", month: "short",
                hour: "2-digit", minute: "2-digit", hour12: false,
            }),
        shortClock: (offset) =>
            at(offset).toLocaleString("en-GB", {
                timeZone: TZ, weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false,
            }),
    };
}

// Mirrors the server's formatter. Ages here are relative to wherever the slider sits, not to
// the wall clock, so they cannot be rendered server-side the way the freshness line is.
export function age(seconds) {
    const minutes = Math.floor(seconds / 60);
    if (minutes < 1) return "just now";
    if (minutes < 60) return `${minutes} min ago`;
    if (minutes < 1440) return `${Math.floor(minutes / 60)} h ${minutes % 60} min ago`;
    const hours = Math.floor(minutes / 60);
    return `${Math.floor(hours / 24)} d ${hours % 24} h ago`;
}

export const number = (v) => (v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2));
export const plural = (n, noun) => `${n} ${noun}${n === 1 ? "" : "s"}`;

const ENTITIES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
// Station names come off the stream, so they are never trusted into innerHTML.
export const esc = (t) => String(t).replace(/[&<>"']/g, (c) => ENTITIES[c]);

/** Index of the band a value falls in. The top band is open-ended and catches the rest. */
export function bandOf(parameter, value) {
    const bands = parameter.bands;
    for (let i = 0; i < bands.length; i++) {
        if (bands[i].upper === null || value <= bands[i].upper) return i;
    }
    return bands.length - 1;
}

/** First index whose value is >= x, and first whose value is > x, in a sorted typed array. */
export const lowerBound = (a, x) => {
    let lo = 0, hi = a.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] < x) lo = m + 1; else hi = m; }
    return lo;
};
export const upperBound = (a, x) => {
    let lo = 0, hi = a.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] <= x) lo = m + 1; else hi = m; }
    return lo;
};

export function renderLegend(parameter, noData, note) {
    document.getElementById("legend-title").textContent = parameter.caption;
    document.getElementById("legend-rows").innerHTML =
        parameter.bands.map((band) =>
            `<div class="legend-row"><span class="swatch" style="background:${band.colour}"></span>` +
            `${esc(band.label)}<span class="rng">${esc(band.range)}</span></div>`).join("") +
        `<div class="legend-row"><span class="swatch" style="background:${noData.colour}"></span>` +
        `${esc(noData.label)}</div>`;
    document.getElementById("legend-note").textContent = note;
}
