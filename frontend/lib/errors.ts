// SINGLE SOURCE OF TRUTH for turning a taxonomy error CODE into a plain-language message — the
// error-code sibling of status.ts's status→tone/label map (see ARCHITECTURE.md Step 3).
//
// Every code here is a real, stable string a backend layer already sends today:
//   - Worker synchronous errors (worker/src/index.js, models.js): dispatch_failed, invalid_*, etc.
//   - Pipeline-level codes (backend/app/ci_runner.py's _classify_pipeline_error): spec_fetch_failed,
//     spec_invalid, ssrf_blocked_spec, generation_failed, sandbox_unavailable, internal_error.
//   - Corrector-level codes (backend/app/correct.py's _classify_corrector_exception):
//     corrector_auth_failed, corrector_config_error, quota_exhausted_shared/byok,
//     corrector_network_error, corrector_bad_response, corrector_error.
//
// Never render a raw code, a raw exception string, or an HTTP status number to the end user —
// everything that shows an error MUST go through here, same discipline as status.ts.

const ERROR_MESSAGES: Record<string, string> = {
  // Worker: POST /api/runs synchronous errors
  invalid_spec_url: "That doesn't look like a reachable spec URL.",
  invalid_json: "That request wasn't understood — try again.",
  invalid_api_key: "That key doesn't look right — check you pasted it in full.",
  dispatch_failed: "Couldn't start the run right now — try again in a moment.",
  rate_limited: "You've used today's free generations.",

  // Worker: POST /api/models
  unknown_provider: "That provider isn't supported.",
  missing_api_key: "A key is required for that provider.",
  provider_unreachable: "Couldn't reach that provider right now — try again shortly.",
  key_rejected: "Key rejected — check that the key is valid and matches the provider.",
  provider_error: "That provider returned an unexpected error — try again shortly.",
  provider_bad_response: "That provider's response couldn't be read — try again shortly.",

  // Pipeline-level (run.error)
  spec_fetch_failed: "Couldn't fetch that spec URL — check it's reachable and try again.",
  spec_invalid: "That doesn't look like a valid OpenAPI/Swagger document.",
  ssrf_blocked_spec:
    "That spec (or something it references) points to a private/internal address, which can't be validated from here.",
  generation_failed: "Couldn't generate a client from that spec.",
  sandbox_unavailable: "Something went wrong on our end — try again shortly.",
  internal_error: "Something went wrong on our end — try again shortly.",

  // Corrector-level (per-attempt status_after)
  corrector_auth_failed: "Your key was rejected during auto-fix — check it's still valid.",
  corrector_config_error: "Auto-fix is temporarily unavailable — try again shortly.",
  quota_exhausted_shared: "The free daily limit for auto-fixing was reached.",
  quota_exhausted_byok:
    "Your account is out of quota for auto-fixing — check your usage/billing with your provider.",
  corrector_network_error: "Couldn't reach the provider to auto-fix the client.",
  corrector_bad_response: "Auto-fix didn't return a usable result — try again.",
  corrector_error: "Auto-fix didn't succeed this time.",
};

// Codes where switching to (or already using) BYOK is a genuine way around the problem — a shared
// resource was exhausted, or the shared key itself is broken. Deliberately separate from
// ERROR_MESSAGES: the message body is identical regardless of this flag, only whether the UI adds
// an "or bring your own key" hint changes.
const BYOK_SUGGESTING_CODES = new Set(["rate_limited", "quota_exhausted_shared", "corrector_config_error"]);

const FALLBACK_MESSAGE = "Something went wrong — try again shortly.";

export function errorMessage(code: string | null | undefined): string {
  if (!code) return FALLBACK_MESSAGE;
  return ERROR_MESSAGES[code] ?? FALLBACK_MESSAGE;
}

export function suggestsByok(code: string | null | undefined): boolean {
  return code != null && BYOK_SUGGESTING_CODES.has(code);
}
