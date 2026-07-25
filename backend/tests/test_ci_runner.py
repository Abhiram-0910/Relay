"""Hermetic tests for ci_runner's pure logic: base-URL resolution, required-param synthesis, and
the reporter's local (no-callback) mode. The full pipeline is verified separately end-to-end."""
from __future__ import annotations

from urllib.parse import urlsplit

import prance.util.url as _prance_url
import pytest
from prance import ResolvingParser

from app import ci_runner
from app.ci_runner import Reporter, _required_kwargs, _resolve_base_url
from app.sandbox import SSRFError


def test_resolve_base_url_prefers_spec_servers() -> None:
    spec = {"servers": [{"url": "https://api.example.com"}], "paths": {}}
    assert _resolve_base_url(spec, "/anything") == "https://api.example.com"


def test_resolve_base_url_demo_fallback_when_servers_empty() -> None:
    spec = {"servers": [], "paths": {}}
    assert _resolve_base_url(spec, "/v1/forecast") == "https://api.open-meteo.com"
    assert _resolve_base_url(spec, "/unknown") is None


def test_required_kwargs_from_example_and_default() -> None:
    spec = {"paths": {"/x": {"get": {"parameters": [
        {"name": "a", "required": True, "example": 5},
        {"name": "b", "required": True, "schema": {"default": "hi"}},
        {"name": "c", "required": False, "schema": {}},  # optional -> omitted
    ]}}}}
    assert _required_kwargs(spec, "/x", "get") == {"a": 5, "b": "hi"}


def test_required_kwargs_uses_demo_hook_for_unfillable_required() -> None:
    # latitude/longitude are required with no example -> filled from the demo registry.
    spec = {"paths": {"/v1/forecast": {"get": {"parameters": [
        {"name": "latitude", "required": True, "schema": {"type": "string"}},
        {"name": "longitude", "required": True, "schema": {"type": "string"}},
    ]}}}}
    assert _required_kwargs(spec, "/v1/forecast", "get") == {"latitude": 52.52, "longitude": 13.41}


def test_required_kwargs_returns_none_when_unsynthesizable() -> None:
    spec = {"paths": {"/x": {"get": {"parameters": [
        {"name": "mystery", "required": True, "schema": {"type": "string"}},
    ]}}}}
    assert _required_kwargs(spec, "/x", "get") is None


def test_reporter_local_mode_prints_without_callback(capsys, monkeypatch) -> None:
    for var in ("RELAY_RUN_ID", "RELAY_CALLBACK_URL", "RELAY_CALLBACK_SECRET"):
        monkeypatch.delenv(var, raising=False)
    rep = Reporter()
    assert rep.live is False
    rep.send("running", stage="fetching_spec")
    out = capsys.readouterr().out
    assert "fetching_spec" in out and "running" in out


# --- SSRF gate on the spec-fetch step (all hermetic: literal private IPs, no DNS/network) -------

# A private ref that fails FAST to connect if the guard is ever bypassed (loopback:discard-port),
# so a regression fails loudly instead of hanging. Stands in for any private/metadata host.
_MALICIOUS_REF = "http://127.0.0.1:9/evil.yaml"
_MALICIOUS_SPEC = f"""
openapi: "3.0.0"
info: {{title: t, version: "1"}}
paths:
  /x:
    get:
      responses:
        "200":
          description: ok
          content:
            application/json:
              schema:
                $ref: "{_MALICIOUS_REF}"
"""


def _has_ssrf(exc: BaseException) -> bool:
    """True if SSRFError is anywhere in the exception's cause/context chain (prance may wrap it)."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, SSRFError):
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


def test_guard_rejects_private_and_non_http_schemes_allows_public() -> None:
    for bad in ("http://127.0.0.1/x", "http://10.0.0.1/x", "http://169.254.169.254/x",
                "file:///etc/passwd", "python://os/x.yaml"):
        with pytest.raises(SSRFError):
            ci_runner._guard_fetch_url(urlsplit(bad))
    # public host passes the guard through (literal public IP -> offline getaddrinfo)
    ci_runner._guard_fetch_url(urlsplit("http://8.8.8.8/spec.yaml"))


def test_private_spec_url_rejected_before_any_fetch(monkeypatch) -> None:
    def must_not_fetch(*a, **k):
        raise AssertionError("ci_runner fetched a private spec_url instead of rejecting it")
    monkeypatch.setattr(ci_runner.requests, "get", must_not_fetch)
    with pytest.raises(SSRFError):
        ci_runner.run("http://169.254.169.254/openapi.yaml")


def test_malicious_ref_rejected_during_resolution_not_followed(monkeypatch) -> None:
    # Spy sits UNDER the guard: records anything the ORIGINAL fetch would have retrieved.
    fetched: list[str] = []
    original = _prance_url.fetch_url_text
    monkeypatch.setattr(_prance_url, "fetch_url_text",
                        lambda url, *a, **k: (fetched.append(url.geturl()), original(url, *a, **k))[1])

    with pytest.raises(Exception) as exc_info:
        with ci_runner.guarded_prance_resolve():
            ResolvingParser(spec_string=_MALICIOUS_SPEC).specification

    # Our guard is what stopped it (breaks loudly if prance stops routing through fetch_url_text)...
    assert _has_ssrf(exc_info.value)
    # ...and the private host was never actually fetched.
    assert not any("127.0.0.1" in url for url in fetched)
