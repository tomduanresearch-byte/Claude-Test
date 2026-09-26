// Sanity-checks the curated battles: every id referenced in a phase exists,
// every unit is placed somewhere, and coordinates stay on the map.
import { CURATED } from "../public/data/index.js";
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
  b.phases.forEach((p, i) => {
    const where = `${key} phase ${i + 1}`;
    for (const [id, v] of Object.entries(p.positions)) {
      if (!units.has(id)) fail(where, `unknown unit ${id}`);
      const [x, y] = v.filter((n) => typeof n === "number");
      if (!onMap(x, y)) fail(where, `${id} off map`);
      const state = v.find((n) => typeof n === "string");
      if (state && !UNIT_STATES.includes(state)) fail(where, `${id} unknown state ${state}`);
    }
    for (const a of p.arrows) {
      if (!sides.has(a.side)) fail(where, `arrow side ${a.side}`);
      if (!ARROW_KINDS.includes(a.kind)) fail(where, `arrow kind ${a.kind}`);
      if (a.points.length < 2) fail(where, "arrow needs 2+ points");
    }
  });
  for (const id of units.keys()) if (!b.phases.some((p) => p.positions[id])) fail(key, `unit ${id} never placed`);
  console.log(`${key}: ${b.units.length} units, ${b.phases.length} phases`);
}
if (problems) { console.error(`${problems} problem(s)`); process.exit(1); }
console.log("All curated battles OK.");
