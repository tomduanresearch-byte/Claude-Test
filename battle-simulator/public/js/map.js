// Renders a battle onto an SVG map and animates units between phases.
const NS = "http://www.w3.org/2000/svg";
const VW = 1000;
const VH = 640;
const X = (x) => (x / 100) * VW;
const Y = (y) => (y / 100) * VH;

function el(name, attrs = {}, parent) {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

const LINE_FEATURES = new Set(["river", "road", "ridge", "fortification"]);
const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const lerp = (a, b, t) => a + (b - a) * t;

export class BattleMap {
  constructor(container, { onUnitHover } = {}) {
    this.container = container;
    this.onUnitHover = onUnitHover;
    this.svg = el("svg", { viewBox: `0 0 ${VW} ${VH}`, class: "map-svg", role: "img" });
    container.replaceChildren(this.svg);
    this.current = {};
    this.anim = null;
    this.showLabels = true;
  }

  load(battle) {
    this.battle = battle;
    this.sideColor = Object.fromEntries(battle.sides.map((s) => [s.id, s.color]));
    this.svg.replaceChildren();
    this.svg.setAttribute("aria-label", `Map of the ${battle.name}`);
    this.defs();
    this.gTerrain = el("g", { class: "terrain" }, this.svg);
    this.gArrows = el("g", { class: "arrows" }, this.svg);
    this.gUnits = el("g", { class: "units" }, this.svg);
    this.drawTerrain();
    this.unitNodes = {};
    for (const u of battle.units) this.unitNodes[u.id] = this.makeUnit(u);
    this.current = {};
    this.setPhase(0, { instant: true });
  }

  defs() {
    const defs = el("defs", {}, this.svg);
    const grid = el("pattern", { id: "grid", width: 50, height: 50, patternUnits: "userSpaceOnUse" }, defs);
    el("path", { d: "M 50 0 L 0 0 0 50", class: "grid-line" }, grid);
    const trees = el("pattern", { id: "trees", width: 18, height: 18, patternUnits: "userSpaceOnUse" }, defs);
    el("circle", { cx: 5, cy: 5, r: 3.2, class: "tree" }, trees);
    el("circle", { cx: 14, cy: 13, r: 3.2, class: "tree" }, trees);
    const marsh = el("pattern", { id: "marsh", width: 16, height: 10, patternUnits: "userSpaceOnUse" }, defs);
    el("path", { d: "M2 7 h5 M9 3 h5", class: "marsh-line" }, marsh);
    const field = el("pattern", { id: "furrows", width: 10, height: 10, patternUnits: "userSpaceOnUse", patternTransform: "rotate(20)" }, defs);
    el("path", { d: "M0 5 H10", class: "furrow" }, field);
    for (const s of this.battle.sides) {
      for (const kind of ["head", "head-retreat"]) {
        const m = el("marker", { id: `${kind}-${s.id}`, viewBox: "0 0 10 10", refX: 6, refY: 5, markerWidth: 4.5, markerHeight: 4.5, orient: "auto-start-reverse" }, defs);
        el("path", { d: "M0 0 L10 5 L0 10 z", fill: s.color, opacity: kind === "head" ? 0.95 : 0.6 }, m);
      }
    }
  }

  drawTerrain() {
    const g = this.gTerrain;
    el("rect", { x: 0, y: 0, width: VW, height: VH, class: "ground" }, g);
    el("rect", { x: 0, y: 0, width: VW, height: VH, fill: "url(#grid)" }, g);
    const order = ["field", "water", "marsh", "hill", "forest", "town", "ridge", "river", "road", "fortification"];
    const features = [...this.battle.terrain.features].sort((a, b) => order.indexOf(a.type) - order.indexOf(b.type));
    for (const f of features) {
      const pts = f.points.map(([x, y]) => [X(x), Y(y)]);
      if (LINE_FEATURES.has(f.type)) {
        const d = smoothPath(pts);
        if (f.type === "river") el("path", { d, class: "river-bank" }, g);
        el("path", { d, class: `feature feature-${f.type}` }, g);
      } else {
        const d = smoothPath(pts, true);
        if (f.type === "hill") {
          el("path", { d, class: "feature feature-hill" }, g);
          const c = centroid(pts);
          el("path", { d, class: "feature-hill-inner", transform: `translate(${c[0] * 0.4} ${c[1] * 0.4}) scale(0.6)` }, g);
        } else {
          el("path", { d, class: `feature feature-${f.type}` }, g);
          if (f.type === "forest") el("path", { d, fill: "url(#trees)" }, g);
          if (f.type === "marsh") el("path", { d, fill: "url(#marsh)" }, g);
          if (f.type === "field") el("path", { d, fill: "url(#furrows)" }, g);
        }
      }
      if (f.label) {
        const [lx, ly] = labelPoint(f, pts);
        el("text", { x: lx, y: ly, class: `terrain-label terrain-label-${f.type}` }, g).textContent = f.label;
      }
    }
    const compass = el("g", { class: "compass", transform: `translate(${VW - 40} 40)` }, g);
    el("circle", { r: 18 }, compass);
    el("path", { d: "M0 -13 L5 3 L0 0 L-5 3 z" }, compass);
    const up = compassUp(this.battle.terrain.orientation);
    el("text", { y: -22 }, compass).textContent = up;
  }

  makeUnit(u) {
    const g = el("g", { class: `unit unit-${u.type}`, "data-unit": u.id, tabindex: 0 }, this.gUnits);
    const color = this.sideColor[u.side];
    const body = el("g", { class: "unit-body" }, g);
    const rect = el("rect", { class: "unit-rect", fill: color, rx: 2 }, body);
    const sym = el("g", { class: "unit-symbol" }, body);
    const label = el("text", { class: "unit-label" }, g);
    label.textContent = shortName(u.name);
    const node = { g, body, rect, sym, label, unit: u, lastDims: null };
    const hover = (on) => this.onUnitHover?.(on ? u : null, g);
    g.addEventListener("mouseenter", () => hover(true));
    g.addEventListener("mouseleave", () => hover(false));
    g.addEventListener("focus", () => hover(true));
    g.addEventListener("blur", () => hover(false));
    g.addEventListener("click", () => hover(true));
    return node;
  }

  setLabels(on) {
    this.showLabels = on;
    this.svg.classList.toggle("no-labels", !on);
    this.declutter();
  }

  setPhase(index, { instant = false } = {}) {
    const phase = this.battle.phases[index];
    if (!phase) return;
    const from = this.current;
    const to = phase.pos;
    this.drawArrows(phase, instant);
    cancelAnimationFrame(this.anim);
    const duration = instant || matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 1400;
    const start = performance.now();
    const step = (now) => {
      const t = duration ? Math.min(1, (now - start) / duration) : 1;
      const k = ease(t);
      for (const [id, node] of Object.entries(this.unitNodes)) {
        const a = from[id] || to[id];
        const b = to[id];
        if (!b) { node.g.style.display = "none"; continue; }
        node.g.style.display = "";
        this.placeUnit(node, {
          x: lerp(a.x, b.x, k), y: lerp(a.y, b.y, k), w: lerp(a.w, b.w, k), h: lerp(a.h, b.h, k),
          angle: lerp(a.angle, b.angle, k), state: t < 0.5 ? a.state : b.state,
        });
      }
      if (t < 1) this.anim = requestAnimationFrame(step);
      else this.declutter();
    };
    this.anim = requestAnimationFrame(step);
    if (!duration) step(start);
    this.current = to;
  }

  placeUnit(node, s) {
    const w = X(s.w);
    const h = Y(s.h);
    node.g.setAttribute("transform", `translate(${X(s.x).toFixed(1)} ${Y(s.y).toFixed(1)})`);
    node.body.setAttribute("transform", `rotate(${s.angle.toFixed(1)})`);
    node.rect.setAttribute("x", (-w / 2).toFixed(1));
    node.rect.setAttribute("y", (-h / 2).toFixed(1));
    node.rect.setAttribute("width", w.toFixed(1));
    node.rect.setAttribute("height", h.toFixed(1));
    const dims = `${Math.round(w)}x${Math.round(h)}`;
    if (node.lastDims !== dims) {
      node.lastDims = dims;
      drawSymbol(node.sym, node.unit.type, w, h);
    }
    node.g.dataset.state = s.state;
    const rad = (s.angle * Math.PI) / 180;
    const extent = Math.abs(Math.sin(rad)) * w / 2 + Math.abs(Math.cos(rad)) * h / 2;
    node.extent = extent;
    node.label.setAttribute("y", (extent + 12).toFixed(1));
    node.label.style.visibility = "";
  }

  // Moves each unit label to whichever spot (below, above, further out)
  // overlaps the fewest other labels and units; hides it if every spot
  // collides with another label. Hovering a unit still shows its name.
  declutter() {
    if (!this.showLabels) return;
    const nodes = Object.values(this.unitNodes).filter((n) => n.g.style.display !== "none");
    const bodies = nodes.map((n) => [n, n.rect.getBoundingClientRect()]);
    const hit = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
    const placed = [];
    for (const n of nodes) {
      const e = n.extent;
      let best = null;
      for (const y of [e + 12, -(e + 4), e + 25, -(e + 17)]) {
        n.label.setAttribute("y", y.toFixed(1));
        const box = n.label.getBoundingClientRect();
        const score = (placed.some((p) => hit(p, box)) ? 100 : 0) + bodies.filter(([m, r]) => m !== n && hit(r, box)).length;
        if (!best || score < best.score) best = { y, box, score };
        if (!score) break;
      }
      n.label.setAttribute("y", best.y.toFixed(1));
      if (best.score >= 100) n.label.style.visibility = "hidden";
      else placed.push(best.box);
    }
  }

  drawArrows(phase, instant) {
    this.gArrows.replaceChildren();
    for (const a of phase.arrows) {
      const pts = a.points.map(([x, y]) => [X(x), Y(y)]);
      const d = smoothPath(pts);
      const retreat = a.kind === "retreat" || a.kind === "pursuit";
      const path = el("path", {
        d,
        class: `arrow arrow-${a.kind}${instant ? "" : " arrow-draw"}`,
        stroke: this.sideColor[a.side],
        "marker-end": `url(#${retreat ? "head-retreat" : "head"}-${a.side})`,
      }, this.gArrows);
      if (!instant) {
        const len = path.getTotalLength();
        path.style.setProperty("--len", len);
      }
      if (a.label) {
        const mid = pts[Math.floor((pts.length - 1) / 2)];
        const nxt = pts[Math.floor((pts.length - 1) / 2) + 1] || mid;
        const t = el("text", { x: (mid[0] + nxt[0]) / 2, y: (mid[1] + nxt[1]) / 2 - 8, class: "arrow-label", fill: this.sideColor[a.side] }, this.gArrows);
        t.textContent = a.label;
      }
    }
  }
}

function drawSymbol(g, type, w, h) {
  g.replaceChildren();
  const hw = w / 2 - 2;
  const hh = h / 2 - 2;
  if (hw < 2 || hh < 1) return;
  const line = (x1, y1, x2, y2, extra = {}) => el("line", { x1, y1, x2, y2, ...extra }, g);
  const s = Math.min(hh, hw);
  switch (type) {
    case "infantry":
    case "heavy-infantry":
    case "pikes":
      line(-hw, -hh, hw, hh); line(-hw, hh, hw, -hh);
      if (type === "heavy-infantry") line(-hw, 0, hw, 0, { class: "bar" });
      if (type === "pikes") line(0, -hh, 0, hh);
      break;
    case "light-infantry":
      line(-hw, -hh, hw, hh, { class: "dash" }); line(-hw, hh, hw, -hh, { class: "dash" });
      break;
    case "skirmishers":
      for (let x = -hw + 4; x < hw; x += 12) el("circle", { cx: x, cy: 0, r: 1.3, class: "dot" }, g);
      break;
    case "archers":
      for (let x = -hw + 6; x < hw - 2; x += 14) el("path", { d: `M${x - 3} ${s * 0.5} Q${x} ${-s} ${x + 3} ${s * 0.5}` }, g);
      break;
    case "cavalry":
    case "heavy-cavalry":
      line(-hw, hh, hw, -hh);
      if (type === "heavy-cavalry") line(-hw, -hh, -hw + Math.min(8, hw), -hh, { class: "bar" });
      break;
    case "light-cavalry":
      line(-hw, hh, hw, -hh, { class: "dash" });
      break;
    case "elephants":
    case "chariots":
    case "ships":
    case "camp": {
      const t = el("text", { class: "sym-text", "font-size": Math.max(7, Math.min(13, h - 2)) }, g);
      t.textContent = { elephants: "ELE", chariots: "CHR", ships: "⚓", camp: "CAMP" }[type];
      break;
    }
    case "artillery":
      el("circle", { r: Math.max(2, s * 0.6), class: "fill" }, g);
      break;
    case "armor":
      el("rect", { x: -hw * 0.7, y: -hh * 0.6, width: hw * 1.4, height: hh * 1.2, rx: hh * 0.6 }, g);
      break;
    case "commander":
      el("path", { d: starPath(Math.max(3, s * 1.4)), class: "fill" }, g);
      break;
  }
}

function starPath(r) {
  let d = "";
  for (let i = 0; i < 10; i++) {
    const rr = i % 2 ? r * 0.45 : r;
    const a = (Math.PI / 5) * i - Math.PI / 2;
    d += `${i ? "L" : "M"}${(Math.cos(a) * rr).toFixed(2)} ${(Math.sin(a) * rr).toFixed(2)}`;
  }
  return d + "z";
}

// Catmull-Rom spline through the points, as a cubic Bézier path.
function smoothPath(pts, closed = false) {
  if (pts.length < 3) return `M${pts.map((p) => p.join(" ")).join(" L")}${closed ? "z" : ""}`;
  const P = closed ? [pts[pts.length - 1], ...pts, pts[0], pts[1]] : [pts[0], ...pts, pts[pts.length - 1]];
  let d = `M${P[1][0].toFixed(1)} ${P[1][1].toFixed(1)}`;
  const n = closed ? pts.length : pts.length - 1;
  for (let i = 1; i <= n; i++) {
    const [p0, p1, p2, p3] = [P[i - 1], P[i], P[i + 1], P[i + 2]];
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C${c1.map((v) => v.toFixed(1)).join(" ")} ${c2.map((v) => v.toFixed(1)).join(" ")} ${p2.map((v) => v.toFixed(1)).join(" ")}`;
  }
  return closed ? d + "z" : d;
}

function centroid(pts) {
  const s = pts.reduce((a, p) => [a[0] + p[0], a[1] + p[1]], [0, 0]);
  return [s[0] / pts.length, s[1] / pts.length];
}

function labelPoint(f, pts) {
  if (LINE_FEATURES.has(f.type)) {
    const p = pts[Math.min(pts.length - 1, 1)];
    return [p[0] + 10, p[1] + 4];
  }
  return centroid(pts);
}

function compassUp(orientation = "") {
  const m = orientation.toLowerCase().match(/\b(north|south|east|west|northeast|northwest|southeast|southwest)\b[^.]*\bup\b|\bup\b[^.]*\b(north|south|east|west)\b/);
  const dir = m?.[1] || m?.[2] || "north";
  return { north: "N", south: "S", east: "E", west: "W", northeast: "NE", northwest: "NW", southeast: "SE", southwest: "SW" }[dir];
}

function shortName(name) {
  return name.length > 26 ? name.slice(0, 24) + "…" : name;
}
