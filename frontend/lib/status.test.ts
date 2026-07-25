import { describe, expect, it } from "vitest";

import { labelOf, toneOf } from "./status";

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

  it("unknown statuses fall back to neutral, never a false success", () => {
    expect(toneOf("something_new")).toBe("neutral");
  });

  it("labels never inflate — generated_only is not called passed/verified", () => {
    expect(labelOf("generated_only").toLowerCase()).not.toContain("verified");
    expect(labelOf("generated_only").toLowerCase()).not.toContain("passed");
  });
});
