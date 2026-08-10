// BYOK (bring-your-own-key) client logic — kept as pure functions so it's unit-testable without
// rendering React (matching the existing hermetic lib/ test style). The ByokFields component is a
// thin shell over these. See ARCHITECTURE.md "Step 2".
import type { RunSnapshot } from "./types";

export type Provider = "gemini" | "openai" | "grok" | "openrouter" | "anthropic";

export const PROVIDER_OPTIONS: { value: Provider; label: string }[] = [
  { value: "gemini", label: "Google Gemini" },
  { value: "openai", label: "OpenAI" },
  { value: "grok", label: "xAI Grok" },
  { value: "openrouter", label: "OpenRouter" },
  { value: "anthropic", label: "Anthropic" },
];

export const MIN_API_KEY_LEN = 8; // matches the Worker's storeByokKey floor
export const MODEL_FETCH_DEBOUNCE_MS = 500;

export interface ByokState {
  provider: Provider | "";
  apiKey: string;
  model: string;
}

export const EMPTY_BYOK: ByokState = { provider: "", apiKey: "", model: "" };

/** Thrown by fetchModels when the Worker passes through a provider 401/403. Lives here (not api.ts)
 * so both the API layer and the error-message mapping can reference it without a circular import. */
export class KeyRejectedError extends Error {
  constructor() {
    super("key_rejected");
    this.name = "KeyRejectedError";
  }
}

/** The guard that keeps the debounced model-fetch from firing on every keystroke of a half-typed
 * key: only once a provider is chosen AND the key is plausibly complete. */
export function canFetchModels(provider: string, apiKey: string): boolean {
  return provider !== "" && apiKey.trim().length >= MIN_API_KEY_LEN;
}

export interface RunPayload {
  specUrl: string;
  provider?: string;
  apiKey?: string;
  model?: string;
}

/** Attach BYOK fields ONLY when fully configured (provider + key + a chosen model). Ignoring the
 * optional section — or half-filling it — submits a plain shared-free-tier run; the key is never
 * sent. Requiring the model too avoids handing the backend a provider with no model to run. */
export function buildRunPayload(specUrl: string, byok: ByokState): RunPayload {
  const apiKey = byok.apiKey.trim();
  if (byok.provider && apiKey && byok.model) {
    return { specUrl, provider: byok.provider, apiKey, model: byok.model };
  }
  return { specUrl };
}

/** Human message for a failed model-list load — a rejected key is called out plainly rather than
 * left as a silent empty dropdown. fetchModels already turns the Worker's error code into an
 * honest message (lib/errors.ts) before throwing, so a generic Error's .message is used as-is. */
export function modelErrorMessage(err: unknown): string {
  if (err instanceof KeyRejectedError) {
    return "Key rejected — check that the key is valid and matches the provider.";
  }
  if (err instanceof Error && err.message) return err.message;
  return "Couldn’t load models. Check your connection and try again.";
}

/** Human duration between two ISO timestamps — "under a second", "34 seconds", "2 minutes". The
 * receipt's actual trust signal is HOW BRIEFLY the key existed, not how long ago that was (which
 * goes stale the moment it's displayed) — so this formats a gap, never a "time ago". */
function formatGap(fromIso: string, toIso: string): string | null {
  const from = Date.parse(fromIso);
  const to = Date.parse(toIso);
  if (!Number.isFinite(from) || !Number.isFinite(to)) return null;
  const ms = Math.max(0, to - from);
  if (ms < 1000) return "under a second";
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds} second${seconds === 1 ? "" : "s"}`;
  const minutes = Math.round(seconds / 60);
  return `${minutes} minute${minutes === 1 ? "" : "s"}`;
}

/** The honest, Worker-verified BYOK receipt — only once BOTH timestamps are present on the run
 * record (real data off the Worker's KV state, not copy). `text` leads with the human-readable
 * duration; `receivedAt`/`deletedAt` are the exact ISO timestamps, for the UI to show as a tooltip
 * rather than dropping the precision the honesty guarantee depends on. */
export function byokReceiptSummary(
  snapshot: Pick<RunSnapshot, "byokReceivedAt" | "byokDeletedAt">,
): { text: string; receivedAt: string; deletedAt: string } | null {
  const { byokReceivedAt: receivedAt, byokDeletedAt: deletedAt } = snapshot;
  if (!receivedAt || !deletedAt) return null;
  const gap = formatGap(receivedAt, deletedAt);
  const text = gap
    ? `Your key was received, used once, and deleted ${gap} later — never stored.`
    : `Your key was received, used once, and deleted — never stored.`;
  return { text, receivedAt, deletedAt };
}
