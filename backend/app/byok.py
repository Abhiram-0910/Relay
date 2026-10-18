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

import time

import requests

# Retry exactly once, and only for failures a second try can plausibly fix (network error, 5xx).
# A 404/401/other 4xx won't change on retry.
_FETCH_ATTEMPTS = 2
_RETRY_DELAY_SECONDS = 1.0


class ByokKeyMissingError(Exception):
    """The run was dispatched with a BYOK key, but the Worker has none for it (404): the
    single-use ticket expired (TTL) or was already consumed. Reported as `byok_key_missing`."""


class ByokFetchError(Exception):
    """Any other failure fetching the BYOK key: auth, unexpected status, malformed body, or a
    network/5xx error that survived one retry. Reported as `byok_fetch_failed`."""


def fetch_byok_key(callback_url: str, run_id: str, callback_secret: str, timeout: float = 5.0):
    """
    Fetch the user's BYOK key exactly once via the Worker's authenticated,
    delivery-once endpoint. Returns (api_key, provider, model); `model` is None
    for records stored without an explicit model.

    Fails closed: raises ByokKeyMissingError on 404 and ByokFetchError on
    everything else. Network errors and 5xx get one retry first. Never returns
    a missing key; a run that asked for BYOK must not quietly continue on the
    shared key.
    """
    url = f"{callback_url.rstrip('/')}/api/runs/{run_id}/byok-key"
    headers = {"Authorization": f"Bearer {callback_secret}"}
    for attempt in range(1, _FETCH_ATTEMPTS + 1):
        last = attempt == _FETCH_ATTEMPTS
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            if last:
                raise ByokFetchError("byok-key fetch failed: network error") from e
            time.sleep(_RETRY_DELAY_SECONDS)
            continue
        if resp.status_code >= 500 and not last:
            time.sleep(_RETRY_DELAY_SECONDS)
            continue
        break

    if resp.status_code == 404:
        raise ByokKeyMissingError("byok-key not found: ticket expired or already consumed")
    if resp.status_code == 401:
        raise ByokFetchError("byok-key fetch failed: unauthorized (bad CALLBACK_SECRET)")
    if resp.status_code != 200:
        raise ByokFetchError(f"byok-key fetch failed: unexpected status {resp.status_code}")

    try:
        body = resp.json()
        api_key = body.get("apiKey")
    except (ValueError, AttributeError) as e:
        raise ByokFetchError("byok-key fetch failed: malformed response body") from e
    if not api_key:
        raise ByokFetchError("byok-key fetch failed: response carried no key")
    return api_key, body.get("provider"), body.get("model")


def run_with_optional_byok(callback_url, run_id, callback_secret, has_byok, provider_call):
    """
    Wraps a provider call (e.g. Task 9's self_correct step) with the BYOK
    fetch. `provider_call` is a callable taking (api_key, provider, model) —
    all None when there's no BYOK key — and returning whatever the pipeline
    step normally returns. Scopes the key to this call: it's a local variable
    here and in provider_call's own frame, never assigned anywhere
    longer-lived. `provider`/`model` select the adapter and pin the model for
    the correction step (Step 2 multi-provider).

    When has_byok is true, a failed fetch propagates (ByokKeyMissingError /
    ByokFetchError) and provider_call is never reached: no fallback to the
    shared key.
    """
    api_key = None
    provider = None
    model = None
    if has_byok:
        api_key, provider, model = fetch_byok_key(callback_url, run_id, callback_secret)

    try:
        return provider_call(api_key, provider, model)
    finally:
        # Best-effort scope hygiene — Python can't guarantee a string is
        # zeroed in memory without a native extension, and that's not worth
        # adding here. The real protections (delivery-once, short TTL, never
        # touching disk or logs) are all upstream of this line; this is just
        # not holding a reference around longer than necessary.
        api_key = None
