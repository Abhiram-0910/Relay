// SINGLE SOURCE OF TRUTH for how a status maps to color + label.
//
// The color mapping is a correctness rule, not a style preference: color must never imply more
// confidence than the backend earned. Everything that renders a status MUST go through here — no
// inline color decisions in components — so the mapping can't drift as more UI is added.
//
//   emerald (success)  -> verified_pass ONLY (and run-level "succeeded", i.e. all live-called passed)
//   amber   (warn)     -> generated_only, verified_live_validation_failed, and in-progress states
//   red     (danger)   -> call_failed, sandbox_timeout, failed, ssrf_blocked, rate_limited, error,
//                         and the 7 corrector-level codes (Step 3) — all failure modes
//   slate   (neutral)  -> anything unknown

export type Tone = "success" | "warn" | "danger" | "neutral";

const TONE_BY_STATUS: Record<string, Tone> = {
  // success — genuinely verified against a live call
  verified_pass: "success",
  succeeded: "success",
  // warn — generated but not proven, reached-but-mismatched, or still working
  generated_only: "warn",
  verified_live_validation_failed: "warn",
  queued: "warn",
  running: "warn",
  // danger — failed, blocked, or rate-limited
  call_failed: "danger",
  sandbox_timeout: "danger",
  failed: "danger",
  ssrf_blocked: "danger",
  rate_limited: "danger",
  error: "danger",
  // danger — corrector-level codes (Step 3, correct.py's _classify_corrector_exception). Only ever
  // appear in an attempt's status_after, never a run/endpoint status, but this map is the shared
  // short-label vocabulary for any status-like token, so they belong here rather than duplicated.
  corrector_auth_failed: "danger",
  corrector_config_error: "danger",
  quota_exhausted_shared: "danger",
  quota_exhausted_byok: "danger",
  corrector_network_error: "danger",
  corrector_bad_response: "danger",
  corrector_error: "danger",
};

// Literal Tailwind class strings so the content scanner can see them (no dynamic construction).
const TONE_CLASSES: Record<Tone, { badge: string; dot: string; text: string; border: string }> = {
  success: {
    badge: "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
    dot: "bg-emerald-400",
    text: "text-emerald-300",
    border: "border-emerald-500/40",
  },
  warn: {
    badge: "bg-amber-500/10 text-amber-300 border-amber-500/30",
    dot: "bg-amber-400",
    text: "text-amber-300",
    border: "border-amber-500/40",
  },
  danger: {
    badge: "bg-red-500/10 text-red-300 border-red-500/30",
    dot: "bg-red-400",
    text: "text-red-300",
    border: "border-red-500/40",
  },
  neutral: {
    badge: "bg-slate-500/10 text-slate-300 border-slate-500/30",
    dot: "bg-slate-400",
    text: "text-slate-300",
    border: "border-slate-500/40",
  },
};

// Honest human labels — deliberately not inflated (e.g. generated_only is NOT called "passed").
const LABELS: Record<string, string> = {
  verified_pass: "Verified live",
  verified_live_validation_failed: "Reached API · response mismatch",
  call_failed: "Call failed",
  sandbox_timeout: "Timed out",
  generated_only: "Generated · not live-tested",
  ssrf_blocked: "Blocked (SSRF)",
  queued: "Queued",
  running: "Running",
  succeeded: "Succeeded",
  failed: "Failed",
  rate_limited: "Daily limit reached",
  error: "Error",
  corrector_auth_failed: "Key rejected",
  corrector_config_error: "Auto-fix unavailable",
  quota_exhausted_shared: "Free-tier limit",
  quota_exhausted_byok: "Quota exhausted",
  corrector_network_error: "Network error",
  corrector_bad_response: "Bad response",
  corrector_error: "Auto-fix failed",
};

export const toneOf = (status: string): Tone => TONE_BY_STATUS[status] ?? "neutral";
export const styleOf = (status: string) => TONE_CLASSES[toneOf(status)];
export const labelOf = (status: string): string => LABELS[status] ?? status;
