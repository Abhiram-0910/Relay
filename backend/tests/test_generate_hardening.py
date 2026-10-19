"""Phase 3 piece 4b: generator hardening against hostile specs. Hermetic: no network, no sandbox;
generated modules are imported (and their method called, with requests stubbed) in a subprocess."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

from app import ci_runner
from app.generate import generate_all_endpoints, generate_endpoint_client
from tests.source_inert import assert_generated_source_inert

_OK = {"200": {"description": "ok", "content": {"application/json": {"schema": {"type": "object"}}}}}


def _one_op_spec(*, title="T", operation_id="getX", params=(), openapi="3.0.3") -> dict:
    return {"openapi": openapi, "info": {"title": title, "version": "1"},
            "paths": {"/x": {"get": {"operationId": operation_id, "parameters": list(params),
                                     "responses": _OK}}}}


# --- step 3: one failing operation is skipped, not fatal ----------------------------------------

def test_a_failing_operation_is_skipped_not_fatal(monkeypatch, tmp_path: Path) -> None:
    """Any exception while generating one operation skips it with a generator_error reason carrying
    only the exception type, while the rest still generate."""
    from app import generate

    real_plan = generate.plan_parameters

    def plan(path_item, operation, **kwargs):
        if operation.get("operationId") == "getX":
            raise TypeError("spec text that must not reach the reason")
        return real_plan(path_item, operation, **kwargs)

    monkeypatch.setattr(generate, "plan_parameters", plan)
    spec = _one_op_spec()
    spec["paths"]["/y"] = {"get": {"operationId": "getY", "responses": _OK}}

    by_path = {i["endpoint"]["path"]: i for i in generate_all_endpoints(spec, tmp_path)}
    assert by_path["/x"]["status"] == "skipped"
    assert by_path["/x"]["reason"] == "generator_error: TypeError"
    assert by_path["/y"]["status"] == "generated"


def test_openapi_31_type_list_param_generates(tmp_path: Path) -> None:
    """`type: ["integer", "null"]` used to crash the old _py_type (4b skipped it); since 4d it is
    planned by app.params as `int | None`."""
    spec = _one_op_spec(openapi="3.1.0", params=[
        {"name": "n", "in": "query", "schema": {"type": ["integer", "null"]}}])
    [item] = generate_all_endpoints(spec, tmp_path)
    assert item["status"] == "generated"
    assert item["params"] == [{"wire_name": "n", "local_name": "n", "location": "query", "required": False,
                               "py_type": "int | None", "lossy": []}]


def test_generator_error_reaches_the_report_and_the_ci_log(monkeypatch, capsys) -> None:
    from unittest.mock import MagicMock

    monkeypatch.setattr(ci_runner, "resolve_and_validate_host", lambda url: "8.8.8.8")
    fake_resp = MagicMock(text='openapi: "3.0.0"\ninfo: {title: t, version: "1"}\npaths: {}\n',
                          is_redirect=False)
    fake_resp.raise_for_status.return_value = None
    monkeypatch.setattr(ci_runner.requests, "get", lambda *a, **k: fake_resp)
    monkeypatch.delenv("RELAY_HAS_BYOK", raising=False)
    evil_path = "/x\n::set-output name=x::y"
    monkeypatch.setattr(ci_runner, "generate_all_endpoints", lambda spec, tmp: iter([
        {"index": 1, "total": 1, "status": "skipped", "endpoint": {"method": "GET", "path": evil_path},
         "reason": "generator_error: TypeError"}]))

    result = ci_runner.run("https://example.com/openapi.yaml", reporter=MagicMock())

    assert result["skipped"] == [{"endpoint": {"method": "GET", "path": evil_path},
                                  "reason": "generator_error: TypeError"}]
    out = capsys.readouterr().out
    assert f"skipped GET {evil_path!r}: generator_error: TypeError" in out
    assert "\n::set-output" not in out  # the spec's newline never reaches the log raw


# --- steps 1 and 2: spec text in docstrings and identifiers ------------------------------------

def test_title_is_inert_in_the_docstring(tmp_path: Path) -> None:
    title = 'Evil"""\nimport os\\\n' + "x" * 200
    code = generate_endpoint_client(_one_op_spec(title=title), "/x", "get", tmp_path)["client_code"]
    first = code.splitlines()[0]
    assert first.startswith('"""Generated client for Evil\'\'\' import os/ ') and first.endswith('."""')
    assert len(first) <= len('"""Generated client for ."""') + 80
    assert code.splitlines()[1] == "from __future__ import annotations"
    assert_generated_source_inert(code)


def test_digit_leading_title_and_operation_id_get_valid_class_names(tmp_path: Path) -> None:
    result = generate_endpoint_client(_one_op_spec(title="3D Api", operation_id="1st-op"),
                                      "/x", "get", tmp_path)
    assert result["class_name"] == "Api3DApiClient"
    assert result["model_name"] == "Op1stOpResponse"
    assert result["method_name"] == "get_x"  # operationId isn't an identifier -> path fallback


# --- the inert checker itself --------------------------------------------------------------------

@pytest.mark.parametrize("src", [
    "import os\n",
    "from subprocess import run\n",
    "print('x')\n",
    "x = open('f')\n",
    "class A(make_base()):\n    pass\n",
    "class A:\n    x = __import__('os')\n",
    "class A:\n    def f(self):\n        return self.__class__\n",
    "@register()\ndef f():\n    pass\n",
    "Unknown.model_rebuild()\n",
    "a.b = 1\n",
    "if True:\n    pass\n",
])
def test_inert_checker_rejects(src: str) -> None:
    with pytest.raises(AssertionError):
        assert_generated_source_inert(src)


