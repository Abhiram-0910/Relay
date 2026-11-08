// worker/test/modelRank.test.js — Phase 3 piece 8, BYOK automatic model selection.
//
// Fixture provenance:
//   - openai-model-ids.json: a REAL /v1/models capture (133 ids, 2026-10-19). Ids only, no created.
//   - openrouter-models.json: OpenRouter's REAL public /api/v1/models (no key needed), fetched
//     2026-11-05, trimmed to id + created + the structured-output params the filter reads.
//   - anthropic / gemini / grok lists below are SYNTHETIC shapes (no key available to capture the
//     real lists): they prove the ranking LOGIC only, not that the real ids look like this.
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { rankModels, compareVersions, isValidModelId } from "../src/modelRank.js";
import { handleModelsFetch, OPENAI_KNOWN_DEAD_IDS } from "../src/models.js";
import OPENAI_MODEL_IDS from "./fixtures/openai-model-ids.json";
import OPENROUTER from "./fixtures/openrouter-models.json";

class FakeKV {
  constructor() { this.store = new Map(); }
  async get(key, type) {
    if (!this.store.has(key)) return null;
    const v = this.store.get(key);
    return type === "json" ? JSON.parse(v) : v;
  }
  async put(key, value) { this.store.set(key, value); }
  async delete(key) { this.store.delete(key); }
}
const req = (body, ip = "1.2.3.4") => new Request("https://worker.example/api/models", {
  method: "POST", headers: { "Content-Type": "application/json", "CF-Connecting-IP": ip }, body: JSON.stringify(body),
});
const ok = (body) => ({ status: 200, ok: true, json: async () => body });

let fetchMock;
beforeEach(() => { fetchMock = vi.fn(); vi.stubGlobal("fetch", fetchMock); });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

// Real path: provider body -> models.js filter -> rankModels. Returns the route's JSON.
async function ladderFor(provider, body, env = { RELAY_KV: new FakeKV() }) {
  fetchMock.mockResolvedValue(ok(body));
  const res = await handleModelsFetch(req({ provider, apiKey: "sk-test-key-123456" }), env);
  return { status: res.status, body: await res.json() };
}
const openaiBody = (extra = []) => ({ data: [...OPENAI_MODEL_IDS, ...extra].map((id) => ({ id })) });
const ids = (ladder) => ladder.map((r) => r.id);

describe("rankModels over the real 133-id OpenAI capture", () => {
  it("proposes one model per tier, balanced by default, all real chat ids", async () => {
    const { status, body } = await ladderFor("openai", openaiBody());
    expect(status).toBe(200);
    expect(body.ladder).toEqual([
      { id: "gpt-6-luna", tier: "small" },
      { id: "gpt-6.1-sol", tier: "balanced" },
      { id: "gpt-6-astra", tier: "flagship" },
    ]);
    expect(body.default_index).toBe(1);
    const nonChat = /image|transcribe|tts|realtime|whisper|embedding|moderation|sora|dall-e|instruct|davinci|babbage|audio|search|live/;
    for (const id of ids(body.ladder)) {
      expect(OPENAI_MODEL_IDS).toContain(id);
      expect(OPENAI_KNOWN_DEAD_IDS.has(id)).toBe(false);
      expect(id).not.toMatch(nonChat);
      expect(id).not.toMatch(/^o\d|-pro\b/); // reasoning families are never auto-picked
    }
  });

  it("is deterministic: same ladder for a shuffled copy of the list", () => {
    const models = OPENAI_MODEL_IDS.map((id) => ({ id }));
    const a = rankModels("openai", models);
    const b = rankModels("openai", [...models].reverse());
    expect(b).toEqual(a);
  });
});

