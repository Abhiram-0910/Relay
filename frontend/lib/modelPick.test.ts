import { describe, expect, it } from "vitest";

import {
  canGoHigher,
  canGoLower,
  confirm,
  confirmedModel,
  currentRung,
  higher,
  IDLE_PICK,
  lower,
  proposeFromLadder,
  TIER_LABELS,
} from "./modelPick";
import type { LadderRung } from "./types";

const LADDER: LadderRung[] = [
  { id: "gpt-6-luna", tier: "small" },
  { id: "gpt-6.1-sol", tier: "balanced" },
  { id: "gpt-6-astra", tier: "flagship" },
];

describe("modelPick — one proposal, confirm / higher / lower", () => {
  it("proposes the Worker's default, unconfirmed: a run can't use it yet", () => {
    const p = proposeFromLadder(LADDER, 1);
    expect(currentRung(p)).toEqual(LADDER[1]);
    expect(TIER_LABELS[currentRung(p)!.tier]).toBe("Balanced (recommended)");
    expect(confirmedModel(p)).toBe("");
    expect(confirmedModel(confirm(p))).toBe("gpt-6.1-sol");
  });

  it("higher and lower move one step and drop the confirmation", () => {
    const confirmed = confirm(proposeFromLadder(LADDER, 1));
    const up = higher(confirmed);
    expect(currentRung(up)?.id).toBe("gpt-6-astra");
    expect(confirmedModel(up)).toBe("");
    const down = lower(proposeFromLadder(LADDER, 1));
    expect(currentRung(down)?.id).toBe("gpt-6-luna");
    expect(confirmedModel(down)).toBe("");
  });

  it("stops at both edges", () => {
    const top = proposeFromLadder(LADDER, 2);
    expect(canGoHigher(top)).toBe(false);
    expect(higher(top)).toBe(top);
    expect(canGoLower(top)).toBe(true);
    const bottom = proposeFromLadder(LADDER, 0);
    expect(canGoLower(bottom)).toBe(false);
    expect(lower(bottom)).toBe(bottom);
    const single = proposeFromLadder([LADDER[1]], 0);
    expect(canGoHigher(single) || canGoLower(single)).toBe(false);
  });

  it("an empty ladder is an error, and an out-of-range default is clamped", () => {
    expect(proposeFromLadder([], -1).status).toBe("error");
    expect(currentRung(proposeFromLadder(LADDER, 9))?.id).toBe("gpt-6-astra");
  });

  it("reset (IDLE) has no proposal and no model", () => {
    expect(currentRung(IDLE_PICK)).toBeNull();
    expect(confirmedModel(IDLE_PICK)).toBe("");
    expect(canGoHigher(IDLE_PICK) || canGoLower(IDLE_PICK)).toBe(false);
    expect(confirm(IDLE_PICK)).toBe(IDLE_PICK);
  });
});
