// Shared helpers for every page: formatting, the electoral vote bar, the map,
// sortable tables, and a small tooltip.
(function () {
  const PM = {};

  // ColorBrewer RdBu, the same scale as the Python map: R+25 deep red -> D+25 deep blue
  PM.MARGIN_RANGE = 25;
  PM.MARGIN_SCALE = [
    "#67001f", "#b2182b", "#d6604d", "#f4a582", "#fddbc7", "#f7f7f7",
    "#d1e5f0", "#92c5de", "#4393c3", "#2166ac", "#053061",
  ].map((c, i, a) => [i / (a.length - 1), c]);
  PM.OTHER_COLOR = "#c9a227";

  // Segment colors shared by the 2028 ratings and past-result bands
  PM.SHADES = {
    D: ["#1d4f91", "#4f86c6", "#a6c8ea"],   // strong -> weak
    R: ["#a21d2a", "#d6604d", "#f2aaa0"],
    tossup: "#bdbdbd",
    other: "#c9a227",
  };
  const LIGHT = new Set(["#a6c8ea", "#f2aaa0", "#bdbdbd", "#c9a227"]);
  PM.textClass = (color) => (LIGHT.has(color.toLowerCase()) ? "dark-text" : "light-text");

  PM.fmtMargin = (m) => (m == null ? "—" : Math.abs(m) < 0.05 ? "Even" : `${m > 0 ? "D" : "R"}+${Math.abs(m).toFixed(1)}`);
  PM.fmtSigned = (x) => `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(1)}`;
  PM.fmtInt = (n) => (n == null ? "—" : Math.round(n).toLocaleString("en-US"));
  PM.fmtPct = (x, d = 1) => (x == null ? "—" : `${x.toFixed(d)}%`);
  PM.fmtDate = (s) => {
    if (!s) return "—";
    const d = new Date(s + "T12:00:00");
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
  };
  PM.surname = (full) => full.trim().split(/\s+/).pop();
  PM.marginClass = (m) => (m > 0.05 ? "d" : m < -0.05 ? "r" : "");
  PM.esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // Tooltip ------------------------------------------------------------------
  let tip;
  PM.showTip = (html, x, y) => {
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "tip";
      tip.setAttribute("role", "tooltip");
      document.body.appendChild(tip);
    }
    tip.innerHTML = html;
    tip.hidden = false;
    const pad = 14, w = tip.offsetWidth, h = tip.offsetHeight;
    let left = x + pad, top = y + pad;
    if (left + w > window.innerWidth - 8) left = x - w - pad;
    if (top + h > window.innerHeight - 8) top = y - h - pad;
    tip.style.left = Math.max(8, left) + "px";
    tip.style.top = Math.max(8, top) + "px";
  };
  PM.hideTip = () => { if (tip) tip.hidden = true; };

  // Electoral vote bar --------------------------------------------------------
  // opts: { left: {name, ev, sub, color}, right: {...}, center: html,
  //         segments: [{label, ev, color, detail}], total,
  //         threshold (default 270), thresholdLabel, unit ("electoral vote" / "seat") }
  PM.renderEvBar = (el, opts) => {
    const side = (s, cls) => `
      <div class="ev-side ${cls}">
        <span class="ev-name"><span class="dot" style="background:${s.color}"></span>${PM.esc(s.name)}</span>
        <span class="ev-num">${s.ev}</span>
        ${s.sub ? `<span class="ev-sub">${s.sub}</span>` : ""}
      </div>`;
    const segs = opts.segments.filter((s) => s.ev > 0);
    el.innerHTML = `
      <div class="ev-head">
        ${side(opts.left, "left")}
        <div class="ev-center">${opts.center || ""}</div>
        ${side(opts.right, "right")}
      </div>
      <div class="ev-bar-wrap">
        <div class="ev-bar" role="img" aria-label="${PM.esc(segs.map((s) => `${s.label}: ${s.ev}`).join(", "))}">
          ${segs.map((s, i) => `
            <div class="ev-seg ${PM.textClass(s.color)}" data-i="${i}" tabindex="0"
                 style="flex:${s.ev} 1 0;background:${s.color}">${s.ev / opts.total >= 0.035 ? s.ev : ""}</div>`).join("")}
        </div>
        <div class="ev-270" style="left:${((opts.threshold ?? 270) / opts.total) * 100}%"><span>${opts.thresholdLabel ?? "270 to win"}</span></div>
      </div>
      <div class="legend">
        ${opts.segments.map((s) => `<span><i style="background:${s.color}"></i>${PM.esc(s.label)} <span class="muted">${s.ev}</span></span>`).join("")}
      </div>`;
    el.querySelectorAll(".ev-seg").forEach((node) => {
      const s = segs[+node.dataset.i];
      const unit = opts.unit ?? "electoral vote";
      const html = `<strong>${PM.esc(s.label)}</strong>: ${s.ev} ${unit}${s.ev === 1 ? "" : "s"}${s.detail ? `<br>${s.detail}` : ""}`;
      node.addEventListener("mousemove", (e) => PM.showTip(html, e.clientX, e.clientY));
      node.addEventListener("mouseleave", PM.hideTip);
      node.addEventListener("focus", () => { const r = node.getBoundingClientRect(); PM.showTip(html, r.left, r.bottom); });
      node.addEventListener("blur", PM.hideTip);
    });
  };

  // Map --------------------------------------------------------------------------
  // rows: [{state, margin, hover}]; rows with color set (e.g. third-party wins)
  // are drawn as a separate flat-colored layer.
  // opts: { zmin, zmax } to color by something other than margin (e.g. win probability 0..1)
  PM.renderMap = (el, rows, opts = {}) => {
    const css = getComputedStyle(document.documentElement);
    const border = css.getPropertyValue("--surface").trim() || "#fff";
    const ink = css.getPropertyValue("--ink").trim() || "#111";
    const zmin = opts.zmin ?? -PM.MARGIN_RANGE, zmax = opts.zmax ?? PM.MARGIN_RANGE;
    const scaled = rows.filter((r) => !r.color);
    const flat = rows.filter((r) => r.color);
    const base = {
      type: "choropleth", locationmode: "USA-states",
      hovertemplate: "%{text}<extra></extra>",
      marker: { line: { color: border, width: 1 } },
      showscale: false,
    };
    const traces = [{
      ...base,
      locations: scaled.map((r) => r.state),
      z: scaled.map((r) => Math.max(zmin, Math.min(zmax, r.margin))),
      text: scaled.map((r) => r.hover),
      zmin, zmax, colorscale: PM.MARGIN_SCALE,
    }];
    // One flat-colored layer per color (third-party wins, states with no race, ...)
    [...new Set(flat.map((r) => r.color))].forEach((c) => {
      const rs = flat.filter((r) => r.color === c);
      traces.push({
        ...base,
        locations: rs.map((r) => r.state), z: rs.map(() => 1), text: rs.map((r) => r.hover),
        zmin: 0, zmax: 1, colorscale: [[0, c], [1, c]],
      });
    });
    const layout = {
      geo: {
        scope: "usa", projection: { type: "albers usa" },
        bgcolor: "rgba(0,0,0,0)", showlakes: false, showland: true, landcolor: "rgba(0,0,0,0)",
        subunitcolor: border,
      },
      margin: { l: 0, r: 0, t: 0, b: 0 },
      paper_bgcolor: "rgba(0,0,0,0)",
      dragmode: false,
      hoverlabel: { bgcolor: ink, bordercolor: ink, font: { color: border, size: 13, family: css.getPropertyValue("--sans") } },
    };
    window.Plotly.react(el, traces, layout, { displayModeBar: false, responsive: true, scrollZoom: false });
    return el;
  };

  // Grey for states with nothing on the ballot (theme-aware)
  PM.noRaceColor = () => (window.matchMedia("(prefers-color-scheme: dark)").matches ? "#3a3d44" : "#dcd9d2");

  PM.renderScale = (el, { other, otherColor, left, right } = {}) => {
    const stops = PM.MARGIN_SCALE.map(([p, c]) => `${c} ${p * 100}%`).join(",");
    el.innerHTML = `
      <span>${left ?? `R+${PM.MARGIN_RANGE}`}</span>
      <span class="ramp" style="background:linear-gradient(90deg,${stops})"></span>
      <span>${right ?? `D+${PM.MARGIN_RANGE}`}</span>
      ${other ? `<span class="other-key"><i${otherColor ? ` style="background:${otherColor}"` : ""}></i>${PM.esc(other)}</span>` : ""}`;
  };

  // Sortable table -------------------------------------------------------------
  // columns: [{key, label, num, sort: row => value, render: row => html}]
  PM.sortableTable = (table, columns, rows, initial) => {
    let sortKey = initial?.key ?? columns[0].key;
    let dir = initial?.dir ?? 1;
    const head = table.querySelector("thead") || table.createTHead();
    const body = table.tBodies[0] || table.createTBody();
    const draw = () => {
      const col = columns.find((c) => c.key === sortKey);
      const val = col.sort || ((r) => r[col.key]);
      const sorted = [...rows].sort((a, b) => {
        const x = val(a), y = val(b);
        if (x == null && y == null) return 0;
        if (x == null) return 1;
        if (y == null) return -1;
        return (x < y ? -1 : x > y ? 1 : 0) * dir;
      });
      head.innerHTML = `<tr>${columns.map((c) => `
        <th class="${c.num ? "num" : ""}" data-sort="${c.key}" scope="col" tabindex="0"
            ${c.key === sortKey ? `aria-sort="${dir > 0 ? "ascending" : "descending"}"` : ""}>${c.label}</th>`).join("")}</tr>`;
      body.innerHTML = sorted.map((r) => `<tr>${columns.map((c) =>
        `<td class="${c.num ? "num" : ""}">${c.render ? c.render(r) : PM.esc(r[c.key] ?? "—")}</td>`).join("")}</tr>`).join("");
      head.querySelectorAll("th").forEach((th) => {
        const go = () => {
          if (th.dataset.sort === sortKey) dir = -dir;
          else { sortKey = th.dataset.sort; dir = columns.find((c) => c.key === sortKey).num ? -1 : 1; }
          draw();
        };
        th.addEventListener("click", go);
        th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
      });
    };
    draw();
    return { update(newRows) { rows = newRows; draw(); } };
  };

  // Forecast helpers -----------------------------------------------------------
  // Win probabilities use the same red-gray-blue scale as margins: 50% is gray,
  // 95% or more for either side is the deepest color.
  PM.PROB_MIN = 0.05; PM.PROB_MAX = 0.95;
  // Color for a value on the red-gray-blue scale between lo (deep red) and hi (deep blue)
  PM.scaleColor = (v, lo, hi) => {
    const x = (Math.max(lo, Math.min(hi, v)) - lo) / (hi - lo);
    const sc = PM.MARGIN_SCALE, i = Math.min(sc.length - 2, Math.floor(x * (sc.length - 1)));
    const f = (x - sc[i][0]) / (sc[i + 1][0] - sc[i][0]);
    const rgb = (h) => [1, 3, 5].map((k) => parseInt(h.slice(k, k + 2), 16));
    const [a, b] = [rgb(sc[i][1]), rgb(sc[i + 1][1])];
    return `rgb(${a.map((c, k) => Math.round(c + (b[k] - c) * f)).join(",")})`;
  };
  PM.probColor = (p) => PM.scaleColor(p, PM.PROB_MIN, PM.PROB_MAX);
  PM.marginColor = (m) => PM.scaleColor(m, -PM.MARGIN_RANGE, PM.MARGIN_RANGE);
  // "63%", with the extremes shown as "<1%" / ">99%" rather than a false 0 or 100
  PM.fmtProb = (p) => (p == null ? "—" : p < 0.005 ? "<1%" : p > 0.995 ? ">99%" : `${Math.round(p * 100)}%`);
  // The leader's chance, e.g. "D 72%" / "R 88%"
  PM.fmtChance = (pDem) => (pDem >= 0.5 ? `D ${PM.fmtProb(pDem)}` : `R ${PM.fmtProb(1 - pDem)}`);
  PM.probClass = (pDem) => (pDem > 0.5 ? "d" : pDem < 0.5 ? "r" : "");
  // Bar colors for the probability bands, reusing the rating shades
  PM.BAND_COLORS = {
    "Safe D": PM.SHADES.D[0], "Likely D": PM.SHADES.D[1], "Lean D": PM.SHADES.D[2], "Toss-up": PM.SHADES.tossup,
    "Lean R": PM.SHADES.R[2], "Likely R": PM.SHADES.R[1], "Safe R": PM.SHADES.R[0],
  };
  // How much each ingredient counts, as a tiny stacked bar: polls / markets / fundamentals
  PM.WEIGHT_COLORS = { polls: "var(--ink-2)", markets: "var(--other)", fundamentals: "var(--rule-strong)" };
  PM.weightBar = (w) => `<span class="w-bar" title="Polls ${Math.round(w.polls * 100)}%, markets ${Math.round(w.markets * 100)}%, fundamentals ${Math.round(w.fundamentals * 100)}%">` +
    ["polls", "markets", "fundamentals"].map((k) => `<i style="width:${w[k] * 100}%;background:${PM.WEIGHT_COLORS[k]}"></i>`).join("") +
    `</span>${["polls", "markets", "fundamentals"].map((k) => Math.round(w[k] * 100)).join("/")}`;

  // Polls / Forecast toggle. The choice lives in the URL hash ("#forecast" or
  // "#polls", optionally followed by "&" and whatever else the page keeps there).
  PM.VIEWS = { polls: "Polls", forecast: "Forecast", results: "Results" };
  PM.hashParts = () => location.hash.slice(1).split("&").filter(Boolean);
  PM.hashRest = () => PM.hashParts().filter((p) => !(p in PM.VIEWS));
  PM.setHash = (view, rest) => history.replaceState(null, "", "#" + [view, ...rest].filter(Boolean).join("&"));
  // opts: { views: ["polls", "forecast", "results"], initial: view shown when the URL doesn't pick one }
  PM.viewToggle = (el, onChange, opts = {}) => {
    const views = opts.views ?? ["polls", "forecast"];
    let view = PM.hashParts().find((p) => views.includes(p)) ?? opts.initial ?? "forecast";
    el.innerHTML = `<div class="toggle view-toggle" role="group" aria-label="View">${
      views.map((v) => `<button type="button" data-view="${v}">${PM.VIEWS[v]}</button>`).join("")}</div>`;
    const set = (v, push) => {
      view = v;
      document.body.dataset.view = v;
      el.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.view === v)));
      if (push) PM.setHash(v, PM.hashRest());
      onChange(v);
    };
    el.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => { if (b.dataset.view !== view) set(b.dataset.view, true); }));
    set(view, false);
    return { get: () => view };
  };

  // Distribution of simulated seats: one bar per seat count, blue where Democrats
  // have control, red where they don't, with the control line marked.
  // opts: { hist: [{seats, p}], threshold, thresholdLabel, label, unit, bin }
  // bin > 1: each entry covers seats..seats+bin-1 (e.g. electoral votes in fives)
  PM.renderHistogram = (el, opts) => {
    const hist = opts.hist.filter((h) => h.p >= 0.0005);
    const b = opts.bin || 1;
    const lo = Math.min(hist[0].seats, opts.threshold - b), hi = Math.max(hist[hist.length - 1].seats + b - 1, opts.threshold);
    // Drawn at the container's real width so text stays full size on phones
    const W = Math.max(280, el.clientWidth || 1000), H = W < 600 ? 190 : 260;
    const n = hi - lo + 1, padL = 8, padR = 8, padT = 30, padB = 34;
    const bw = (W - padL - padR) / n, maxP = Math.max(...hist.map((h) => h.p));
    const x = (s) => padL + (s - lo) * bw;
    const y = (p) => padT + (H - padT - padB) * (1 - p / maxP);
    const gap = b * bw > 6 ? 2 : b * bw > 3 ? 1 : 0;   // a surface gap between bars, sized to the bar (not to one seat)
    const bars = hist.map((h, i) => {
      const yy = y(h.p), hh = H - padB - yy, w = Math.max(1, b * bw - gap), r = Math.max(0, Math.min(4, w / 2, hh));
      const fill = h.seats >= opts.threshold ? "var(--dem)" : "var(--rep)";
      // rounded top, square at the baseline
      const path = `M${x(h.seats) + gap / 2},${H - padB}V${yy + r}q0,-${r} ${r},-${r}h${w - 2 * r}q${r},0 ${r},${r}V${H - padB}Z`;
      return `<path d="${path}" fill="${fill}"/><rect class="hit" data-i="${i}" x="${x(h.seats)}" y="${padT}" width="${b * bw}" height="${H - padT - padB}" fill="transparent"/>`;
    }).join("");
    const step = [1, 2, 5, 10, 20, 50, 100].map((k) => k * b).find((k) => k * bw >= 40) || 100 * b;   // labels at least 40px apart
    const ticks = [];
    for (let s = Math.ceil(lo / step) * step; s <= hi; s += step)
      ticks.push(`<text x="${x(s) + (b === 1 ? bw / 2 : 0)}" y="${H - padB + 20}" class="hist-tick">${s}</text>`);
    const tx = x(opts.threshold);
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" class="hist" role="img" aria-label="${PM.esc(opts.label)}: distribution across simulations">
      <line x1="${padL}" x2="${W - padR}" y1="${H - padB}" y2="${H - padB}" class="hist-axis"/>
      ${bars}
      <line x1="${tx}" x2="${tx}" y1="${padT - 12}" y2="${H - padB}" class="hist-line"/>
      <text x="${tx + 6}" y="${padT - 4}" class="hist-label">${PM.esc(opts.thresholdLabel)}</text>
      ${ticks.join("")}
    </svg>`;
    const svg = el.querySelector("svg");
    svg.addEventListener("mousemove", (e) => {
      const t = e.target.closest(".hit");
      if (!t) return PM.hideTip();
      const h = hist[+t.dataset.i];
      PM.showTip(`<strong>${b === 1 ? h.seats : `${h.seats}–${h.seats + b - 1}`} ${PM.esc(opts.unit)}</strong><br>${(h.p * 100).toFixed(1)}% of simulations` +
        `<br>${h.seats >= opts.threshold ? (opts.demWins ?? "Democrats control") : (opts.repWins ?? "Republicans control")}`, e.clientX, e.clientY);
    });
    svg.addEventListener("mouseleave", PM.hideTip);
    if (!el._resize) {
      let lastW = el.clientWidth;
      el._resize = () => { if (el.clientWidth !== lastW) { lastW = el.clientWidth; PM.renderHistogram(el, el._opts); } };
      window.addEventListener("resize", el._resize);
    }
    el._opts = opts;
  };

  // Tracker: each party's chance over time, one point per day, plus the markets'
  // Democratic chance as a dashed line for comparison. Days rebuilt after the fact
  // ("reconstructed") sit on a shaded background.
  // opts: { points: [{date, kind, p_dem, p_rep, market_dem}], end ("YYYY-MM-DD", right edge),
  //         endLabel (e.g. "Election Day"), dem, rep (names), marketLabel }
  PM.renderTracker = (el, opts) => {
    const day = (s) => new Date(s + "T00:00:00Z").getTime();
    const pts = opts.points.filter((p) => p.p_dem != null);
    if (!pts.length) { el.innerHTML = `<p class="muted small">No history yet.</p>`; return; }
    const t0 = day(pts[0].date), tEnd = Math.max(day(opts.end), day(pts[pts.length - 1].date));
    const W = Math.max(280, el.clientWidth || 1000), H = W < 600 ? 220 : 280;
    const padL = 40, padR = W < 600 ? 56 : 70, padT = 30, padB = 28;   // notes sit above the plot, clear of the lines
    const x = (t) => padL + ((t - t0) / Math.max(1, tEnd - t0)) * (W - padL - padR);
    const y = (p) => padT + (1 - p) * (H - padT - padB);
    const line = (key) => {
      const ps = pts.filter((p) => p[key] != null);
      return ps.map((p, i) => `${i ? "L" : "M"}${x(day(p.date)).toFixed(1)},${y(p[key]).toFixed(1)}`).join("");
    };
    const grid = [0, 0.25, 0.5, 0.75, 1].map((g) => `
      <line x1="${padL}" x2="${W - padR}" y1="${y(g)}" y2="${y(g)}" class="${g === 0.5 ? "trk-mid" : "trk-grid"}"/>
      <text x="${padL - 6}" y="${y(g) + 4}" class="trk-axis" text-anchor="end">${g * 100}%</text>`).join("");
    // x labels: weekly for short spans, monthly otherwise
    const spanDays = (tEnd - t0) / 864e5, ticks = [];
    const d = new Date(t0);
    if (spanDays > 70 && d.getUTCDate() !== 1) { d.setUTCDate(1); d.setUTCMonth(d.getUTCMonth() + 1); }
    while (d.getTime() <= tEnd) {
      ticks.push(`<text x="${x(d.getTime())}" y="${H - 8}" class="trk-axis" text-anchor="middle">${
        d.toLocaleDateString("en-US", spanDays > 70 ? { month: "short", timeZone: "UTC" } : { month: "short", day: "numeric", timeZone: "UTC" })}</text>`);
      if (spanDays > 70) d.setUTCMonth(d.getUTCMonth() + 1); else d.setUTCDate(d.getUTCDate() + (spanDays > 21 ? 14 : 7));
    }
    const recon = pts.filter((p) => p.kind === "reconstructed");
    const firstDaily = pts.find((p) => p.kind !== "reconstructed");
    const shade = recon.length ? `<rect x="${x(t0)}" y="${padT}" width="${x(day(firstDaily ? firstDaily.date : recon[recon.length - 1].date)) - x(t0)}"
      height="${H - padT - padB}" class="trk-recon"/><text x="${x(t0) + 2}" y="${padT - 10}" class="trk-note">Reconstructed</text>` : "";
    const endMark = day(opts.end) <= tEnd && opts.endLabel ? `<line x1="${x(day(opts.end))}" x2="${x(day(opts.end))}" y1="${padT}" y2="${H - padB}" class="trk-end"/>
      <text x="${x(day(opts.end))}" y="${padT - 10}" class="trk-note" text-anchor="middle">${PM.esc(opts.endLabel)}</text>` : "";
    const last = pts[pts.length - 1], lx = x(day(last.date));
    const dots = pts.length < 3 ? pts.map((p) => `<circle cx="${x(day(p.date))}" cy="${y(p.p_dem)}" r="4" fill="var(--dem)"/>
      <circle cx="${x(day(p.date))}" cy="${y(p.p_rep)}" r="4" fill="var(--rep)"/>`).join("") : "";
    // End labels, nudged apart if the two lines finish close together
    let yd = y(last.p_dem), yr = y(last.p_rep);
    if (Math.abs(yd - yr) < 16) { const m = (yd + yr) / 2, s = yd < yr ? -8 : 8; yd = m + s; yr = m - s; }
    el.innerHTML = `<svg viewBox="0 0 ${W} ${H}" class="trk" role="img" aria-label="Chance of winning over time">
      ${shade}${grid}${ticks.join("")}${endMark}
      <path d="${line("market_dem")}" class="trk-market"/>
      <path d="${line("p_rep")}" class="trk-line" stroke="var(--rep)"/>
      <path d="${line("p_dem")}" class="trk-line" stroke="var(--dem)"/>
      ${dots}
      <text x="${lx + 6}" y="${yd + 4}" class="trk-end-label" fill="var(--dem)">D ${PM.fmtProb(last.p_dem)}</text>
      <text x="${lx + 6}" y="${yr + 4}" class="trk-end-label" fill="var(--rep)">R ${PM.fmtProb(last.p_rep)}</text>
      <line class="trk-cross" y1="${padT}" y2="${H - padB}" visibility="hidden"/>
      <rect x="${padL}" y="${padT}" width="${W - padL - padR}" height="${H - padT - padB}" fill="transparent" class="trk-hit"/>
    </svg>
    <div class="legend trk-legend">
      <span><i style="background:var(--dem)"></i>${PM.esc(opts.dem)}</span>
      <span><i style="background:var(--rep)"></i>${PM.esc(opts.rep)}</span>
      <span><i class="dash"></i>${PM.esc(opts.marketLabel)}</span>
    </div>`;
    const svg = el.querySelector("svg"), cross = svg.querySelector(".trk-cross");
    svg.querySelector(".trk-hit").addEventListener("mousemove", (e) => {
      const r = svg.getBoundingClientRect(), mx = (e.clientX - r.left) * (W / r.width);
      const p = pts.reduce((b, q) => (Math.abs(x(day(q.date)) - mx) < Math.abs(x(day(b.date)) - mx) ? q : b));
      const px = x(day(p.date));
      cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.setAttribute("visibility", "visible");
      PM.showTip(`<strong>${PM.fmtDate(p.date)}</strong>${p.kind === "reconstructed" ? " <span style=\"opacity:.75\">(reconstructed)</span>" : ""}` +
        `<br>${PM.esc(opts.dem)}: <strong>${PM.fmtProb(p.p_dem)}</strong><br>${PM.esc(opts.rep)}: <strong>${PM.fmtProb(p.p_rep)}</strong>` +
        (p.market_dem != null ? `<br>${PM.esc(opts.marketLabel)}: ${PM.fmtProb(p.market_dem)}` : ""), e.clientX, e.clientY);
    });
    svg.querySelector(".trk-hit").addEventListener("mouseleave", () => { cross.setAttribute("visibility", "hidden"); PM.hideTip(); });
    if (!el._resize) {
      let lastW = el.clientWidth;
      el._resize = () => { if (el.clientWidth !== lastW) { lastW = el.clientWidth; PM.renderTracker(el, el._opts); } };
      window.addEventListener("resize", el._resize);
    }
    el._opts = opts;
  };

  // Stale-data warning: if the newest data is more than two days old (US Eastern
  // dates, like the build), say so at the top of the page. The daily update
  // runs every morning, so this means it has failed at least twice in a row.
  PM.staleNotice = (asOf) => {
    if (!asOf) return;
    const today = new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });   // YYYY-MM-DD
    const days = Math.round((new Date(today + "T00:00:00Z") - new Date(asOf + "T00:00:00Z")) / 864e5);
    if (days <= 2) return;
    const n = document.createElement("p");
    n.className = "notice stale";
    n.setAttribute("role", "status");
    n.innerHTML = `<strong>Heads up:</strong> this page's data was last updated ${PM.fmtDate(asOf)}, ${days} days ago. ` +
      `The daily update may have stopped working, so the numbers could be out of date.`;
    document.querySelector("main").prepend(n);
  };

  // Re-draw maps when the OS switches light/dark
  PM.onThemeChange = (fn) => window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", fn);

  window.PM = PM;
})();
