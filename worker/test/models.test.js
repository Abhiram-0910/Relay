// worker/test/models.test.js
import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { handleModelsFetch, OPENAI_KNOWN_DEAD_IDS } from "../src/models.js";
import OPENAI_MODEL_IDS from "./fixtures/openai-model-ids.json";
import OPENAI_REJECTIONS from "./fixtures/openai-rejections.json";

// Same minimal fake KV the byok/worker suites use.
class FakeKV {
  constructor() {
    this.store = new Map();
  }
  async get(key, type) {
    if (!this.store.has(key)) return null;
    const value = this.store.get(key).value;
    return type === "json" ? JSON.parse(value) : value;
  }
  async put(key, value, opts) {
    this.store.set(key, { value, ttl: opts?.expirationTtl ?? null });
  }
  async delete(key) {
    this.store.delete(key);
  }
}

function makeEnv(overrides = {}) {
  return { RELAY_KV: new FakeKV(), ...overrides };
}

function req(bodyObj, ip) {
  return new Request("https://worker.example/api/models", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(ip ? { "CF-Connecting-IP": ip } : {}) },
    body: JSON.stringify(bodyObj),
  });
}

// The filtered, normalized list is no longer returned (piece 8: the route answers with a ranked
// ladder only); it lives in the provider-scoped cache, which is where the filter tests read it now.
const cachedIds = async (env, provider) =>
  (await env.RELAY_KV.get(`models:${provider}`, "json")).models.map((m) => m.id);

// A stand-in for the provider's HTTP Response — the handler only touches status/ok/json().
function fakeResponse(status, body) {
  return { status, ok: status >= 200 && status < 300, json: async () => body };
}

const GEMINI_BODY = {
  models: [
    { name: "models/gemini-3.5-flash", displayName: "Gemini 3.5 Flash",
      supportedGenerationMethods: ["generateContent", "countTokens"] },
    { name: "models/embedding-001", displayName: "Embedding",
      supportedGenerationMethods: ["embedContent"] }, // filtered out — can't generateContent
  ],
};

// A real /v1/models id list (no secrets) and what the OpenAI filter must do with it.
const DEFINITIVE_NON_CHAT =
  /image|transcribe|tts|realtime|whisper|embedding|moderation|sora|dall-e|instruct|davinci|babbage/;
const MUST_KEEP = [
  "gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-5", "gpt-5-mini", "gpt-5-nano",
  "o3", "o3-mini", "o4-mini",
];
// Unresolved until a funded probe: the probe hit billing_not_active on most ids, and these families
// had no 404 saying "not supported in v1/chat/completions" for EVERY probed id (codex: one did, five
// said "deprecated"). They stay allowed and are deliberately not asserted on.
const UNRESOLVED = ["audio", "search", "pro", "live", "codex", "chat-latest"];

