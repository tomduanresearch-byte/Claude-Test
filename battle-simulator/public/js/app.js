import { CURATED } from "../data/index.js";
import { normalizeBattle, formatYear, slug } from "./normalize.js";
import { BattleMap } from "./map.js";
import { searchBattles, ERAS } from "./search.js";
import { generateBattle, checkGenerator } from "./generate.js";

const STORE_KEY = "battle-atlas:generated";
const app = document.getElementById("app");
const tooltip = document.getElementById("tooltip");

const state = {
  query: "",
  era: "",
  generator: { available: false },
  generating: null,
  map: null,
  playing: null,
};

// ---------- library ----------

function loadGenerated() {
  try {
    return JSON.parse(localStorage.getItem(STORE_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveGenerated(id, raw) {
  try {
    const all = loadGenerated();
    all[id] = raw;
    localStorage.setItem(STORE_KEY, JSON.stringify(all));
  } catch {
    // Storage full or blocked: the battle still works for this visit.
  }
}

function removeGenerated(id) {
  try {
    const all = loadGenerated();
    delete all[id];
    localStorage.setItem(STORE_KEY, JSON.stringify(all));
  } catch {}
}

const sessionBattles = new Map();

function library() {
  const curated = Object.entries(CURATED).map(([id, raw]) => normalizeBattle(raw, { id, source: "curated" }));
  const generated = Object.entries(loadGenerated()).map(([id, raw]) => normalizeBattle(raw, { id, source: "generated" }));
  const ids = new Set([...curated, ...generated].map((b) => b.id));
  const session = [...sessionBattles.values()].filter((b) => !ids.has(b.id));
  return [...curated, ...generated, ...session];
}

function findBattle(id) {
  return library().find((b) => b.id === id);
}

// ---------- routing ----------

function parseRoute() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  if (parts[0] === "battle" && parts[1]) {
    return { view: "battle", id: parts[1], tab: parts[2] || "context", phase: Number(parts[3]) || 1 };
  }
  return { view: "home" };
}

function go(hash) {
  if (location.hash === hash) render();
  else location.hash = hash;
}

function render() {
  stopPlaying();
  hideTooltip();
  const route = parseRoute();
  if (route.view === "battle") {
    const battle = findBattle(route.id);
    if (battle) return renderBattle(battle, route.tab, route.phase);
  }
  renderHome();
}

window.addEventListener("hashchange", render);

// ---------- home ----------

function renderHome() {
  document.title = "Battle Atlas";
  const view = document.getElementById("tpl-home").content.cloneNode(true);
  app.replaceChildren(view);
  const form = app.querySelector(".search");
  const input = form.querySelector("input");
  input.value = state.query;

  const chips = app.querySelector(".chips");
  for (const era of ["", ...ERAS]) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip";
    b.textContent = era || "All periods";
    b.setAttribute("aria-pressed", String(state.era === era));
    b.addEventListener("click", () => {
      state.era = era;
      chips.querySelectorAll(".chip").forEach((c) => c.setAttribute("aria-pressed", String(c === b)));
      updateResults();
    });
    chips.appendChild(b);
  }

  input.addEventListener("input", () => {
    state.query = input.value;
    updateResults();
  });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const hits = searchBattles(library(), state.query, state.era);
    if (hits.length === 1) go(`#/battle/${hits[0].id}`);
    else if (!hits.length && state.query.trim() && state.generator.available) startGeneration(state.query.trim());
  });
  updateResults();
  if (!matchMedia("(pointer: coarse)").matches) input.focus();
}

function updateResults() {
  const results = app.querySelector(".results");
  const title = app.querySelector(".results-title");
  if (!results) return;
  const hits = searchBattles(library(), state.query, state.era);
  const q = state.query.trim();
  title.textContent = q || state.era
    ? `${hits.length} ${hits.length === 1 ? "battle" : "battles"} in the atlas`
    : "In the atlas";
  results.replaceChildren(...hits.map(battleCard));
  renderGenerateSlot(q, hits.length);
}

function battleCard(b) {
  const a = document.createElement("a");
  a.className = "card";
  a.href = `#/battle/${encodeURIComponent(b.id)}`;
  const sides = b.sides.slice(0, 3);
  a.innerHTML = `
    <div class="card-stripe">${sides.map((s) => `<span style="background:${s.color}"></span>`).join("")}</div>
    <p class="eyebrow">${esc(b.war || b.era)} · ${esc(formatYear(b.year))}</p>
    <h3>${esc(b.name)}</h3>
    <p class="card-sides">${sides.map((s) => `<span><i style="background:${s.color}"></i>${esc(shortSide(s))}</span>`).join('<em>vs</em>')}</p>
    <p class="card-summary">${esc(b.summary)}</p>
    <p class="card-foot">${esc(b.location)}${b.source === "generated" ? '<span class="badge">Generated</span>' : ""}</p>`;
  return a;
}

