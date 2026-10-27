import { toneOf } from "./status";

// Summary line for ValidationReport's skipped-operations list. Neutral on purpose: operations are
// skipped for different reasons (no JSON response, an unsupported parameter style, a generator
// error), and each row shows its own reason.
export function skippedSummary(count: number): string {
  return `${count} operation${count === 1 ? "" : "s"} skipped — reasons below`;
}

// The short "HTTP 503" note EndpointRow shows beside a target_http_error badge; null otherwise.
export function httpStatusNote(v: { status: string; http_status?: number | null }): string | null {
  return v.status === "target_http_error" && typeof v.http_status === "number" ? `HTTP ${v.http_status}` : null;
}

// The problem part of ValidationReport's summary line, e.g. "2 failed · 1 needs attention"; "" if
// none. Mirrors the run-level rule (ci_runner: any live-called status other than verified_pass
// fails the run), so the line never reads fewer problems than the run status implies: danger
// statuses read "failed", everything else that isn't verified_pass/generated_only (the warn
// findings about the target or spec, a response mismatch, an unknown status) "needs attention".
export function problemSummary(statuses: string[]): string {
  const problems = statuses.filter((s) => s !== "verified_pass" && s !== "generated_only");
  const failed = problems.filter((s) => toneOf(s) === "danger").length;
  const attention = problems.length - failed;
  return [
    failed > 0 && `${failed} failed`,
    attention > 0 && `${attention} need${attention === 1 ? "s" : ""} attention`,
  ].filter(Boolean).join(" · ");
}

// Plain sentences for a generated_only verdict's reason. The backend sends stable codes
// (app/synthesis.py SKIP_*, app/servers.py SERVER_*); backend/tests/test_error_code_coverage.py
// fails if one has no entry here. Never render the raw reason.
const GENERATED_ONLY_REASONS: Record<string, string> = {
  not_get: "Only GET operations are called live; this one was generated but not called.",
  requires_auth: "This operation needs authentication, which Relay doesn't send yet, so it wasn't called live.",
  possibly_state_changing:
    "Its name suggests it could change something (for example delete or log out), so Relay didn't call it live.",
  no_sample_value: "No sample value for a required parameter.",
  live_budget_exceeded: "Not called live: this run reached its limit of live calls.",
  server_url_invalid: "The spec's server URL isn't usable, so there was nowhere safe to call.",
  server_variable_invalid: "The spec's server URL has a variable without a usable default value.",
  server_url_missing_host: "The spec's server URL has no host, so there was nowhere to call.",
  // Pre-5a runs still in KV sent these sentences instead of codes.
  "non-GET; not called live": "Only GET operations are called live; this one was generated but not called.",
  "required-param values unavailable": "No sample values for this operation's required parameters.",
};

const FALLBACK_REASON = "Generated, but not called live.";
const LOCAL_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;

export function generatedOnlyReason(reason: string): string {
  const i = reason.indexOf(":");
  const code = i < 0 ? reason : reason.slice(0, i);
  if (code === "no_sample_value" && i >= 0 && LOCAL_NAME.test(reason.slice(i + 1))) {
    return `No sample value for the required parameter ${reason.slice(i + 1)}.`;
  }
  return Object.prototype.hasOwnProperty.call(GENERATED_ONLY_REASONS, code)
    ? GENERATED_ONLY_REASONS[code]
    : FALLBACK_REASON;
}
