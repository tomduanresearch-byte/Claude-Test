// Sanity-checks the curated battles: every id referenced in a phase exists,
// every unit is placed somewhere, and coordinates stay on the map.
import { CURATED, PEOPLE } from "../public/data/index.js";
import { ZH_BATTLES, ZH_PEOPLE } from "../public/data/zh/index.js";
import { ERAS, UNIT_TYPES, UNIT_STATES, TERRAIN_TYPES, ARROW_KINDS } from "../public/js/schema.js";

let problems = 0;
const fail = (battle, msg) => { problems++; console.error(`${battle}: ${msg}`); };
const onMap = (x, y) => x >= 0 && x <= 100 && y >= 0 && y <= 100;

for (const [key, b] of Object.entries(CURATED)) {
  if (!ERAS.includes(b.era)) fail(key, `unknown era ${b.era}`);
  const sides = new Set(b.sides.map((s) => s.id));
  const units = new Map(b.units.map((u) => [u.id, u]));
  if (units.size !== b.units.length) fail(key, "duplicate unit ids");
  for (const u of b.units) {
    if (!sides.has(u.side)) fail(key, `unit ${u.id} has unknown side ${u.side}`);
    if (!UNIT_TYPES.includes(u.type)) fail(key, `unit ${u.id} has unknown type ${u.type}`);
  }
  for (const f of b.terrain.features) {
    if (!TERRAIN_TYPES.includes(f.type)) fail(key, `unknown terrain ${f.type}`);
    for (const [x, y] of f.points) if (!onMap(x, y)) fail(key, `terrain point off map ${x},${y}`);
  }
  for (const f of b.figures || []) {
    if (!PEOPLE[f.person]) fail(key, `unknown person ${f.person}`);
    if (!sides.has(f.side)) fail(key, `figure ${f.person} has unknown side ${f.side}`);
  }
  if (!(b.figures || []).length) fail(key, "no figures");
  b.phases.forEach((p, i) => {
    const where = `${key} phase ${i + 1}`;
    for (const [id, v] of Object.entries(p.positions)) {
      if (!units.has(id)) fail(where, `unknown unit ${id}`);
      const [x, y] = v.filter((n) => typeof n === "number");
      if (!onMap(x, y)) fail(where, `${id} off map`);
      const state = v.find((n) => typeof n === "string");
      if (state && !UNIT_STATES.includes(state)) fail(where, `${id} unknown state ${state}`);
    }
    for (const f of p.overlays || []) if (!TERRAIN_TYPES.includes(f.type)) fail(where, `unknown overlay ${f.type}`);
    for (const a of p.arrows) {
      if (!sides.has(a.side)) fail(where, `arrow side ${a.side}`);
      if (!ARROW_KINDS.includes(a.kind)) fail(where, `arrow kind ${a.kind}`);
      if (a.points.length < 2) fail(where, "arrow needs 2+ points");
    }
  });
  for (const id of units.keys()) if (!b.phases.some((p) => p.positions[id])) fail(key, `unit ${id} never placed`);
  console.log(`${key}: ${b.units.length} units, ${b.phases.length} phases`);
}
// Chinese overlays must line up with the originals, and translate every text field.
const TEXT = ["name", "date", "war", "location", "result", "summary", "sourceNotes"];
for (const [key, b] of Object.entries(CURATED)) {
  const z = ZH_BATTLES[key];
  if (!z) { fail(key, "no Chinese translation"); continue; }
  const where = `zh/${key}`;
  for (const f of TEXT) if (b[f] && !z[f]) fail(where, `missing ${f}`);
  for (const f of ["background", "stakes", "prelude"]) if (!z.context?.[f]) fail(where, `missing context.${f}`);
  if ((z.context?.causes || []).length !== b.context.causes.length) fail(where, "causes count differs");
  if ((z.sides || []).length !== b.sides.length) fail(where, "sides count differs");
  b.sides.forEach((s, i) => {
    const zs = z.sides?.[i] || {};
    for (const f of ["name", "strength", "objective", "casualties"]) if (s[f] && !zs[f]) fail(where, `side ${i} missing ${f}`);
    if ((zs.leaders || []).length !== s.leaders.length) fail(where, `side ${i} leaders count differs`);
    if ((zs.composition || []).length !== s.composition.length) fail(where, `side ${i} composition count differs`);
  });
  if (!z.terrain?.description || !z.terrain?.orientation) fail(where, "missing terrain text");
  if ((z.terrain?.features || []).length !== b.terrain.features.length) fail(where, "feature label count differs");
  for (const u of b.units) {
    const zu = z.units?.[u.id];
    if (!zu?.name || (u.description && !zu.description)) fail(where, `unit ${u.id} untranslated`);
  }
  for (const id of Object.keys(z.units || {})) if (!b.units.some((u) => u.id === id)) fail(where, `unknown unit ${id}`);
  for (const f of b.figures) if (!z.figures?.[f.person]?.role) fail(where, `figure ${f.person} role untranslated`);
  if (!z.strategy?.overview || (z.strategy.keyMoves || []).length !== b.strategy.keyMoves.length) fail(where, "strategy differs");
  if ((z.phases || []).length !== b.phases.length) fail(where, "phase count differs");
  b.phases.forEach((p, i) => {
    const zp = z.phases?.[i] || {};
    for (const f of ["title", "time", "summary", "narrative", "insight"]) if (p[f] && !zp[f]) fail(where, `phase ${i + 1} missing ${f}`);
    if ((zp.arrows || []).length !== p.arrows.length) fail(where, `phase ${i + 1} arrow label count differs`);
    if ((zp.overlays || []).length !== (p.overlays || []).length) fail(where, `phase ${i + 1} overlay label count differs`);
  });
  if (!z.aftermath?.outcome || z.aftermath.consequences?.length !== b.aftermath.consequences.length || z.aftermath.lessons?.length !== b.aftermath.lessons.length) fail(where, "aftermath differs");
}
for (const id of Object.keys(PEOPLE)) {
  const z = ZH_PEOPLE[id];
  if (!z?.name || !z.bio || !z.legacy || !z.life) fail("zh/people", `${id} untranslated`);
}

if (problems) { console.error(`${problems} problem(s)`); process.exit(1); }
console.log("All curated battles OK.");
