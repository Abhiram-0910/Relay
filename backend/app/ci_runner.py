"""End-to-end pipeline runner for CI: fetch a spec, generate typed clients, live-validate the
idempotent GET endpoints in the sandbox (self-correcting via Gemini on failure), and report
checkpoint snapshots to the Cloudflare Worker's KV via an authenticated callback.

Invoked by the GitHub Action (workflow_dispatch) with these env vars:
    RELAY_RUN_ID          run id minted by the Worker (KV key)
    RELAY_SPEC_URL        OpenAPI 3.0/3.1 spec URL to process
    RELAY_CALLBACK_URL    Worker base URL for progress callbacks
    RELAY_CALLBACK_SECRET shared secret; sent as `Authorization: Bearer` on every callback
    GEMINI_API_KEY        for self-correction (Task 9)

With no callback env set, runs in local mode and prints each snapshot instead of POSTing — so the
whole pipeline is runnable/verifiable without the Worker.

The callback is authenticated with RELAY_CALLBACK_SECRET (a shared secret), NOT the Action's
built-in GITHUB_TOKEN — that token is for talking to GitHub, and grants nothing on the Worker.
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

import prance.util.url as _prance_url
import requests
from openapi_spec_validator import validate as validate_spec
from openapi_spec_validator.validation.exceptions import OpenAPIValidationError
from prance import ResolvingParser
from prance import ValidationError as PranceValidationError
from prance.util.formats import ParseError as PranceParseError
from prance.util.formats import parse_spec
from prance.util.url import ResolutionError as PranceResolutionError

from urllib.parse import urljoin, urlsplit

from app.byok import ByokFetchError, ByokKeyMissingError, run_with_optional_byok
from app.correct import self_correct
from app.generate import generate_all_endpoints
from app.servers import resolve_base_url
from app.synthesis import SKIP_LIVE_BUDGET_EXCEEDED, plan_live_call
from app.sandbox import STATUS_PASS, SandboxError, SSRFError, resolve_and_validate_host, run_in_sandbox
from app.spec_version import (UNRECOGNISED, SpecVersionInvalid, UnsupportedSpecVersion, check_spec_version,
                              spec_version_label)

# SECURITY (Step 6, F2): resolve_and_validate_host only checks the URL it's given. `requests.get`
# follows redirects by default and re-resolves DNS on every hop with NO host check of its own --
# so a spec/ref host that passes the check can redirect (or DNS-rebind) to a private/metadata
# target with zero further validation, defeating the whole point of the check. This bounded,
# re-validated-per-hop fetch is used for BOTH the spec_url fetch below and the $ref fetches
# guarded_prance_resolve gates. The DNS-rebinding half of this (a public host's DNS record
# changing between check and connect, no redirect involved) is a separate, narrower, already-
# accepted residual — see ARCHITECTURE.md.
_MAX_REDIRECTS = 5


def _redirect_safe_get(url: str, *, timeout: float) -> requests.Response:
    """GET url, re-running resolve_and_validate_host on every redirect hop instead of trusting
    requests' default unbounded, unvalidated redirect chain."""
    for _ in range(_MAX_REDIRECTS + 1):
        resolve_and_validate_host(url)
        resp = requests.get(url, timeout=timeout, allow_redirects=False)
        location = resp.headers.get("location") if resp.is_redirect else None
        if location:
            url = urljoin(url, location)
            continue
        return resp
    raise SSRFError(f"too many redirects fetching {url!r}")


# $ref resolution runs on the CI runner, which has network access — an untrusted spec must not be
# able to make prance fetch a private/metadata host or read local files. We gate EVERY url prance
# fetches at its single choke point (prance.util.url.fetch_url_text, which all remote/file/python
# ref fetches route through, transitively included), reusing the sandbox's SSRF host check.
_ALLOWED_REF_SCHEMES = {"http", "https"}


def _guard_fetch_url(parsed_url) -> None:
    """Reject a URL prance is about to fetch for a $ref (raises SSRFError to block it).

    file:// and python:// would read runner files / import packages, so only http(s) is allowed,
    and its resolved host must be public (reuses sandbox.resolve_and_validate_host)."""
    scheme = (parsed_url.scheme or "").lower()
    if scheme not in _ALLOWED_REF_SCHEMES:
        raise SSRFError(f"refusing $ref with non-http(s) scheme: {parsed_url.geturl()!r}")
    resolve_and_validate_host(parsed_url.geturl())  # raises SSRFError if host is private/reserved


