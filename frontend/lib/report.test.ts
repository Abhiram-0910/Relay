import { describe, expect, it } from "vitest";

import { generatedOnlyReason, httpStatusNote, problemSummary, skippedSummary } from "./report";

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

describe("generatedOnlyReason", () => {
  it("maps every backend reason code to a sentence", () => {
    for (const code of ["not_get", "requires_auth", "possibly_state_changing", "live_budget_exceeded",
                        "server_url_invalid", "server_variable_invalid", "server_url_missing_host"]) {
      const text = generatedOnlyReason(code);
      expect(text).not.toBe("Generated, but not called live.");
      expect(text).not.toContain(code);
    }
  });

  it("names the parameter for no_sample_value", () => {
    expect(generatedOnlyReason("no_sample_value:latitude")).toBe("No sample value for the required parameter latitude.");
  });

  it("never echoes a name that isn't a plain identifier", () => {
    for (const bad of ["no_sample_value:<script>", "no_sample_value:", "no_sample_value:a b", `no_sample_value:${"a".repeat(65)}`]) {
      expect(generatedOnlyReason(bad)).toBe("No sample value for a required parameter.");
    }
  });

  it("maps the pre-5a sentences still stored in old runs", () => {
    expect(generatedOnlyReason("non-GET; not called live")).toBe(generatedOnlyReason("not_get"));
    expect(generatedOnlyReason("required-param values unavailable")).toMatch(/No sample values/);
  });

  it("falls back for anything unknown, never showing raw text", () => {
    for (const unknown of ["something_new", "no usable base URL (server_url_invalid)", "constructor", "toString", "x:y"]) {
      expect(generatedOnlyReason(unknown)).toBe("Generated, but not called live.");
    }
  });
});
