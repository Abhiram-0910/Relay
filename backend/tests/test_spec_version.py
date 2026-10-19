"""Piece 4e-i: the spec-version gate, the public spec_version label, and the no_operations_generated
outcome. Hermetic: the spec fetch is stubbed."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app import ci_runner
from app.spec_version import (SpecVersionInvalid, UnsupportedSpecVersion, check_spec_version,
                              spec_version_label)

OK = "supported"


@pytest.mark.parametrize("document,expected", [
    ({"openapi": "3.0.0"}, OK),
    ({"openapi": "3.0.3"}, OK),
    ({"openapi": "3.1.0"}, OK),
    ({"openapi": "3.1.1-rc.1"}, OK),
    ({"swagger": "2.0"}, UnsupportedSpecVersion),
    ({"swagger": "1.2"}, UnsupportedSpecVersion),
    ({"openapi": "3.2.0"}, UnsupportedSpecVersion),
    ({"openapi": "4.0.0"}, UnsupportedSpecVersion),
    ({"openapi": "2.0"}, UnsupportedSpecVersion),
    ({"openapi": "3.1"}, UnsupportedSpecVersion),   # a quoted "3.1" is version-shaped, not 3.0.x/3.1.x
    ({"swagger": "2.0", "openapi": "3.0.0"}, SpecVersionInvalid),
    ({"info": {"title": "no version"}}, SpecVersionInvalid),
    ({"openapi": 3.1}, SpecVersionInvalid),         # YAML `openapi: 3.1` parses as a float
    ({"swagger": 2.0}, SpecVersionInvalid),
    ({"openapi": "three"}, SpecVersionInvalid),
    ({"swagger": "abc"}, SpecVersionInvalid),
    ({"openapi": "3.0.0\n"}, SpecVersionInvalid),   # fullmatch: no trailing newline
    ({"openapi": " 3.0.0"}, SpecVersionInvalid),
    ([{"openapi": "3.0.0"}], SpecVersionInvalid),   # the document itself isn't a mapping
])
def test_version_rules(document, expected) -> None:
    if expected is OK:
        check_spec_version(document)
    else:
        with pytest.raises(expected):
            check_spec_version(document)


@pytest.mark.parametrize("value", [
    3.1, 3, True, None, ["3.0.0"], {"v": "3.0.0"},
    "3." + "9" * 10_000 + ".0",                     # huge, but version-shaped
    "x" * 100_000,
    "３.０.０",                          # fullwidth digits
    "3.١.0",                                    # Arabic-Indic digit
    "3.0.0‮", "3.0.0;rm -rf /", "\U0001F600",
])
def test_malformed_versions_never_pass_and_never_echo(value) -> None:
    with pytest.raises((SpecVersionInvalid, UnsupportedSpecVersion)):
        check_spec_version({"openapi": value})
    assert spec_version_label({"openapi": value}) == "unrecognised"


@pytest.mark.parametrize("document,label", [
    ({"swagger": "2.0"}, "swagger 2.0"),
    ({"openapi": "3.1.0"}, "openapi 3.1.0"),
    ({"openapi": "3.0.4"}, "openapi 3.0.4"),
    ({"openapi": "10.200.300"}, "openapi 10.200.300"),
    ({"openapi": "3.1.1-rc.1"}, "unrecognised"),     # supported, but not label-safe
    ({"openapi": "100.0.0"}, "unrecognised"),
    ({"openapi": "3.0.1000"}, "unrecognised"),
    ({"swagger": "2.0", "openapi": "3.0.0"}, "unrecognised"),
    ({}, "unrecognised"),
    ("not a mapping", "unrecognised"),
])
def test_spec_version_label(document, label) -> None:
    assert spec_version_label(document) == label


# --- through ci_runner.run -----------------------------------------------------------------------

def _stub_fetch(monkeypatch, text: str) -> None:
    monkeypatch.setattr(ci_runner, "resolve_and_validate_host", lambda url: "8.8.8.8")
    resp = MagicMock(text=text, is_redirect=False)
    resp.raise_for_status.return_value = None
    monkeypatch.setattr(ci_runner.requests, "get", lambda *a, **k: resp)
    monkeypatch.delenv("RELAY_HAS_BYOK", raising=False)


def _failed_send(rep: MagicMock):
    call = rep.send.call_args_list[-1]
    assert call.args == ("failed",)
    return call.kwargs


SWAGGER_WITH_REMOTE_REF = json.dumps({
    "swagger": "2.0", "info": {"title": "t", "version": "1"},
    "paths": {"/x": {"get": {"responses": {"200": {"description": "ok",
                                                   "schema": {"$ref": "https://evil.example/s.json"}}}}}},
})


def test_unsupported_version_fails_before_any_ref_resolution(monkeypatch, capsys) -> None:
    _stub_fetch(monkeypatch, SWAGGER_WITH_REMOTE_REF)
    resolver = MagicMock(side_effect=AssertionError("resolver must not run"))
    monkeypatch.setattr(ci_runner, "ResolvingParser", resolver)
    monkeypatch.setattr(ci_runner._prance_url, "fetch_url_text",
                        MagicMock(side_effect=AssertionError("no $ref fetch")))
    rep = MagicMock()

    with pytest.raises(UnsupportedSpecVersion):
        ci_runner.run("https://example.com/swagger.json", reporter=rep)

    resolver.assert_not_called()
    failed = _failed_send(rep)
    assert failed["error"] == "unsupported_spec_version"
    assert failed["progress"] == {"spec_version": "swagger 2.0"}
    assert "spec_version: swagger 2.0" in capsys.readouterr().out


@pytest.mark.parametrize("text", ['{"openapi": 3.1, "info": {}, "paths": {}}', "openapi: 3.1\ninfo: {}\npaths: {}\n"])
def test_float_version_is_spec_invalid(monkeypatch, text) -> None:
    _stub_fetch(monkeypatch, text)
    rep = MagicMock()
    with pytest.raises(SpecVersionInvalid):
        ci_runner.run("https://example.com/openapi.yaml", reporter=rep)
    assert _failed_send(rep)["error"] == "spec_invalid"
    assert _failed_send(rep)["progress"] == {"spec_version": "unrecognised"}


_NO_JSON_OP = {"openapi": "3.0.3", "info": {"title": "t", "version": "1"},
               "paths": {"/x": {"get": {"responses": {"204": {"description": "no content"}}}}}}


@pytest.mark.parametrize("spec,skipped", [
    (_NO_JSON_OP, [{"endpoint": {"method": "GET", "path": "/x"},
                    "reason": "GET /x has no JSON response schema to generate a model from"}]),
    ({"openapi": "3.0.3", "info": {"title": "t", "version": "1"}, "paths": {}}, []),
])
def test_zero_generated_operations_fail_with_the_skipped_list(monkeypatch, spec, skipped) -> None:
    _stub_fetch(monkeypatch, json.dumps(spec))
    rep = MagicMock()
    with pytest.raises(ci_runner._NoOperationsGenerated):
        ci_runner.run("https://example.com/openapi.json", reporter=rep)
    failed = _failed_send(rep)
    assert failed["error"] == "no_operations_generated"
    assert failed["result"] == {"title": "t", "spec_version": "openapi 3.0.3", "endpoint_count": len(skipped),
                                "generated": [], "parameters": [], "skipped": skipped, "validated": []}
    assert ci_runner._classify_pipeline_error(ci_runner._NoOperationsGenerated(failed["result"])) == \
        "no_operations_generated"


def test_spec_version_never_contains_the_url(monkeypatch, capsys) -> None:
    url = "https://example.com/spec.json?token=SECRET_TOKEN_MARKER"
    _stub_fetch(monkeypatch, json.dumps({"openapi": url, "info": {"title": "t", "version": "1"}, "paths": {}}))
    rep = MagicMock()
    with pytest.raises(SpecVersionInvalid):
        ci_runner.run(url, reporter=rep)
    sent = json.dumps([c.kwargs for c in rep.send.call_args_list], default=str)
    assert "SECRET_TOKEN_MARKER" not in sent
    assert "SECRET_TOKEN_MARKER" not in capsys.readouterr().out
    assert _failed_send(rep)["progress"] == {"spec_version": "unrecognised"}


def test_spec_version_is_logged_and_published_on_success(monkeypatch, capsys) -> None:
    spec = {"openapi": "3.1.0", "info": {"title": "t", "version": "1"}, "paths": {"/x": {"get": {
        "operationId": "getX",
        "responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"type": "object"}}}}}}}}}
    _stub_fetch(monkeypatch, json.dumps(spec))
    monkeypatch.setattr(ci_runner, "_live_validate", lambda *a, **k: [])
    rep = MagicMock()
    result = ci_runner.run("https://example.com/openapi.json", reporter=rep)
    assert result["status"] == "succeeded" and result["spec_version"] == "openapi 3.1.0"
    assert "spec_version: openapi 3.1.0" in capsys.readouterr().out
    validated = [c for c in rep.send.call_args_list if c.kwargs.get("stage") == "spec_validated"]
    assert validated[0].kwargs["progress"] == {"title": "t", "spec_version": "openapi 3.1.0"}
