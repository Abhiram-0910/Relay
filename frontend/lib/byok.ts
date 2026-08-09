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

/** The honest, Worker-verified BYOK receipt — rendered only once BOTH timestamps are present on the
 * run record (real data off the Worker's KV state, not copy). */
export function byokReceiptText(
  snapshot: Pick<RunSnapshot, "byokReceivedAt" | "byokDeletedAt">,
): string | null {
  if (!snapshot.byokReceivedAt || !snapshot.byokDeletedAt) return null;
  return `Your key was received at ${snapshot.byokReceivedAt}, used once, and deleted at ${snapshot.byokDeletedAt}.`;
}
