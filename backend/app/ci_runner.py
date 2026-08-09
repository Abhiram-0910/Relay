"""End-to-end pipeline runner for CI: fetch a spec, generate typed clients, live-validate the
idempotent GET endpoints in the sandbox (self-correcting via Gemini on failure), and report
checkpoint snapshots to the Cloudflare Worker's KV via an authenticated callback.

Invoked by the GitHub Action (workflow_dispatch) with these env vars:
    RELAY_RUN_ID          run id minted by the Worker (KV key)
    RELAY_SPEC_URL        OpenAPI/Swagger spec URL to process
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
import os
import sys
import tempfile
import time
from pathlib import Path

import prance.util.url as _prance_url
import requests
from openapi_spec_validator import validate as validate_spec
from openapi_spec_validator.validation.exceptions import OpenAPIValidationError
from prance import ResolvingParser
from prance import ValidationError as PranceValidationError
from prance.util.formats import ParseError as PranceParseError
from prance.util.url import ResolutionError as PranceResolutionError

from app.byok import run_with_optional_byok
from app.correct import self_correct
from app.generate import generate_all_endpoints
from app.sandbox import STATUS_PASS, SandboxError, SSRFError, resolve_and_validate_host, run_in_sandbox

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


@contextlib.contextmanager
def guarded_prance_resolve():
    """Gate every URL prance fetches during $ref resolution through _guard_fetch_url.

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
        return original(url, *args, **kwargs)

    _prance_url.fetch_url_text = guarded
    try:
        yield
    finally:
        _prance_url.fetch_url_text = original

# Demo hook: for spec targets whose required params have no example/default and whose spec omits
# `servers`, supply a known-good base_url + params so the proven Open-Meteo flow works end to end.
# ponytail: hardcoded demo target; general base_url/param synthesis from the spec is a later task.
_DEMO_TARGETS = {
    "/v1/forecast": {"base_url": "https://api.open-meteo.com",
                     "params": {"latitude": 52.52, "longitude": 13.41}},
}


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


def _resolve_base_url(spec: dict, path: str) -> str | None:
    servers = spec.get("servers") or []
    if servers and servers[0].get("url"):
        return servers[0]["url"]
    demo = _DEMO_TARGETS.get(path)
    return demo["base_url"] if demo else None


def _required_kwargs(spec: dict, path: str, method: str) -> dict | None:
    """Build kwargs for the REQUIRED params from spec example/default (or the demo hook). Returns
    None if any required param can't be filled — that endpoint is reported generated-only, not
    live-called. Optional params are omitted to keep the call minimal and predictable."""
    operation = spec["paths"][path][method.lower()]
    demo_params = _DEMO_TARGETS.get(path, {}).get("params", {})
    kwargs: dict = {}
    for param in operation.get("parameters", []):
        if not param.get("required"):
            continue
        schema = param.get("schema", {})
        value = param.get("example", schema.get("example", schema.get("default")))
        if value is None:
            value = demo_params.get(param["name"])
        if value is None:
            return None
        kwargs[param["name"]] = value
    return kwargs


