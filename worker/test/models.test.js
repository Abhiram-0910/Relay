// worker/test/models.test.js
import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { handleModelsFetch } from "../src/models.js";

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
    const second = await handleModelsFetch(req({ provider: "openai", apiKey: "sk-b" }, "9.9.9.9"), env);
    expect(second.status).toBe(200);

    fetchMock.mockClear();
    const third = await handleModelsFetch(req({ provider: "openai", apiKey: "sk-c" }, "9.9.9.9"), env);
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
  it("cache HIT returns the cached list without calling the provider", async () => {
    const env = makeEnv();
    const cached = { models: [{ id: "gemini-3.5-flash", label: "Gemini 3.5 Flash" }] };
    await env.RELAY_KV.put("models:gemini", JSON.stringify(cached));

    const res = await handleModelsFetch(req({ provider: "gemini", apiKey: "sk-user" }), env);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(cached);
    expect(fetchMock).not.toHaveBeenCalled(); // the whole point of the cache
  });

  it("cache MISS calls the provider, normalizes, and populates the cache", async () => {
    const env = makeEnv();
    fetchMock.mockResolvedValue(fakeResponse(200, GEMINI_BODY));

    const res = await handleModelsFetch(req({ provider: "gemini", apiKey: "sk-user" }), env);
    expect(res.status).toBe(200);
    const body = await res.json();
    // Only the generateContent-capable model survives; name prefix stripped, label kept.
    expect(body.models).toEqual([{ id: "gemini-3.5-flash", label: "Gemini 3.5 Flash" }]);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // The cache is now populated with the SAME normalized payload.
    const stored = await env.RELAY_KV.get("models:gemini", "json");
    expect(stored).toEqual(body);
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
    const res = await handleModelsFetch(req({ provider: "openai", apiKey: "sk-oai" }), env);
    const ids = (await res.json()).models.map((m) => m.id);
    expect(ids).toEqual(["gpt-5", "o3"]); // embeddings + image models filtered out
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer sk-oai");
  });

  it("anthropic uses x-api-key + anthropic-version and maps display_name to label", async () => {
    const env = makeEnv();
    fetchMock.mockResolvedValue(fakeResponse(200, {
      data: [{ id: "claude-opus-4-6", display_name: "Claude Opus 4.6" }],
    }));
    const res = await handleModelsFetch(req({ provider: "anthropic", apiKey: "sk-ant" }), env);
    expect((await res.json()).models).toEqual([{ id: "claude-opus-4-6", label: "Claude Opus 4.6" }]);
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
    const res = await handleModelsFetch(req({ provider: "openrouter", apiKey: "sk-or" }), env);
    const ids = (await res.json()).models.map((m) => m.id);
    expect(ids).toEqual(["a/chat", "c/unknown"]);
  });

  it("openrouter caps at OPENROUTER_MAX_MODELS (300) after the capability filter", async () => {
    const env = makeEnv();
    const data = Array.from({ length: 350 }, (_, i) => (
      { id: `p/model-${i}`, name: `Model ${i}`, supported_parameters: ["tools"] }
    ));
    fetchMock.mockResolvedValue(fakeResponse(200, { data }));
    const res = await handleModelsFetch(req({ provider: "openrouter", apiKey: "sk-or" }), env);
    const models = (await res.json()).models;
    expect(models).toHaveLength(300);
    expect(models[0].id).toBe("p/model-0"); // slice keeps the front of the (already-filtered) list
  });

  it("grok uses Bearer auth against api.x.ai, no chat-prefix filter (all models kept)", async () => {
    const env = makeEnv();
    fetchMock.mockResolvedValue(fakeResponse(200, { data: [{ id: "grok-5" }, { id: "grok-5-mini" }] }));
    const res = await handleModelsFetch(req({ provider: "grok", apiKey: "sk-grok" }), env);
    const ids = (await res.json()).models.map((m) => m.id);
    expect(ids).toEqual(["grok-5", "grok-5-mini"]); // unlike openai, no family filter to drop anything
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
