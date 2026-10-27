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

  it("maps both BYOK handoff codes to their own messages, not the generic fallback", () => {
    expect(errorMessage("byok_key_missing")).toMatch(/expired/i);
    expect(errorMessage("byok_fetch_failed")).toMatch(/couldn't be passed/i);
  });

  it("maps model_unsupported to its own pick-another-model message", () => {
    expect(errorMessage("model_unsupported")).toBe(
      "That model can't be used for auto-fix: it doesn't support this request format, or your key can't access it. Pick a different model.",
    );
  });

  it("maps the spec-version and nothing-generated codes to their own messages", () => {
    expect(errorMessage("unsupported_spec_version")).toBe(
      "This spec's version isn't supported. Relay verifies OpenAPI 3.0 and 3.1. For Swagger 2.0, convert it with swagger2openapi or the Swagger Editor and re-submit.",
    );
    expect(errorMessage("no_operations_generated")).toBe(
      "Relay couldn't generate a client for any operation in this spec. Typical causes: no JSON responses, or parameter styles Relay doesn't support yet.",
    );
    expect(errorMessage("spec_invalid")).not.toMatch(/swagger/i);
    expect(errorMessage("unsupported_spec_construct")).toBe(
      "This spec uses a construct Relay can't resolve yet (for example recursive schemas). The spec itself may be valid.",
    );
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
    expect(suggestsByok("byok_key_missing")).toBe(false);
    expect(suggestsByok("byok_fetch_failed")).toBe(false);
    expect(suggestsByok("unsupported_spec_version")).toBe(false);
    expect(suggestsByok("unsupported_spec_construct")).toBe(false);
    expect(suggestsByok("no_operations_generated")).toBe(false);
    expect(suggestsByok("model_unsupported")).toBe(false);
    expect(suggestsByok(null)).toBe(false);
  });
});
