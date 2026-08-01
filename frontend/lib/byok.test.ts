import { describe, expect, it } from "vitest";

import {
  buildRunPayload,
  byokReceiptText,
  canFetchModels,
  KeyRejectedError,
  modelErrorMessage,
  type ByokState,
} from "./byok";

const full: ByokState = { provider: "openai", apiKey: "sk-live-abcdef123456", model: "gpt-5" };

describe("buildRunPayload — the key must never ride along on a non-BYOK run", () => {
  it("sends only specUrl when BYOK is unused", () => {
    const payload = buildRunPayload("https://spec", { provider: "", apiKey: "", model: "" });
    expect(payload).toEqual({ specUrl: "https://spec" });
    expect("apiKey" in payload).toBe(false);
  });

  it("does NOT attach a partially-filled section (key typed, no model chosen)", () => {
    const payload = buildRunPayload("https://spec", { provider: "openai", apiKey: "sk-live-abcdef123456", model: "" });
    expect(payload).toEqual({ specUrl: "https://spec" });
    expect("apiKey" in payload).toBe(false);
  });

  it("attaches provider/apiKey/model only when fully configured", () => {
    expect(buildRunPayload("https://spec", full)).toEqual({
      specUrl: "https://spec",
      provider: "openai",
      apiKey: "sk-live-abcdef123456",
      model: "gpt-5",
    });
  });

  it("trims whitespace off the key", () => {
    const payload = buildRunPayload("https://spec", { ...full, apiKey: "  sk-live-abcdef123456  " });
    expect(payload.apiKey).toBe("sk-live-abcdef123456");
  });
});

describe("canFetchModels — the debounce guard", () => {
  it("is false with no provider, or a too-short/whitespace key (won't fire mid-typing)", () => {
    expect(canFetchModels("", "sk-live-abcdef123456")).toBe(false);
    expect(canFetchModels("openai", "")).toBe(false);
    expect(canFetchModels("openai", "short")).toBe(false);
    expect(canFetchModels("openai", "        ")).toBe(false);
  });

  it("is true only once a provider is chosen and the key is plausibly complete", () => {
    expect(canFetchModels("openai", "sk-live-abcdef123456")).toBe(true);
  });
});

describe("modelErrorMessage — a rejected key is called out, not left silent", () => {
  it("maps KeyRejectedError to a clear 'key rejected' message", () => {
    expect(modelErrorMessage(new KeyRejectedError()).toLowerCase()).toContain("key rejected");
  });

  it("falls back to a generic message for other failures", () => {
    const msg = modelErrorMessage(new Error("network boom"));
    expect(msg.toLowerCase()).not.toContain("key rejected");
    expect(msg.length).toBeGreaterThan(0);
  });
});

describe("byokReceiptText — only shown once both timestamps are present", () => {
  it("returns null until both byokReceivedAt and byokDeletedAt exist", () => {
    expect(byokReceiptText({})).toBeNull();
    expect(byokReceiptText({ byokReceivedAt: "t1" })).toBeNull();
    expect(byokReceiptText({ byokDeletedAt: "t2" })).toBeNull();
  });

  it("renders the honest received/used-once/deleted line when both are present", () => {
    const text = byokReceiptText({ byokReceivedAt: "2026-08-01T10:00:00Z", byokDeletedAt: "2026-08-01T10:00:30Z" });
    expect(text).toContain("2026-08-01T10:00:00Z");
    expect(text).toContain("2026-08-01T10:00:30Z");
    expect(text).toContain("used once");
  });
});
