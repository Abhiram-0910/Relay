import { StatusBadge } from "@/components/StatusBadge";
import { toneOf } from "@/lib/status";
import type { RunResult, ValidatedEndpoint } from "@/lib/types";

function EndpointRow({ v }: { v: ValidatedEndpoint }) {
  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.03] p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <code className="font-mono text-sm">
          <span className="text-muted">{v.endpoint.method}</span>{" "}
          <span className="text-foreground">{v.endpoint.path}</span>
        </code>
        <StatusBadge status={v.status} />
      </div>
      {v.reason && <p className="mt-2 text-xs text-muted">{v.reason}</p>}
      {v.attempts && v.attempts.length > 0 && (
        <div className="mt-2 border-t border-white/5 pt-2">
          <p className="text-xs text-muted">
            Self-corrected in {v.attempts.length} attempt{v.attempts.length > 1 ? "s" : ""}:
          </p>
          <ol className="mt-1 space-y-1">
            {v.attempts.map((a, i) => (
              <li key={i} className="font-mono text-[11px] text-slate-400">
                {a.model}: {a.status_before} → {a.status_after}
                {a.changed_files.length > 0 && ` (patched ${a.changed_files.join(", ")})`}
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}

export function ValidationReport({ result }: { result: RunResult }) {
  const validated = result.validated ?? [];
  const verified = validated.filter((v) => v.status === "verified_pass").length;
  const liveFailed = validated.filter((v) => toneOf(v.status) === "danger").length;
  const notTested = validated.filter((v) => v.status === "generated_only").length;

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold">{result.title || "Generated client"}</h2>
        <p className="mt-1 text-sm text-muted">
          {result.endpoint_count ?? validated.length} operation
          {(result.endpoint_count ?? validated.length) === 1 ? "" : "s"} · {verified} verified live
          {notTested > 0 && ` · ${notTested} generated only`}
          {liveFailed > 0 && ` · ${liveFailed} failed`}
          {result.skipped && result.skipped.length > 0 && ` · ${result.skipped.length} skipped`}
        </p>
      </div>

      <div className="space-y-2">
        {validated.map((v, i) => (
          <EndpointRow key={i} v={v} />
        ))}
      </div>

      {result.skipped && result.skipped.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted">
            {result.skipped.length} operation{result.skipped.length > 1 ? "s" : ""} skipped (no JSON response)
          </summary>
          <ul className="mt-2 space-y-1">
            {result.skipped.map((s, i) => (
              <li key={i} className="font-mono text-xs text-slate-400">
                {s.endpoint.method} {s.endpoint.path} — {s.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
