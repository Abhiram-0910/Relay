"""Execution check for generated corpus clients (piece 4f). Runs INSIDE the relay-sandbox image with
--network none; uses only the stdlib and the image's own `requests` (it imports nothing from relay).

For each operation: import the generated client, check its signature against the generator's
parameter table, call the method with dummy values for REQUIRED parameters only while
requests.Session.send is patched to capture the PreparedRequest (nothing is sent), then check the
request: no exception, URL under the base URL, no `{`/`}` left in the path, every required header
present. Operations whose request body is required are skipped (no dummy body).

Usage: python corpus_check.py <ops.json>   (prints one `CORPUS:` JSON line per operation)
"""
from __future__ import annotations

import importlib
import inspect
import json
import sys
import types
import typing
from urllib.parse import unquote, urlsplit

import requests

PREFIX = "CORPUS:"
_SCALAR_DUMMIES = {int: 1, float: 1.0, bool: True, str: "x"}


class _Captured(Exception):
    pass


def dummy(tp):
    """A dummy value for a type hint: int 1, float 1.0, bool True, str/Any "x", Literal -> its first
    member, list[T] -> [dummy T], X | None -> dummy X."""
    if tp is typing.Any:
        return "x"
    if tp is type(None):
        return None
    origin = typing.get_origin(tp)
    if origin is typing.Literal:
        return typing.get_args(tp)[0]
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        return dummy(args[0]) if args else None
    if origin is list or tp is list:
        args = typing.get_args(tp)
        return [dummy(args[0]) if args else "x"]
    return _SCALAR_DUMMIES.get(tp, "x")


def _fail(error: str, detail: str = "") -> dict:
    return {"ok": False, "error": error, "detail": repr(detail)[:200]}


def check_operation(client, op: dict, base_url: str) -> dict:
    method = getattr(client, op["method_name"])
    sig = inspect.signature(method)
    problems = []
    for p in op["params"]:
        param = sig.parameters.get(p["local_name"])
        if param is None:
            problems.append(f"no parameter {p['local_name']}")
        elif p["location"] == "path" and (param.kind is not param.POSITIONAL_OR_KEYWORD
                                          or param.default is not param.empty):
            problems.append(f"path parameter {p['local_name']} is not positional without a default")
        elif p["location"] != "path" and p["required"] and (param.kind is not param.KEYWORD_ONLY
                                                             or param.default is not param.empty):
            problems.append(f"required {p['location']} parameter {p['local_name']} is not keyword-only without a default")
    if problems:
        return _fail("SignatureMismatch", "; ".join(problems))
    body = sig.parameters.get("body")
    if body is not None and body.default is body.empty:
        return {"ok": None, "skipped": "required_body"}

    hints = typing.get_type_hints(method)
    kwargs = {p["local_name"]: dummy(hints.get(p["local_name"], typing.Any)) for p in op["params"] if p["required"]}

    def send(self, request, **_kw):
        raise _Captured(request)

    original = requests.sessions.Session.send
    requests.sessions.Session.send = send
    try:
        method(**kwargs)
        return _fail("NoRequestSent")
    except _Captured as captured:
        prepared = captured.args[0]
    except Exception as exc:
        return _fail(type(exc).__name__, str(exc))
    finally:
        requests.sessions.Session.send = original

    url = prepared.url
    if not (url == base_url or url.startswith(base_url + "/") or url.startswith(base_url + "?")):
        return _fail("WrongBaseURL", url)
    path = unquote(urlsplit(url).path)  # requests percent-encodes a literal "{" as %7B
    if "{" in path or "}" in path:
        return _fail("UnfilledPathTemplate", path)
    missing = [p["wire_name"] for p in op["params"]
               if p["location"] == "header" and p["required"] and p["wire_name"] not in prepared.headers]
    if missing:
        return _fail("MissingRequiredHeader", ", ".join(missing))
    return {"ok": True}


def check_package(root: str, op: dict, base_url: str) -> dict:
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        module = importlib.import_module(f"{op['package']}.client")
        client = getattr(module, op["class_name"])(base_url)
        return check_operation(client, op, base_url)
    except Exception as exc:
        return _fail(type(exc).__name__, str(exc))


def main(ops_path: str, only: int | None = None) -> int:
    """Each operation is checked in a fresh interpreter (`--op N`): every generated models.py is a
    standalone copy of the schema graph, and importing many large ones into one process exhausts the
    container's memory. A crash or kill then costs only that operation, reported, never a pass."""
    import subprocess

    with open(ops_path) as f:
        job = json.load(f)
    if only is not None:
        op = job["ops"][only]
        print(PREFIX + json.dumps({"op_id": op["op_id"], **check_package(job["root"], op, job["base_url"])}), flush=True)
        return 0
    for i, op in enumerate(job["ops"]):
        try:
            proc = subprocess.run([sys.executable, __file__, ops_path, "--op", str(i)],
                                  capture_output=True, text=True, timeout=60)
            line = next((l for l in proc.stdout.splitlines() if l.startswith(PREFIX)), None)
            result = line or PREFIX + json.dumps({"op_id": op["op_id"], **_fail(
                "NoCheckerResult", f"exit {proc.returncode}: {proc.stderr[-150:]}")})
        except subprocess.TimeoutExpired:
            result = PREFIX + json.dumps({"op_id": op["op_id"], **_fail("CheckTimeout", "60 s")})
        print(result, flush=True)
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    sys.exit(main(args[0], int(args[2]) if len(args) == 3 and args[1] == "--op" else None))