describe("FUTURE-MODEL: new versions move the ladder with no code change", () => {
  it("openai: a higher mini and base move balanced/flagship; dated twin and unknown family excluded", async () => {
    // gpt-6.9-luna then gpt-6.10-luna: a minor bump past 9, which a string compare gets wrong.
    const future = ["gpt-7-mini", "gpt-7", "gpt-7-2027-01-15", "gpt-7-mini-2027-01-15", "gpt-7-nova", "zeta-1",
                    "gpt-6.9-luna", "gpt-6.10-luna"];
    const { body } = await ladderFor("openai", openaiBody(future));
    expect(body.ladder).toEqual([
      { id: "gpt-6.10-luna", tier: "small" },
      { id: "gpt-7-mini", tier: "balanced" },
      { id: "gpt-7", tier: "flagship" },
    ]);
    expect(ids(body.ladder)).not.toContain("gpt-7-nova");
  });

  it("openai: a dated snapshot with no alias is kept; the latest snapshot wins", () => {
    const r = rankModels("openai", [{ id: "gpt-8-mini-2028-01-01" }, { id: "gpt-8-mini-2028-06-01" }]);
    expect(r.ladder).toEqual([{ id: "gpt-8-mini-2028-06-01", tier: "balanced" }]);
  });

  // SYNTHETIC lists for these three providers — logic only.
  it.each([
    ["anthropic",
      ["claude-haiku-4-5-20251001", "claude-sonnet-4-5-20250929", "claude-sonnet-4-5", "claude-opus-4-6", "claude-3-5-haiku-20241022"],
      ["claude-sonnet-6", "claude-sonnet-6-20270101", "claude-opus-6-1", "claude-nova-7"],
      ["claude-haiku-4-5-20251001", "claude-sonnet-6", "claude-opus-6-1"]],
    ["gemini",
      ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-pro", "gemini-3-pro-preview", "gemini-flash-latest"],
      ["gemini-4.1-flash", "gemini-4.1-flash-001", "gemini-5-argon", "gemini-5-quark"],
      ["gemini-2.5-flash-lite", "gemini-4.1-flash", "gemini-5-argon"]],
    ["grok",
      ["grok-3-mini", "grok-4", "grok-4-0709", "grok-4-1-fast-reasoning"],
      ["grok-5-mini", "grok-5", "grok-5-0101", "grok-nova-1"],
      ["grok-5-mini", "grok-5"]],
  ])("%s: new versions win, dated twin and unknown family excluded", (provider, now, future, expected) => {
    const r = rankModels(provider, [...now, ...future].map((id) => ({ id })));
    expect(ids(r.ladder)).toEqual(expected);
  });

  it("openrouter (real public list): one model per tier from the allowlisted vendors", async () => {
    const { body } = await ladderFor("openrouter", OPENROUTER);
    expect(body.ladder).toEqual([
      { id: "anthropic/claude-haiku-5.5", tier: "small" },
      { id: "openai/gpt-6.1-sol", tier: "balanced" },
      { id: "google/gemini-4-argon", tier: "flagship" },
    ]);
    // ...and a future vendor release moves it, ranked by the vendor's own table. Prepended: the real
    // list leads with its newest models, and models.js's 300-model cap keeps the front.
    const future = { data: [{ id: "anthropic/claude-sonnet-6", created: 1800000000 },
                            { id: "qwen/qwen-9-max", created: 1800000001 }, ...OPENROUTER.data] };
    const next = await ladderFor("openrouter", future);
    expect(next.body.ladder[1]).toEqual({ id: "anthropic/claude-sonnet-6", tier: "balanced" });
    expect(ids(next.body.ladder)).not.toContain("qwen/qwen-9-max"); // vendor not allowlisted
  });
});

describe("version compare and edges", () => {
  it("compares numerically: v10 beats v9, 5.10 beats 5.9, 6 equals 6.0", () => {
    expect(compareVersions([10], [9])).toBeGreaterThan(0);
    expect(compareVersions([5, 10], [5, 9])).toBeGreaterThan(0);
    expect(compareVersions([6], [6, 0])).toBe(0);
    expect(ids(rankModels("openai", [{ id: "gpt-9" }, { id: "gpt-10" }]).ladder)).toEqual(["gpt-10"]);
  });

  it("empty or unrankable input -> empty ladder, and the route says no_suitable_model", async () => {
    expect(rankModels("openai", [])).toEqual({ ladder: [], default_index: -1 });
    expect(rankModels("nope", [{ id: "gpt-5" }]).ladder).toEqual([]);
    const { status, body } = await ladderFor("openai", { data: [{ id: "o3" }, { id: "gpt-4o" }] });
    expect(status).toBe(422);
    expect(body).toEqual({ error: "no_suitable_model", provider: "openai" });
  });

  it("default is the nearest tier to balanced, the cheaper one on a tie", () => {
    expect(rankModels("grok", [{ id: "grok-3-mini" }, { id: "grok-4" }]).default_index).toBe(0);
    expect(rankModels("grok", [{ id: "grok-4" }]).default_index).toBe(0);
  });

  it("caps id length and shape (shared with POST /api/runs)", () => {
    expect(isValidModelId("a".repeat(128))).toBe(true);
    expect(isValidModelId("a".repeat(129))).toBe(false);
    expect(isValidModelId("openai/gpt-5:batch")).toBe(false);
    expect(rankModels("openai", [{ id: `gpt-5-mini${"x".repeat(200)}` }]).ladder).toEqual([]);
  });
});

describe("POST /api/models response", () => {
  const KEY = "sk-live-SENTINEL-do-not-echo-0123456789";

  it("returns only {provider, ladder, default_index}: no full list, no key, nothing logged", async () => {
    const spies = ["log", "info", "warn", "error", "debug"].map((m) => vi.spyOn(console, m).mockImplementation(() => {}));
    fetchMock.mockResolvedValue(ok(openaiBody()));
    const res = await handleModelsFetch(req({ provider: "openai", apiKey: KEY }), { RELAY_KV: new FakeKV() });
    const text = await res.text();
    const body = JSON.parse(text);
    expect(Object.keys(body).sort()).toEqual(["default_index", "ladder", "provider"]);
    expect(body.ladder.length).toBeLessThanOrEqual(3);
    for (const rung of body.ladder) expect(Object.keys(rung).sort()).toEqual(["id", "tier"]);
    expect(text).not.toContain(KEY);
    const logged = spies.flatMap((s) => s.mock.calls).map((c) => JSON.stringify(c)).join("\n");
    expect(logged).not.toContain(KEY);
  });

  it("the rate limit still applies before any provider call", async () => {
    const env = { RELAY_KV: new FakeKV(), RATE_LIMIT: "1" };
    fetchMock.mockResolvedValue(ok(openaiBody()));
    expect((await handleModelsFetch(req({ provider: "openai", apiKey: KEY }, "5.5.5.5"), env)).status).toBe(200);
    fetchMock.mockClear();
    const blocked = await handleModelsFetch(req({ provider: "openai", apiKey: KEY }, "5.5.5.5"), env);
    expect(blocked.status).toBe(429);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
