"""Hermetic tests for ci_runner's pure logic: base-URL resolution, required-param synthesis, and
the reporter's local (no-callback) mode. The full pipeline is verified separately end-to-end."""
from __future__ import annotations

from app import ci_runner
from app.ci_runner import Reporter, _required_kwargs, _resolve_base_url


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
