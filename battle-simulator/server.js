// Serves the simulator and, when an Anthropic API key is available, generates
// battles on demand with Claude. The static app works without the server too
// (open public/index.html), but only the curated library is available then.
import http from "node:http";
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import Anthropic from "@anthropic-ai/sdk";
import { BATTLE_SCHEMA, SYSTEM_PROMPT, describeProgress } from "./public/js/schema.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const PUBLIC = path.join(here, "public");
const PORT = Number(process.env.PORT) || 5173;
const MODEL = process.env.BATTLE_MODEL || "claude-opus-5";

const client = hasCredentials() ? new Anthropic() : null;

function hasCredentials() {
  return Boolean(process.env.ANTHROPIC_API_KEY || process.env.ANTHROPIC_AUTH_TOKEN);
}

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
};

async function serveStatic(req, res) {
  const url = new URL(req.url, "http://localhost");
  const rel = decodeURIComponent(url.pathname === "/" ? "/index.html" : url.pathname);
  const file = path.normalize(path.join(PUBLIC, rel));
  if (!file.startsWith(PUBLIC)) return send(res, 403, "Forbidden");
  try {
    const body = await fs.readFile(file);
    res.writeHead(200, { "Content-Type": MIME[path.extname(file)] || "application/octet-stream" });
    res.end(body);
  } catch {
    send(res, 404, "Not found");
  }
}

function send(res, status, text) {
  res.writeHead(status, { "Content-Type": "text/plain; charset=utf-8" });
  res.end(text);
}

async function readJson(req) {
  let raw = "";
  for await (const chunk of req) {
    raw += chunk;
    if (raw.length > 10_000) throw new Error("Request too large");
  }
  return JSON.parse(raw || "{}");
}

// Streams newline-delimited JSON: progress events while Claude writes, then
// one "done" event carrying the battle (or an "error" event).
async function generate(req, res) {
  if (!client) {
    res.writeHead(503, { "Content-Type": "application/json" });
    return res.end(JSON.stringify({ error: "Set ANTHROPIC_API_KEY to enable generation." }));
  }
  let query;
  try {
    query = String((await readJson(req)).query || "").trim().slice(0, 300);
  } catch {
    return send(res, 400, "Bad request");
  }
  if (!query) return send(res, 400, "Missing query");

  res.writeHead(200, { "Content-Type": "application/x-ndjson", "Cache-Control": "no-cache" });
  const emit = (event) => res.write(JSON.stringify(event) + "\n");
  emit({ type: "progress", stage: "Researching the battle…", chars: 0, phases: 0 });

  try {
    const stream = client.beta.messages.stream({
      model: MODEL,
      max_tokens: 64000,
      betas: ["server-side-fallback-2026-07-01"],
      fallbacks: "default",
      thinking: { type: "adaptive" },
      output_config: { format: { type: "json_schema", schema: BATTLE_SCHEMA } },
      system: SYSTEM_PROMPT,
      messages: [{ role: "user", content: `Build the interactive walkthrough for: ${query}` }],
    });

    let text = "";
    let lastEmit = 0;
    stream.on("text", (delta) => {
      text += delta;
      if (text.length - lastEmit > 400) {
        lastEmit = text.length;
        emit({ type: "progress", ...describeProgress(text) });
      }
    });
    res.on("close", () => { if (!res.writableEnded) stream.abort(); });

    const message = await stream.finalMessage();
    if (message.stop_reason === "refusal") throw new Error("Claude declined to generate this battle.");
    if (message.stop_reason === "max_tokens") throw new Error("The response was cut off before it finished. Try again.");
    const out = message.content.filter((b) => b.type === "text").map((b) => b.text).join("");
    emit({ type: "done", battle: JSON.parse(out) });
  } catch (err) {
    console.error("generate failed:", err);
    emit({ type: "error", message: errorMessage(err) });
  }
  res.end();
}

function errorMessage(err) {
  if (err instanceof Anthropic.AuthenticationError) return "The API key was rejected.";
  if (err instanceof Anthropic.RateLimitError) return "Rate limited by the API. Wait a moment and try again.";
  if (err instanceof Anthropic.APIConnectionError) return "Could not reach the Claude API.";
  if (err instanceof Anthropic.APIError) return `Claude API error (${err.status ?? "unknown"}).`;
  return err?.message || "Generation failed.";
}

const server = http.createServer(async (req, res) => {
  if (req.method === "GET" && req.url === "/api/status") {
    res.writeHead(200, { "Content-Type": "application/json" });
    return res.end(JSON.stringify({ generate: Boolean(client), model: client ? MODEL : null }));
  }
  if (req.method === "POST" && req.url === "/api/generate") return generate(req, res);
  if (req.method === "GET") return serveStatic(req, res);
  send(res, 405, "Method not allowed");
});

server.listen(PORT, () => {
  console.log(`Battle simulator on http://localhost:${PORT}`);
  console.log(client ? `Generation enabled (${MODEL}).` : "Generation disabled: set ANTHROPIC_API_KEY to enable it.");
});
