"""Piece 4e-ii: resolve_base_url (OpenAPI servers precedence, variables, relative URLs, validation)
and its use by ci_runner. Hermetic."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app import ci_runner
from app.servers import (SERVER_URL_INVALID, SERVER_URL_MISSING_HOST, SERVER_VARIABLE_INVALID,
                         resolve_base_url)

SPEC_URL = "https://specs.example.com/apis/v3/openapi.json"


def _spec(top=None, path_item=None, operation=None) -> dict:
    op = {"responses": {}}
    item = {"get": op}
    spec = {"openapi": "3.0.3", "paths": {"/x": item}}
    if operation is not None:
        op["servers"] = operation
    if path_item is not None:
        item["servers"] = path_item
    if top is not None:
        spec["servers"] = top
    return spec


def resolve(spec, spec_url=SPEC_URL):
    return resolve_base_url(spec, "/x", "get", spec_url)


# --- precedence and defaults ---------------------------------------------------------------------

def test_operation_beats_path_item_beats_top_level() -> None:
    top, item, op = [{"url": "https://top.example"}], [{"url": "https://item.example"}], [{"url": "https://op.example"}]
    assert resolve(_spec(top, item, op)) == ("https://op.example", None)
    assert resolve(_spec(top, item)) == ("https://item.example", None)
    assert resolve(_spec(top)) == ("https://top.example", None)


def test_an_empty_list_falls_through_to_the_next_level() -> None:
    assert resolve(_spec([{"url": "https://top.example"}], [], [])) == ("https://top.example", None)


def test_first_entry_of_the_winning_list() -> None:
    assert resolve(_spec([{"url": "https://first.example"}, {"url": "https://second.example"}])) == (
        "https://first.example", None)


@pytest.mark.parametrize("top", [None, []])
def test_absent_or_empty_servers_mean_the_spec_origin(top) -> None:
    assert resolve(_spec(top)) == ("https://specs.example.com", None)


def test_relative_url_resolves_against_the_final_spec_url() -> None:
    """After a redirect the spec came from the final URL, so that is what "/api/v3" is relative to."""
    spec = _spec([{"url": "/api/v3"}])
    assert resolve(spec, "https://petstore3.swagger.io/api/v3/openapi.json") == (
        "https://petstore3.swagger.io/api/v3", None)
    assert resolve(spec, "https://mirror.example.net/specs/petstore.json") == (
        "https://mirror.example.net/api/v3", None)
    assert resolve(_spec([{"url": "v2"}]), "https://h.example/a/b/spec.yaml") == ("https://h.example/a/b/v2", None)


def test_open_meteo_shaped_path_item_servers() -> None:
    spec = _spec(path_item=[{"url": "https://api.open-meteo.com"}, {"url": "https://customer-api.open-meteo.com"}])
    assert resolve(spec, "https://raw.githubusercontent.com/open-meteo/open-meteo/main/openapi/forecast.yml") == (
        "https://api.open-meteo.com", None)


# --- variables -----------------------------------------------------------------------------------

def _with_vars(url: str, variables) -> dict:
    return _spec([{"url": url, "variables": variables}])


def test_variable_defaults_are_substituted() -> None:
    spec = _with_vars("https://{region}.api.example.com/{version}",
                      {"region": {"default": "eu", "enum": ["eu", "us"]}, "version": {"default": "v2"}})
    assert resolve(spec) == ("https://eu.api.example.com/v2", None)


@pytest.mark.parametrize("variables", [
    {"region": {"default": "ap", "enum": ["eu", "us"]}},   # default not in enum
    {"region": {"enum": ["eu"]}},                          # no default
    {"region": {"default": 3}},                            # non-string default
    {"region": "eu"},                                      # variable isn't an object
    {"other": {"default": "eu"}},                          # {region} undeclared
    None,
    {"region": {"default": "eu", "enum": "eu"}},           # enum isn't a list
    {"region": {"default": "{region}"}},                   # substitution can't re-open a brace
])
def test_unusable_variables_are_unresolved(variables) -> None:
    assert resolve(_with_vars("https://{region}.example.com", variables)) == (None, SERVER_VARIABLE_INVALID)


@pytest.mark.parametrize("value,expected", [
    ("evil.com/", ("https://evil.com/.example.com", None)),  # the spec author's own URL; the SSRF guard still applies
    ("@evil.com", (None, SERVER_URL_INVALID)),
    ("x\nevil", (None, SERVER_URL_INVALID)),
    ("x evil", (None, SERVER_URL_INVALID)),
    ("x\\@evil", (None, SERVER_URL_INVALID)),
    ("x?q=1", (None, SERVER_URL_INVALID)),
    ("x#frag", (None, SERVER_URL_INVALID)),
    ("__import__('os')", (None, SERVER_URL_INVALID)),
])
def test_variable_values_are_plain_text_and_validated(value, expected) -> None:
    assert resolve(_with_vars("https://{v}.example.com", {"v": {"default": value}})) == expected


# --- validation ----------------------------------------------------------------------------------

@pytest.mark.parametrize("url,expected", [
    ("ftp://files.example.com", (None, SERVER_URL_INVALID)),
    ("javascript:alert(1)", (None, SERVER_URL_INVALID)),
    ("file:///etc/passwd", (None, SERVER_URL_INVALID)),
    ("data:text/plain,x", (None, SERVER_URL_INVALID)),
    ("//cdn.example.org/api", ("https://cdn.example.org/api", None)),  # scheme-relative: inherits https
    ("https://user:pass@api.example.com", (None, SERVER_URL_INVALID)),
    ("https://user@api.example.com", (None, SERVER_URL_INVALID)),
    ("https://api.example.com\x00", (None, SERVER_URL_INVALID)),
    ("https://api.example.com/\x7f", (None, SERVER_URL_INVALID)),
    ("https://api.example.com/\u0085", (None, SERVER_URL_INVALID)),
    ("https://api.example.com/a b", (None, SERVER_URL_INVALID)),
    ("https://api.example.com/" + "a" * 2100, (None, SERVER_URL_INVALID)),
    ("https://api.example.com:99999", (None, SERVER_URL_INVALID)),
    ("https://", (None, SERVER_URL_MISSING_HOST)),
    ("http:///nohost", (None, SERVER_URL_MISSING_HOST)),
    ("http://[::1]:8080/", ("http://[::1]:8080", None)),  # resolves; the sandbox pre-flight then blocks it
    ("https://[2001:db8::1]/api", ("https://[2001:db8::1]/api", None)),
    ("https://api.example.com/v1/", ("https://api.example.com/v1", None)),
    ("https://api.example.com//", ("https://api.example.com/", None)),  # one trailing slash stripped
    ("HTTPS://API.EXAMPLE.COM", ("HTTPS://API.EXAMPLE.COM", None)),
    ("", (None, SERVER_URL_INVALID)),
])
def test_url_validation(url, expected) -> None:
    assert resolve(_spec([{"url": url}])) == expected


@pytest.mark.parametrize("entry", [{"url": 3}, {"description": "no url"}, "https://a.example", None])
def test_malformed_server_entries(entry) -> None:
    assert resolve(_spec([entry])) == (None, SERVER_URL_INVALID)


def test_reasons_never_contain_spec_text() -> None:
    marker = "SPECTEXT"
    hostile = [f"ftp://{marker}", f"https://{marker}@x", f"https://{{{marker}}}.x", f"https://x/{marker}\n",
               f"https://{marker}" + "a" * 3000, f"//{marker}:99999"]
    for url in hostile:
        base_url, reason = resolve(_spec([{"url": url, "variables": {marker: {"default": "a", "enum": ["b"]}}}]))
        assert base_url is None and reason in {SERVER_URL_INVALID, SERVER_VARIABLE_INVALID, SERVER_URL_MISSING_HOST}
        assert marker not in reason


# --- through ci_runner ---------------------------------------------------------------------------

OPEN_METEO_SHAPED = {
    "openapi": "3.1.0", "info": {"title": "Open-Meteo APIs", "version": "1"},
    "paths": {"/v1/forecast": {
        "servers": [{"url": "https://api.open-meteo.com"}, {"url": "https://customer-api.open-meteo.com"}],
        "get": {"operationId": "getForecast", "parameters": [
            {"name": "latitude", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "longitude", "in": "query", "required": True, "schema": {"type": "string"}}],
            "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"type": "object"}}}}}},
    }},
}


def _run_with(monkeypatch, spec: dict, final_url: str):
    monkeypatch.setattr(ci_runner, "resolve_and_validate_host", lambda url: "8.8.8.8")
    resp = MagicMock(text=json.dumps(spec), is_redirect=False, url=final_url)
    resp.raise_for_status.return_value = None
    monkeypatch.setattr(ci_runner.requests, "get", lambda *a, **k: resp)
    monkeypatch.delenv("RELAY_HAS_BYOK", raising=False)
    call_specs = []

    def sandbox(pkg, call_spec):
        call_specs.append(call_spec)
        return {"status": "verified_pass", "passed": True, "detail": "stub"}

    monkeypatch.setattr(ci_runner, "run_in_sandbox", sandbox)
    monkeypatch.setattr(ci_runner, "self_correct", MagicMock(side_effect=AssertionError("no correction")))
    return ci_runner.run("https://submitted.example/openapi.json", reporter=MagicMock()), call_specs


def test_open_meteo_path_servers_resolve_with_no_hook_base_url(monkeypatch) -> None:
    result, call_specs = _run_with(monkeypatch, OPEN_METEO_SHAPED,
                                   "https://raw.githubusercontent.com/open-meteo/open-meteo/main/openapi/forecast.yml")
    assert [c["base_url"] for c in call_specs] == ["https://api.open-meteo.com"]
    assert call_specs[0]["kwargs"] == {"latitude": 52.52, "longitude": 13.41}  # demo params still apply
    assert result["validated"][0]["status"] == "verified_pass"


def test_relative_servers_use_the_final_url_after_redirects(monkeypatch) -> None:
    spec = {**OPEN_METEO_SHAPED, "servers": [{"url": "/api/v3"}],
            "paths": {"/v1/forecast": {k: v for k, v in OPEN_METEO_SHAPED["paths"]["/v1/forecast"].items()
                                       if k != "servers"}}}
    _, call_specs = _run_with(monkeypatch, spec, "https://final.example.org/specs/openapi.json")
    assert call_specs[0]["base_url"] == "https://final.example.org/api/v3"


def test_unresolvable_base_url_is_generated_only_and_never_reaches_the_sandbox(monkeypatch) -> None:
    spec = {**OPEN_METEO_SHAPED, "servers": [{"url": "ftp://files.example"}],
            "paths": {"/v1/forecast": {k: v for k, v in OPEN_METEO_SHAPED["paths"]["/v1/forecast"].items()
                                       if k != "servers"}}}
    result, call_specs = _run_with(monkeypatch, spec, "https://final.example.org/openapi.json")
    assert call_specs == []
    assert result["validated"] == [{"endpoint": {"method": "GET", "path": "/v1/forecast"},
                                    "status": "generated_only", "reason": "no usable base URL (server_url_invalid)"}]
