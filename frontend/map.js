import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { bandOf, esc, lowerBound, number, plural, renderLegend, timeline, upperBound } from "./map_helpers.js";

const node = document.getElementById("map");
const payload = document.getElementById("map-payload");
if (node && payload) start(node, JSON.parse(payload.textContent));

function start(node, D) {
    const NC = D.cities.length;
    const NP = D.parameters.length;
    const WINDOW = D.windowHours;
    const LAST_HOUR = D.hoursInSpan - 1;
    const HOUR = 3600;
    const { clock, shortClock } = timeline(D.spanStart);

    // One bucket per column: the hour it covers, the city and parameter it belongs to, and the
    // total and count of the readings inside it. Sorted by hour, which is what makes a window
    // a contiguous slice.
    const H = Int32Array.from(D.hours);
    const C = Int32Array.from(D.cityIndexes);
    const P = Uint8Array.from(D.parameterIndexes);
    const T = Float64Array.from(D.totals);
    const N = Int32Array.from(D.counts);

    // Per (city, parameter): the window's total and count. Nothing is divided until it is
    // printed, so a busy hour outweighs a quiet one the way it should.
    const total = new Float64Array(NC * NP);
    const count = new Int32Array(NC * NP);

    const liveNow = D.now - D.spanStart;
    let selected = Math.max(0, D.parameters.findIndex((p) => p.name === D.parameter));
    let end = LAST_HOUR; // The newest bucket in the window
    let city = null;
    let playing = null;
    let shapes = null; // The boundaries layer, once the GeoJSON has arrived

    /* ── aggregation ──────────────────────────────────────────────────────── */

    /** Sum the buckets whose hour is in [end - WINDOW + 1, end]. Returns the readings inside. */
    function aggregate() {
        total.fill(0);
        count.fill(0);
        const lo = lowerBound(H, end - WINDOW + 1);
        const hi = upperBound(H, end);
        let inWindow = 0;
        for (let i = lo; i < hi; i++) {
            const k = C[i] * NP + P[i];
            total[k] += T[i];
            count[k] += N[i];
            inWindow += N[i];
        }
        return inWindow;
    }

    // Bucket `end` covers [end h, end + 1 h), so the window closes an hour after it opens. The
    // newest bucket is still filling, and its close would be in the future -> cap it at now.
    const windowOpens = () => (end - WINDOW + 1) * HOUR;
    const windowCloses = () => Math.min((end + 1) * HOUR, liveNow);

    /* ── map ──────────────────────────────────────────────────────────────── */

    // No `zoomSnap` below 1: at fractional zoom Leaflet transform-scales the tile container
    // and the first screenful of tiles loads but never paints until something moves the map.
    const map = L.map(node, { preferCanvas: true, zoomControl: false });
    L.control.zoom({ position: "topleft" }).addTo(map);
    // Belgium, framed before the boundaries arrive so the tiles are already in place when they
    // do and nothing jumps.
    map.fitBounds([[49.49, 2.54], [51.51, 6.41]], { paddingTopLeft: [350, 20], paddingBottomRight: [30, 150] });

    const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_";
    L.tileLayer(`${ESRI}Base/MapServer/tile/{z}/{y}/{x}`, {
        attribution: "Esri · OpenStreetMap contributors · data: OpenAQ · boundaries: Statbel",
        maxZoom: 16,
    }).addTo(map);
    map.createPane("labels").style.pointerEvents = "none";
    // Above overlayPane (400), where the shapes are, so town names print on top of the fills,
    // but below tooltipPane (650) so a hover card is never printed over.
    map.getPane("labels").style.zIndex = 620;
    L.tileLayer(`${ESRI}Reference/MapServer/tile/{z}/{y}/{x}`, { pane: "labels", maxZoom: 16 }).addTo(map);

    // The join between a boundary and its readings. Both sides carry the REFNIS code as a
    // number, so a plain Map lookup is the whole of it.
    const indexByRefnis = new Map(D.cities.map(([refnis], i) => [refnis, i]));

    const dark = () => document.documentElement.dataset.theme === "dark";
    const line = () => (dark() ? "#f4f4f1" : "#2b2a27");

    function styleOf(feature) {
        const i = indexByRefnis.get(feature.properties.refnis);
        const k = i * NP + selected;
        // An unknown code, or nothing measured in this window: grey, and lighter than a band so
        // the eye goes to the municipalities that have something to say.
        if (i === undefined || count[k] === 0) {
            return { color: line(), weight: 0.6, opacity: 0.5, fillColor: D.noData.colour, fillOpacity: 0.3 };
        }
        const parameter = D.parameters[selected];
        const band = parameter.bands[bandOf(parameter, total[k] / count[k])];
        return { color: line(), weight: 0.6, opacity: 0.5, fillColor: band.colour, fillOpacity: 0.8 };
    }

    fetch(D.boundariesUrl)
        .then((response) => response.json())
        .then((boundaries) => {
            shapes = L.geoJSON(boundaries, {
                style: styleOf,
                onEachFeature: (feature, layer) => {
                    const i = indexByRefnis.get(feature.properties.refnis);
                    if (i === undefined) return; // Painted grey above, and there is nothing to say about it
                    // A function, so the card reads the aggregates as they are when the mouse arrives
                    layer.bindTooltip(() => hint(i), { className: "hint", sticky: true, opacity: 1 });
                    layer.on("click", () => selectCity(i));
                },
            }).addTo(map);
        });

    function hint(i) {
        const parameter = D.parameters[selected];
        const k = i * NP + selected;
        const name = `<b>${esc(D.cities[i][1])}</b>`;
        if (count[k] === 0) return `${name}<em>no ${esc(parameter.label)} in this window</em>`;
        const value = total[k] / count[k];
        const band = parameter.bands[bandOf(parameter, value)];
        return `${name}${esc(parameter.label)} <strong>${number(value)}</strong> ${esc(parameter.unit)} · ${esc(band.label)}` +
            `<br><em>window average from ${plural(count[k], "measurement")}</em>`;
    }

    /* ── panels ───────────────────────────────────────────────────────────── */

    function selectCity(i) {
        city = i;
        const [refnis, name] = D.cities[i];
        let measured = 0;
        const rows = D.parameters.map((parameter, pi) => {
            const k = i * NP + pi;
            measured += count[k];
            if (count[k] === 0) {
                return `<tr class="none"><td class="p">${esc(parameter.label)}</td>` +
                    `<td class="num" colspan="3">nothing in this window</td></tr>`;
            }
            const value = total[k] / count[k];
            const band = parameter.bands[bandOf(parameter, value)];
            return `<tr>
                <td class="p">${esc(parameter.label)}</td>
                <td class="num">${number(value)}<span class="unit">${esc(parameter.unit)}</span></td>
                <td class="num">${count[k]}</td>
                <td><span class="chip" style="background:${band.colour};color:${band.ink}">${esc(band.label)}</span></td>
            </tr>`;
        }).join("");
        document.getElementById("detail").className = "panel sheet open";
        document.getElementById("detail").innerHTML = `
            <div class="dt-head"><h2>${esc(name)}</h2><button class="close" id="detail-close">×</button></div>
            <div class="dt-meta">REFNIS ${refnis} · window ${esc(clock(windowOpens()))} → ${esc(clock(windowCloses()))}</div>
            <table class="readings">
                <thead><tr><th>Pollutant</th><th class="num">Window average</th><th class="num">Measurements</th><th>Band</th></tr></thead>
                <tbody>${rows}</tbody>
            </table>
            <div class="dt-foot">Averages over every station inside the municipality.
                <strong>${measured}</strong> measurements across all six pollutants fall inside this ${WINDOW}-hour window.</div>`;
        document.getElementById("detail-close").onclick = closeDetail;
    }
    function closeDetail() {
        city = null;
        document.getElementById("detail").className = "panel sheet";
    }

    function drawTable() {
        const parameter = D.parameters[selected];
        const rows = [];
        for (let i = 0; i < NC; i++) {
            const k = i * NP + selected;
            if (count[k] > 0) rows.push([D.cities[i][1], total[k] / count[k], count[k]]);
        }
        rows.sort((a, b) => b[1] - a[1]);
        document.getElementById("tv-title").textContent = `${parameter.label} by municipality — window average`;
        document.getElementById("tv-sub").textContent =
            `${rows.length} municipalities reporting · window ${clock(windowOpens())} → ${clock(windowCloses())} · values in ${parameter.unit}`;
        document.getElementById("tv-body").innerHTML = rows.map(([name, value, n]) => {
            const band = parameter.bands[bandOf(parameter, value)];
            return `<tr><td>${esc(name)}</td><td class="num">${number(value)}</td>
                <td class="num">${n}</td>
                <td><span class="chip" style="background:${band.colour};color:${band.ink}">${esc(band.label)}</span></td></tr>`;
        }).join("");
    }

    /* ── render ───────────────────────────────────────────────────────────── */

    function render() {
        const inWindow = aggregate();
        const parameter = D.parameters[selected];
        let reporting = 0, measured = 0, anything = 0;
        for (let i = 0; i < NC; i++) {
            const k = i * NP + selected;
            if (count[k] > 0) {
                reporting++;
                measured += count[k];
            }
            for (let pi = 0; pi < NP; pi++) {
                if (count[i * NP + pi] > 0) { anything++; break; }
            }
        }
        const en = (n) => n.toLocaleString("en-GB");
        document.getElementById("s-cities").textContent = en(reporting);
        document.getElementById("s-cities-l").textContent = `Municipalities reporting ${parameter.label}`;
        document.getElementById("s-measurements").textContent = en(measured);
        document.getElementById("s-measurements-l").textContent = `${parameter.label} in this window`;
        document.getElementById("s-total").textContent = inWindow === 0
            ? `Nothing inside this window · every municipality is grey.`
            : `${en(inWindow)} readings across all six pollutants, from ${en(anything)} of ${en(NC)} municipalities.`;
        document.getElementById("w-end").textContent = clock(windowCloses());
        document.getElementById("w-range").textContent = `${WINDOW}h window from ${clock(windowOpens())}`;

        const empty = document.getElementById("empty");
        empty.className = inWindow === 0 ? "panel sheet show" : "panel sheet";
        if (inWindow === 0) {
            empty.innerHTML =
                `<b>No measurements in this ${WINDOW}-hour window.</b>
                 <span>Data arrives approximately every 6 hours. Blue segments on the slider mark the windows that
                 do hold data.</span>`;
        }
        if (shapes) shapes.setStyle(styleOf);
        renderLegend(parameter, D.noData,
            `Values in ${parameter.unit}. Fill is the band of the window average over every station in the ` +
            `municipality; grey is a municipality with nothing measured in this window. ${parameter.source}.`);
        if (city !== null) selectCity(city);
        if (document.getElementById("tableview").classList.contains("open")) drawTable();
    }

    /* ── controls ─────────────────────────────────────────────────────────── */

    const segment = document.getElementById("param-seg");
    segment.innerHTML = D.parameters
        .map((p, i) => `<button data-i="${i}" aria-pressed="${i === selected}">${esc(p.label)}</button>`).join("");
    segment.onclick = (e) => {
        const button = e.target.closest("button");
        if (!button) return;
        selected = +button.dataset.i;
        [...segment.children].forEach((x) => x.setAttribute("aria-pressed", x === button));
        render();
    };

    // The slider moves in whole buckets. Its first position is the first window that is a
    // whole WINDOW buckets wide, so every position means the same thing.
    const FIRST = WINDOW - 1;
    const slider = document.getElementById("slider");
    slider.min = FIRST;
    slider.max = LAST_HOUR;
    slider.step = 1;
    slider.value = LAST_HOUR;
    slider.oninput = () => {
        end = +slider.value;
        render();
    };

    // Where the window holds something. A bucket at hour h is inside every window whose end is
    // h .. h + WINDOW - 1, so each one lights up WINDOW slider positions.
    (() => {
        const positions = LAST_HOUR - FIRST;
        const runs = [];
        for (let i = 0; i < H.length; i++) {
            const lo = Math.max(FIRST, H[i]), hi = Math.min(LAST_HOUR, H[i] + WINDOW - 1);
            const last = runs[runs.length - 1];
            if (last && lo <= last[1] + 1) last[1] = Math.max(last[1], hi);
            else runs.push([lo, hi]);
        }
        // Half a position of padding each side, so a single lit position is not zero wide
        document.getElementById("coverage").innerHTML = runs.map(([lo, hi]) =>
            `<i style="left:${((lo - FIRST - 0.5) / positions) * 100}%;width:${((hi - lo + 1) / positions) * 100}%"></i>`,
        ).join("");
    })();

    document.getElementById("ticks").innerHTML = Array.from({ length: 5 }, (_, k) =>
        `<span>${esc(shortClock((FIRST + 1 + ((LAST_HOUR - FIRST) * k) / 4) * HOUR))}</span>`).join("");

    document.getElementById("btn-play").onclick = (e) => {
        if (playing) {
            clearInterval(playing);
            playing = null;
            e.target.textContent = "▶ Play";
            return;
        }
        e.target.textContent = "❚❚ Pause";
        playing = setInterval(() => {
            end = end >= LAST_HOUR ? FIRST : end + 1;
            slider.value = end;
            render();
        }, 250);
    };

    const tableview = document.getElementById("tableview");
    const tableButton = document.getElementById("btn-table");
    tableButton.onclick = () => {
        const open = tableview.classList.toggle("open");
        tableButton.setAttribute("aria-pressed", open);
        if (open) drawTable();
    };
    document.getElementById("btn-table-close").onclick = () => tableButton.onclick();

    document.getElementById("btn-theme").onclick = (e) => {
        const wasDark = dark();
        document.documentElement.dataset.theme = wasDark ? "light" : "dark";
        e.target.textContent = wasDark ? "☾" : "☀";
        if (shapes) shapes.setStyle(styleOf);
    };

    render();
}
