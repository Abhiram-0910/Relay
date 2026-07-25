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
 *
 * Credentials (three, distinct — see ARCHITECTURE.md):
 *   env.GH_PAT           Worker->GitHub, triggers workflow_dispatch. Only use.
 *   env.CALLBACK_SECRET  Action->Worker, authenticates progress callbacks. Shared secret.
 *   (the Action's built-in GITHUB_TOKEN is NOT used here — wrong audience.)
 */

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

function secondsToMidnightUTC(now = new Date()) {
  const midnight = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1);
  return Math.max(1, Math.ceil((midnight - now.getTime()) / 1000));
}

function isValidSpecUrl(value) {
  if (typeof value !== "string") return false;
  try {
    const u = new URL(value);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

/** Per-IP daily counter in KV. Over-limit costs ZERO writes (read-only). Best-effort atomic. */
async function checkAndBumpRateLimit(env, ip) {
  const limit = parseInt(env.RATE_LIMIT ?? "3", 10);
  const day = new Date().toISOString().slice(0, 10).replace(/-/g, "");
  const key = `rl:${ip}:${day}`;
  const count = parseInt((await env.RELAY_KV.get(key)) ?? "0", 10);
  if (count >= limit) {
    return { allowed: false, limit, remaining: 0, retryAfter: secondsToMidnightUTC() };
  }
  await env.RELAY_KV.put(key, String(count + 1), { expirationTtl: secondsToMidnightUTC() });
  return { allowed: true, limit, remaining: limit - count - 1, key, previous: count };
}

async function refundRateLimit(env, rl) {
  if (rl?.key) await env.RELAY_KV.put(rl.key, String(rl.previous), { expirationTtl: secondsToMidnightUTC() });
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
  const rl = await checkAndBumpRateLimit(env, ip);
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
                     error: null, updatedAt: now }),
    { expirationTtl: DAY_SECONDS }
  );

  const callbackUrl = new URL(request.url).origin; // this Worker's own public origin
  const resp = await triggerWorkflow(env, {
    run_id: runId,
    spec_url: payload.specUrl,
    callback_url: callbackUrl,
  });
  if (!resp.ok) {
    await refundRateLimit(env, rl); // dispatch failed -> don't consume the user's daily quota
    const detail = await resp.text().catch(() => "");
    return json({ error: "dispatch_failed", githubStatus: resp.status, detail: detail.slice(0, 300) }, 502, CORS);
  }

  return json({ runId, status: "queued", statusUrl: `/api/runs/${runId}` }, 202, CORS);
}

// GET /api/runs/:id — poll. Namespaced by runId, so concurrent visitors never cross.
async function handleGetRun(env, runId) {
  const entry = await env.RELAY_KV.get(runKey(runId), "json");
  if (!entry) return json({ error: "not_found" }, 404, CORS);
  return json(entry, 200, CORS);
}

// POST /api/runs/:id/progress — Action callback. Auth by shared secret; Worker enforces the
// KV write-rate throttle itself, so a buggy/retrying reporter can't blow the write budget.
async function handleProgress(request, env, runId) {
  const auth = request.headers.get("Authorization") || "";
  if (!env.CALLBACK_SECRET || auth !== `Bearer ${env.CALLBACK_SECRET}`) {
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
    updatedAt: now,
  };
  await env.RELAY_KV.put(runKey(runId), JSON.stringify(merged), { expirationTtl: DAY_SECONDS });
  return new Response(null, { status: 204 });
}

export async function handle(request, env) {
  const { pathname } = new URL(request.url);
  if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });

  if (pathname === "/api/runs" && request.method === "POST") {
    return handleCreateRun(request, env);
  }
  const progressMatch = pathname.match(/^\/api\/runs\/([^/]+)\/progress$/);
  if (progressMatch && request.method === "POST") {
    return handleProgress(request, env, progressMatch[1]);
  }
  const runMatch = pathname.match(/^\/api\/runs\/([^/]+)$/);
  if (runMatch && request.method === "GET") {
    return handleGetRun(env, runMatch[1]);
  }
  return json({ error: "not_found" }, 404, CORS);
}

export default { fetch: (request, env) => handle(request, env) };
