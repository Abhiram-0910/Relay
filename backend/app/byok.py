# backend/app/byok.py
#
# New, additive module for BYOK support in ci_runner.py — Step 1 of the
# Phase 2 plan. This session has no view of the real ci_runner.py, so this is
# a self-contained module with an explicit integration note rather than a
# diff against source Claude hasn't actually seen.
#
# Integration into ci_runner.py:
#   1. The workflow YAML needs a new workflow_dispatch input `has_byok`
#      (string "true"/"false", set by the Worker per ARCHITECTURE.md's
#      buildDispatchInputs) passed through to the job as an env var, e.g.:
#         env:
#           RELAY_HAS_BYOK: ${{ inputs.has_byok }}
#      alongside the existing RELAY_RUN_ID / RELAY_CALLBACK_URL /
#      RELAY_CALLBACK_SECRET.
#   2. Right before the Task 9 self-correction step's provider call:
#         from app.byok import run_with_optional_byok
#         has_byok = os.environ.get("RELAY_HAS_BYOK") == "true"
#         result = run_with_optional_byok(
#             callback_url=os.environ["RELAY_CALLBACK_URL"],
#             run_id=os.environ["RELAY_RUN_ID"],
#             callback_secret=os.environ["RELAY_CALLBACK_SECRET"],
#             has_byok=has_byok,
#             provider_call=lambda api_key, provider, model: self_correct(
#                 ..., api_key=api_key, provider=provider, model=model),
#         )
#      self_correct (Task 9) needs a new optional api_key param: if given,
#      call the user's provider with it instead of the shared GEMINI_API_KEY;
#      if None, behave exactly as today.
#   3. Never log the returned key. Never write it to disk. Never let it
#      outlive provider_call's own frame — don't assign it to a module-level
#      variable, a class attribute, or interpolate it into the attempt log
#      self_correct already writes (that log currently records model output
#      and error text — make sure the key itself never ends up in it).

import requests


class ByokFetchError(Exception):
    """Network/auth failure fetching the BYOK key. Callers should catch this
    and fall back to the shared free-tier key — a user's key not showing up
    should degrade the run, not crash it."""


def fetch_byok_key(callback_url: str, run_id: str, callback_secret: str, timeout: float = 5.0):
    """
    Fetch the user's BYOK key exactly once via the Worker's authenticated,
    delivery-once endpoint.

    Returns (api_key, provider, model) on success, or (None, None, None) if no
    key was ever stored for this run — a legitimate, common outcome, not an
    error. `model` is None for records stored without an explicit model (e.g.
    shared-key/Gemini-default runs).

    Raises ByokFetchError on auth/network failure so the caller can decide to
    fall back rather than let the whole run crash over a key-fetch hiccup.
    """
    url = f"{callback_url.rstrip('/')}/api/runs/{run_id}/byok-key"
    headers = {"Authorization": f"Bearer {callback_secret}"}
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as e:
        raise ByokFetchError("byok-key fetch failed: network error") from e

    if resp.status_code == 404:
        return None, None, None
    if resp.status_code == 401:
        raise ByokFetchError("byok-key fetch failed: unauthorized (bad CALLBACK_SECRET)")
    if resp.status_code != 200:
        raise ByokFetchError(f"byok-key fetch failed: unexpected status {resp.status_code}")

    body = resp.json()
    return body.get("apiKey"), body.get("provider"), body.get("model")


def run_with_optional_byok(callback_url, run_id, callback_secret, has_byok, provider_call):
    """
    Wraps a provider call (e.g. Task 9's self_correct step) with the BYOK
    fetch. `provider_call` is a callable taking (api_key, provider, model) —
    all None when there's no BYOK key — and returning whatever the pipeline
    step normally returns. Scopes the key to this call: it's a local variable
    here and in provider_call's own frame, never assigned anywhere
    longer-lived. `provider`/`model` select the adapter and pin the model for
    the correction step (Step 2 multi-provider).
    """
    api_key = None
    provider = None
    model = None
    if has_byok:
        try:
            api_key, provider, model = fetch_byok_key(callback_url, run_id, callback_secret)
        except ByokFetchError as e:
            # Log the fact of the fallback, never request/response detail
            # that could carry a token.
            print(f"[ci_runner] BYOK key fetch failed for run {run_id} "
                  f"({type(e).__name__}); falling back to shared free-tier key")
            api_key = None

    try:
        return provider_call(api_key, provider, model)
    finally:
        # Best-effort scope hygiene — Python can't guarantee a string is
        # zeroed in memory without a native extension, and that's not worth
        # adding here. The real protections (delivery-once, short TTL, never
        # touching disk or logs) are all upstream of this line; this is just
        # not holding a reference around longer than necessary.
        api_key = None
