import { formatYear } from "./normalize.js";

export { ERAS } from "./schema.js";

// Every word in the query must match somewhere in the battle; matches in the
// name, commanders and sides rank above matches in the prose.
export function searchBattles(battles, query, era) {
  const words = tokens(query);
  const pool = era ? battles.filter((b) => b.era === era) : battles;
  if (!words.length) return [...pool].sort((a, b) => a.year - b.year);
  return pool
    .map((b) => ({ b, score: score(b, words) }))
    .filter((r) => r.score > 0)
    .sort((x, y) => y.score - x.score || x.b.year - y.b.year)
    .map((r) => r.b);
}

function tokens(q) {
  return String(q).toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "")
    .replace(/\b(the|of|battle|at|a|an|and|vs|versus)\b/g, " ")
    .split(/[^a-z0-9]+/).filter(Boolean);
}

function fold(s) {
  return String(s).toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
}

function score(b, words) {
  const primary = fold([b.name, b.war, ...b.sides.flatMap((s) => [s.name, ...s.leaders]), ...b.tags].join(" | "));
  const secondary = fold([b.location, b.region, b.era, b.summary, formatYear(b.year), b.date].join(" | "));
  let total = 0;
  for (const w of words) {
    const s = yearMatch(b.year, w) || (primary.includes(w) ? 3 : 0) || (secondary.includes(w) ? 1 : 0);
    if (!s) return 0;
    total += s;
  }
  return total;
}

// "1805" matches that year, "1800s" the century, "bc"/"ad" the era side.
function yearMatch(year, w) {
  const abs = Math.abs(year);
  if (w === "bc" || w === "bce") return year < 0 ? 1 : 0;
  if (w === "ad" || w === "ce") return year > 0 ? 1 : 0;
  let m = w.match(/^(\d{2,4})s$/);
  if (m) {
    const start = Number(m[1]);
    const span = start % 100 === 0 ? 100 : 10;
    return abs >= start && abs < start + span ? 2 : 0;
  }
  m = w.match(/^(\d{1,2})(st|nd|rd|th)$/);
  if (m) return Math.ceil(abs / 100) === Number(m[1]) ? 2 : 0;
  if (/^\d{3,4}$/.test(w)) return abs === Number(w) ? 4 : 0;
  return 0;
}
