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
