import type { ReactNode } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { styleOf } from "@/lib/status";

/** The one place any error/alert surface gets built — replaces 3 near-duplicate hand-rolled blocks
 * (Step 5). `role="alert"` is safe here: traced page.tsx's state machine and confirmed the 3 real
 * call sites (rate-limit, generic error, run-failed) are mutually exclusive by construction — never
 * rendered together, and StatusBadge itself has no aria-live of its own, so there's no double
 * announcement risk. Colors always route through status.ts — never hardcoded here. */
export function Banner({ status, children }: { status: string; children: ReactNode }) {
  const s = styleOf(status);
  return (
    <div role="alert" className={`rounded-xl border p-4 ${s.border} ${s.surface}`}>
      <StatusBadge status={status} />
      <p className="mt-2 text-sm text-slate-300">{children}</p>
    </div>
  );
}