function shortSide(s) {
  const lead = s.leaders[0] ? s.leaders[0].replace(/\s*\(.*\)$/, "") : "";
  return lead ? `${lead}` : s.name;
}

function renderGenerateSlot(q, hitCount) {
  const slot = app.querySelector(".generate-slot");
  if (!slot) return;
  slot.replaceChildren();
  if (state.generating) {
    slot.appendChild(progressPanel());
    return;
  }
  if (!q) return;
  const box = document.createElement("div");
  box.className = "generate";
  const avail = state.generator.available;
  box.innerHTML = `
    <div>
      <p class="eyebrow">${hitCount ? "Looking for something else?" : "Not in the atlas yet"}</p>
      <h3>Generate “${esc(q)}” with Claude</h3>
      <p>${avail
        ? "Claude researches the battle and builds a full walkthrough: context, forces, map and phases. Takes one to three minutes."
        : "Generation isn't available here. Open this page on claude.ai, or run the project's local server with an Anthropic API key (see the README)."}</p>
    </div>`;
  const btn = document.createElement("button");
  btn.className = "btn btn-primary";
  btn.textContent = "Generate walkthrough";
  btn.disabled = !avail;
  btn.addEventListener("click", () => startGeneration(q));
  box.appendChild(btn);
  slot.appendChild(box);
}

function progressPanel() {
  const g = state.generating;
  const box = document.createElement("div");
  box.className = "generate generating";
  const pct = Math.min(96, Math.round((g.chars / 28000) * 100));
  box.innerHTML = g.error
    ? `<div><p class="eyebrow">Generation failed</p><h3>${esc(g.query)}</h3><p>${esc(g.error)}</p></div>`
    : `<div style="flex:1">
        <p class="eyebrow">Generating · ${esc(g.query)}</p>
        <h3>${esc(g.stage)}</h3>
        <div class="bar"><span style="width:${Math.max(4, pct)}%"></span></div>
        <p class="muted">${g.phases ? `${g.phases} phase${g.phases === 1 ? "" : "s"} drafted` : "Working…"}</p>
      </div>`;
  const btn = document.createElement("button");
  btn.className = "btn";
  if (g.error) {
    btn.textContent = "Dismiss";
    btn.addEventListener("click", () => { state.generating = null; updateResults(); });
  } else {
    btn.textContent = "Stop";
    btn.addEventListener("click", () => g.controller.abort());
  }
  box.appendChild(btn);
  return box;
}

async function startGeneration(query) {
  if (state.generating && !state.generating.error) return;
  const controller = new AbortController();
  const job = { query, stage: "Researching the battle…", chars: 0, phases: 0, controller };
  state.generating = job;
  updateResults();
  try {
    const raw = await generateBattle(query, (p) => {
      if (state.generating !== job) return;
      if (p.stage === job.stage && p.phases === job.phases && p.chars - job.chars < 600) return;
      Object.assign(job, p);
      updateResults();
    }, controller.signal);
    if (!raw || !Array.isArray(raw.phases) || !raw.phases.length || !Array.isArray(raw.units) || !raw.units.length) {
      throw new Error("The answer was missing its map or phases. Try again.");
    }
    const id = `gen-${slug(raw.name || query)}`;
    saveGenerated(id, raw);
    sessionBattles.set(id, normalizeBattle(raw, { id, source: "generated" }));
    state.generating = null;
    go(`#/battle/${encodeURIComponent(id)}`);
  } catch (err) {
    job.error = err.message || "Generation failed.";
    updateResults();
  }
}

// ---------- battle ----------

const TABS = [
  ["context", "1 · Context"],
  ["simulator", "2 · Walkthrough"],
  ["aftermath", "3 · Aftermath"],
];

