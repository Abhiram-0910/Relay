// Shapes returned by the Relay Worker API (see worker/src/index.js + backend/app/ci_runner.py).

export type RunStatus = "queued" | "running" | "succeeded" | "failed";

export interface Endpoint {
  method: string;
  path: string;
}

export interface CorrectionAttempt {
  model: string;
  status_before: string;
  changed_files: string[];
  status_after: string;
  detail: string;
}

export interface ValidatedEndpoint {
  endpoint: Endpoint;
  // verified_pass | verified_live_validation_failed | call_failed | generated_only | ssrf_blocked
  status: string;
  passed?: boolean;
  reason?: string;
  attempts?: CorrectionAttempt[];
}

export interface RunResult {
  title?: string | null;
  endpoint_count?: number;
  generated?: Endpoint[];
  skipped?: { endpoint: Endpoint; reason: string }[];
  validated?: ValidatedEndpoint[];
}

export interface RunSnapshot {
  runId: string;
  status: RunStatus;
  stage: string | null;
  progress: Record<string, unknown> | null;
  result: RunResult | null;
  error: string | null;
  updatedAt: number;
  // Stamped by the Worker when a BYOK key is delivered-once to the run (honest receipt data).
  byokReceivedAt?: string;
  byokDeletedAt?: string;
}

/** One selectable model from POST /api/models (uniform shape across all providers). */
export interface ModelOption {
  id: string;
  label: string;
}

export interface ModelsResponse {
  models: ModelOption[];
}

export interface CreateRunResponse {
  runId: string;
  status: string;
  statusUrl: string;
}

export interface RateLimitInfo {
  error: "rate_limited";
  limit: number;
  remaining: number;
  retryAfter: number;
}

export interface CodeFile {
  endpoint: Endpoint | null;
  name: string;
  content: string;
  truncated: boolean;
}

export interface CodeBundle {
  files: CodeFile[];
  truncated: boolean;
  dropped: number;
}
