"""Hermetic tests for ci_runner's pure logic: base-URL resolution, required-param synthesis, and
the reporter's local (no-callback) mode. The full pipeline is verified separately end-to-end."""
from __future__ import annotations

from urllib.parse import urlsplit

import prance.util.url as _prance_url
import pytest
import requests
from openapi_spec_validator.validation.exceptions import OpenAPIValidationError
from prance import ResolvingParser
from prance import ValidationError as PranceValidationError
from prance.util.formats import ParseError as PranceParseError
from prance.util.url import ResolutionError as PranceResolutionError

from app import ci_runner
from app.byok import ByokFetchError, ByokKeyMissingError
from app.ci_runner import Reporter, _code_files, _required_kwargs
from app.sandbox import SandboxError, SSRFError


def test_resolve_base_url_prefers_spec_servers() -> None:
    from app.servers import resolve_base_url

    spec = {"servers": [{"url": "https://api.example.com"}], "paths": {}}
    assert resolve_base_url(spec, "/anything", "get", "https://specs.example/o.json") == (
        "https://api.example.com", None)


def test_resolve_base_url_falls_back_to_the_spec_origin_not_a_demo_hook() -> None:
    """Empty servers mean "/" per OpenAPI: the spec document's own origin. The Open-Meteo base URL
    now comes from that spec's path-item servers, never from _DEMO_TARGETS."""
    from app.servers import resolve_base_url

    spec = {"servers": [], "paths": {}}
    assert resolve_base_url(spec, "/v1/forecast", "get", "https://specs.example/a/o.json") == (
        "https://specs.example", None)
    assert "base_url" not in ci_runner._DEMO_TARGETS["/v1/forecast"]


def _kwargs_for(parameters: list[dict], path: str = "/x", path_item_parameters: list[dict] = ()):
    """_required_kwargs over the generator's own parameter table for this operation."""
    from app.naming import _safe_identifier
    from app.params import plan_parameters

    path_item = {"get": {"parameters": parameters}, "parameters": list(path_item_parameters)}
    table = plan_parameters(path_item, path_item["get"], alias_prefix="Op",
                            sanitise=_safe_identifier).to_jsonable()
    return _required_kwargs({"paths": {path: path_item}}, path, "get", table)


def test_required_kwargs_from_example_and_default() -> None:
    assert _kwargs_for([
        {"name": "a", "in": "query", "required": True, "example": 5},
        {"name": "b", "in": "query", "required": True, "schema": {"default": "hi"}},
        {"name": "c", "in": "query", "required": False, "schema": {}},  # optional -> omitted
    ]) == {"a": 5, "b": "hi"}


def test_required_kwargs_uses_demo_hook_for_unfillable_required() -> None:
    # latitude/longitude are required with no example -> filled from the demo registry.
    assert _kwargs_for([
        {"name": "latitude", "in": "query", "required": True, "schema": {"type": "string"}},
        {"name": "longitude", "in": "query", "required": True, "schema": {"type": "string"}},
    ], path="/v1/forecast") == {"latitude": 52.52, "longitude": 13.41}


def test_required_kwargs_returns_none_when_unsynthesizable() -> None:
    assert _kwargs_for([
        {"name": "mystery", "in": "query", "required": True, "schema": {"type": "string"}},
    ]) is None


def test_required_kwargs_are_keyed_by_local_name_and_cover_every_location() -> None:
    assert _kwargs_for([
        {"name": "id", "in": "query", "required": True, "example": "q"},
        {"name": "page-size", "in": "query", "required": True, "example": 10},
        {"name": "X-Req", "in": "header", "required": True, "example": "h"},
        {"name": "session", "in": "cookie", "required": False, "example": "c"},  # ignored by the plan
    ], path_item_parameters=[{"name": "id", "in": "path", "required": True, "example": 7}]) == {
        "id": 7, "id_query": "q", "param_2": 10, "param_3": "h"}


def test_required_kwargs_synthesise_enum_and_array_values() -> None:
    assert _kwargs_for([
        {"name": "unit", "in": "query", "required": True, "schema": {"type": "string", "enum": [None, "c", "f"]}},
        {"name": "tags", "in": "query", "required": True,
         "schema": {"type": "array", "items": {"type": "string", "enum": ["t1", "t2"]}}},
        {"name": "ids", "in": "query", "required": True, "schema": {"type": "array", "example": 3}},
        {"name": "raw", "in": "query", "required": True, "schema": {"type": "array", "items": {}}},
    ]) is None  # `raw` has nothing to synthesise from -> generated-only, as before
    assert _kwargs_for([
        {"name": "unit", "in": "query", "required": True, "schema": {"type": "string", "enum": [None, "c", "f"]}},
        {"name": "tags", "in": "query", "required": True,
         "schema": {"type": "array", "items": {"type": "string", "enum": ["t1", "t2"]}}},
        {"name": "ids", "in": "query", "required": True, "schema": {"type": "array", "example": 3}},
    ]) == {"unit": "c", "tags": ["t1"], "ids": [3]}