def _safe_ref_fetch_url_text(url, cache={}, encoding=None):  # noqa: B006 (mirrors prance's own signature)
    """Drop-in replacement for prance.util.url.fetch_url_text's http(s) branch: bounded,
    per-hop-revalidated redirects (_redirect_safe_get) instead of prance's own bare `requests.get`,
    which follows redirects with no host check at all (Step 6 security review, F2). _guard_fetch_url
    has already rejected file/python schemes before this ever runs, so the http(s) case is the only
    one reachable here; same tuple-and-cache contract as the function this replaces, so
    ResolvingParser's caller-side behavior is unaffected."""
    url_key = "text_" + _prance_url.urlresource(url)
    cached = cache.get(url_key)
    if cached is not None:
        return cached
    resp = _redirect_safe_get(url.geturl(), timeout=15)
    if not resp.ok:
        raise _prance_url.ResolutionError(
            f'Cannot fetch URL "{url.geturl()}": {resp.status_code} {resp.reason}'
        )
    entry = (resp.text, resp.headers.get("content-type", "text/plain"))
    cache[url_key] = entry
    return entry


class SpecRecursionError(PranceResolutionError):
    """A recursive $ref (a schema that contains itself). Legal OpenAPI, but Relay can't resolve it
    yet: classified unsupported_spec_construct, not spec_invalid. Carries no spec text."""


def _raise_on_recursion(limit, parsed_url, recursions=()):
    """prance recursion_limit_handler: raise our own subtype so the classifier keys on the type,
    never on prance's message text."""
    raise SpecRecursionError("recursive $ref: not supported yet")


@contextlib.contextmanager
def guarded_prance_resolve():
    """Gate every URL prance fetches during $ref resolution through _guard_fetch_url, and replace
    the actual http(s) fetch with the redirect-safe one (_safe_ref_fetch_url_text) instead of
    letting prance's own unbounded, unvalidated-redirect requests.get run.

    Wraps prance's single fetch choke point. Asserts that function still exists so a prance
    restructure fails LOUDLY (the default-suite test below breaks) instead of silently reopening
    the gap. Restores the original in `finally` so the patch is scoped to this block.
    """
    assert hasattr(_prance_url, "fetch_url_text"), (
        "prance.util.url.fetch_url_text is gone — the $ref SSRF guard would be bypassed. "
        "Pin prance or re-point the guard at the new fetch choke point before proceeding."
    )
    original = _prance_url.fetch_url_text

    def guarded(url, *args, **kwargs):
        _guard_fetch_url(url)
        scheme = (url.scheme or "").lower()
        if scheme in _ALLOWED_REF_SCHEMES:
            return _safe_ref_fetch_url_text(url, *args, **kwargs)
        return original(url, *args, **kwargs)  # unreachable given the guard above; kept for parity

    _prance_url.fetch_url_text = guarded
    try:
        yield
    finally:
        _prance_url.fetch_url_text = original

# Owner decision (piece 5a): Open-Meteo's /v1/forecast declares latitude/longitude as bare
# `type: string` with no example, default, enum or bounds, so no synthesis tier can fill them and the
# default demo would never be live-called. These values apply only to that exact operation of that
# spec (path + info.title + info.version: robust to upstream edits, unlike a byte hash of a spec
# fetched from a moving branch), only for a parameter no tier fills, labelled `demo_preset`.
# Values are strings because the spec declares strings. No base URL here: that comes from servers.
DEMO_PRESETS = {
    ("/v1/forecast", "Open-Meteo Weather Forecast API", "1.0"): {"latitude": "52.52", "longitude": "13.41"},
}

# Live-call budget (piece 5a step 0e): ~2.5 s per sandbox call measured, 20 s host kill worst case.
# 10 x (20 s + 1 s pause) = 210 s worst case, well inside generate.yml's 20-minute job timeout.
MAX_LIVE_OPERATIONS = 10
LIVE_CALL_PAUSE_SECONDS = 1.0  # between live calls to the same host; no retries


