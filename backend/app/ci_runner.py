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

import json
import os
import sys
import tempfile
import time
from pathlib import Path

import requests
from openapi_spec_validator import validate as validate_spec
from prance import ResolvingParser

from app.correct import self_correct
from app.generate import generate_all_endpoints
from app.sandbox import STATUS_PASS, run_in_sandbox

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
             result: dict | None = None, error: str | None = None, retries: int = 1) -> None:
        snapshot = {"status": status, "stage": stage, "progress": progress,
                    "result": result, "error": error}
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


def _live_validate(spec: dict, generated: list[dict]) -> list[dict]:
    """Sandbox-validate each idempotent GET endpoint, self-correcting on failure. Returns a
    per-endpoint verdict list (never the generated code itself — keep the KV result small)."""
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
                result = self_correct(pkg, call_spec, report)
                report = result.final_report
                attempts = [a.__dict__ for a in result.attempts]

        verdicts.append({"endpoint": endpoint, "status": report["status"],
                         "passed": report["status"] == STATUS_PASS, "attempts": attempts})
    return verdicts


def run(spec_url: str, reporter: Reporter | None = None) -> dict:
    """Run the full pipeline for one spec URL, returning the final result dict."""
    rep = reporter or Reporter()
    try:
        rep.send("running", stage="fetching_spec")
        resp = requests.get(spec_url, timeout=15)
        resp.raise_for_status()
        spec = ResolvingParser(spec_string=resp.text).specification
        validate_spec(spec)
        title = spec.get("info", {}).get("title")
        rep.send("running", stage="spec_validated", progress={"title": title})

        with tempfile.TemporaryDirectory() as tmp:
            items = list(generate_all_endpoints(spec, Path(tmp)))
        generated = [{k: v for k, v in it.items() if k not in {"index", "total", "status"}}
                     for it in items if it["status"] == "generated"]
        skipped = [{"endpoint": it["endpoint"], "reason": it["reason"]}
                   for it in items if it["status"] == "skipped"]
        rep.send("running", stage="generated",
                 progress={"generated": len(generated), "skipped": len(skipped)})

        rep.send("running", stage="live_validating")
        verdicts = _live_validate(spec, generated)

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
        rep.send("failed", stage="error", error=f"{type(exc).__name__}: {exc}", retries=3)
        raise


if __name__ == "__main__":
    spec = os.environ.get("RELAY_SPEC_URL") or (sys.argv[1] if len(sys.argv) > 1 else None)
    if not spec:
        sys.exit("RELAY_SPEC_URL (or argv[1]) required")
    outcome = run(spec)
    print("\nFINAL:", outcome["status"])