def test_code_files_emits_models_and_client_per_endpoint() -> None:
    generated = [
        {"endpoint": {"method": "GET", "path": "/a"}, "models_code": "M_A", "client_code": "C_A"},
        {"endpoint": {"method": "GET", "path": "/b"}, "models_code": "M_B", "client_code": "C_B"},
    ]
    files = _code_files(generated)
    assert [(f["endpoint"]["path"], f["name"], f["content"]) for f in files] == [
        ("/a", "models.py", "M_A"),
        ("/a", "client.py", "C_A"),
        ("/b", "models.py", "M_B"),
        ("/b", "client.py", "C_B"),
    ]


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


# --- Step 3: pipeline-level error classification ------------------------------------------------

def test_classify_pipeline_error_maps_every_known_type() -> None:
    classify = ci_runner._classify_pipeline_error
    assert classify(SSRFError("x")) == "ssrf_blocked_spec"
    assert classify(SandboxError("x")) == "sandbox_unavailable"
    assert classify(requests.ConnectionError("x")) == "spec_fetch_failed"
    assert classify(requests.Timeout("x")) == "spec_fetch_failed"
    assert classify(PranceParseError("x")) == "spec_invalid"
    assert classify(PranceResolutionError("x")) == "spec_invalid"
    assert classify(PranceValidationError("x")) == "spec_invalid"
    # OpenAPIValidationError's real constructor needs jsonschema.ValidationError's args; isinstance
    # is all the classifier checks, so a bare instance via __new__ is enough to prove the mapping.
    assert classify(OpenAPIValidationError.__new__(OpenAPIValidationError)) == "spec_invalid"
    assert classify(ci_runner._GenerationError("x")) == "generation_failed"
    assert classify(ByokKeyMissingError("x")) == "byok_key_missing"
    assert classify(ByokFetchError("x")) == "byok_fetch_failed"
    assert classify(RuntimeError("something else entirely")) == "internal_error"


# --- piece 4i: recursive $ref is classified on its own type, ahead of the generic ResolutionError --

def _spec_with_schema(schema: str, components: str = "") -> str:
    return f"""
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
              schema: {schema}
{components}"""


_RECURSIVE_SPEC = _spec_with_schema('{$ref: "#/components/schemas/Node"}', """
components:
  schemas:
    Node:
      type: object
      properties:
        children: {type: array, items: {$ref: "#/components/schemas/Node"}}
""")


def _load_error(text: str) -> Exception:
    with pytest.raises(Exception) as exc_info:
        ci_runner.load_spec_text(text, "https://specs.example/o.yaml")
    return exc_info.value


def test_recursive_ref_is_unsupported_construct_not_spec_invalid() -> None:
    exc = _load_error(_RECURSIVE_SPEC)
    assert type(exc) is ci_runner.SpecRecursionError
    assert ci_runner._classify_pipeline_error(exc) == "unsupported_spec_construct"
    assert "Node" not in str(exc)  # no spec text in the message


def test_unresolvable_internal_ref_stays_spec_invalid() -> None:
    exc = _load_error(_spec_with_schema('{$ref: "#/components/schemas/Missing"}'))
    assert isinstance(exc, PranceResolutionError) and not isinstance(exc, ci_runner.SpecRecursionError)
    assert ci_runner._classify_pipeline_error(exc) == "spec_invalid"


def test_relay_ref_fetch_failure_stays_spec_invalid(monkeypatch) -> None:
    from unittest.mock import MagicMock

    monkeypatch.setattr(ci_runner, "resolve_and_validate_host", lambda url: "8.8.8.8")
    monkeypatch.setattr(ci_runner, "_redirect_safe_get",
                        lambda url, *, timeout: MagicMock(ok=False, status_code=404, reason="Not Found"))
    exc = _load_error(_spec_with_schema('{$ref: "https://8.8.8.8/shared.yaml#/Thing"}'))
    assert type(exc) is PranceResolutionError
    assert ci_runner._classify_pipeline_error(exc) == "spec_invalid"


