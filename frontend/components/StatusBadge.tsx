import { labelOf, styleOf } from "@/lib/status";

/** The only place a status becomes a colored badge — always via the status SSOT. */
export function StatusBadge({ status, pulse = false }: { status: string; pulse?: boolean }) {
  const s = styleOf(status);
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium ${s.badge}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${s.dot} ${pulse ? "animate-pulse" : ""}`} />
      {labelOf(status)}
    </span>
  );
}
