import type { CodeBundle, CreateRunResponse, RateLimitInfo, RunSnapshot } from "./types";

// Worker URL comes from env — never hardcoded in a component.
const BASE = process.env.NEXT_PUBLIC_WORKER_URL;

/** Thrown when the Worker returns 429 — carries the honest cap details for the UI. */
export class RateLimitedError extends Error {
  constructor(public info: RateLimitInfo) {
    super("rate_limited");
    this.name = "RateLimitedError";
  }
}

function requireBase(): string {
  if (!BASE) {
    throw new Error("NEXT_PUBLIC_WORKER_URL is not set — configure it in .env.local");
  }
  return BASE;
}

export async function createRun(specUrl: string): Promise<CreateRunResponse> {
  const res = await fetch(`${requireBase()}/api/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ specUrl }),
  });
  if (res.status === 429) {
    throw new RateLimitedError((await res.json()) as RateLimitInfo);
  }
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { error?: string };
    throw new Error(body.error ? `Worker rejected the request: ${body.error}` : `Request failed (${res.status})`);
  }
  return (await res.json()) as CreateRunResponse;
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
