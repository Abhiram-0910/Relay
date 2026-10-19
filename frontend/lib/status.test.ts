import { describe, expect, it } from "vitest";

import { labelOf, styleOf, toneOf } from "./status";

// The color mapping is a correctness rule (color must not imply unearned confidence), so pin it.
describe("status → tone SSOT", () => {
  it("emerald/success is verified_pass and the succeeded terminal ONLY", () => {
    expect(toneOf("verified_pass")).toBe("success");
    expect(toneOf("succeeded")).toBe("success");
    // nothing softer may read as success
    expect(toneOf("generated_only")).not.toBe("success");
    expect(toneOf("verified_live_validation_failed")).not.toBe("success");
  });

  it("amber/warn is generated_only, validation-mismatch, and in-progress states", () => {
    for (const s of ["generated_only", "verified_live_validation_failed", "queued", "running"]) {
      expect(toneOf(s)).toBe("warn");
    }
  });

  it("red/danger is every failure/blocked/limited state", () => {
    for (const s of ["call_failed", "failed", "ssrf_blocked", "rate_limited", "error"]) {
      expect(toneOf(s)).toBe("danger");
    }
  });

  it("red/danger covers sandbox_timeout and every corrector-level code (Step 3)", () => {
    for (const s of ["sandbox_timeout", "corrector_auth_failed", "corrector_config_error",
                     "quota_exhausted_shared", "quota_exhausted_byok", "corrector_network_error",
                     "corrector_bad_response", "corrector_error", "model_unsupported"]) {
      expect(toneOf(s)).toBe("danger");
      expect(labelOf(s)).not.toBe(s); // a real short label, not a fallback to the raw code
    }
  });

  it("model_unsupported is danger with its own label", () => {
    expect(toneOf("model_unsupported")).toBe("danger");
    expect(labelOf("model_unsupported")).toBe("Model unsupported");
  });

  it("red/danger covers every pipeline-level code (Step 3/5) with a real short label", () => {
    for (const s of ["spec_fetch_failed", "spec_invalid", "unsupported_spec_version",
                     "no_operations_generated", "ssrf_blocked_spec",
                     "generation_failed", "sandbox_unavailable", "internal_error",
                     "byok_key_missing", "byok_fetch_failed"]) {
      expect(toneOf(s)).toBe("danger");
      expect(labelOf(s)).not.toBe(s);
    }
  });

  it("unknown statuses fall back to neutral, never a false success", () => {
    expect(toneOf("something_new")).toBe("neutral");
  });

  it("labels never inflate — generated_only is not called passed/verified", () => {
    expect(labelOf("generated_only").toLowerCase()).not.toContain("verified");
    expect(labelOf("generated_only").toLowerCase()).not.toContain("passed");
  });

  it("styleOf exposes a surface tint for every tone (Step 5's <Banner>), not just badge/dot/text/border", () => {
    for (const s of ["verified_pass", "queued", "failed", "something_new"]) {
      expect(styleOf(s).surface).toMatch(/^bg-/);
    }
  });
});
