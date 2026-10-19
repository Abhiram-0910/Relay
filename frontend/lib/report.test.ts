import { describe, expect, it } from "vitest";

import { skippedSummary } from "./report";

describe("skippedSummary", () => {
  it("does not claim a single cause for every skip", () => {
    expect(skippedSummary(3)).toBe("3 operations skipped — reasons below");
    expect(skippedSummary(3)).not.toMatch(/JSON/i);
  });

  it("uses the singular for one operation", () => {
    expect(skippedSummary(1)).toBe("1 operation skipped — reasons below");
  });
});
