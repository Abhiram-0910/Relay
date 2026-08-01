import { buildRunPayload, KeyRejectedError, type ByokState } from "./byok";
import type {
  CodeBundle,
  CreateRunResponse,
  ModelOption,
  ModelsResponse,
  RateLimitInfo,
  RunSnapshot,
} from "./types";

export { KeyRejectedError };

/** Thrown when the Worker returns 429 — carries the honest cap details for the UI. */
export class RateLimitedError extends Error {
  constructor(public info: RateLimitInfo) {
    super("rate_limited");
    this.name = "RateLimitedError";
  }
}

// Worker URL comes from env — never hardcoded in a component. Read lazily (not a module-level
// const) so it's picked up per call and tests can set it before invoking.
function requireBase(): string {
  const base = process.env.NEXT_PUBLIC_WORKER_URL;
  if (!base) {
    throw new Error("NEXT_PUBLIC_WORKER_URL is not set — configure it in .env.local");
  }
  return base;
}

export async function createRun(specUrl: string, byok?: ByokState): Promise<CreateRunResponse> {
  // buildRunPayload attaches apiKey/provider/model ONLY when BYOK is fully configured — a plain
  // run (no BYOK, or a half-filled section) sends just { specUrl }, never a key.
  const body = byok ? buildRunPayload(specUrl, byok) : { specUrl };
  const res = await fetch(`${requireBase()}/api/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (res.status === 429) {
    throw new RateLimitedError((await res.json()) as RateLimitInfo);
  }
  if (!res.ok) {
    const errBody = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(errBody.error ? `Worker rejected the request: ${errBody.error}` : `Request failed (${res.status})`);
  }
  return (await res.json()) as CreateRunResponse;
}

/** Live model list for a provider, fetched through the Worker with the user's key. A provider
 * 401/403 comes back as 401 from the Worker → KeyRejectedError, so the UI can say "key rejected"
 * up front rather than after a wasted run. */
export async function fetchModels(provider: string, apiKey: string): Promise<ModelOption[]> {
  const res = await fetch(`${requireBase()}/api/models`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider, apiKey }),
  });
  if (res.status === 401 || res.status === 403) throw new KeyRejectedError();
  if (!res.ok) throw new Error(`Fetching models failed (${res.status})`);
  return ((await res.json()) as ModelsResponse).models ?? [];
}

/** Poll one run. Returns null if the run id is unknown/expired (404). */
export async function getRun(runId: string): Promise<RunSnapshot | null> {
  const res = await fetch(`${requireBase()}/api/runs/${runId}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Poll failed (${res.status})`);
  return (await res.json()) as RunSnapshot;
}

/** Fetch the generated source on demand. Returns null if not stored (404). */
export async function getCode(runId: string): Promise<CodeBundle | null> {
  const res = await fetch(`${requireBase()}/api/runs/${runId}/code`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Fetching code failed (${res.status})`);
  return (await res.json()) as CodeBundle;
}