def test_inert_checker_accepts_real_generator_output(tmp_path: Path) -> None:
    recursive = {"type": "object", "properties": {"children": {"type": "array", "items": {"$ref": "#"}}}}
    spec = _one_op_spec()
    spec["paths"]["/x"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] = recursive
    result = generate_endpoint_client(spec, "/x", "get", tmp_path)
    assert ".model_rebuild()" in result["models_code"]  # the one module-level call we allow
    assert_generated_source_inert(result["models_code"])
    assert_generated_source_inert(result["client_code"])


# --- step 5: hostile specs -----------------------------------------------------------------------

# Each injection payload tries to create $CANARY_FILE, so a successful injection is observable.
_CANARY = 'open(__import__("os").environ["CANARY_FILE"],"w")'
PAYLOADS = {
    "triple-quote-newline-import": '"""\nimport os\n' + _CANARY + '\n"""',
    "double-quote-breakout": f'x"); {_CANARY}; ("',
    "single-quote-breakout": f"x'); {_CANARY.replace(chr(34), chr(39))}; ('",
    "backslash-newline": "x\\\n" + _CANARY,
    "null-byte": "x\x00y",
    "unicode-confusables": "\uff53\uff45\uff4c\uff46\u202e\uff02)",  # fullwidth "self", RLO, fullwidth quote
    "stray-braces": "x}{" + _CANARY + "}",
    "digit-leading": "1st-op",
    "keyword-class": "class",
    "keyword-def": "def",
    "name-self": "self",
    "name-body": "body",
    "name-requests": "requests",
}


def _hostile_spec(p: str) -> dict:
    """The payload as title, operationId, path text, param names, enum members and defaults."""
    model = {"type": "object", "properties": {p: {"type": "string", "enum": [p, "ok"], "default": p,
                                                  "description": p}}}
    ok = {"200": {"description": "ok", "content": {"application/json": {"schema": model}}}}
    return {"openapi": "3.0.3", "info": {"title": p, "version": "1"}, "paths": {
        f"/a/{p}/{{id}}": {"get": {
            "operationId": p,
            "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}},
                           {"name": p, "in": "query", "schema": {"type": "string", "enum": [p], "default": p}}],
            "responses": ok}},
        f"/b/{{{p}}}": {"post": {
            "operationId": p + "B",
            "parameters": [{"name": p, "in": "path", "required": True, "schema": {"type": "string"}}],
            "requestBody": {"required": True, "content": {"application/json": {"schema": model}}},
            "responses": ok}},
    }}


# Imports the generated client (exit code 1 if that fails), then calls the method with every
# parameter set ("v", or a stub model for `body`) and Session.send stubbed out, so a payload that
# only runs when the URL/arguments are built is observed too, and the prepared URL can be checked.
# A call that errors for an ordinary reason is reported on stdout, not as a failed exit.
_RUN_GENERATED = r'''
import importlib, inspect, sys
sys.path.insert(0, sys.argv[1])
mod = importlib.import_module("client_pkg.client")
import requests
class _Stop(Exception): pass
def _send(self, request, **k):
    print("URL: " + request.url)
    raise _Stop
requests.sessions.Session.send = _send
class _Arg:
    def model_dump(self, **k): return {}
meth = getattr(getattr(mod, sys.argv[2])("https://example.invalid"), sys.argv[3])
kwargs = {n: (_Arg() if n == "body" else "v") for n in inspect.signature(meth).parameters}
try:
    meth(**kwargs)
except _Stop:
    print("CALL: reached requests")
except Exception as exc:
    print(f"CALL: {type(exc).__name__}")
'''


@pytest.mark.parametrize("payload", list(PAYLOADS.values()), ids=list(PAYLOADS))
def test_hostile_spec_never_executes_spec_text(payload: str, tmp_path: Path) -> None:
    items = list(generate_all_endpoints(_hostile_spec(payload), tmp_path / "gen"))
    assert len(items) == 2
    outcomes = []
    for n, item in enumerate(items):
        op = f"{item['endpoint']['method']} {'/a' if item['endpoint']['path'].startswith('/a') else '/b'}"
        if item["status"] == "skipped":
            assert item["reason"]
            outcomes.append(f"{op}: skipped ({item['reason']})")
            continue
        for name in ("models_code", "client_code"):
            compile(item[name], name, "exec")
            assert_generated_source_inert(item[name])

        root = tmp_path / f"op{n}"
        pkg = root / "client_pkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")
        (pkg / "models.py").write_text(item["models_code"])
        (pkg / "client.py").write_text(item["client_code"])
        canary = tmp_path / f"canary{n}"
        proc = subprocess.run(
            [sys.executable, "-c", _RUN_GENERATED, str(root), item["class_name"], item["method_name"]],
            env={"CANARY_FILE": str(canary), "PATH": "/usr/bin:/bin"},
            capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, proc.stderr[-500:]
        assert not canary.exists(), "spec text executed"
        assert "CALL: reached requests" in proc.stdout, proc.stdout
        url = proc.stdout.split("URL: ", 1)[1].split("\n", 1)[0]
        if op == "GET /a":  # the query parameter carries the payload as its wire name, unchanged
            assert f"?{quote(payload, safe='')}=v" in url, url
        outcomes.append(f"{op}: generated, imported, call reached requests: {url}")
    print(json.dumps({"payload": payload, "outcomes": outcomes}, ensure_ascii=True))
