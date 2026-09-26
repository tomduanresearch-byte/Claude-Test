// Turns a battle (curated or Claude-generated) into the one shape the UI
// renders, filling gaps so a slightly incomplete generation still plays.
//
// Positions come in two forms:
//   generated: [{ unit, x, y, w, h, angle, state }]
//   curated:   { unitId: [x, y, w?, h?, angle?, "state"?] } — numbers in order
//              x, y, w, h, angle; a string anywhere is the state. Omitted
//              values carry over from the previous phase.

// Muted inks that read on the parchment map plates.
const PALETTE = ["#9e3328", "#2f4f7f", "#3f6b4f", "#a0741f", "#5e3a78"];

const DEFAULT_SIZE = {
  commander: [3, 3], camp: [6, 4], artillery: [5, 2], skirmishers: [20, 1.5],
  archers: [10, 2], elephants: [10, 2], chariots: [12, 2],
  cavalry: [7, 3.5], "heavy-cavalry": [7, 3.5], "light-cavalry": [8, 3],
};

const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
const num = (v, fallback) => (typeof v === "number" && Number.isFinite(v) ? v : fallback);
const arr = (v) => (Array.isArray(v) ? v : []);
const text = (v) => (typeof v === "string" ? v : "");

// Curated battles name people by id ({ person: "hannibal", side, role }) and
// the details come from the shared registry; generated battles carry them inline.
export function normalizeBattle(raw, { id, source, people = {} } = {}) {
  const sides = arr(raw.sides).map((s, i) => ({
    id: text(s.id) || String.fromCharCode(97 + i),
    name: text(s.name) || `Side ${i + 1}`,
    leaders: arr(s.leaders).map(text).filter(Boolean),
    strength: text(s.strength),
    strengthNumber: num(s.strengthNumber, 0),
    composition: arr(s.composition).map((c) => ({ type: text(c.type), count: text(c.count), description: text(c.description) })),
    objective: text(s.objective),
    casualties: text(s.casualties),
    casualtiesNumber: num(s.casualtiesNumber, 0),
    color: text(s.color) || PALETTE[i % PALETTE.length],
  }));
  const sideIds = new Set(sides.map((s) => s.id));

  const seen = new Set();
  const units = arr(raw.units)
    .filter((u) => u && text(u.id) && !seen.has(u.id) && seen.add(u.id))
    .map((u) => ({
      id: u.id,
      side: sideIds.has(u.side) ? u.side : sides[0]?.id,
      name: text(u.name) || u.id,
      type: text(u.type) || "infantry",
      size: text(u.size),
      commander: text(u.commander),
      description: text(u.description),
    }));

  let prev = {};
  const phases = arr(raw.phases).map((p, i) => {
    const incoming = readPositions(p.positions);
    const pos = {};
    for (const u of units) {
      const before = prev[u.id];
      const next = incoming[u.id];
      if (!next && !before) continue;
      const [dw, dh] = DEFAULT_SIZE[u.type] || [10, 3];
      pos[u.id] = {
        x: clamp(num(next?.x, before?.x ?? 50), 0, 100),
        y: clamp(num(next?.y, before?.y ?? 50), 0, 100),
        w: clamp(num(next?.w, before?.w ?? dw), 1, 60),
        h: clamp(num(next?.h, before?.h ?? dh), 0.8, 30),
        angle: num(next?.angle, before?.angle ?? 0),
        state: text(next?.state) || (next ? "ready" : before.state),
      };
    }
    prev = pos;
    return {
      index: i,
      title: text(p.title) || `Phase ${i + 1}`,
      time: text(p.time),
      summary: text(p.summary),
      narrative: text(p.narrative),
      insight: text(p.insight),
      pos,
      // Terrain shown only in this phase (a dam, a flood); curated battles only.
      overlays: arr(p.overlays)
        .map((f) => ({ type: text(f.type), label: text(f.label), points: arr(f.points).filter((pt) => Array.isArray(pt) && pt.length >= 2) }))
        .filter((f) => f.points.length >= 2),
      arrows: arr(p.arrows)
        .map((a) => ({
          side: sideIds.has(a.side) ? a.side : sides[0]?.id,
          kind: text(a.kind) || "move",
          label: text(a.label),
          points: arr(a.points).filter((pt) => Array.isArray(pt) && pt.length >= 2).map(([x, y]) => [clamp(num(x, 50), 0, 100), clamp(num(y, 50), 0, 100)]),
        }))
        .filter((a) => a.points.length >= 2),
    };
  });

  const figures = arr(raw.figures).map((f) => {
    const p = (f.person && people[f.person]) || {};
    return {
      person: text(f.person),
      name: text(f.name) || text(p.name),
      nativeName: text(f.nativeName) || text(p.nativeName),
      side: sideIds.has(f.side) ? f.side : "",
      role: text(f.role),
      life: text(f.life) || text(p.life),
      bio: text(f.bio) || text(p.bio),
      legacy: text(f.legacy) || text(p.legacy),
    };
  }).filter((f) => f.name);

  const ctx = raw.context || {};
  const terrain = raw.terrain || {};
  const strategy = raw.strategy || {};
  const after = raw.aftermath || {};
  const name = text(raw.name) || "Untitled battle";

  return {
    id: id || slug(name),
    source: source || "curated",
    name,
    nativeName: text(raw.nativeName),
    date: text(raw.date),
    year: num(raw.year, 0),
    era: text(raw.era),
    war: text(raw.war),
    location: text(raw.location),
    region: text(raw.region),
    result: text(raw.result),
    summary: text(raw.summary),
    tags: arr(raw.tags).map(text).filter(Boolean),
    context: {
      background: text(ctx.background),
      causes: arr(ctx.causes).map(text).filter(Boolean),
      stakes: text(ctx.stakes),
      prelude: text(ctx.prelude),
    },
    sides,
    figures,
    terrain: {
      description: text(terrain.description),
      orientation: text(terrain.orientation),
      features: arr(terrain.features)
        .map((f) => ({ type: text(f.type), label: text(f.label), points: arr(f.points).filter((pt) => Array.isArray(pt) && pt.length >= 2) }))
        .filter((f) => f.points.length >= 2),
    },
    units,
    strategy: { overview: text(strategy.overview), keyMoves: arr(strategy.keyMoves).map(text).filter(Boolean) },
    phases,
    aftermath: {
      outcome: text(after.outcome),
      consequences: arr(after.consequences).map(text).filter(Boolean),
      lessons: arr(after.lessons).map(text).filter(Boolean),
    },
    sourceNotes: text(raw.sourceNotes),
  };
}

function readPositions(positions) {
  const out = {};
  if (Array.isArray(positions)) {
    for (const p of positions) if (p && text(p.unit)) out[p.unit] = p;
    return out;
  }
  for (const [id, v] of Object.entries(positions || {})) {
    if (!Array.isArray(v)) continue;
    const nums = v.filter((x) => typeof x === "number");
    const state = v.find((x) => typeof x === "string");
    out[id] = { x: nums[0], y: nums[1], w: nums[2], h: nums[3], angle: nums[4], state };
  }
  return out;
}

export function slug(s) {
  return String(s).toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

export function formatYear(year) {
  if (!year) return "";
  return year < 0 ? `${-year} BC` : `AD ${year}`;
}
