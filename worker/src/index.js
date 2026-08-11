/**
 * Relay Worker — public front door for the OpenAPI→validated-client pipeline.
 *
 * It never runs the pipeline itself; it triggers a GitHub Action (the compute, which has Docker
 * for the sandbox) via workflow_dispatch, stores progress/results in KV, and rate-limits per IP.
 *
 * Endpoints:
 *   POST /api/runs                  { specUrl } -> 202 { runId, status, statusUrl }   (CORS)
 *   GET  /api/runs/:id              -> 200 run snapshot | 404                          (CORS)
 *   POST /api/runs/:id/progress     Action-only callback (Bearer CALLBACK_SECRET)      (no CORS)
 *   POST /api/models                { provider, apiKey } -> 200 { models:[{id,label}] } (CORS)
 *
 * Credentials (three, distinct — see ARCHITECTURE.md):
 *   env.GH_PAT           Worker->GitHub, triggers workflow_dispatch. Only use.
 *   env.CALLBACK_SECRET  Action->Worker, authenticates progress callbacks. Shared secret.
 *   (the Action's built-in GITHUB_TOKEN is NOT used here — wrong audience.)
 */

import { storeByokKey, buildDispatchInputs, handleByokKeyFetch, isAuthorizedCallback } from "./byok.js";
import { handleModelsFetch } from "./models.js";
import { checkAndBumpRateLimit, refundRateLimit } from "./rateLimit.js";

const DAY_SECONDS = 86400;
const PROGRESS_THROTTLE_MS = 1000; // KV allows 1 write/sec per key; coalesce faster updates
const TERMINAL = new Set(["succeeded", "failed"]);

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

const json = (body, status = 200, extraHeaders = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...extraHeaders },
  });

const runKey = (id) => `run:${id}`;
const codeKey = (id) => `code:${id}`;

// Generated code is stored in its OWN KV entry (fetched on demand), not the polled status payload.
// Size-guarded so a huge spec can't exceed KV's value limit — truncation is flagged, never silent.
const CODE_MAX_FILE_BYTES = 256 * 1024; // per file
const CODE_MAX_TOTAL_BYTES = 2 * 1024 * 1024; // total stored (well under KV's 25 MiB value cap)

function isValidSpecUrl(value) {
  if (typeof value !== "string") return false;
  try {
    const u = new URL(value);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

async function triggerWorkflow(env, inputs) {
  const url = `https://api.github.com/repos/${env.GH_OWNER}/${env.GH_REPO}/actions/workflows/${env.GH_WORKFLOW}/dispatches`;
  return fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GH_PAT}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "relay-worker",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: env.GH_REF ?? "main", inputs }),
  });
}

// POST /api/runs — rate-limit (BEFORE dispatch), mint runId, create KV entry, dispatch.
async function handleCreateRun(request, env) {
  let payload;
  try {
    payload = await request.json();
  } catch {
    return json({ error: "invalid_json" }, 400, CORS);
  }
  if (!isValidSpecUrl(payload?.specUrl)) {
    return json({ error: "invalid_spec_url" }, 400, CORS);
  }

  // Rate limit is the FIRST gate — before the expensive workflow_dispatch.
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  const rl = await checkAndBumpRateLimit(env, "runs", ip);
  if (!rl.allowed) {
    return json(
      { error: "rate_limited", limit: rl.limit, remaining: 0, retryAfter: rl.retryAfter },
      429,
      { ...CORS, "Retry-After": String(rl.retryAfter) }
    );
  }

  const runId = crypto.randomUUID();
  const now = Date.now();
  await env.RELAY_KV.put(
    runKey(runId),
    JSON.stringify({ runId, status: "queued", stage: null, progress: null, result: null,
                     error: null, error_detail: null, updatedAt: now }),
    { expirationTtl: DAY_SECONDS }
  );

  const callbackUrl = new URL(request.url).origin; // this Worker's own public origin

  // Store an optional BYOK key in KV (single-use ticket, short TTL) BEFORE dispatch, then build
  // the dispatch inputs via buildDispatchInputs — which has no apiKey parameter, so the key is
  // structurally incapable of riding along into GitHub's workflow_dispatch payload.
  let hasByok;
  try {
    hasByok = await storeByokKey(env, runId, payload.apiKey, payload.provider, payload.model);
  } catch {
    await refundRateLimit(env, rl); // malformed key/model -> don't consume the user's daily quota
    return json({ error: "invalid_api_key" }, 400, CORS);
  }

  const resp = await triggerWorkflow(env, buildDispatchInputs(runId, payload.specUrl, callbackUrl, hasByok));
  if (!resp.ok) {
    await refundRateLimit(env, rl); // dispatch failed -> don't consume the user's daily quota
    const detail = await resp.text().catch(() => "");
    return json({ error: "dispatch_failed", githubStatus: resp.status, detail: detail.slice(0, 300) }, 502, CORS);
  }

  return json({ runId, status: "queued", statusUrl: `/api/runs/${runId}` }, 202, CORS);
}

// GET /api/runs/:id — poll. Namespaced by runId, so concurrent visitors never cross.
// error_detail (raw exception text, Step 3) is stored in KV but deliberately stripped here — this
// is the one public, unauthenticated endpoint for a run's state, so "server-side only, no
// disclosure UI yet" means it never leaves the Worker, not just "the UI doesn't render it."
async function handleGetRun(env, runId) {
  const entry = await env.RELAY_KV.get(runKey(runId), "json");
  if (!entry) return json({ error: "not_found" }, 404, CORS);
  const { error_detail, ...safe } = entry;
  return json(safe, 200, CORS);
}

