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
  //         segments: [{label, ev, color, detail}], total }
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
        <div class="ev-270" style="left:${(270 / opts.total) * 100}%"><span>270 to win</span></div>
      </div>
      <div class="legend">
        ${opts.segments.map((s) => `<span><i style="background:${s.color}"></i>${PM.esc(s.label)} <span class="muted">${s.ev}</span></span>`).join("")}
      </div>`;
    el.querySelectorAll(".ev-seg").forEach((node) => {
      const s = segs[+node.dataset.i];
      const html = `<strong>${PM.esc(s.label)}</strong>: ${s.ev} electoral vote${s.ev === 1 ? "" : "s"}${s.detail ? `<br>${s.detail}` : ""}`;
      node.addEventListener("mousemove", (e) => PM.showTip(html, e.clientX, e.clientY));
      node.addEventListener("mouseleave", PM.hideTip);
      node.addEventListener("focus", () => { const r = node.getBoundingClientRect(); PM.showTip(html, r.left, r.bottom); });
      node.addEventListener("blur", PM.hideTip);
    });
  };

  // Map --------------------------------------------------------------------------
  // rows: [{state, margin, hover}]; rows with color set (e.g. third-party wins)
  // are drawn as a separate flat-colored layer.
  PM.renderMap = (el, rows) => {
    const css = getComputedStyle(document.documentElement);
    const border = css.getPropertyValue("--surface").trim() || "#fff";
    const ink = css.getPropertyValue("--ink").trim() || "#111";
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
      z: scaled.map((r) => Math.max(-PM.MARGIN_RANGE, Math.min(PM.MARGIN_RANGE, r.margin))),
      text: scaled.map((r) => r.hover),
      zmin: -PM.MARGIN_RANGE, zmax: PM.MARGIN_RANGE, colorscale: PM.MARGIN_SCALE,
    }];
    if (flat.length) {
      traces.push({
        ...base,
        locations: flat.map((r) => r.state), z: flat.map(() => 1), text: flat.map((r) => r.hover),
        zmin: 0, zmax: 1, colorscale: [[0, flat[0].color], [1, flat[0].color]],
      });
    }
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
  };

  PM.renderScale = (el, { other } = {}) => {
    const stops = PM.MARGIN_SCALE.map(([p, c]) => `${c} ${p * 100}%`).join(",");
    el.innerHTML = `
      <span>R+${PM.MARGIN_RANGE}</span>
      <span class="ramp" style="background:linear-gradient(90deg,${stops})"></span>
      <span>D+${PM.MARGIN_RANGE}</span>
      ${other ? `<span class="other-key"><i></i>${PM.esc(other)}</span>` : ""}`;
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

  // Re-draw maps when the OS switches light/dark
  PM.onThemeChange = (fn) => window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", fn);

  window.PM = PM;
})();
