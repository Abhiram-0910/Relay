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