let fetchMock;
beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("handleModelsFetch — validation", () => {
  it("unknown provider -> 400, no provider call", async () => {
    const env = makeEnv();
    const res = await handleModelsFetch(req({ provider: "nope", apiKey: "sk-x" }), env);
    expect(res.status).toBe(400);
    expect((await res.json()).error).toBe("unknown_provider");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("missing apiKey -> 400, no provider call", async () => {
    const env = makeEnv();
    const res = await handleModelsFetch(req({ provider: "gemini" }), env);
    expect(res.status).toBe(400);
    expect((await res.json()).error).toBe("missing_api_key");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

// Step 6 security review (F6): this endpoint had no rate limit at all — same mechanism as
// POST /api/runs (rateLimit.js), not a bespoke one, and a separate bucket from it.
describe("handleModelsFetch — rate limit", () => {
  it("blocks past the per-IP daily limit with 429 rate_limited, no provider call once blocked", async () => {
    const env = makeEnv({ RATE_LIMIT: "2" });
    fetchMock.mockResolvedValue(fakeResponse(200, GEMINI_BODY));

    const first = await handleModelsFetch(req({ provider: "gemini", apiKey: "sk-a" }, "9.9.9.9"), env);
    expect(first.status).toBe(200);
    const second = await handleModelsFetch(req({ provider: "gemini", apiKey: "sk-b" }, "9.9.9.9"), env);
    expect(second.status).toBe(200);

    fetchMock.mockClear();
    const third = await handleModelsFetch(req({ provider: "gemini", apiKey: "sk-c" }, "9.9.9.9"), env);
    expect(third.status).toBe(429);
    expect((await third.json()).error).toBe("rate_limited");
    expect(fetchMock).not.toHaveBeenCalled(); // blocked before ever reaching the provider
  });

  it("uses its own bucket, independent of POST /api/runs's limit", async () => {
    const env = makeEnv({ RATE_LIMIT: "1" });
    fetchMock.mockResolvedValue(fakeResponse(200, GEMINI_BODY));
    await handleModelsFetch(req({ provider: "gemini", apiKey: "sk-a" }, "9.9.9.8"), env);
    const stored = await env.RELAY_KV.get(`rl:models:9.9.9.8:${new Date().toISOString().slice(0, 10).replace(/-/g, "")}`);
    expect(stored).toBe("1"); // the "models" bucket, never "rl:9.9.9.8:..." or "rl:runs:..."
  });
});

describe("handleModelsFetch — cache", () => {
  it("cache HIT ranks the cached list without calling the provider", async () => {
    const env = makeEnv();
    const cached = { models: [{ id: "gemini-3.5-flash", label: "Gemini 3.5 Flash" }] };
    await env.RELAY_KV.put("models:gemini", JSON.stringify(cached));

    const res = await handleModelsFetch(req({ provider: "gemini", apiKey: "sk-user" }), env);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      provider: "gemini", ladder: [{ id: "gemini-3.5-flash", tier: "balanced" }], default_index: 0,
    });
    expect(fetchMock).not.toHaveBeenCalled(); // the whole point of the cache
  });

  it("cache MISS calls the provider, normalizes, and populates the cache", async () => {
    const env = makeEnv();
    fetchMock.mockResolvedValue(fakeResponse(200, GEMINI_BODY));

    const res = await handleModelsFetch(req({ provider: "gemini", apiKey: "sk-user" }), env);
    expect(res.status).toBe(200);
    expect((await res.json()).ladder).toEqual([{ id: "gemini-3.5-flash", tier: "balanced" }]);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // The cache holds the normalized list: only the generateContent-capable model, prefix stripped.
    const stored = await env.RELAY_KV.get("models:gemini", "json");
    expect(stored).toEqual({ models: [{ id: "gemini-3.5-flash", label: "Gemini 3.5 Flash" }] });
  });

  it("provider-scoped cache never contains the apiKey (key-scoped it is not)", async () => {
    const env = makeEnv();
    const apiKey = "sk-secret-should-never-be-cached-1234567890";
    fetchMock.mockResolvedValue(fakeResponse(200, GEMINI_BODY));

    await handleModelsFetch(req({ provider: "gemini", apiKey }), env);

    const rawEntry = env.RELAY_KV.store.get("models:gemini").value; // the literal cached string
    expect(rawEntry).not.toContain(apiKey);
    expect(JSON.stringify([...env.RELAY_KV.store.keys()])).not.toContain(apiKey); // nor any key
  });
});

describe("handleModelsFetch — provider errors", () => {
  it("provider 401 passes through as key_rejected and is NOT cached", async () => {
    const env = makeEnv();
    fetchMock.mockResolvedValue(fakeResponse(401, { error: "invalid api key" }));

    const res = await handleModelsFetch(req({ provider: "openai", apiKey: "sk-bad" }), env);
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("key_rejected");
    expect(await env.RELAY_KV.get("models:openai")).toBeNull(); // a bad key must not poison the cache
  });

  it("provider 403 also passes through as key_rejected", async () => {
    const env = makeEnv();
    fetchMock.mockResolvedValue(fakeResponse(403, {}));
    const res = await handleModelsFetch(req({ provider: "anthropic", apiKey: "sk-ant-bad" }), env);
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("key_rejected");
  });
});

describe("handleModelsFetch — per-provider request shape", () => {
  // Step 6 security review (F9): the key used to ride in the URL query string, which
  // subrequest-URL logging (wrangler tail, Logpush) routinely captures in full -- moved to the
  // x-goog-api-key header, matching how every other provider here sends its key.
  it("gemini sends the key via the x-goog-api-key header, never in the URL", async () => {
    const env = makeEnv();
    fetchMock.mockResolvedValue(fakeResponse(200, GEMINI_BODY));
    await handleModelsFetch(req({ provider: "gemini", apiKey: "sk-gem" }), env);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("https://generativelanguage.googleapis.com/v1beta/models");
    expect(url).not.toContain("sk-gem");
    expect(opts.headers["x-goog-api-key"]).toBe("sk-gem");
    expect(opts.headers.Authorization).toBeUndefined();
  });

  it("openai uses Bearer auth and the chat-prefix filter drops non-chat models", async () => {
    const env = makeEnv();
    fetchMock.mockResolvedValue(fakeResponse(200, {
      data: [{ id: "gpt-5" }, { id: "text-embedding-3-large" }, { id: "o3" }, { id: "dall-e-3" }],
    }));
    await handleModelsFetch(req({ provider: "openai", apiKey: "sk-oai" }), env);
    expect(await cachedIds(env, "openai")).toEqual(["gpt-5", "o3"]); // embeddings + image models filtered out
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer sk-oai");
  });

  it("openai filter over the real 133-id /v1/models capture", async () => {
    const env = makeEnv();
    fetchMock.mockResolvedValue(fakeResponse(200, { data: OPENAI_MODEL_IDS.map((id) => ({ id })) }));
    await handleModelsFetch(req({ provider: "openai", apiKey: "sk-oai" }), env);
    const kept = new Set(await cachedIds(env, "openai"));
    const excluded = OPENAI_MODEL_IDS.filter((id) => !kept.has(id));

    const nonChat = OPENAI_MODEL_IDS.filter((id) => DEFINITIVE_NON_CHAT.test(id));
    expect(nonChat.length).toBeGreaterThan(0);
    for (const id of nonChat) expect(kept.has(id), id).toBe(false);
    for (const id of MUST_KEEP) expect(kept.has(id), id).toBe(true);
    for (const id of OPENAI_KNOWN_DEAD_IDS) expect(kept.has(id), id).toBe(false);

    const unresolved = [...kept].filter((id) => UNRESOLVED.some((f) => id.includes(f)));
    console.log(`openai filter: kept=${kept.size} excluded=${excluded.length} ` +
      `(unresolved, kept: ${unresolved.length})`);
  });

  it("OPENAI_KNOWN_DEAD_IDS is exactly the probe's 404 ids (drift fails loudly)", () => {
    const fixture404 = OPENAI_REJECTIONS.filter((r) => r.status === 404).map((r) => r.id).sort();
    expect([...OPENAI_KNOWN_DEAD_IDS].sort()).toEqual(fixture404);
  });

  it("anthropic uses x-api-key + anthropic-version and maps display_name to label", async () => {
    const env = makeEnv();
    fetchMock.mockResolvedValue(fakeResponse(200, {
      data: [{ id: "claude-opus-4-6", display_name: "Claude Opus 4.6" }],
    }));
    await handleModelsFetch(req({ provider: "anthropic", apiKey: "sk-ant" }), env);
    expect((await env.RELAY_KV.get("models:anthropic", "json")).models).toEqual(
      [{ id: "claude-opus-4-6", label: "Claude Opus 4.6", created: 0 }]); // no created_at in this stub
    const headers = fetchMock.mock.calls[0][1].headers;
    expect(headers["x-api-key"]).toBe("sk-ant");
    expect(headers["anthropic-version"]).toBe("2023-06-01");
  });

  it("openrouter keeps only structured-output-capable models", async () => {
    const env = makeEnv();
    fetchMock.mockResolvedValue(fakeResponse(200, {
      data: [
        { id: "a/chat", name: "A Chat", supported_parameters: ["tools", "temperature"] },
        { id: "b/plain", name: "B Plain", supported_parameters: ["temperature"] }, // no structured
        { id: "c/unknown", name: "C Unknown" }, // no field -> kept (benefit of the doubt)
      ],
    }));
    await handleModelsFetch(req({ provider: "openrouter", apiKey: "sk-or" }), env);
    expect(await cachedIds(env, "openrouter")).toEqual(["a/chat", "c/unknown"]);
  });

  it("openrouter caps at OPENROUTER_MAX_MODELS (300) after the capability filter", async () => {
    const env = makeEnv();
    const data = Array.from({ length: 350 }, (_, i) => (
      { id: `p/model-${i}`, name: `Model ${i}`, supported_parameters: ["tools"] }
    ));
    fetchMock.mockResolvedValue(fakeResponse(200, { data }));
    await handleModelsFetch(req({ provider: "openrouter", apiKey: "sk-or" }), env);
    const ids = await cachedIds(env, "openrouter");
    expect(ids).toHaveLength(300);
    expect(ids[0]).toBe("p/model-0"); // slice keeps the front of the (already-filtered) list
  });

  it("grok uses Bearer auth against api.x.ai, no chat-prefix filter (all models kept)", async () => {
    const env = makeEnv();
    fetchMock.mockResolvedValue(fakeResponse(200, { data: [{ id: "grok-5" }, { id: "grok-5-mini" }] }));
    await handleModelsFetch(req({ provider: "grok", apiKey: "sk-grok" }), env);
    expect(await cachedIds(env, "grok")).toEqual(["grok-5", "grok-5-mini"]); // unlike openai, no family filter to drop anything
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.x.ai/v1/models");
    expect(opts.headers.Authorization).toBe("Bearer sk-grok");
  });
});

describe("handleModelsFetch — network/response failures (Step 4)", () => {
  it("the fetch itself failing (network down, DNS, etc.) -> provider_unreachable, not cached", async () => {
    const env = makeEnv();
    fetchMock.mockRejectedValue(new Error("network down"));
    const res = await handleModelsFetch(req({ provider: "openai", apiKey: "sk-oai" }), env);
    expect(res.status).toBe(502);
    expect((await res.json()).error).toBe("provider_unreachable");
    expect(await env.RELAY_KV.get("models:openai")).toBeNull();
  });

  it("a real non-401/403 provider error (e.g. 500) -> provider_error, not cached", async () => {
    const env = makeEnv();
    fetchMock.mockResolvedValue(fakeResponse(500, { error: "internal" }));
    const res = await handleModelsFetch(req({ provider: "anthropic", apiKey: "sk-ant" }), env);
    expect(res.status).toBe(502);
    const body = await res.json();
    expect(body.error).toBe("provider_error");
    expect(body.status).toBe(500); // the real upstream status rides along, unlike key_rejected
    expect(await env.RELAY_KV.get("models:anthropic")).toBeNull();
  });

  it("a 2xx with an unparseable body -> provider_bad_response, not cached", async () => {
    const env = makeEnv();
    fetchMock.mockResolvedValue({
      status: 200, ok: true,
      json: async () => { throw new SyntaxError("Unexpected token in JSON"); },
    });
    const res = await handleModelsFetch(req({ provider: "openrouter", apiKey: "sk-or" }), env);
    expect(res.status).toBe(502);
    expect((await res.json()).error).toBe("provider_bad_response");
    expect(await env.RELAY_KV.get("models:openrouter")).toBeNull();
  });
});

// Phase 3 piece 8: Anthropic's list call. A key not scoped to one workspace got a 400 live
// (2026-11-08); that one case maps to a fixed code, and no provider text ever leaves the Worker.
describe("handleModelsFetch — anthropic request shape and workspace 400", () => {
  let fetchMock;
  beforeEach(() => { fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock); });
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  const CANARY = "CANARY-provider-text-7f3a";
  const WORKSPACE_400 = { type: "error", error: { type: "invalid_request_error",
    message: `This API key is not scoped to a workspace, so this request must include the anthropic-workspace-id header ${CANARY}` } };
  const call = async (status, body, env = makeEnv()) => {
    fetchMock.mockResolvedValue(fakeResponse(status, body));
    const res = await handleModelsFetch(req({ provider: "anthropic", apiKey: "sk-ant-key" }), env);
    return { res, text: await res.text(), env };
  };

  it("pins the request: GET /v1/models?limit=1000 with exactly x-api-key + anthropic-version", async () => {
    await call(200, { data: [] });
    const [url, opts] = fetchMock.mock.calls[0];
    const u = new URL(url);
    expect(`${u.origin}${u.pathname}`).toBe("https://api.anthropic.com/v1/models");
    expect([...u.searchParams]).toEqual([["limit", "1000"]]);
    expect(opts.method).toBe("GET");
    expect(opts.headers).toEqual({ "x-api-key": "sk-ant-key", "anthropic-version": "2023-06-01" });
  });

  it("the workspace 400 -> provider_key_needs_workspace, {error, provider} only, not cached", async () => {
    const { res, text, env } = await call(400, WORKSPACE_400);
    expect(res.status).toBe(400);
    expect(JSON.parse(text)).toEqual({ error: "provider_key_needs_workspace", provider: "anthropic" });
    expect(await env.RELAY_KV.get("models:anthropic")).toBeNull();
  });

  it("a 400 with other text, or another error type, stays provider_error", async () => {
    for (const body of [
      { error: { type: "invalid_request_error", message: `limit out of range ${CANARY}` } },
      { error: { type: "permission_error", message: "anthropic-workspace-id" } },
      "not json at all",
    ]) {
      const { res, text } = await call(400, body);
      expect(res.status).toBe(502);
      expect(JSON.parse(text)).toEqual({ error: "provider_error", provider: "anthropic", status: 400 });
    }
  });

  it("an unreadable 400 body stays provider_error", async () => {
    fetchMock.mockResolvedValue({ status: 400, ok: false, json: async () => { throw new SyntaxError("x"); } });
    const res = await handleModelsFetch(req({ provider: "anthropic", apiKey: "sk-ant-key" }), makeEnv());
    expect((await res.json()).error).toBe("provider_error");
  });

  it("provider text (canary) never reaches the response, the cache or the console", async () => {
    const spies = ["log", "info", "warn", "error", "debug"].map((m) => vi.spyOn(console, m).mockImplementation(() => {}));
    const env = makeEnv();
    const outputs = [];
    for (const [status, body] of [[400, WORKSPACE_400], [400, { error: { type: "invalid_request_error", message: CANARY } }],
                                  [401, { error: { message: CANARY } }], [503, { error: { message: CANARY } }]]) {
      outputs.push((await call(status, body, env)).text);
    }
    expect(outputs.join("\n")).not.toContain(CANARY);
    expect(JSON.stringify([...env.RELAY_KV.store.entries()])).not.toContain(CANARY);
    expect(spies.flatMap((s) => s.mock.calls).map((c) => JSON.stringify(c)).join("\n")).not.toContain(CANARY);
  });

  it("401 and 5xx are unchanged: key_rejected and provider_error", async () => {
    const r401 = await call(401, WORKSPACE_400); // workspace text on a 401 is still just a rejected key
    expect(r401.res.status).toBe(401);
    expect(JSON.parse(r401.text)).toEqual({ error: "key_rejected", provider: "anthropic" });
    const r503 = await call(503, WORKSPACE_400);
    expect(r503.res.status).toBe(502);
    expect(JSON.parse(r503.text)).toEqual({ error: "provider_error", provider: "anthropic", status: 503 });
  });

  it("only anthropic gets the mapping: the same 400 from openai stays provider_error", async () => {
    fetchMock.mockResolvedValue(fakeResponse(400, WORKSPACE_400));
    const res = await handleModelsFetch(req({ provider: "openai", apiKey: "sk-oai-key" }), makeEnv());
    expect((await res.json()).error).toBe("provider_error");
  });
});
