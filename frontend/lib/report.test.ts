import { describe, expect, it } from "vitest";

import { httpStatusNote, problemSummary, skippedSummary } from "./report";

describe("skippedSummary", () => {
  it("does not claim a single cause for every skip", () => {
    expect(skippedSummary(3)).toBe("3 operations skipped — reasons below");
    expect(skippedSummary(3)).not.toMatch(/JSON/i);
  });

  it("uses the singular for one operation", () => {
    expect(skippedSummary(1)).toBe("1 operation skipped — reasons below");
  });
});

describe("httpStatusNote", () => {
  it("shows the code only beside target_http_error", () => {
    expect(httpStatusNote({ status: "target_http_error", http_status: 503 })).toBe("HTTP 503");
    expect(httpStatusNote({ status: "call_failed", http_status: 400 })).toBeNull();
    expect(httpStatusNote({ status: "target_http_error", http_status: null })).toBeNull();
    expect(httpStatusNote({ status: "target_http_error" })).toBeNull();
  });
});

describe("problemSummary", () => {
  it("is empty when every operation passed or was only generated", () => {
    expect(problemSummary([])).toBe("");
    expect(problemSummary(["verified_pass", "generated_only", "verified_pass"])).toBe("");
  });

  it("counts danger statuses as failed", () => {
    expect(problemSummary(["call_failed", "sandbox_timeout", "verified_pass"])).toBe("2 failed");
  });

  it("counts every warn finding as needing attention, not as nothing", () => {
    for (const s of ["target_unreachable", "target_http_error", "non_json_response",
                     "verified_live_validation_failed"]) {
      expect(problemSummary([s, "verified_pass"])).toBe("1 needs attention");
    }
    expect(problemSummary(["target_unreachable", "target_http_error", "non_json_response"]))
      .toBe("3 need attention");
  });

  it("shows failed and attention counts separately", () => {
    expect(problemSummary(["call_failed", "call_failed", "target_http_error", "generated_only"]))
      .toBe("2 failed · 1 needs attention");
  });

  it("never undercounts: an unknown status needs attention too", () => {
    expect(problemSummary(["some_future_status"])).toBe("1 needs attention");
  });

  it("totals every status that would fail the run (anything but verified_pass/generated_only)", () => {
    const statuses = ["verified_pass", "generated_only", "call_failed", "ssrf_blocked",
                      "target_unreachable", "non_json_response", "verified_live_validation_failed"];
    const runFailing = statuses.filter((s) => s !== "verified_pass" && s !== "generated_only").length;
    const counted = (problemSummary(statuses).match(/\d+/g) ?? []).reduce((n, d) => n + Number(d), 0);
    expect(counted).toBe(runFailing);
    expect(problemSummary(statuses)).toBe("2 failed · 3 need attention");
  });
});
