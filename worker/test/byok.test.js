// worker/test/byok.test.js
import { describe, it, expect, beforeEach } from "vitest";
import {
  storeByokKey,
  buildDispatchInputs,
  handleByokKeyFetch,
} from "../src/byok.js";

// Minimal fake KV matching the subset of the Workers KV API this module
// uses. Mirrors the shape existing worker tests already fake per
// ARCHITECTURE.md ("Worker unit tests (fake KV+fetch)").
class FakeKV {
  constructor() {
    this.store = new Map();
  }
  async get(key) {
    return this.store.has(key) ? this.store.get(key).value : null;
  }
  async put(key, value, opts) {
    this.store.set(key, { value, ttl: opts?.expirationTtl ?? null });
  }
  async delete(key) {
    this.store.delete(key);
  }
}

function makeEnv() {
  return { RELAY_KV: new FakeKV(), CALLBACK_SECRET: "test-secret" };
}

describe("storeByokKey", () => {
  it("stores the key with a short TTL and returns true", async () => {
    const env = makeEnv();
    const stored = await storeByokKey(env, "run-1", "sk-live-abcdef123456", "openai");
    expect(stored).toBe(true);
    const raw = await env.RELAY_KV.get("byok:run-1");
    expect(raw).not.toBeNull();
    const record = JSON.parse(raw);
    expect(record.apiKey).toBe("sk-live-abcdef123456");
    expect(record.provider).toBe("openai");
    expect(env.RELAY_KV.store.get("byok:run-1").ttl).toBe(240);
  });

  it("no-ops and returns false when no apiKey is supplied", async () => {
    const env = makeEnv();
    const stored = await storeByokKey(env, "run-1", undefined, "openai");
    expect(stored).toBe(false);
    expect(await env.RELAY_KV.get("byok:run-1")).toBeNull();
  });

  it("rejects obviously malformed keys instead of storing them", async () => {
    const env = makeEnv();
    await expect(storeByokKey(env, "run-1", "short", "openai")).rejects.toThrow();
    expect(await env.RELAY_KV.get("byok:run-1")).toBeNull();
  });
});

describe("buildDispatchInputs — the structural leak-proof guarantee", () => {
  it("never contains the apiKey value, because apiKey is not a parameter", () => {
    // This is the concrete version of "prove the key never enters GitHub's
    // system": buildDispatchInputs has no apiKey parameter at all, so no
    // apiKey value — real or fabricated for this test — can appear in its
    // output, regardless of what the caller does with the key elsewhere.
    const secretValue = "sk-live-should-never-appear-anywhere-1234567890";
    const inputs = buildDispatchInputs("run-1", "https://example.com/spec.json", "https://cb.example.com", true);
    const serialized = JSON.stringify(inputs);
    expect(serialized).not.toContain(secretValue);
    expect(Object.keys(inputs).sort()).toEqual(
      ["callback_url", "has_byok", "run_id", "spec_url"].sort()
    );
    expect(inputs.has_byok).toBe("true");
  });

  it("has_byok is a plain boolean-string flag, never the key itself", () => {
    const inputsWith = buildDispatchInputs("run-1", "url", "cb", true);
    const inputsWithout = buildDispatchInputs("run-1", "url", "cb", false);
    expect(inputsWith.has_byok).toBe("true");
    expect(inputsWithout.has_byok).toBe("false");
  });
});

describe("handleByokKeyFetch", () => {
  it("rejects requests without the correct bearer token, KV entry untouched", async () => {
    const env = makeEnv();
    await storeByokKey(env, "run-1", "sk-live-abcdef123456", "openai");
    const req = new Request("https://worker.example/api/runs/run-1/byok-key", {
      headers: { Authorization: "Bearer wrong-secret" },
    });
    const res = await handleByokKeyFetch(req, env, "run-1");
    expect(res.status).toBe(401);
    expect(await env.RELAY_KV.get("byok:run-1")).not.toBeNull(); // untouched
  });

  it("returns 404 when no key was ever stored for this run", async () => {
    const env = makeEnv();
    const req = new Request("https://worker.example/api/runs/run-1/byok-key", {
      headers: { Authorization: "Bearer test-secret" },
    });
    const res = await handleByokKeyFetch(req, env, "run-1");
    expect(res.status).toBe(404);
    const body = await res.json();
    expect(body.found).toBe(false);
  });

  it("delivers the key exactly once, then deletes it (second fetch is 404)", async () => {
    const env = makeEnv();
    await storeByokKey(env, "run-1", "sk-live-abcdef123456", "openai");
    await env.RELAY_KV.put("run:run-1", JSON.stringify({ status: "running" }));

    const req = () =>
      new Request("https://worker.example/api/runs/run-1/byok-key", {
        headers: { Authorization: "Bearer test-secret" },
      });

    const first = await handleByokKeyFetch(req(), env, "run-1");
    expect(first.status).toBe(200);
    const firstBody = await first.json();
    expect(firstBody.apiKey).toBe("sk-live-abcdef123456");
    expect(firstBody.provider).toBe("openai");
    expect(firstBody.deliveredAt).toBeTruthy();

    // Delivery-once: the ticket is gone even though its TTL hasn't expired.
    expect(await env.RELAY_KV.get("byok:run-1")).toBeNull();

    const second = await handleByokKeyFetch(req(), env, "run-1");
    expect(second.status).toBe(404);
  });

  it("stamps the run record with honest receivedAt/deletedAt, never the key itself", async () => {
    const env = makeEnv();
    await storeByokKey(env, "run-1", "sk-live-abcdef123456", "openai");
    await env.RELAY_KV.put("run:run-1", JSON.stringify({ status: "running" }));

    const req = new Request("https://worker.example/api/runs/run-1/byok-key", {
      headers: { Authorization: "Bearer test-secret" },
    });
    await handleByokKeyFetch(req, env, "run-1");

    const run = JSON.parse(await env.RELAY_KV.get("run:run-1"));
    expect(run.byokReceivedAt).toBeTruthy();
    expect(run.byokDeletedAt).toBeTruthy();
    expect(run.apiKey).toBeUndefined();
    expect(JSON.stringify(run)).not.toContain("sk-live-abcdef123456");
  });
});
