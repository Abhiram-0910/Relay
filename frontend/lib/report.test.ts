import { describe, expect, it } from "vitest";

import { httpStatusNote, skippedSummary } from "./report";

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
