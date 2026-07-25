import type { RunSnapshot } from "@/lib/types";

// The checkpoint stages ci_runner emits, in order. Used to show a simple timeline.
const STAGES: { key: string; label: string }[] = [
  { key: "fetching_spec", label: "Fetching spec" },
  { key: "spec_validated", label: "Spec validated" },
  { key: "generated", label: "Clients generated" },
  { key: "live_validating", label: "Validating live in sandbox" },
  { key: "done", label: "Done" },
];

function stageIndex(stage: string | null): number {
  const i = STAGES.findIndex((s) => s.key === stage);
  return i === -1 ? (stage ? 0 : -1) : i;
}

export function RunProgress({ snapshot }: { snapshot: RunSnapshot }) {
  const terminal = snapshot.status === "succeeded" || snapshot.status === "failed";
  const active = stageIndex(snapshot.stage);

  return (
    <ol className="space-y-3">
      {STAGES.map((stage, i) => {
        const done = terminal ? snapshot.status === "succeeded" || i < STAGES.length - 1 : i < active;
        const current = !terminal && i === active;
        return (
          <li key={stage.key} className="flex items-center gap-3">
            <span
              className={`grid h-5 w-5 place-items-center rounded-full border text-[10px] ${
                current
                  ? "border-amber-500/50 bg-amber-500/10 text-amber-300"
                  : done
                    ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                    : "border-line bg-white/5 text-muted"
              }`}
            >
              {current ? <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-400" /> : done ? "✓" : i + 1}
            </span>
            <span className={`text-sm ${current ? "text-foreground" : done ? "text-slate-300" : "text-muted"}`}>
              {stage.label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