def test_ssrf_blocked_ref_stays_ssrf_blocked_spec() -> None:
    exc = _load_error(_MALICIOUS_SPEC)
    assert ci_runner._classify_pipeline_error(exc) == "ssrf_blocked_spec"


def test_classify_checks_recursion_before_generic_resolution_error() -> None:
    classify = ci_runner._classify_pipeline_error
    assert classify(ci_runner.SpecRecursionError("x")) == "unsupported_spec_construct"
    assert classify(PranceResolutionError("x")) == "spec_invalid"


class _CapturingReporter:
    """Duck-typed stand-in for Reporter — run() only ever calls .send()/.send_code() on whatever
    it's given, so this skips real env-var/HTTP plumbing and just records what was sent."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, status, *, stage=None, progress=None, result=None, error=None,
             error_detail=None, retries=1) -> None:
        self.sent.append({"status": status, "stage": stage, "error": error, "error_detail": error_detail})

    def send_code(self, files, retries=2) -> None:
        pass


def test_run_reports_ssrf_blocked_spec_code(monkeypatch) -> None:
    monkeypatch.setattr(ci_runner.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not fetch")))
    rep = _CapturingReporter()
    with pytest.raises(SSRFError):
        ci_runner.run("http://169.254.169.254/openapi.yaml", reporter=rep)

    failed = [s for s in rep.sent if s["status"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["error"] == "ssrf_blocked_spec"
    assert failed[0]["error_detail"]  # raw text present — kept for KV/debugging, never rendered raw


_MINIMAL_VALID_SPEC = """\
openapi: "3.0.0"
info: {title: t, version: "1"}
paths: {}
"""


def test_run_reports_generation_failed_code(monkeypatch) -> None:
    """Generation has no exception type of its own (unlike SSRF/sandbox/fetch/parse) — this proves
    the _GenerationError wrap+classify still lands the right code."""
    from unittest.mock import MagicMock

    monkeypatch.setattr(ci_runner, "resolve_and_validate_host", lambda url: "8.8.8.8")
    fake_resp = MagicMock(text=_MINIMAL_VALID_SPEC, is_redirect=False)
    fake_resp.raise_for_status.return_value = None
    monkeypatch.setattr(ci_runner.requests, "get", lambda *a, **k: fake_resp)

    def boom(spec, tmp_dir):
        raise RuntimeError("template blew up")

    monkeypatch.setattr(ci_runner, "generate_all_endpoints", boom)

    rep = _CapturingReporter()
    with pytest.raises(ci_runner._GenerationError):
        ci_runner.run("https://example.com/openapi.yaml", reporter=rep)

    failed = [s for s in rep.sent if s["status"] == "failed"]
    assert len(failed) == 1
    assert failed[0]["error"] == "generation_failed"
    assert "template blew up" in failed[0]["error_detail"]


# --- Step 6 security review (F2): redirects must be re-validated per hop, not trusted blindly ---

def test_redirect_safe_get_follows_a_redirect_to_a_still_valid_host(monkeypatch) -> None:
    from unittest.mock import MagicMock

    calls = []

    def fake_get(url, *, timeout, allow_redirects):
        assert allow_redirects is False  # never delegate redirect-following to requests itself
        calls.append(url)
        if url == "https://a.example/spec":
            return MagicMock(is_redirect=True, headers={"location": "https://b.example/real-spec"})
        return MagicMock(is_redirect=False, status_code=200, headers={})

    monkeypatch.setattr(ci_runner.requests, "get", fake_get)
    monkeypatch.setattr(ci_runner, "resolve_and_validate_host", lambda url: "8.8.8.8")

    resp = ci_runner._redirect_safe_get("https://a.example/spec", timeout=5)
    assert calls == ["https://a.example/spec", "https://b.example/real-spec"]
    assert resp.is_redirect is False


def test_redirect_safe_get_revalidates_the_redirect_target_not_just_the_original_url(monkeypatch) -> None:
    """The whole point of the fix: a host that passes the check can no longer redirect to a
    private/metadata target with zero further validation."""
    from unittest.mock import MagicMock

    def fake_get(url, *, timeout, allow_redirects):
        return MagicMock(is_redirect=True, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    def fake_resolve(url):
        if "169.254.169.254" in url:
            raise SSRFError(f"private target: {url}")
        return "8.8.8.8"

    monkeypatch.setattr(ci_runner.requests, "get", fake_get)
    monkeypatch.setattr(ci_runner, "resolve_and_validate_host", fake_resolve)

    with pytest.raises(SSRFError, match="private target"):
        ci_runner._redirect_safe_get("https://a.example/spec", timeout=5)


def test_redirect_safe_get_bounds_the_redirect_chain(monkeypatch) -> None:
    from unittest.mock import MagicMock

    monkeypatch.setattr(ci_runner, "resolve_and_validate_host", lambda url: "8.8.8.8")
    monkeypatch.setattr(
        ci_runner.requests, "get",
        lambda url, **k: MagicMock(is_redirect=True, headers={"location": url + "x"}),  # never terminates
    )
    with pytest.raises(SSRFError, match="too many redirects"):
        ci_runner._redirect_safe_get("https://a.example/spec", timeout=5)


def test_ref_fetch_uses_the_same_redirect_safe_get_as_the_primary_spec_fetch(monkeypatch) -> None:
    """guarded_prance_resolve's http(s) $ref fetch must go through _redirect_safe_get, not
    prance's own bare requests.get (which follows redirects with no host check at all)."""
    from unittest.mock import MagicMock

    calls = []

    def fake_redirect_safe_get(url, *, timeout):
        calls.append(url)
        return MagicMock(ok=True, text="shared: content", headers={"content-type": "text/plain"})

    monkeypatch.setattr(ci_runner, "_redirect_safe_get", fake_redirect_safe_get)
    parsed = urlsplit("https://example.com/shared.yaml")

    content, content_type = ci_runner._safe_ref_fetch_url_text(parsed, cache={})
    assert content == "shared: content"
    assert calls == ["https://example.com/shared.yaml"]


