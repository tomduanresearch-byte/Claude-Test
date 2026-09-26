// Generates a battle with Claude through whichever route this page has:
//   - "sample": the page is a published claude.ai Artifact, so it can ask
//     Claude directly on the viewer's account;
//   - "server": the local server.js with an ANTHROPIC_API_KEY.
// Both return the raw battle JSON described in schema.js.
import { BATTLE_SCHEMA, SYSTEM_PROMPT, describeProgress } from "./schema.js";

let backend = null;

export async function checkGenerator() {
  const sample = await findSample();
  if (sample) {
    backend = { kind: "sample", sample };
    return { available: true, via: "sample" };
  }
  try {
    const res = await fetch("api/status", { cache: "no-store" });
    if (res.ok && (await res.json()).generate) {
      backend = { kind: "server" };
      return { available: true, via: "server" };
    }
  } catch {}
  return { available: false };
}

async function findSample() {
  if (!window.claude?.use) return null;
  try {
    return await window.claude.use("sample");
  } catch {
    return null;
  }
}

export function generateBattle(query, onProgress, signal) {
  if (backend?.kind === "sample") return viaSample(backend.sample, query, onProgress, signal);
  return viaServer(query, onProgress, signal);
}

const SAMPLE_ERRORS = {
  not_granted: "Generation needs your permission to use Claude from this page.",
  rate_limited: "Too many requests right now. Wait a minute and try again.",
  refused: "Claude declined to generate this battle.",
  invalid_json: "The answer came back incomplete. Try again, or try a more specific battle name.",
  empty_completion: "Claude returned nothing. Try again.",
  sampling_disabled: "Asking Claude from pages is turned off for this account.",
  session_expired: "Your claude.ai session expired. Reload the page.",
};

async function viaSample(sample, query, onProgress, signal) {
  const prompt = `${SYSTEM_PROMPT}

Keep it compact so the whole answer fits in one reply: 5-7 phases, 8-14 units, and keep each narrative to one paragraph.

Reply with only one JSON object (no prose, no code fence) that matches this JSON Schema exactly:
${JSON.stringify(BATTLE_SCHEMA)}

Build the interactive walkthrough for: ${query}`;
  try {
    return await sample.json(prompt, {
      modelTier: "default",
      signal,
      onText: ({ text }) => onProgress?.(describeProgress(text)),
    });
  } catch (e) {
    if (e?.code === "cancelled") throw new Error("Stopped.");
    throw new Error(SAMPLE_ERRORS[e?.code] || e?.message || "Generation failed.");
  }
}

async function viaServer(query, onProgress, signal) {
  const res = await fetch("api/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query }),
    signal,
  });
  if (!res.ok || !res.body) {
    let msg = `Server error (${res.status})`;
    try { msg = (await res.json()).error || msg; } catch {}
    throw new Error(msg);
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      let nl;
      while ((nl = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, nl).trim();
        buffer = buffer.slice(nl + 1);
        if (!line) continue;
        const event = JSON.parse(line);
        if (event.type === "progress") onProgress?.(event);
        else if (event.type === "error") throw new Error(event.message);
        else if (event.type === "done") return event.battle;
      }
    }
  } catch (e) {
    if (e.name === "AbortError") throw new Error("Stopped.");
    throw e;
  }
  throw new Error("The connection closed before the battle was finished.");
}