class Reporter:
    """Posts checkpoint snapshots to the Worker; the Worker enforces the KV write-rate throttle,
    so this just emits at stage boundaries. Local mode (no callback env) prints instead."""

    def __init__(self) -> None:
        self.run_id = os.environ.get("RELAY_RUN_ID")
        self.base = os.environ.get("RELAY_CALLBACK_URL")
        self.secret = os.environ.get("RELAY_CALLBACK_SECRET")
        self.live = bool(self.run_id and self.base and self.secret)

    def send(self, status: str, *, stage: str | None = None, progress: dict | None = None,
             result: dict | None = None, error: str | None = None, error_detail: str | None = None,
             retries: int = 1) -> None:
        # error is a stable taxonomy CODE (Step 3) meant to be rendered; error_detail is the raw
        # exception text, kept for debugging only. Both land in KV either way (this reporter has no
        # opinion on that) — never printed anywhere else, since local mode is the only print path
        # and that's a developer's own terminal, not the Action's public log.
        snapshot = {"status": status, "stage": stage, "progress": progress,
                    "result": result, "error": error, "error_detail": error_detail}
        if not self.live:
            print("[report]", json.dumps({k: v for k, v in snapshot.items() if v is not None}))
            return
        url = f"{self.base.rstrip('/')}/api/runs/{self.run_id}/progress"
        for attempt in range(retries + 1):
            try:
                resp = requests.post(url, json=snapshot,
                                     headers={"Authorization": f"Bearer {self.secret}"}, timeout=10)
                if resp.status_code < 500:  # 2xx accept, 204 coalesced, 4xx won't fix by retrying
                    return
            except requests.RequestException:
                pass
            time.sleep(1)

    def send_code(self, files: list[dict], retries: int = 2) -> None:
        """POST the generated source to the Worker's code:{runId} store (size-guarded there)."""
        if not self.live:
            total = sum(len(f["content"]) for f in files)
            print(f"[code] {len(files)} files, {total} bytes (local mode, not posted)")
            return
        url = f"{self.base.rstrip('/')}/api/runs/{self.run_id}/code"
        for attempt in range(retries + 1):
            try:
                resp = requests.post(url, json={"files": files, "truncated": False},
                                     headers={"Authorization": f"Bearer {self.secret}"}, timeout=15)
                if resp.status_code < 500:
                    return
            except requests.RequestException:
                pass
            time.sleep(1)


def _code_files(generated: list[dict]) -> list[dict]:
    """Flatten generated endpoints into the code payload: models.py + client.py per endpoint."""
    files: list[dict] = []
    for item in generated:
        endpoint = item["endpoint"]
        files.append({"endpoint": endpoint, "name": "models.py", "content": item["models_code"]})
        files.append({"endpoint": endpoint, "name": "client.py", "content": item["client_code"]})
    return files


def _live_validate(spec: dict, generated: list[dict], spec_url: str, api_key: str | None = None,
                   provider: str | None = None, model: str | None = None) -> list[dict]:
    """Sandbox-validate each idempotent GET endpoint, self-correcting on failure. Returns a
    per-endpoint verdict list (never the generated code itself — keep the KV result small).

    `api_key`, when set (BYOK), overrides the shared GEMINI_API_KEY for every self-correction on
    this pass — fetched once upstream and threaded through, since the key is delivery-once.
    `provider`/`model` (BYOK) pick the corrector adapter and pin the model (no cross-model
    escalation on the user's key); both None → shared-key Gemini flash→pro path."""
    verdicts: list[dict] = []
    info = spec.get("info") or {}
    live_calls = 0
    last_call_at: dict[str, float] = {}  # host -> monotonic time its last live call finished
    for item in generated:
        endpoint = item["endpoint"]
        presets = DEMO_PRESETS.get((endpoint["path"], info.get("title"), info.get("version")))
        plan = plan_live_call(spec, endpoint["path"], endpoint["method"], item["params"], presets)
        if plan.skip_reason:  # a stable code, never spec text beyond a sanitised local name
            verdicts.append({"endpoint": endpoint, "status": "generated_only", "reason": plan.skip_reason})
            continue
        base_url, base_url_problem = resolve_base_url(spec, endpoint["path"], endpoint["method"], spec_url)
        if base_url is None:
            verdicts.append({"endpoint": endpoint, "status": "generated_only", "reason": base_url_problem})
            continue
        if live_calls >= MAX_LIVE_OPERATIONS:
            verdicts.append({"endpoint": endpoint, "status": "generated_only",
                             "reason": SKIP_LIVE_BUDGET_EXCEEDED})
            continue
        if "demo_preset" in plan.value_sources.values():
            print(f"demo_preset applied: GET {endpoint['path']}")  # path is a DEMO_PRESETS key, not spec text
        host = urlsplit(base_url).netloc
        wait = last_call_at.get(host, -math.inf) + LIVE_CALL_PAUSE_SECONDS - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        live_calls += 1
        kwargs = plan.kwargs

        with tempfile.TemporaryDirectory() as pkg_root:
            pkg = Path(pkg_root) / "client_pkg"
            pkg.mkdir()
            (pkg / "__init__.py").write_text("")
            (pkg / "models.py").write_text(item["models_code"])
            (pkg / "client.py").write_text(item["client_code"])
            call_spec = {"base_url": base_url, "module": "client",
                         "class_name": item["class_name"], "method": item["method_name"],
                         "kwargs": kwargs, "endpoint": endpoint}

            report = run_in_sandbox(pkg, call_spec)
            attempts: list[dict] = []
            if report["status"] != STATUS_PASS:
                result = self_correct(pkg, call_spec, report, api_key=api_key,
                                      provider=provider, model=model)
                report = result.final_report
                attempts = [a.__dict__ for a in result.attempts]

        last_call_at[host] = time.monotonic()
        verdicts.append({"endpoint": endpoint, "status": report["status"],
                         "passed": report["status"] == STATUS_PASS, "attempts": attempts,
                         "http_status": report.get("http_status"), "value_sources": plan.value_sources})
    return verdicts