# --- Phase 3 piece 2: BYOK fails closed, never falls back to the shared key --------------------

_ONE_GET_SPEC = """\
openapi: "3.0.0"
info: {title: t, version: "1"}
servers: [{url: "https://api.example.com"}]
paths:
  /things:
    get:
      responses:
        "200":
          description: ok
          content:
            application/json:
              schema: {type: object, properties: {id: {type: integer}}}
"""


def _byok_resp(status):
    from unittest.mock import MagicMock
    return MagicMock(status_code=status)


@pytest.mark.parametrize("byok_outcomes,code", [
    ([_byok_resp(404)], "byok_key_missing"),
    ([_byok_resp(401)], "byok_fetch_failed"),
    ([_byok_resp(500), _byok_resp(500)], "byok_fetch_failed"),
    ([requests.ConnectionError("x"), requests.ConnectionError("x")], "byok_fetch_failed"),
    ([requests.ConnectionError("x"), _byok_resp(404)], "byok_fetch_failed"),
], ids=["404", "401", "500x2", "network", "network_then_404"])
def test_run_byok_fetch_failure_reports_code_and_never_reaches_corrector(monkeypatch, byok_outcomes, code) -> None:
    """A BYOK run whose key can't be fetched must fail with a classified code, never self-correct
    on the shared key. The endpoint is live-callable and the sandbox spy would fail it, so any
    fallback would reach self_correct/genai."""
    from unittest.mock import MagicMock

    from google import genai

    for k, v in {"RELAY_HAS_BYOK": "true", "RELAY_CALLBACK_URL": "https://worker.example",
                 "RELAY_RUN_ID": "run-1", "RELAY_CALLBACK_SECRET": "s"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(ci_runner, "resolve_and_validate_host", lambda url: "8.8.8.8")
    monkeypatch.setattr("time.sleep", lambda s: None)
    outcomes = iter(byok_outcomes)

    def fake_get(url, **kwargs):
        if url.endswith("/byok-key"):
            nxt = next(outcomes)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt
        return MagicMock(text=_ONE_GET_SPEC, is_redirect=False)

    monkeypatch.setattr(ci_runner.requests, "get", fake_get)
    sandbox = MagicMock(return_value={"status": "call_failed", "detail": "x"})
    corrector = MagicMock()
    genai_client = MagicMock()
    monkeypatch.setattr(ci_runner, "run_in_sandbox", sandbox)
    monkeypatch.setattr(ci_runner, "self_correct", corrector)
    monkeypatch.setattr(genai, "Client", genai_client)

    rep = _CapturingReporter()
    with pytest.raises(Exception):
        ci_runner.run("https://example.com/openapi.yaml", reporter=rep)

    failed = [s for s in rep.sent if s["status"] == "failed"]
    assert [f["error"] for f in failed] == [code]
    sandbox.assert_not_called()
    corrector.assert_not_called()
    genai_client.assert_not_called()