function renderBattle(b, tab, phase) {
  document.title = `${b.name} · Battle Atlas`;
  const view = document.getElementById("tpl-battle").content.cloneNode(true);
  app.replaceChildren(view);
  app.querySelector(".eyebrow").textContent = [b.war, formatYear(b.year) && b.date].filter(Boolean).join(" · ");
  app.querySelector("h1").textContent = b.name;
  const meta = app.querySelector(".meta");
  meta.innerHTML = `${esc(b.location)}${b.result ? ` <span class="result">${esc(b.result)}</span>` : ""}${b.source === "generated" ? ' <span class="badge">Generated by Claude</span>' : ""}`;

  const tabs = app.querySelector(".tabs");
  for (const [key, label] of TABS) {
    const a = document.createElement("a");
    a.href = `#/battle/${encodeURIComponent(b.id)}/${key}`;
    a.textContent = label;
    a.setAttribute("role", "tab");
    a.setAttribute("aria-selected", String(key === tab));
    tabs.appendChild(a);
  }

  const body = app.querySelector(".tab-body");
  if (tab === "simulator") renderSimulator(body, b, phase);
  else if (tab === "aftermath") renderAftermath(body, b);
  else renderContext(body, b);
  window.scrollTo({ top: 0 });
}

function renderContext(body, b) {
  const max = Math.max(...b.sides.map((s) => s.strengthNumber), 1);
  body.innerHTML = `
    <div class="context">
      <p class="summary">${esc(b.summary)}</p>

      <h2>Who fought</h2>
      <div class="sides">
        ${b.sides.map((s) => `
          <article class="side" style="--side:${s.color}">
            <h3>${esc(s.name)}</h3>
            <p class="leaders">${s.leaders.map(esc).join(" · ")}</p>
            <dl>
              <dt>Strength</dt><dd>${esc(s.strength)}</dd>
              <dt>Aim</dt><dd>${esc(s.objective)}</dd>
            </dl>
            <table class="composition">
              ${s.composition.map((c) => `<tr><th>${esc(c.type)}<small>${esc(c.description)}</small></th><td>${esc(c.count)}</td></tr>`).join("")}
            </table>
          </article>`).join("")}
      </div>

      <div class="forces">
        <h3>Forces at the start</h3>
        ${b.sides.map((s) => `
          <div class="force-row">
            <span class="force-name">${esc(shortSide(s))}</span>
            <span class="force-bar"><span style="width:${(s.strengthNumber / max) * 100}%;background:${s.color}"></span></span>
            <span class="force-num">${s.strengthNumber ? s.strengthNumber.toLocaleString() : "?"}</span>
          </div>`).join("")}
      </div>

      <div class="two-col">
        <section>
          <h2>Why they fought</h2>
          ${paras(b.context.background)}
          ${b.context.causes.length ? `<ul class="causes">${b.context.causes.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : ""}
          ${b.context.stakes ? `<p class="stakes"><strong>What was at stake.</strong> ${esc(b.context.stakes)}</p>` : ""}
        </section>
        <section>
          <h2>The road to battle</h2>
          ${paras(b.context.prelude)}
          <h2>The ground</h2>
          ${paras(b.terrain.description)}
        </section>
      </div>

      <section class="plan">
        <h2>The winning plan</h2>
        ${paras(b.strategy.overview)}
        <ol class="moves">${b.strategy.keyMoves.map((m) => `<li>${esc(m)}</li>`).join("")}</ol>
      </section>

      <div class="cta">
        <a class="btn btn-primary btn-lg" href="#/battle/${encodeURIComponent(b.id)}/simulator/1">Walk through the battle: ${b.phases.length} phases →</a>
      </div>
    </div>`;
}

function renderAftermath(body, b) {
  const max = Math.max(...b.sides.map((s) => s.strengthNumber), 1);
  body.innerHTML = `
    <div class="context">
      <p class="summary">${esc(b.aftermath.outcome)}</p>
      <div class="forces">
        <h3>Losses against starting strength</h3>
        ${b.sides.map((s) => `
          <div class="force-row">
            <span class="force-name">${esc(shortSide(s))}</span>
            <span class="force-bar">
              <span class="ghost" style="width:${(s.strengthNumber / max) * 100}%;background:${s.color}"></span>
              <span style="width:${(Math.min(s.casualtiesNumber, s.strengthNumber || s.casualtiesNumber) / max) * 100}%;background:${s.color}"></span>
            </span>
            <span class="force-num">${esc(s.casualties || "?")}</span>
          </div>`).join("")}
        <p class="muted">Solid bar: estimated killed, wounded or captured. Faded bar: strength at the start.</p>
      </div>
      <div class="two-col">
        <section>
          <h2>What happened next</h2>
          <ul class="causes">${b.aftermath.consequences.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>
        </section>
        <section>
          <h2>Lessons</h2>
          <ul class="causes">${b.aftermath.lessons.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>
          ${b.sourceNotes ? `<h2>About the sources</h2><p class="muted">${esc(b.sourceNotes)}</p>` : ""}
        </section>
      </div>
      <div class="cta">
        <a class="btn" href="#/battle/${encodeURIComponent(b.id)}/simulator/1">↺ Replay the walkthrough</a>
        ${b.source === "generated" ? '<button class="btn btn-quiet" id="delete-gen">Remove from atlas</button>' : ""}
      </div>
    </div>`;
  body.querySelector("#delete-gen")?.addEventListener("click", () => {
    removeGenerated(b.id);
    sessionBattles.delete(b.id);
    go("#/");
  });
}

function renderSimulator(body, b, phaseNum) {
  const total = b.phases.length;
  let index = Math.min(Math.max(1, phaseNum), total) - 1;
  body.innerHTML = `
    <div class="sim">
      <div class="sim-map">
        <div class="map-frame"></div>
        <div class="legend">
          ${b.sides.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("")}
          <span class="legend-note">${esc(b.terrain.orientation)}</span>
        </div>
        <div class="controls">
          <button class="btn icon" data-act="prev" aria-label="Previous phase">‹</button>
          <button class="btn icon play" data-act="play" aria-label="Play">▶</button>
          <button class="btn icon" data-act="next" aria-label="Next phase">›</button>
          <ol class="stepper">
            ${b.phases.map((p, i) => `<li><button data-phase="${i}" title="${esc(p.title)}"><span>${i + 1}</span></button></li>`).join("")}
          </ol>
          <label class="toggle"><input type="checkbox" checked data-act="labels"> Labels</label>
        </div>
        <details class="key">
          <summary>Map key</summary>
          <div class="key-grid">
            <span><svg viewBox="0 0 40 20"><rect x="1" y="1" width="38" height="18" rx="2"/><line x1="3" y1="3" x2="37" y2="17"/><line x1="3" y1="17" x2="37" y2="3"/></svg>Infantry</span>
            <span><svg viewBox="0 0 40 20"><rect x="1" y="1" width="38" height="18" rx="2"/><line x1="3" y1="17" x2="37" y2="3"/></svg>Cavalry</span>
            <span><svg viewBox="0 0 40 20"><rect x="1" y="1" width="38" height="18" rx="2"/><circle cx="12" cy="10" r="1.5"/><circle cx="28" cy="10" r="1.5"/></svg>Skirmishers</span>
            <span><svg viewBox="0 0 40 20"><rect x="1" y="1" width="38" height="18" rx="2"/><path d="M10 14 Q20 -2 30 14"/></svg>Archers</span>
            <span><svg viewBox="0 0 40 20"><rect x="1" y="1" width="38" height="18" rx="2"/><circle cx="20" cy="10" r="5" class="fill"/></svg>Artillery</span>
            <span class="k-state k-engaged">Engaged</span>
            <span class="k-state k-routed">Routed</span>
            <span class="k-state k-destroyed">Destroyed</span>
          </div>
        </details>
      </div>
      <aside class="phase" aria-live="polite"></aside>
    </div>`;

  const frame = body.querySelector(".map-frame");
  const map = new BattleMap(frame, { onUnitHover: (u, node) => (u ? showTooltip(b, u, node) : hideTooltip()) });
  map.load(b);
  state.map = map;

  const panel = body.querySelector(".phase");
  const stepButtons = [...body.querySelectorAll(".stepper button")];
  const playBtn = body.querySelector('[data-act="play"]');

  const show = (i, { instant = false, fromUser = true } = {}) => {
    index = Math.min(Math.max(0, i), total - 1);
    const p = b.phases[index];
    map.setPhase(index, { instant });
    stepButtons.forEach((btn, j) => {
      btn.classList.toggle("done", j < index);
      btn.setAttribute("aria-current", j === index ? "step" : "false");
    });
    panel.innerHTML = `
      <p class="eyebrow">Phase ${index + 1} of ${total}${p.time ? ` · ${esc(p.time)}` : ""}</p>
      <h2>${esc(p.title)}</h2>
      <p class="phase-summary">${esc(p.summary)}</p>
      ${paras(p.narrative)}
      ${p.insight ? `<div class="insight"><p class="eyebrow">What to notice</p><p>${esc(p.insight)}</p></div>` : ""}
      <div class="phase-nav">
        ${index > 0 ? `<button class="btn" data-go="${index - 1}">← ${esc(b.phases[index - 1].title)}</button>` : "<span></span>"}
        ${index < total - 1
          ? `<button class="btn btn-primary" data-go="${index + 1}">Next: ${esc(b.phases[index + 1].title)} →</button>`
          : `<a class="btn btn-primary" href="#/battle/${encodeURIComponent(b.id)}/aftermath">See the aftermath →</a>`}
      </div>`;
    panel.querySelectorAll("[data-go]").forEach((btn) => btn.addEventListener("click", () => { stopPlaying(); show(Number(btn.dataset.go)); }));
    history.replaceState(null, "", `#/battle/${encodeURIComponent(b.id)}/simulator/${index + 1}`);
    if (fromUser && matchMedia("(max-width: 900px)").matches) frame.scrollIntoView({ block: "start", behavior: "smooth" });
  };

  body.querySelector('[data-act="prev"]').addEventListener("click", () => { stopPlaying(); show(index - 1); });
  body.querySelector('[data-act="next"]').addEventListener("click", () => { stopPlaying(); show(index + 1); });
  stepButtons.forEach((btn) => btn.addEventListener("click", () => { stopPlaying(); show(Number(btn.dataset.phase)); }));
  body.querySelector('[data-act="labels"]').addEventListener("change", (e) => map.setLabels(e.target.checked));
  playBtn.addEventListener("click", () => {
    if (state.playing) return stopPlaying();
    if (index >= total - 1) show(0, { fromUser: false });
    playBtn.textContent = "❚❚";
    playBtn.setAttribute("aria-label", "Pause");
    state.playing = setInterval(() => {
      if (index >= total - 1) return stopPlaying();
      show(index + 1, { fromUser: false });
    }, 7000);
    state.stopPlayButton = () => { playBtn.textContent = "▶"; playBtn.setAttribute("aria-label", "Play"); };
  });

  show(index, { instant: true, fromUser: false });
  state.keyHandler = (e) => {
    if (e.target.closest("input, textarea")) return;
    if (e.key === "ArrowRight") { stopPlaying(); show(index + 1); }
    if (e.key === "ArrowLeft") { stopPlaying(); show(index - 1); }
    if (e.key === " ") { e.preventDefault(); playBtn.click(); }
  };
}

function stopPlaying() {
  clearInterval(state.playing);
  state.playing = null;
  state.stopPlayButton?.();
  state.stopPlayButton = null;
}

document.addEventListener("keydown", (e) => {
  if (parseRoute().tab === "simulator") state.keyHandler?.(e);
});

// ---------- tooltip ----------

function showTooltip(b, u, node) {
  const side = b.sides.find((s) => s.id === u.side);
  const st = node.dataset.state;
  tooltip.innerHTML = `
    <p class="eyebrow" style="color:${side?.color}">${esc(side?.name || "")}</p>
    <strong>${esc(u.name)}</strong>
    <p class="tt-meta">${esc(u.type.replace(/-/g, " "))}${u.size ? ` · ${esc(u.size)}` : ""}${st && st !== "ready" ? ` · <b>${esc(st)}</b>` : ""}</p>
    ${u.commander ? `<p class="tt-meta">Led by ${esc(u.commander)}</p>` : ""}
    ${u.description ? `<p>${esc(u.description)}</p>` : ""}`;
  tooltip.hidden = false;
  const r = node.getBoundingClientRect();
  const tw = tooltip.offsetWidth;
  const th = tooltip.offsetHeight;
  let left = r.left + r.width / 2 - tw / 2;
  left = Math.max(8, Math.min(window.innerWidth - tw - 8, left));
  let top = r.top - th - 10;
  if (top < 8) top = r.bottom + 10;
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

function hideTooltip() {
  tooltip.hidden = true;
}

window.addEventListener("scroll", hideTooltip, { passive: true });

// ---------- helpers ----------

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function paras(text) {
  return String(text || "").split(/\n\s*\n/).filter((t) => t.trim()).map((t) => `<p>${esc(t.trim())}</p>`).join("");
}

// ---------- boot ----------

render();
checkGenerator().then((status) => {
  state.generator = status;
  const note = document.getElementById("gen-status");
  note.textContent = status.available ? "Claude generation on" : "Library mode";
  note.title = status.available
    ? "Search for any battle, then generate a walkthrough with Claude."
    : "Only the built-in battles are available here.";
  if (parseRoute().view === "home") updateResults();
});
