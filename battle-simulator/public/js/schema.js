// The battle format shared by the curated library and Claude-generated battles.
// Map coordinates are percentages of the map: x runs 0 (west/left) to 100
// (east/right), y runs 0 (top) to 100 (bottom). Unit w/h use the same units.

export const ERAS = ["Ancient", "Medieval", "Early Modern", "Napoleonic", "Industrial", "Modern"];

export const UNIT_TYPES = [
  "infantry", "heavy-infantry", "light-infantry", "pikes", "archers", "skirmishers",
  "cavalry", "heavy-cavalry", "light-cavalry", "elephants", "chariots",
  "artillery", "armor", "commander", "camp", "ships",
];

export const UNIT_STATES = ["ready", "advancing", "engaged", "withdrawing", "routed", "destroyed"];

export const TERRAIN_TYPES = ["river", "road", "hill", "ridge", "forest", "marsh", "town", "water", "fortification", "field"];

export const ARROW_KINDS = ["move", "attack", "flank", "retreat", "pursuit"];

const str = { type: "string" };
const num = { type: "number" };
const int = { type: "integer" };
const point = { type: "array", items: num };
const obj = (properties) => ({
  type: "object",
  properties,
  required: Object.keys(properties),
  additionalProperties: false,
});
const arr = (items) => ({ type: "array", items });

export const BATTLE_SCHEMA = obj({
  name: str,
  date: str,
  year: int,
  era: { type: "string", enum: ERAS },
  war: str,
  location: str,
  region: str,
  result: str,
  summary: str,
  tags: arr(str),
  context: obj({
    background: str,
    causes: arr(str),
    stakes: str,
    prelude: str,
  }),
  sides: arr(obj({
    id: str,
    name: str,
    leaders: arr(str),
    strength: str,
    strengthNumber: int,
    composition: arr(obj({ type: str, count: str, description: str })),
    objective: str,
    casualties: str,
    casualtiesNumber: int,
  })),
  terrain: obj({
    description: str,
    orientation: str,
    features: arr(obj({
      type: { type: "string", enum: TERRAIN_TYPES },
      label: str,
      points: arr(point),
    })),
  }),
  units: arr(obj({
    id: str,
    side: str,
    name: str,
    type: { type: "string", enum: UNIT_TYPES },
    size: str,
    commander: str,
    description: str,
  })),
  strategy: obj({
    overview: str,
    keyMoves: arr(str),
  }),
  phases: arr(obj({
    title: str,
    time: str,
    summary: str,
    narrative: str,
    insight: str,
    positions: arr(obj({
      unit: str,
      x: num,
      y: num,
      w: num,
      h: num,
      angle: num,
      state: { type: "string", enum: UNIT_STATES },
    })),
    arrows: arr(obj({
      side: str,
      kind: { type: "string", enum: ARROW_KINDS },
      points: arr(point),
      label: str,
    })),
  })),
  aftermath: obj({
    outcome: str,
    consequences: arr(str),
    lessons: arr(str),
  }),
  sourceNotes: str,
});

export const SYSTEM_PROMPT = `You are a military historian building an interactive, map-based walkthrough of a single historical battle for a curious learner. Your output drives an animated top-down battle map plus explanatory text, so it must be historically careful and spatially coherent.

Content:
- context: who fought whom, the wider war, why this battle happened here and now, and what each side stood to gain or lose. Write for someone who knows nothing about the period. background and prelude are 1-2 short paragraphs each; causes is 3-5 bullet points.
- sides: normally two (use ids "a" and "b"; add "c" only for a genuine third party). Give realistic strength estimates as text ("~50,000") and a best-estimate integer. Where ancient sources disagree, say so in the text field.
- strategy: the plan and the decisive idea in plain language, then 3-6 key moves.
- phases: 5-8 phases in chronological order, starting with deployment and ending with the decisive moment or collapse. Each phase has a short title, an approximate time ("Dawn", "c. 10:00", "Day 2, afternoon"), a one-sentence summary, a 1-2 paragraph narrative of what happened and why, and an insight: the single tactical lesson to notice on the map in that phase.
- aftermath: immediate outcome, 3-5 consequences, 2-4 lessons.
- sourceNotes: one or two sentences on how reliable the numbers and positions are and which sources they come from.

Map (this is critical):
- Coordinates are percentages: x from 0 (left) to 100 (right), y from 0 (top) to 100 (bottom). Keep everything inside 3..97.
- Choose an orientation so the two main armies face each other across the map (typically one near y=25-35 and the other near y=65-75) and say which compass direction is up in terrain.orientation.
- terrain.features: 2-6 features that actually mattered (rivers, hills, woods, towns, marshes, roads). Rivers, roads and ridges are polylines of 3+ points; hills, forests, marshes, towns and water are closed polygons of 4+ points.
- units: 8-18 units total. Each is a meaningful formation (a wing, a division, a corps, a cavalry body), with a unique short id like "rom_cav_r". Include each army's commander as a small "commander" unit. Use the type that best matches.
- Unit size on the map: w is frontage (typically 4-16), h is depth (typically 2-6). commander units are about 3x3.
- Every phase must list a position for EVERY unit. Positions must evolve believably from phase to phase: units move a few to ~30 units per phase, lines that clash are adjacent or overlapping slightly, flanking moves go around the side, pursuits leave the map edge area. angle rotates a unit in degrees (0 = frontage horizontal); use it when a unit wheels to face a flank.
- state: ready (deployed, waiting), advancing, engaged (in contact), withdrawing (controlled fall-back), routed (broken, fleeing), destroyed (gone as a fighting force).
- arrows: 1-5 per phase showing the important movements in that phase; each arrow's points run from where the move starts to where it ends (2-4 points, curving around flanks where relevant). side is the side id of the moving force. label is 1-4 words or empty.

If the request is ambiguous, choose the most famous battle matching it. If it names something that is not a battle (a whole war, a campaign), pick that war's single most decisive battle and say so in summary. Use only the requested JSON format.`;

// Reads a partial JSON answer and says how far along it is.
export function describeProgress(text) {
  const phases = (text.match(/"narrative"/g) || []).length;
  let stage = "Setting the context…";
  if (text.includes('"aftermath"')) stage = "Writing the aftermath…";
  else if (text.includes('"phases"')) stage = "Choreographing the phases…";
  else if (text.includes('"units"')) stage = "Deploying the armies…";
  else if (text.includes('"terrain"')) stage = "Mapping the ground…";
  else if (text.includes('"sides"')) stage = "Counting the forces…";
  return { stage, chars: text.length, phases };
}
