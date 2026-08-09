import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { handle } from "../src/index.js";

// In-memory KV double (Map-backed) matching the subset of the KV API the Worker uses.
function fakeKV() {
  const store = new Map();
  return {
    store,
    async get(key, type) {
      const v = store.get(key);
      if (v == null) return null;
      return type === "json" ? JSON.parse(v) : v;
    },
    async put(key, value) {
      store.set(key, value);
    },
  };
}

function makeEnv(overrides = {}) {
  return {
    RELAY_KV: fakeKV(),
    GH_OWNER: "Abhiram-0910",
    GH_REPO: "Relay",
    GH_WORKFLOW: "generate.yml",
    GH_REF: "main",
    RATE_LIMIT: "3",
    GH_PAT: "pat-secret",
    CALLBACK_SECRET: "callback-secret",
    ...overrides,
  };
}

function req(method, path, { body, headers } = {}) {
  return new Request(`https://relay-worker.example.com${path}`, {
    method,
    headers: { "Content-Type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

let dispatch;
beforeEach(() => {
  dispatch = vi.fn(async () => new Response(null, { status: 204 })); // GitHub dispatch OK
  vi.stubGlobal("fetch", dispatch);
});
afterEach(() => vi.unstubAllGlobals());

const SPEC = { specUrl: "https://example.com/openapi.yml" };
const IP = (ip) => ({ "CF-Connecting-IP": ip });

describe("POST /api/runs", () => {
  it("creates a run, stores it queued, and dispatches with the right inputs", async () => {
    const env = makeEnv();
    const res = await handle(req("POST", "/api/runs", { body: SPEC, headers: IP("1.1.1.1") }), env);
    expect(res.status).toBe(202);
    const { runId, status, statusUrl } = await res.json();
    expect(status).toBe("queued");
    expect(statusUrl).toBe(`/api/runs/${runId}`);

    const stored = await env.RELAY_KV.get(`run:${runId}`, "json");
    expect(stored.status).toBe("queued");

    expect(dispatch).toHaveBeenCalledOnce();
    const [url, opts] = dispatch.mock.calls[0];
    expect(url).toContain("/repos/Abhiram-0910/Relay/actions/workflows/generate.yml/dispatches");
    expect(opts.headers.Authorization).toBe("Bearer pat-secret");
    const sent = JSON.parse(opts.body);
    expect(sent.inputs.run_id).toBe(runId);
    expect(sent.inputs.spec_url).toBe(SPEC.specUrl);
    expect(sent.inputs.callback_url).toBe("https://relay-worker.example.com");
  });

  it("rejects an invalid spec URL with 400 and never dispatches", async () => {
    const env = makeEnv();
    const res = await handle(req("POST", "/api/runs", { body: { specUrl: "not-a-url" }, headers: IP("1.1.1.1") }), env);
    expect(res.status).toBe(400);
    expect(dispatch).not.toHaveBeenCalled();
  });

  it("blocks past the per-IP daily limit BEFORE dispatching", async () => {
    const env = makeEnv({ RATE_LIMIT: "1" });
    const first = await handle(req("POST", "/api/runs", { body: SPEC, headers: IP("9.9.9.9") }), env);
    expect(first.status).toBe(202);
    const second = await handle(req("POST", "/api/runs", { body: SPEC, headers: IP("9.9.9.9") }), env);
    expect(second.status).toBe(429);
    expect((await second.json()).error).toBe("rate_limited");
    expect(dispatch).toHaveBeenCalledOnce(); // the blocked one never reached dispatch
  });

  it("refunds the rate-limit count when dispatch fails", async () => {
    const env = makeEnv({ RATE_LIMIT: "2" });
    dispatch.mockResolvedValueOnce(new Response("boom", { status: 500 }));
    const res = await handle(req("POST", "/api/runs", { body: SPEC, headers: IP("7.7.7.7") }), env);
    expect(res.status).toBe(502);
    // count was refunded to 0, so a subsequent request still succeeds
    const day = new Date().toISOString().slice(0, 10).replace(/-/g, "");
    expect(await env.RELAY_KV.get(`rl:7.7.7.7:${day}`)).toBe("0");
  });
});

// Step 4: the BYOK fields (apiKey/provider/model) were only ever tested against byok.js's
// exported functions directly (test/byok.test.js) — never through the real POST /api/runs route
// this handler actually serves. These prove the real wiring, not just the isolated functions.
describe("POST /api/runs — BYOK wiring", () => {
  it("stores byok:{runId} and flags has_byok=true in the real dispatch inputs", async () => {
    const env = makeEnv();
    const body = { specUrl: SPEC.specUrl, apiKey: "sk-live-abcdef123456", provider: "openai", model: "gpt-5" };
    const res = await handle(req("POST", "/api/runs", { body, headers: IP("6.6.6.1") }), env);
    expect(res.status).toBe(202);
    const { runId } = await res.json();

    const byokEntry = await env.RELAY_KV.get(`byok:${runId}`, "json");
    expect(byokEntry.apiKey).toBe("sk-live-abcdef123456");
    expect(byokEntry.provider).toBe("openai");
    expect(byokEntry.model).toBe("gpt-5");

    const sent = JSON.parse(dispatch.mock.calls[0][1].body);
    expect(sent.inputs.has_byok).toBe("true");
    // buildDispatchInputs already proves this structurally (byok.test.js) — this proves the real
    // route never regresses it by, say, spreading the body into inputs somewhere along the way.
    expect(JSON.stringify(sent.inputs)).not.toContain("sk-live-abcdef123456");
  });

  it("a plain run (no BYOK fields) gets has_byok=false and no byok:{runId} entry at all", async () => {
    const env = makeEnv();
    const res = await handle(req("POST", "/api/runs", { body: SPEC, headers: IP("6.6.6.2") }), env);
    const { runId } = await res.json();
    expect(await env.RELAY_KV.get(`byok:${runId}`)).toBeNull();
    const sent = JSON.parse(dispatch.mock.calls[0][1].body);
    expect(sent.inputs.has_byok).toBe("false");
  });

  it("rejects a malformed apiKey with 400 invalid_api_key, never dispatches, refunds the rate limit", async () => {
    const env = makeEnv({ RATE_LIMIT: "2" });
    const body = { specUrl: SPEC.specUrl, apiKey: "short", provider: "openai" };
    const res = await handle(req("POST", "/api/runs", { body, headers: IP("6.6.6.3") }), env);
    expect(res.status).toBe(400);
    expect((await res.json()).error).toBe("invalid_api_key");
    expect(dispatch).not.toHaveBeenCalled();
    const day = new Date().toISOString().slice(0, 10).replace(/-/g, "");
    expect(await env.RELAY_KV.get(`rl:6.6.6.3:${day}`)).toBe("0");
  });

  it("rejects a malformed model (empty string) the same way, apiKey alone isn't enough to pass", async () => {
    const env = makeEnv();
    const body = { specUrl: SPEC.specUrl, apiKey: "sk-live-abcdef123456", provider: "openai", model: "" };
    const res = await handle(req("POST", "/api/runs", { body, headers: IP("6.6.6.4") }), env);
    expect(res.status).toBe(400);
    expect((await res.json()).error).toBe("invalid_api_key");
    expect(dispatch).not.toHaveBeenCalled();
  });
});

describe("run-id isolation", () => {
  it("two concurrent runs get distinct ids and never cross-contaminate polls", async () => {
    const env = makeEnv();
    const a = await (await handle(req("POST", "/api/runs", { body: SPEC, headers: IP("2.2.2.2") }), env)).json();
    const b = await (await handle(req("POST", "/api/runs", { body: SPEC, headers: IP("3.3.3.3") }), env)).json();
    expect(a.runId).not.toBe(b.runId);

    // advance run A to succeeded via its own progress callback
    await handle(req("POST", `/api/runs/${a.runId}/progress`, {
      body: { status: "succeeded", stage: "done", result: { title: "A" } },
      headers: { Authorization: "Bearer callback-secret" },
    }), env);

    const polledA = await (await handle(req("GET", `/api/runs/${a.runId}`), env)).json();
    const polledB = await (await handle(req("GET", `/api/runs/${b.runId}`), env)).json();
    expect(polledA.status).toBe("succeeded");
    expect(polledA.result.title).toBe("A");
    expect(polledB.status).toBe("queued"); // untouched by A's update
  });

  it("returns 404 for an unknown run id", async () => {
    const env = makeEnv();
    const res = await handle(req("GET", "/api/runs/does-not-exist"), env);
    expect(res.status).toBe(404);
  });
});

describe("POST /api/runs/:id/progress", () => {
  async function seedRun(env) {
    const created = await (await handle(req("POST", "/api/runs", { body: SPEC, headers: IP("4.4.4.4") }), env)).json();
    return created.runId;
  }

  it("rejects a wrong/absent callback secret with 401", async () => {
    const env = makeEnv();
    const runId = await seedRun(env);
    const res = await handle(req("POST", `/api/runs/${runId}/progress`, {
      body: { status: "running" }, headers: { Authorization: "Bearer wrong" },
    }), env);
    expect(res.status).toBe(401);
  });

  it("accepts a valid update and reflects it on GET", async () => {
    const env = makeEnv();
    const runId = await seedRun(env);
    const res = await handle(req("POST", `/api/runs/${runId}/progress`, {
      body: { status: "running", stage: "generated", progress: { generated: 1 } },
      headers: { Authorization: "Bearer callback-secret" },
    }), env);
    expect(res.status).toBe(204);
    const polled = await (await handle(req("GET", `/api/runs/${runId}`), env)).json();
    expect(polled.stage).toBe("generated");
  });

  it("coalesces a rapid second non-terminal update (throttle) but always writes terminal", async () => {
    const env = makeEnv();
    const runId = await seedRun(env);
    const auth = { Authorization: "Bearer callback-secret" };

    await handle(req("POST", `/api/runs/${runId}/progress`, { body: { status: "running", stage: "one" }, headers: auth }), env);
    // immediate second non-terminal update -> coalesced, stage stays "one"
    await handle(req("POST", `/api/runs/${runId}/progress`, { body: { status: "running", stage: "two" }, headers: auth }), env);
    let polled = await (await handle(req("GET", `/api/runs/${runId}`), env)).json();
    expect(polled.stage).toBe("one");

    // a terminal update is written even within the throttle window
    await handle(req("POST", `/api/runs/${runId}/progress`, { body: { status: "succeeded", stage: "done" }, headers: auth }), env);
    polled = await (await handle(req("GET", `/api/runs/${runId}`), env)).json();
    expect(polled.status).toBe("succeeded");
    expect(polled.stage).toBe("done");
  });
});

describe("error_detail (Step 3): stored in KV, never returned by GET", () => {
  it("stores error_detail from a progress callback but strips it from the polled response", async () => {
    const env = makeEnv();
    const runId = (await (await handle(req("POST", "/api/runs", { body: SPEC, headers: IP("5.5.5.5") }), env)).json()).runId;
    const auth = { Authorization: "Bearer callback-secret" };

    await handle(req("POST", `/api/runs/${runId}/progress`, {
      body: { status: "failed", stage: "error", error: "spec_invalid",
             error_detail: "OpenAPIValidationError: 'paths' is a required property" },
      headers: auth,
    }), env);

    // Stored in KV (server-side reality) ...
    const stored = await env.RELAY_KV.get(`run:${runId}`, "json");
    expect(stored.error).toBe("spec_invalid");
    expect(stored.error_detail).toContain("OpenAPIValidationError");

    // ... but never returned by the public poll endpoint.
    const polled = await (await handle(req("GET", `/api/runs/${runId}`), env)).json();
    expect(polled.error).toBe("spec_invalid");
    expect(polled.error_detail).toBeUndefined();
    expect(JSON.stringify(polled)).not.toContain("OpenAPIValidationError");
  });
});

describe("generated code (code:{runId})", () => {
  const auth = { Authorization: "Bearer callback-secret" };
  const filePayload = (content) => ({
    files: [{ endpoint: { method: "GET", path: "/x" }, name: "client.py", content }],
  });

  it("stores code (auth) and serves it back on GET; unknown id is 404", async () => {
    const env = makeEnv();
    await handle(req("POST", "/api/runs/r1/code", { body: filePayload("print('hi')"), headers: auth }), env);
    const res = await handle(req("GET", "/api/runs/r1/code"), env);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.files[0].content).toBe("print('hi')");
    expect(body.truncated).toBe(false);

    expect((await handle(req("GET", "/api/runs/nope/code"), env)).status).toBe(404);
  });

  it("rejects a store without the callback secret", async () => {
    const env = makeEnv();
    const res = await handle(req("POST", "/api/runs/r2/code", { body: filePayload("x"), headers: { Authorization: "Bearer wrong" } }), env);
    expect(res.status).toBe(401);
    expect((await handle(req("GET", "/api/runs/r2/code"), env)).status).toBe(404); // nothing stored
  });

  it("size-guards a huge file: truncates content AND flags truncated (never silent)", async () => {
    const env = makeEnv();
    const huge = "a".repeat(300 * 1024); // > 256 KB per-file cap
    await handle(req("POST", "/api/runs/r3/code", { body: filePayload(huge), headers: auth }), env);
    const body = await (await handle(req("GET", "/api/runs/r3/code"), env)).json();
    expect(body.truncated).toBe(true);
    expect(body.files[0].truncated).toBe(true);
    expect(body.files[0].content.length).toBeLessThan(huge.length);
  });
});