def _live_validate(spec: dict, generated: list[dict], api_key: str | None = None,
                   provider: str | None = None, model: str | None = None) -> list[dict]:
    """Sandbox-validate each idempotent GET endpoint, self-correcting on failure. Returns a
    per-endpoint verdict list (never the generated code itself — keep the KV result small).

    `api_key`, when set (BYOK), overrides the shared GEMINI_API_KEY for every self-correction on
    this pass — fetched once upstream and threaded through, since the key is delivery-once.
    `provider`/`model` (BYOK) pick the corrector adapter and pin the model (no cross-model
    escalation on the user's key); both None → shared-key Gemini flash→pro path."""
    verdicts: list[dict] = []
    for item in generated:
        endpoint = item["endpoint"]
        if endpoint["method"] != "GET":  # only live-call safe, idempotent GETs
            verdicts.append({"endpoint": endpoint, "status": "generated_only",
                             "reason": "non-GET; not called live"})
            continue
        base_url = _resolve_base_url(spec, endpoint["path"])
        kwargs = _required_kwargs(spec, endpoint["path"], endpoint["method"])
        if base_url is None or kwargs is None:
            verdicts.append({"endpoint": endpoint, "status": "generated_only",
                             "reason": "no base_url or required-param values available"})
            continue

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

        verdicts.append({"endpoint": endpoint, "status": report["status"],
                         "passed": report["status"] == STATUS_PASS, "attempts": attempts})
    return verdicts


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
    if isinstance(exc, SSRFError):
        return "ssrf_blocked_spec"
    if isinstance(exc, SandboxError):
        return "sandbox_unavailable"
    if isinstance(exc, requests.RequestException):
        return "spec_fetch_failed"
    if isinstance(exc, (PranceParseError, PranceResolutionError, PranceValidationError, OpenAPIValidationError)):
        return "spec_invalid"
    return "internal_error"


def run(spec_url: str, reporter: Reporter | None = None) -> dict:
    """Run the full pipeline for one spec URL, returning the final result dict."""
    rep = reporter or Reporter()
    try:
        rep.send("running", stage="fetching_spec")
        resolve_and_validate_host(spec_url)  # reject a private/metadata spec_url BEFORE fetching it
        resp = requests.get(spec_url, timeout=15)
        resp.raise_for_status()
        with guarded_prance_resolve():  # gate every $ref prance fetches during resolution
            spec = ResolvingParser(spec_string=resp.text).specification
        validate_spec(spec)
        title = spec.get("info", {}).get("title")
        rep.send("running", stage="spec_validated", progress={"title": title})

        try:
            with tempfile.TemporaryDirectory() as tmp:
                items = list(generate_all_endpoints(spec, Path(tmp)))
        except Exception as exc:
            raise _GenerationError(str(exc)) from exc
        generated = [{k: v for k, v in it.items() if k not in {"index", "total", "status"}}
                     for it in items if it["status"] == "generated"]
        skipped = [{"endpoint": it["endpoint"], "reason": it["reason"]}
                   for it in items if it["status"] == "skipped"]
        rep.send("running", stage="generated",
                 progress={"generated": len(generated), "skipped": len(skipped)})

        # Store the generated source (separate KV entry, fetched on demand) before the terminal
        # result, so it's available the moment the frontend sees "succeeded".
        rep.send_code(_code_files(generated))

        rep.send("running", stage="live_validating")
        # BYOK: if the Worker stored a user key for this run, fetch it once (delivery-once) and
        # thread it through this validation pass; otherwise self-correction uses the shared key.
        has_byok = os.environ.get("RELAY_HAS_BYOK") == "true"
        verdicts = run_with_optional_byok(
            callback_url=os.environ.get("RELAY_CALLBACK_URL"),
            run_id=os.environ.get("RELAY_RUN_ID"),
            callback_secret=os.environ.get("RELAY_CALLBACK_SECRET"),
            has_byok=has_byok,
            provider_call=lambda api_key, provider, model: _live_validate(
                spec, generated, api_key=api_key, provider=provider, model=model),
        )

        # A run fails if any endpoint that was actually live-called ended up not passing.
        live_called = [v for v in verdicts if v["status"] != "generated_only"]
        failed = [v for v in live_called if not v.get("passed")]
        result = {
            "title": title,
            "endpoint_count": len(items),
            "generated": [g["endpoint"] for g in generated],
            "skipped": skipped,
            "validated": verdicts,
        }
        final_status = "failed" if failed else "succeeded"
        rep.send(final_status, stage="done", result=result, retries=3)
        return {"status": final_status, **result}
    except Exception as exc:
        code = _classify_pipeline_error(exc)
        detail = f"{type(exc).__name__}: {exc}"[:500]
        rep.send("failed", stage="error", error=code, error_detail=detail, retries=3)
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