// POST /api/runs/:id/progress — Action callback. Auth by shared secret; Worker enforces the
// KV write-rate throttle itself, so a buggy/retrying reporter can't blow the write budget.
async function handleProgress(request, env, runId) {
  if (!(await isAuthorizedCallback(request, env))) {
    return json({ error: "unauthorized" }, 401);
  }
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_json" }, 400);
  }

  const existing = await env.RELAY_KV.get(runKey(runId), "json");
  if (!existing) return json({ error: "not_found" }, 404);

  const now = Date.now();
  const terminal = TERMINAL.has(body.status);
  const statusChanged = body.status && body.status !== existing.status;
  const throttled = now - (existing.updatedAt || 0) < PROGRESS_THROTTLE_MS;
  // Coalesce only rapid SAME-status, non-terminal updates (drop = 204, no write). A status
  // transition (e.g. queued->running) or a terminal result always writes, even within the window.
  if (!terminal && !statusChanged && throttled) {
    return new Response(null, { status: 204 });
  }

  const merged = {
    ...existing,
    status: body.status ?? existing.status,
    stage: body.stage ?? existing.stage,
    progress: body.progress ?? existing.progress,
    result: body.result ?? existing.result,
    error: body.error ?? existing.error,
    // error_detail: raw exception text (Step 3, ci_runner.py) -- stored for debugging, never
    // returned by handleGetRun (stripped there, not here -- KV is the one place it's allowed to be).
    error_detail: body.error_detail ?? existing.error_detail,
    updatedAt: now,
  };
  await env.RELAY_KV.put(runKey(runId), JSON.stringify(merged), { expirationTtl: DAY_SECONDS });
  return new Response(null, { status: 204 });
}

// Enforce the size caps, truncating file contents if needed and FLAGGING it (never silent).
function guardCode(payload) {
  let truncated = Boolean(payload?.truncated);
  let budget = CODE_MAX_TOTAL_BYTES;
  const files = [];
  for (const f of payload?.files ?? []) {
    let content = String(f?.content ?? "");
    let fileTruncated = Boolean(f?.truncated);
    if (content.length > CODE_MAX_FILE_BYTES) {
      content = content.slice(0, CODE_MAX_FILE_BYTES);
      fileTruncated = true;
      truncated = true;
    }
    if (content.length > budget) {
      content = content.slice(0, Math.max(0, budget));
      fileTruncated = true;
      truncated = true;
    }
    budget -= content.length;
    files.push({ endpoint: f?.endpoint ?? null, name: f?.name ?? "file", content, truncated: fileTruncated });
    if (budget <= 0) {
      truncated = true; // any remaining files are dropped
      break;
    }
  }
  const dropped = (payload?.files?.length ?? 0) - files.length;
  return { files, truncated: truncated || dropped > 0, dropped };
}

// POST /api/runs/:id/code — Action callback (Bearer CALLBACK_SECRET). Stores the guarded code.
async function handleStoreCode(request, env, runId) {
  if (!(await isAuthorizedCallback(request, env))) {
    return json({ error: "unauthorized" }, 401);
  }
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_json" }, 400);
  }
  const guarded = guardCode(body);
  await env.RELAY_KV.put(codeKey(runId), JSON.stringify(guarded), { expirationTtl: DAY_SECONDS });
  return new Response(null, { status: 204 });
}

// GET /api/runs/:id/code — public (CORS). Fetched on demand when the user opens the viewer.
async function handleGetCode(env, runId) {
  const code = await env.RELAY_KV.get(codeKey(runId), "json");
  if (!code) return json({ error: "not_found" }, 404, CORS);
  return json(code, 200, CORS);
}

export async function handle(request, env) {
  const { pathname } = new URL(request.url);
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });

  if (pathname === "/api/runs" && request.method === "POST") {
    return handleCreateRun(request, env);
  }
  if (pathname === "/api/models" && request.method === "POST") {
    return handleModelsFetch(request, env); // BYOK model-list fetch (transient key, provider cache)
  }
  const progressMatch = pathname.match(/^\/api\/runs\/([^/]+)\/progress$/);
  if (progressMatch && request.method === "POST") {
    return handleProgress(request, env, progressMatch[1]);
  }
  const codeMatch = pathname.match(/^\/api\/runs\/([^/]+)\/code$/);
  if (codeMatch && request.method === "POST") {
    return handleStoreCode(request, env, codeMatch[1]);
  }
  if (codeMatch && request.method === "GET") {
    return handleGetCode(env, codeMatch[1]);
  }
  const byokMatch = pathname.match(/^\/api\/runs\/([^/]+)\/byok-key$/);
  if (byokMatch && request.method === "GET") {
    return handleByokKeyFetch(request, env, byokMatch[1]); // Action-only (Bearer CALLBACK_SECRET)
  }
  const runMatch = pathname.match(/^\/api\/runs\/([^/]+)$/);
  if (runMatch && request.method === "GET") {
    return handleGetRun(env, runMatch[1]);
  }
  return json({ error: "not_found" }, 404, CORS);
}

export default { fetch: (request, env) => handle(request, env) };
