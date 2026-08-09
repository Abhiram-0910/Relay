import { describe, expect, it } from "vitest";

import { errorMessage, suggestsByok } from "./errors";

describe("errorMessage — code → plain language, never raw", () => {
  it("maps a known code to its message", () => {
    expect(errorMessage("spec_invalid")).toMatch(/OpenAPI/i);
  });

  it("never echoes the code itself back as the message", () => {
    for (const code of ["spec_fetch_failed", "ssrf_blocked_spec", "corrector_auth_failed",
                        "quota_exhausted_byok", "sandbox_unavailable"]) {
      expect(errorMessage(code)).not.toBe(code);
      expect(errorMessage(code).length).toBeGreaterThan(0);
    }
  });

  it("falls back to a generic message for an unrecognized code, never blank/raw", () => {
    expect(errorMessage("some_future_code_nobody_mapped_yet").length).toBeGreaterThan(0);
    expect(errorMessage("some_future_code_nobody_mapped_yet")).not.toContain("some_future_code");
  });

  it("falls back for null/undefined without throwing", () => {
    expect(errorMessage(null).length).toBeGreaterThan(0);
    expect(errorMessage(undefined).length).toBeGreaterThan(0);
  });
});

describe("suggestsByok — only where switching key source genuinely helps", () => {
  it("true for shared-resource exhaustion", () => {
    expect(suggestsByok("rate_limited")).toBe(true);
    expect(suggestsByok("quota_exhausted_shared")).toBe(true);
    expect(suggestsByok("corrector_config_error")).toBe(true);
  });

  it("false when already BYOK, or unrelated to key source", () => {
    expect(suggestsByok("quota_exhausted_byok")).toBe(false);
    expect(suggestsByok("corrector_auth_failed")).toBe(false);
    expect(suggestsByok("spec_invalid")).toBe(false);
    expect(suggestsByok(null)).toBe(false);
  });
});