class _NoOperationsGenerated(Exception):
    """Generation finished but produced nothing to deliver. Carries the result (with the skipped
    list) so the failed run still shows why."""

    def __init__(self, result: dict) -> None:
        super().__init__(f"0 of {result['endpoint_count']} operations generated")
        self.result = result


class _GenerationError(Exception):
    """Marks a failure during generate_all_endpoints for _classify_pipeline_error below.
    Generation has no exception type of its own to key off (unlike SSRFError/SandboxError/etc.), so
    this wraps whatever it actually raised — same "adapter classifies, shared function maps" shape
    as correct.py's corrector marker exceptions."""


def _classify_pipeline_error(exc: Exception) -> str:
    """Map a pipeline-stage exception to a stable status code (Step 3: honest error taxonomy).
    Safe by construction: SSRFError/SandboxError/requests.RequestException/the prance+
    openapi_spec_validator exceptions each occur at exactly one stage of `run()` (verified — nothing
    else in this pipeline can raise them), so type alone is enough to place the failure. Anything
    unrecognized stays internal_error, the honest catch-all — never a guessed bucket."""
    if isinstance(exc, _GenerationError):
        return "generation_failed"
    if isinstance(exc, _NoOperationsGenerated):
        return "no_operations_generated"
    if isinstance(exc, UnsupportedSpecVersion):
        return "unsupported_spec_version"
    if isinstance(exc, SpecVersionInvalid):
        return "spec_invalid"
    if isinstance(exc, ByokKeyMissingError):
        return "byok_key_missing"
    if isinstance(exc, ByokFetchError):
        return "byok_fetch_failed"
    if isinstance(exc, SSRFError):
        return "ssrf_blocked_spec"
    if isinstance(exc, SandboxError):
        return "sandbox_unavailable"
    if isinstance(exc, requests.RequestException):
        return "spec_fetch_failed"
    if isinstance(exc, SpecRecursionError):  # before the generic ResolutionError branch below
        return "unsupported_spec_construct"
    if isinstance(exc, (PranceParseError, PranceResolutionError, PranceValidationError, OpenAPIValidationError)):
        return "spec_invalid"
    return "internal_error"


def load_spec_text(text: str, final_url: str,
                   on_version: Callable[[str], None] = lambda label: None) -> tuple[dict, str, str]:
    """Parse -> version gate -> $ref resolution -> validation, for spec text fetched from
    `final_url`. Returns (spec, final_url, spec_version label). `on_version` gets the public label
    as soon as it is known, before the gate can raise.

    The gate runs on the parsed document BEFORE $ref resolution, so an unsupported spec never
    triggers a remote $ref fetch. The label is strictly version-shaped (never the URL or raw spec
    text), so it is safe for the public Action log and the run record."""
    document = parse_spec(text)
    label = spec_version_label(document)
    on_version(label)
    check_spec_version(document)
    with guarded_prance_resolve():  # gate every $ref prance fetches during resolution
        spec = ResolvingParser(spec_string=text, recursion_limit_handler=_raise_on_recursion).specification
    validate_spec(spec)
    return spec, final_url, label


def load_spec(spec_url: str,
              on_version: Callable[[str], None] = lambda label: None) -> tuple[dict, str, str]:
    """Fetch, then load_spec_text. The returned URL is the FINAL one after redirects, which is what
    relative `servers` URLs resolve against. _redirect_safe_get validates the host on every
    redirect hop, not just the original URL (F2)."""
    resp = _redirect_safe_get(spec_url, timeout=15)
    resp.raise_for_status()
    return load_spec_text(resp.text, resp.url, on_version)


