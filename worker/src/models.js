// worker/src/models.js
//
// POST /api/models {provider, apiKey} — live model-list fetch for the BYOK provider dropdown
// (Step 2, see ARCHITECTURE.md). Worker-side, NOT browser-direct: Anthropic/OpenAI don't serve
// permissive CORS to browser origins, and this reuses the same browser->Worker HTTPS trust
// boundary the BYOK key already crosses.
//
// The apiKey here is TRANSIENT: used only for the one outbound list-models fetch, never written to
// a byok:{runId} entry (those are minted only at run-create) and never part of the cache key or
// value. The cache is PROVIDER-scoped (`models:{provider}`, ~1h) because a model list is a
// per-provider public fact — a bad/limited key just surfaces as a run-time 4xx later (Step 3's
// problem), not this endpoint's. A 401/403 from the provider passes through cleanly, doubling as
// up-front key validation before a run is spent.
//
// Step 6 security review (F6): this endpoint had no rate limit at all — unauthenticated, and
// every cache-miss request (including a rejected key, which is deliberately never cached) forces
// a fresh outbound fetch to the real provider on the project's own Worker account. Reuses the
// exact same per-IP daily mechanism as POST /api/runs (rateLimit.js), not a bespoke one.

import { checkAndBumpRateLimit } from "./rateLimit.js";

const MODELS_CACHE_TTL_SECONDS = 3600; // 1h — model lists change rarely; the cache is the throttle
const MODELS_FETCH_TIMEOUT_MS = 10000;

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

const json = (body, status = 200, extraHeaders = {}) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...CORS, ...extraHeaders },
  });

// OpenRouter model objects carry `supported_parameters`; keep only models that can actually do the
// structured output the self-correction step needs (this is what trims the ~400-model list, not an
// arbitrary cut).
const STRUCTURED_PARAMS = new Set(["structured_outputs", "response_format", "tools"]);

// OpenAI's /v1/models returns EVERY model (embeddings, tts, image, moderation, ...). This prefix
// list is a FILTER over the live response down to the chat-capable families — it is NOT a hardcoded
// model list and does not replace the live fetch. A new family simply won't show until a prefix is
// added; a wrong pick surfaces as a run-time 4xx (Step 3), never a broken run.
const OPENAI_CHAT_PREFIXES = ["gpt-", "o1", "o3", "o4", "chatgpt-"];
const OPENROUTER_MAX_MODELS = 300; // UI-sanity ceiling after the capability filter; not curation

const bearer = (apiKey) => ({ Authorization: `Bearer ${apiKey}` });
const stripModelsPrefix = (name) => String(name ?? "").replace(/^models\//, "");

// Per provider: how to build the list-models request from the user's key, and how to normalize the
// provider-specific response into the uniform { id, label } shape the frontend consumes.
const PROVIDERS = {
  gemini: {
    // Step 6 security review (F9): the key used to ride in the URL query string (?key=...)
    // instead of a header, unlike every other provider here -- subrequest-URL logging (wrangler
    // tail, Cloudflare Logpush, any tracing integration) routinely captures full URLs, turning a
    // transient BYOK key into a logged one. x-goog-api-key is Google's own documented header
    // alternative for this API; same auth, no URL exposure.
    build: (apiKey) => ({
      url: "https://generativelanguage.googleapis.com/v1beta/models",
      headers: { "x-goog-api-key": apiKey },
    }),
    normalize: (body) =>
      (body.models ?? [])
        .filter((m) => (m.supportedGenerationMethods ?? []).includes("generateContent"))
        .map((m) => ({ id: stripModelsPrefix(m.name), label: m.displayName || stripModelsPrefix(m.name) })),
  },
  openai: {
    build: (apiKey) => ({ url: "https://api.openai.com/v1/models", headers: bearer(apiKey) }),
    normalize: (body) =>
      (body.data ?? [])
        .map((m) => m.id)
        .filter((id) => OPENAI_CHAT_PREFIXES.some((p) => String(id).startsWith(p)))
        .map((id) => ({ id, label: id })),
  },
  grok: {
    // OpenAI-shaped; all xAI models are chat models, so no chat filter needed.
    build: (apiKey) => ({ url: "https://api.x.ai/v1/models", headers: bearer(apiKey) }),
    normalize: (body) => (body.data ?? []).map((m) => ({ id: m.id, label: m.id })),
  },
  anthropic: {
    build: (apiKey) => ({
      url: "https://api.anthropic.com/v1/models",
      headers: { "x-api-key": apiKey, "anthropic-version": "2023-06-01" },
    }),
    normalize: (body) => (body.data ?? []).map((m) => ({ id: m.id, label: m.display_name || m.id })),
  },
  openrouter: {
    build: (apiKey) => ({ url: "https://openrouter.ai/api/v1/models", headers: bearer(apiKey) }),
    normalize: (body) => {
      const structuredCapable = (m) => {
        const sp = m.supported_parameters;
        // Keep when we can confirm structured-output support; give the benefit of the doubt when
        // the field is absent (run-time 4xx catches a genuine mismatch).
        return Array.isArray(sp) ? sp.some((p) => STRUCTURED_PARAMS.has(p)) : true;
      };
      return (body.data ?? [])
        .filter(structuredCapable)
        .slice(0, OPENROUTER_MAX_MODELS)
        .map((m) => ({ id: m.id, label: m.name || m.id }));
    },
  },
};

export async function handleModelsFetch(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_json" }, 400);
  }

  const { provider, apiKey } = body ?? {};
  const cfg = PROVIDERS[provider];
  if (!cfg) return json({ error: "unknown_provider" }, 400);
  if (typeof apiKey !== "string" || apiKey.length < 1) {
    return json({ error: "missing_api_key" }, 400);
  }

  // Rate limit AFTER cheap validation, BEFORE the possible outbound fetch — same ordering as
  // POST /api/runs (validate first, gate before the expensive/costly step). Separate "models"
  // bucket: this endpoint's limit is independent of the run-creation one.
  const ip = request.headers.get("CF-Connecting-IP") || "unknown";
  const rl = await checkAndBumpRateLimit(env, "models", ip);
  if (!rl.allowed) {
    return json(
      { error: "rate_limited", limit: rl.limit, remaining: 0, retryAfter: rl.retryAfter },
      429,
      { "Retry-After": String(rl.retryAfter) }
    );
  }

  // Provider-scoped cache — the apiKey is deliberately not part of this key.
  const cacheKey = `models:${provider}`;
  const cached = await env.RELAY_KV.get(cacheKey, "json");
  if (cached) return json(cached, 200); // hit -> no provider call

  const { url, headers } = cfg.build(apiKey);
  let resp;
  try {
    resp = await fetch(url, { method: "GET", headers, signal: AbortSignal.timeout(MODELS_FETCH_TIMEOUT_MS) });
  } catch {
    return json({ error: "provider_unreachable", provider }, 502);
  }

  if (resp.status === 401 || resp.status === 403) {
    // Clean pass-through so the frontend can say "key rejected". Never cached.
    return json({ error: "key_rejected", provider }, 401);
  }
  if (!resp.ok) {
    return json({ error: "provider_error", provider, status: resp.status }, 502);
  }

  let data;
  try {
    data = await resp.json();
  } catch {
    return json({ error: "provider_bad_response", provider }, 502);
  }

  const result = { models: cfg.normalize(data) };
  // Cache ONLY the normalized, provider-scoped list — the apiKey is never in the key or the value.
  await env.RELAY_KV.put(cacheKey, JSON.stringify(result), { expirationTtl: MODELS_CACHE_TTL_SECONDS });
  return json(result, 200);
}
