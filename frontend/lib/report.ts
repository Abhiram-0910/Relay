// Summary line for ValidationReport's skipped-operations list. Neutral on purpose: operations are
// skipped for different reasons (no JSON response, an unsupported parameter style, a generator
// error), and each row shows its own reason.
export function skippedSummary(count: number): string {
  return `${count} operation${count === 1 ? "" : "s"} skipped — reasons below`;
}
