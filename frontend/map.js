import L from "leaflet";
import "leaflet/dist/leaflet.css";

// Leaflet comes through npm rather than a CDN <script>. Vite is already bundling the
// stylesheet, so this adds no failure mode that was not there — and it removes one, because a
// bundled asset cannot fail to arrive on demo morning.

const node = document.getElementById("map");
const payload = document.getElementById("map-payload");
if (node && payload) start(node, JSON.parse(payload.textContent));

function start(node, D) {
    const NS = D.stations.length;
    const NP = D.parameters.length;
    const WINDOW = D.windowSeconds;
    const TZ = "Europe/Brussels";

    // Typed arrays: the whole history is rescanned on every slider tick, and this is what
    // keeps that in the low milliseconds rather than the tens.
    const T = Int32Array.from(D.t);
    const S = Int32Array.from(D.s);
    const P = Uint8Array.from(D.p);
    const V = Float64Array.from(D.v);

    // Per (station, parameter): the window's total and count, and the last reading at or
    // before the window end. The last-known pair is the one that is always populated — the
    // window's is empty most of the time, and that is the normal state, not a failure.
    const sum = new Float64Array(NS * NP);
    const count = new Int32Array(NS * NP);
    const lastValue = new Float64Array(NS * NP);
    const lastTime = new Int32Array(NS * NP);

    const liveNow = Math.floor(D.now - D.epoch);
    let selected = Math.max(0, D.parameters.findIndex((p) => p.name === D.parameter));
    let end = liveNow;
    let station = null;
    let playing = null;

    /* ── time ─────────────────────────────────────────────────────────────── */

    const at = (offset) => new Date((D.epoch + offset) * 1000);
    const clock = (offset) =>
        at(offset).toLocaleString("en-GB", {
            timeZone: TZ, weekday: "short", day: "2-digit", month: "short",
            hour: "2-digit", minute: "2-digit", hour12: false,
        });
    const shortClock = (offset) =>
        at(offset).toLocaleString("en-GB", {
            timeZone: TZ, weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false,
        });

    // Mirrors the server's formatter. Ages here are relative to wherever the slider sits, not
    // to the wall clock, so they cannot be rendered server-side the way the freshness line is.
    function age(seconds) {
        const minutes = Math.floor(seconds / 60);
        if (minutes < 1) return "just now";
        if (minutes < 60) return `${minutes} min ago`;
        if (minutes < 1440) return `${Math.floor(minutes / 60)} h ${minutes % 60} min ago`;
        const hours = Math.floor(minutes / 60);
        return `${Math.floor(hours / 24)} d ${hours % 24} h ago`;
    }

    const number = (v) => (v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2));
    const count_ = (n) => `${n} measurement${n === 1 ? "" : "s"}`;
    const ENTITIES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
    // Station names come off the stream, so they are never trusted into innerHTML.
    const esc = (t) => String(t).replace(/[&<>"']/g, (c) => ENTITIES[c]);

    /* ── aggregation ──────────────────────────────────────────────────────── */

    const upperBound = (a, x) => {
        let lo = 0, hi = a.length;
        while (lo < hi) { const m = (lo + hi) >> 1; if (a[m] <= x) lo = m + 1; else hi = m; }
        return lo;
    };

    /** Recompute every aggregate for the window ending at `end`. Returns the rows inside it.
     *
     *  The scan runs from the start of history rather than from the window's lower bound,
     *  because the last known value is the thing every marker always shows and it usually
     *  predates the window. Rows are time-ordered, so the last write for a pair wins.
     */
    function aggregate() {
        sum.fill(0);
        count.fill(0);
        lastTime.fill(-1);
        const start = end - WINDOW;
        const hi = upperBound(T, end);
        let inWindow = 0;
        for (let i = 0; i < hi; i++) {
            const k = S[i] * NP + P[i];
            lastValue[k] = V[i];
            lastTime[k] = T[i];
            if (T[i] >= start) {
                sum[k] += V[i];
                count[k]++;
                inWindow++;
            }
        }
        return inWindow;
    }

    function bandOf(parameter, value) {
        const bands = D.parameters[parameter].bands;
        for (let i = 0; i < bands.length; i++) {
            if (bands[i].upper === null || value <= bands[i].upper) return i;
        }
        return bands.length - 1;
    }

    /* ── map ──────────────────────────────────────────────────────────────── */

    // No `zoomSnap` below 1: at fractional zoom Leaflet transform-scales the tile container
    // and the first screenful of tiles loads but never paints until something moves the map.
    const map = L.map(node, { preferCanvas: true, zoomControl: false });
    L.control.zoom({ position: "topleft" }).addTo(map);

    // Esri's light grey canvas. CARTO's equivalent now stamps "API KEY REQUIRED" across every
    // tile, and a basemap that can do that on demo morning is not worth the sign-up. Labels
    // ship as a separate layer, so town names sit above the markers instead of under them.
    const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_";
    L.tileLayer(`${ESRI}Base/MapServer/tile/{z}/{y}/{x}`, {
        attribution: "Esri · OpenStreetMap contributors · data: OpenAQ",
        maxZoom: 16,
    }).addTo(map);
    map.createPane("labels").style.pointerEvents = "none";
    // Above markerPane (600) so town names are not buried under the dots, but below
    // tooltipPane (650) so a marker's hover card is never printed over.
    map.getPane("labels").style.zIndex = 620;
    L.tileLayer(`${ESRI}Reference/MapServer/tile/{z}/{y}/{x}`, { pane: "labels", maxZoom: 16 }).addTo(map);

    const markerLayer = L.layerGroup().addTo(map);
    const labelLayer = L.layerGroup().addTo(map);
    const markers = D.stations.map(([name, lat, lon], i) => {
        const marker = L.circleMarker([lat, lon], { radius: 0, weight: 1.25, opacity: 0, fillOpacity: 0 });
        marker.on("click", () => selectStation(i));
        marker.bindTooltip("", { className: "hint", direction: "top", offset: [0, -6], opacity: 1 });
        markerLayer.addLayer(marker);
        return marker;
    });
    map.fitBounds(
        D.stations.reduce((b, [, lat, lon]) => b.extend([lat, lon]), L.latLngBounds(
            [D.stations[0][1], D.stations[0][2]], [D.stations[0][1], D.stations[0][2]])),
        { paddingTopLeft: [350, 20], paddingBottomRight: [30, 150] },
    );

    const dark = () => document.documentElement.dataset.theme === "dark";
    // A single ring, dark on the light basemap and light on the dark one. WCAG 1.4.11's
    // "adjacent" is spatial, so the test that applies to an interactive marker is
    // marker-versus-basemap — which one ring settles for every band in the palette.
    const ring = () => (dark() ? "#f4f4f1" : "#2b2a27");
    const muted = () => (dark() ? "#8d8c83" : "#86857e");

    function drawMarkers() {
        const parameter = D.parameters[selected];
        const stroke = ring();
        for (let i = 0; i < NS; i++) {
            const marker = markers[i];
            const k = i * NP + selected;
            if (lastTime[k] < 0) {
                // Never reported this pollutant. The EEA leaves a station it cannot classify
                // grey rather than painting it a band it never earned, and so do we.
                marker.setStyle({ color: muted(), fillColor: muted(), opacity: 0.55, fillOpacity: 0 });
                marker.setRadius(3);
                marker.setTooltipContent(
                    `<b>${esc(D.stations[i][0])}</b><em>no ${esc(parameter.label)} reported here</em>`);
                continue;
            }
            const n = count[k];
            const value = lastValue[k];
            const band = parameter.bands[bandOf(selected, value)];
            // Fill is the band of the last known value — the number the marker is showing.
            // Radius is the measurement count, on its own channel.
            marker.setStyle({ color: stroke, fillColor: band.colour, opacity: 1, fillOpacity: 0.9 });
            marker.setRadius(4 + 1.9 * Math.sqrt(Math.max(n, 1)));
            marker.setTooltipContent(
                `<b>${esc(D.stations[i][0])}</b>${esc(parameter.label)} ` +
                `<strong>${number(value)}</strong> ${esc(parameter.unit)} · ${esc(band.label)}` +
                `<br><em>${esc(clock(lastTime[k]))} · ${esc(age(end - lastTime[k]))}</em>` +
                (n > 0
                    ? `<br><em>window average ${number(sum[k] / n)} from ${count_(n)}</em>`
                    : `<br><em>nothing in this window</em>`),
            );
        }
        drawLabels();
    }

    /** Direct value labels, but only where they fit: zoomed in, in view, and few enough. */
    function drawLabels() {
        labelLayer.clearLayers();
        if (map.getZoom() < 11) return;
        const bounds = map.getBounds();
        const picks = [];
        for (let i = 0; i < NS && picks.length <= 80; i++) {
            if (lastTime[i * NP + selected] >= 0 && bounds.contains(markers[i].getLatLng())) picks.push(i);
        }
        if (picks.length > 80) return;
        for (const i of picks) {
            const k = i * NP + selected;
            const label = count[k] > 0
                ? `${number(lastValue[k])} · n=${count[k]}`
                : `${number(lastValue[k])}`;
            labelLayer.addLayer(L.marker(markers[i].getLatLng(), {
                interactive: false,
                icon: L.divIcon({ className: "", html: `<div class="val-label">${label}</div>`, iconAnchor: [-8, 20] }),
            }));
        }
    }
    map.on("zoomend moveend", drawLabels);

    /* ── panels ───────────────────────────────────────────────────────────── */

    function selectStation(i) {
        station = i;
        const [name, lat, lon] = D.stations[i];
        let total = 0;
        const rows = D.parameters.map((parameter, pi) => {
            const k = i * NP + pi;
            total += count[k];
            if (lastTime[k] < 0) {
                return `<tr class="none"><td class="p">${esc(parameter.label)}</td>` +
                    `<td class="num" colspan="3">not reported here</td></tr>`;
            }
            const band = parameter.bands[bandOf(pi, lastValue[k])];
            return `<tr>
                <td class="p">${esc(parameter.label)}</td>
                <td class="num">${number(lastValue[k])}<span class="unit">${esc(parameter.unit)}</span></td>
                <td class="num">${count[k] || "—"}</td>
                <td><span class="chip" style="background:${band.colour};color:${band.ink}">${esc(band.label)}</span></td>
            </tr>`;
        }).join("");
        const newest = Math.max(...D.parameters.map((_, pi) => lastTime[i * NP + pi]));
        document.getElementById("detail").className = "panel sheet open";
        document.getElementById("detail").innerHTML = `
            <div class="dt-head"><h2>${esc(name)}</h2><button class="close" id="detail-close">×</button></div>
            <div class="dt-meta">${lat.toFixed(4)}, ${lon.toFixed(4)} · last message ${esc(clock(newest))}</div>
            <table class="readings">
                <thead><tr><th>Pollutant</th><th class="num">Last known</th><th class="num">In window</th><th>Band</th></tr></thead>
                <tbody>${rows}</tbody>
            </table>
            <div class="dt-foot">Values are the last reading at or before ${esc(clock(end))}.
                <strong>${total}</strong> of them fall inside the ${WINDOW / 3600}-hour window; the rest are older.</div>`;
        document.getElementById("detail-close").onclick = closeDetail;
    }
    function closeDetail() {
        station = null;
        document.getElementById("detail").className = "panel sheet";
    }

    function drawTable() {
        const parameter = D.parameters[selected];
        const rows = [];
        for (let i = 0; i < NS; i++) {
            const k = i * NP + selected;
            if (lastTime[k] >= 0) rows.push([D.stations[i][0], lastValue[k], count[k], lastTime[k]]);
        }
        rows.sort((a, b) => b[1] - a[1]);
        document.getElementById("tv-title").textContent = `${parameter.label} by station — last known value`;
        document.getElementById("tv-sub").textContent =
            `${rows.length} stations reporting · window ${clock(end - WINDOW)} → ${clock(end)} · values in ${parameter.unit}`;
        document.getElementById("tv-body").innerHTML = rows.map(([name, value, n, t]) => {
            const band = parameter.bands[bandOf(selected, value)];
            return `<tr><td>${esc(name)}</td><td class="num">${number(value)}</td>
                <td class="num">${n || "—"}</td>
                <td><span class="chip" style="background:${band.colour};color:${band.ink}">${esc(band.label)}</span>
                    <span class="unit"> ${esc(age(end - t))}</span></td></tr>`;
        }).join("");
    }

    function drawLegend() {
        const parameter = D.parameters[selected];
        document.getElementById("legend-title").textContent = parameter.caption;
        document.getElementById("legend-rows").innerHTML =
            parameter.bands.map((band) =>
                `<div class="legend-row"><span class="swatch" style="background:${band.colour}"></span>` +
                `${esc(band.label)}<span class="rng">${esc(band.range)}</span></div>`).join("") +
            `<div class="legend-row"><span class="swatch" style="background:${D.noData.colour}"></span>` +
            `${esc(D.noData.label)}</div>`;
        document.getElementById("legend-note").textContent =
            `Values in ${parameter.unit}. Fill is the band of the last known value; marker size is the ` +
            `number of measurements behind it. ${parameter.source}.`;
    }

    /* ── render ───────────────────────────────────────────────────────────── */

    function render() {
        const inWindow = aggregate();
        const parameter = D.parameters[selected];
        let reporting = 0, withParameter = 0, measured = 0;
        for (let i = 0; i < NS; i++) {
            const k = i * NP + selected;
            if (lastTime[k] >= 0) {
                reporting++;
                if (count[k] > 0) withParameter++;
                measured += count[k];
            }
        }
        const en = (n) => n.toLocaleString("en-GB");
        document.getElementById("s-stations").textContent = en(reporting);
        document.getElementById("s-stations-l").textContent = `Stations reporting ${parameter.label}`;
        document.getElementById("s-measurements").textContent = en(measured);
        document.getElementById("s-measurements-l").textContent = `${parameter.label} in this window`;
        document.getElementById("s-total").textContent = inWindow === 0
            ? `Nothing inside this window · every marker shows its last known value instead.`
            : `${en(withParameter)} of them measured inside it · ${en(inWindow)} readings across all six pollutants.`;
        document.getElementById("w-end").textContent = clock(end);
        document.getElementById("w-range").textContent = `${WINDOW / 3600}h window from ${clock(end - WINDOW)}`;

        const empty = document.getElementById("empty");
        empty.className = inWindow === 0 ? "panel sheet show" : "panel sheet";
        if (inWindow === 0) {
            empty.innerHTML =
                `<b>No measurements in this ${WINDOW / 3600}-hour window.</b>
                 <span>Data arrives approximately every 6 hours. Every marker shows its last known value and how old it 
                 is; blue segments on the slider mark the windows that do hold data.</span>`;
        }
        drawMarkers();
        drawLegend();
        if (station !== null) selectStation(station);
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

    const slider = document.getElementById("slider");
    slider.min = WINDOW;
    slider.max = liveNow;
    slider.step = 60;
    slider.value = liveNow;
    slider.oninput = () => {
        end = +slider.value;
        render();
    };

    // Where the window actually holds something. A burst covers the slider for its own length
    // plus one window, because the window keeps catching it as it slides past.
    (() => {
        const GAP = 1200;
        const bursts = [];
        let from = T[0], previous = T[0];
        for (let i = 1; i < T.length; i++) {
            if (T[i] - previous > GAP) { bursts.push([from, previous]); from = T[i]; }
            previous = T[i];
        }
        bursts.push([from, previous]);
        const span = liveNow - WINDOW;
        document.getElementById("coverage").innerHTML = bursts.map(([a, b]) => {
            const lo = Math.max(WINDOW, a), hi = Math.min(liveNow, b + WINDOW);
            return hi <= lo ? "" : `<i style="left:${((lo - WINDOW) / span) * 100}%;width:${((hi - lo) / span) * 100}%"></i>`;
        }).join("");
    })();

    document.getElementById("ticks").innerHTML = Array.from({ length: 5 }, (_, k) =>
        `<span>${esc(shortClock(WINDOW + ((liveNow - WINDOW) * k) / 4))}</span>`).join("");

    document.getElementById("btn-play").onclick = (e) => {
        if (playing) {
            clearInterval(playing);
            playing = null;
            e.target.textContent = "▶ Play";
            return;
        }
        e.target.textContent = "❚❚ Pause";
        playing = setInterval(() => {
            end += 1800;
            if (end > liveNow) end = WINDOW;
            slider.value = end;
            render();
        }, 120);
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
        drawMarkers();
    };

    render();
}