def run(spec_url: str, reporter: Reporter | None = None) -> dict:
    """Run the full pipeline for one spec URL, returning the final result dict."""
    rep = reporter or Reporter()
    spec_version = None

    def version_seen(label: str) -> None:
        nonlocal spec_version
        spec_version = label
        print(f"spec_version: {label}")

    try:
        rep.send("running", stage="fetching_spec")
        spec, final_spec_url, _ = load_spec(spec_url, on_version=version_seen)
        title = spec.get("info", {}).get("title")
        rep.send("running", stage="spec_validated", progress={"title": title, "spec_version": spec_version})

        try:
            with tempfile.TemporaryDirectory() as tmp:
                items = list(generate_all_endpoints(spec, Path(tmp)))
        except Exception as exc:
            raise _GenerationError(str(exc)) from exc
        generated = [{k: v for k, v in it.items() if k not in {"index", "total", "status"}}
                     for it in items if it["status"] == "generated"]
        skipped = [{"endpoint": it["endpoint"], "reason": it["reason"]}
                   for it in items if it["status"] == "skipped"]
        # A generator crash is skipped, not fatal, but must stay visible in the Action log. The
        # path is spec text, so repr() it: no raw newline can start a `::workflow-command::` line.
        for s in skipped:
            if s["reason"].startswith("generator_error"):
                print(f"skipped {s['endpoint']['method']} {s['endpoint']['path']!r}: {s['reason']}")
        if not generated:
            raise _NoOperationsGenerated({
                "title": title, "spec_version": spec_version, "endpoint_count": len(items),
                "generated": [], "parameters": [], "skipped": skipped, "validated": [],
            })
        rep.send("running", stage="generated",
                 progress={"generated": len(generated), "skipped": len(skipped)})

        # Store the generated source (separate KV entry, fetched on demand) before the terminal
        # result, so it's available the moment the frontend sees "succeeded".
        rep.send_code(_code_files(generated))

        rep.send("running", stage="live_validating")
        # BYOK: if the Worker stored a user key for this run, fetch it once (delivery-once) and
        # thread it through this validation pass; otherwise self-correction uses the shared key.
        # Fails closed: a BYOK run whose key can't be fetched raises (byok_key_missing /
        # byok_fetch_failed) instead of silently running on the shared key.
        has_byok = os.environ.get("RELAY_HAS_BYOK") == "true"
        verdicts = run_with_optional_byok(
            callback_url=os.environ.get("RELAY_CALLBACK_URL"),
            run_id=os.environ.get("RELAY_RUN_ID"),
            callback_secret=os.environ.get("RELAY_CALLBACK_SECRET"),
            has_byok=has_byok,
            provider_call=lambda api_key, provider, model: _live_validate(
                spec, generated, final_spec_url, api_key=api_key, provider=provider, model=model),
        )

        # A run fails if any endpoint that was actually live-called ended up not passing.
        live_called = [v for v in verdicts if v["status"] != "generated_only"]
        failed = [v for v in live_called if not v.get("passed")]
        result = {
            "title": title,
            "spec_version": spec_version,
            "endpoint_count": len(items),
            "generated": [g["endpoint"] for g in generated],
            # The parameter table per generated operation: what the method takes, how each name and
            # type was derived, and what was weakened (lossy) or left out (ignored).
            "parameters": [{"endpoint": g["endpoint"], "params": g["params"], "lossy": g["lossy"],
                            "ignored": g["ignored"]} for g in generated],
            "skipped": skipped,
            "validated": verdicts,
        }
        final_status = "failed" if failed else "succeeded"
        rep.send(final_status, stage="done", result=result, retries=3)
        return {"status": final_status, **result}
    except Exception as exc:
        code = _classify_pipeline_error(exc)
        detail = f"{type(exc).__name__}: {exc}"[:500]
        if spec_version is None:  # failed before a document was parsed: still log a version line
            spec_version = UNRECOGNISED
            print(f"spec_version: {spec_version}")
        rep.send("failed", stage="error", error=code, error_detail=detail,
                 progress={"spec_version": spec_version}, result=getattr(exc, "result", None), retries=3)
        raise


if __name__ == "__main__":
    spec = os.environ.get("RELAY_SPEC_URL") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not spec:
        sys.exit("RELAY_SPEC_URL (or argv[1]) required")
    try:
        outcome = run(spec)
    except Exception as exc:
        # run() itself still raises on failure (unchanged — test_private_spec_url_rejected_before_
        # any_fetch depends on that) and has already sent the classified code + full error_detail to
        # the Worker/KV before doing so. This is only about what reaches the Action's OWN stdout/
        # stderr, which — unlike KV — is a genuinely public log: never the raw exception or a
        # traceback, just the same stable code a run's own record already carries.
        sys.exit(f"FINAL: failed ({_classify_pipeline_error(exc)})")
    print("\nFINAL:", outcome["status"])
