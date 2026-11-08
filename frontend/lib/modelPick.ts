// BYOK model pick — pure state machine over the Worker's ranked ladder (POST /api/models, Phase 3
// piece 8). The user never sees a model list: one proposed model, then confirm / higher / lower.
// Every move returns a NEW unconfirmed proposal; only confirm() yields a model a run may use.
import type { LadderRung, ModelTier } from "./types";

export type ModelPick =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "proposed" | "confirmed"; ladder: LadderRung[]; index: number };

export const IDLE_PICK: ModelPick = { status: "idle" };

export const TIER_LABELS: Record<ModelTier, string> = {
  small: "Fastest and cheapest",
  balanced: "Balanced (recommended)",
  flagship: "Most capable",
};

export function proposeFromLadder(ladder: LadderRung[], defaultIndex: number): ModelPick {
  if (ladder.length === 0) return { status: "error", message: "No suitable model was found for this key." };
  const index = Math.min(Math.max(defaultIndex, 0), ladder.length - 1);
  return { status: "proposed", ladder, index };
}

const hasLadder = (p: ModelPick): p is Extract<ModelPick, { ladder: LadderRung[] }> =>
  p.status === "proposed" || p.status === "confirmed";

export const canGoHigher = (p: ModelPick) => hasLadder(p) && p.index < p.ladder.length - 1;
export const canGoLower = (p: ModelPick) => hasLadder(p) && p.index > 0;

const move = (p: ModelPick, step: number): ModelPick =>
  hasLadder(p) && p.ladder[p.index + step] ? { status: "proposed", ladder: p.ladder, index: p.index + step } : p;

export const higher = (p: ModelPick) => move(p, 1);
export const lower = (p: ModelPick) => move(p, -1);
export const confirm = (p: ModelPick): ModelPick => (p.status === "proposed" ? { ...p, status: "confirmed" } : p);

/** The proposal on screen, confirmed or not. */
export const currentRung = (p: ModelPick): LadderRung | null => (hasLadder(p) ? p.ladder[p.index] : null);

/** The model id a run may be submitted with — "" until the user has confirmed one. */
export const confirmedModel = (p: ModelPick): string =>
  p.status === "confirmed" ? p.ladder[p.index].id : "";
