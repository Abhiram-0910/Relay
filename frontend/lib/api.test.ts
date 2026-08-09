import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createRun, fetchModels, KeyRejectedError } from "./api";
import { EMPTY_BYOK, type ByokState } from "./byok";

function fakeResponse(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.stubEnv("NEXT_PUBLIC_WORKER_URL", "https://worker.test");
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("createRun — BYOK wiring", () => {
  it("posts only { specUrl } when BYOK is unused (no apiKey on the wire)", async () => {
    fetchMock.mockResolvedValue(fakeResponse(202, { runId: "r1", status: "queued", statusUrl: "/api/runs/r1" }));
    await createRun("https://spec", EMPTY_BYOK);

    const sent = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(sent).toEqual({ specUrl: "https://spec" });
    expect("apiKey" in sent).toBe(false);
  });

  it("posts provider/apiKey/model when BYOK is fully configured", async () => {
    fetchMock.mockResolvedValue(fakeResponse(202, { runId: "r1", status: "queued", statusUrl: "/api/runs/r1" }));
    const byok: ByokState = { provider: "openai", apiKey: "sk-live-abcdef123456", model: "gpt-5" };
    await createRun("https://spec", byok);

    const sent = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(sent).toEqual({ specUrl: "https://spec", provider: "openai", apiKey: "sk-live-abcdef123456", model: "gpt-5" });
  });
});

describe("fetchModels", () => {
  it("returns the normalized model list on 200", async () => {
    const models = [{ id: "gpt-5", label: "gpt-5" }];
    fetchMock.mockResolvedValue(fakeResponse(200, { models }));
    await expect(fetchModels("openai", "sk-live-abcdef123456")).resolves.toEqual(models);

    const sent = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(sent).toEqual({ provider: "openai", apiKey: "sk-live-abcdef123456" });
  });

  it("throws KeyRejectedError on a 401 passthrough (up-front key validation)", async () => {
    fetchMock.mockResolvedValue(fakeResponse(401, { error: "key_rejected" }));
    await expect(fetchModels("openai", "sk-bad")).rejects.toBeInstanceOf(KeyRejectedError);
  });

  it("throws KeyRejectedError on 403 too", async () => {
    fetchMock.mockResolvedValue(fakeResponse(403, {}));
    await expect(fetchModels("anthropic", "sk-bad")).rejects.toBeInstanceOf(KeyRejectedError);
  });

  it("throws the honest per-code message, never a raw status number", async () => {
    fetchMock.mockResolvedValue(fakeResponse(502, { error: "provider_error" }));
    await expect(fetchModels("grok", "sk-live-abcdef123456")).rejects.toThrow(/unexpected error/i);
  });

  it("falls back to a generic message when the body has no recognizable code", async () => {
    fetchMock.mockResolvedValue(fakeResponse(502, {}));
    let caught: Error | undefined;
    await fetchModels("grok", "sk-live-abcdef123456").catch((err: Error) => {
      caught = err;
    });
    expect(caught?.message).not.toMatch(/502/);
    expect(caught?.message.length).toBeGreaterThan(0);
  });
});

describe("createRun — error messages", () => {
  it("throws the honest per-code message on a non-ok, non-429 response", async () => {
    fetchMock.mockResolvedValue(fakeResponse(400, { error: "invalid_spec_url" }));
    await expect(createRun("not-a-url", EMPTY_BYOK)).rejects.toThrow(/reachable spec URL/i);
  });
});
