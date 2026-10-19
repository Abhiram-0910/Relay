"""Hermetic tests for app.params (piece 4c): parameter planning, names, type hints, signature
rendering and wire serialisation. No network."""
from __future__ import annotations

import inspect
import subprocess
import sys
import typing
from pathlib import Path
from typing import Any, Literal

import pytest
import requests

from app import params as P
from app.naming import _safe_identifier
from tests.source_inert import assert_generated_source_inert
from tests.test_generate_hardening import PAYLOADS


def plan(parameters=(), path_item_parameters=(), reserved=(), alias_prefix="Op"):
    return P.plan_parameters({"parameters": list(path_item_parameters)}, {"parameters": list(parameters)},
                             alias_prefix=alias_prefix, sanitise=_safe_identifier, reserved=reserved)


def q(name, schema=None, **kw):
    return {"name": name, "in": "query", "schema": {"type": "string"} if schema is None else schema, **kw}


def one(schema, **kw):
    op = plan([q("v", schema, **kw)])
    assert op.skip_reason is None
    return op.params[0]


# --- locations, merging, skips -------------------------------------------------------------------

def test_path_item_parameters_merge_and_operation_wins() -> None:
    op = plan(
        path_item_parameters=[{"name": "id", "in": "path", "schema": {"type": "integer"}},
                              q("n", {"type": "string"}), q("keep")],
        parameters=[{"name": "X-Trace", "in": "header", "schema": {"type": "string"}},
                    q("n", {"type": "integer"}, required=True)],
    )
    assert [(p.wire_name, p.location, p.py_type, p.required) for p in op.params] == [
        ("id", "path", "int", True), ("n", "query", "int", True), ("keep", "query", "str", False),
        ("X-Trace", "header", "str", False)]


@pytest.mark.parametrize("param,reason", [
    ({"$ref": "#/components/parameters/P"}, P.SKIP_UNRESOLVED_REF),
    ({"in": "query"}, P.SKIP_INVALID_PARAMETER),
    ({"name": "s", "in": "cookie", "required": True}, P.SKIP_REQUIRED_COOKIE),
    ({"name": "f", "in": "formData"}, P.SKIP_UNSUPPORTED_LOCATION),
    ({"name": "j", "in": "query", "required": True, "content": {"application/json": {}}}, P.SKIP_REQUIRED_CONTENT),
    (q("d", {"type": "object"}, style="deepObject"), P.SKIP_UNSUPPORTED_STYLE),
    ({"name": "m", "in": "path", "style": "matrix", "schema": {"type": "string"}}, P.SKIP_UNSUPPORTED_STYLE),
    ({"name": "l", "in": "path", "style": "label", "schema": {"type": "string"}}, P.SKIP_UNSUPPORTED_STYLE),
    ({"name": "h", "in": "header", "style": "form", "schema": {"type": "string"}}, P.SKIP_UNSUPPORTED_STYLE),
    (q("o", {"type": "object", "properties": {"a": {"type": "string"}}}), P.SKIP_OBJECT_PARAMETER),
    ({"name": "H", "in": "header", "schema": {"additionalProperties": True}}, P.SKIP_OBJECT_PARAMETER),
    (q("ao", {"type": "array", "items": {"type": "object"}}), P.SKIP_OBJECT_PARAMETER),
])
def test_operations_that_cannot_be_called_correctly_are_skipped(param, reason) -> None:
    op = plan([q("fine"), param])
    assert op.skip_reason == reason
    assert op.params == []


def test_unresolved_ref_at_path_item_level_also_skips() -> None:
    assert plan(path_item_parameters=[{"$ref": "#/x"}]).skip_reason == P.SKIP_UNRESOLVED_REF


@pytest.mark.parametrize("param,reason", [
    ({"name": "session", "in": "cookie"}, P.IGNORED_OPTIONAL_COOKIE),
    ({"name": "AUTHORIZATION", "in": "header", "required": True}, P.IGNORED_RESERVED_HEADER),
    ({"name": "accept", "in": "header"}, P.IGNORED_RESERVED_HEADER),
    ({"name": "Content-Type", "in": "header"}, P.IGNORED_RESERVED_HEADER),
    ({"name": "j", "in": "query", "content": {"application/json": {}}}, P.IGNORED_OPTIONAL_CONTENT),
])
def test_ignored_parameters_are_recorded_not_dropped_silently(param, reason) -> None:
    op = plan([q("kept"), param])
    assert op.skip_reason is None
    assert [p.wire_name for p in op.params] == ["kept"]
    assert op.ignored == [{"name": param["name"], "in": param["in"], "reason": reason}]


# --- name allocation -----------------------------------------------------------------------------

def _locals(op):
    return [(p.wire_name, p.location, p.local_name) for p in op.params]


@pytest.mark.parametrize("name", ["self", "body", "requests", "response"])
def test_template_locals_are_never_parameter_names(name) -> None:
    assert _locals(plan([q(name)])) == [(name, "query", f"{name}_query")]


def test_path_and_query_sharing_a_name() -> None:
    op = plan([q("id"), {"name": "id", "in": "path", "schema": {"type": "integer"}},
               {"name": "id", "in": "header", "schema": {"type": "string"}}, q("id_query")])
    assert _locals(op) == [("id", "path", "id"), ("id", "query", "id_query"),
                           ("id_query", "query", "id_query_query"), ("id", "header", "id_header")]


def test_literal_param_0_next_to_a_sanitised_name() -> None:
    op = plan([q("page-size"), q("param_0")])
    assert _locals(op) == [("page-size", "query", "param_0"), ("param_0", "query", "param_0_query")]


def test_numeric_suffix_after_location_suffix() -> None:
    op = plan([q("a"), q("a_query"), q("a")])  # third is a duplicate (name, in): operation-level wins
    assert [p.local_name for p in op.params] == ["a", "a_query"]
    op = plan([{"name": "a", "in": "path", "schema": {}}, q("a"), q("a_query"),
               {"name": "a", "in": "header", "schema": {}}, {"name": "a_header", "in": "header", "schema": {}}])
    assert [p.local_name for p in op.params] == ["a", "a_query", "a_query_query", "a_header", "a_header_header"]


@pytest.mark.parametrize("name,expected", [
    ("class", "param_0"), ("def", "param_0"), ("None", "param_0"),
    ("a-b", "param_0"), ("x[y]", "param_0"), ("1st", "param_0"),
    ("__x", "p__x"), ("__import__", "p__import__"), ("_relay_query_string", "p_relay_query_string"),
])
def test_keywords_non_identifiers_and_reserved_prefixes(name, expected) -> None:
    assert _locals(plan([q(name)])) == [(name, "query", expected)]


def test_caller_reserved_names_are_avoided() -> None:
    assert _locals(plan([q("GetXResponse")], reserved={"GetXResponse"})) == [
        ("GetXResponse", "query", "GetXResponse_query")]


def test_allocation_is_deterministic_and_collision_free_for_hostile_names() -> None:
    names = [*PAYLOADS.values(), "self", "self", "id", "param_0", "param_1", "class"]
    params = [q(n) for n in names] + [{"name": n, "in": "header", "schema": {}} for n in names]
    a, b = plan(params), plan(params)
    assert _locals(a) == _locals(b)
    local_names = [p.local_name for p in a.params]
    assert len(local_names) == len(set(local_names))
    reserved = P.TEMPLATE_LOCALS | set(__import__("keyword").kwlist)
    assert not any(n in reserved or n.startswith(("__", P.RESERVED_PREFIX)) for n in local_names)
    assert {(p.wire_name, p.location) for p in a.params} == {(n, loc) for n in names for loc in ("query", "header")}


# --- types ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("schema,py_type,lossy", [
    ({"type": "string"}, "str", ()),
    ({"type": "integer"}, "int", ()),
    ({"type": "number"}, "float", ()),
    ({"type": "boolean"}, "bool", ()),
    ({"type": "string", "format": "date-time"}, "str", ()),
    ({"type": "integer", "nullable": True}, "int | None", ()),
    ({"type": ["integer", "null"]}, "int | None", ()),
    ({"type": ["integer", "string"]}, "int | str", ()),
    ({"type": ["integer", "string", "null"]}, "int | str | None", ()),
    ({"type": "string", "enum": ["a", "b"]}, "Literal['a', 'b']", ()),
    ({"type": "integer", "enum": [1, 2]}, "Literal[1, 2]", ()),
    ({"type": "boolean", "enum": [True]}, "Literal[True]", ()),
    ({"type": "number", "enum": [0.5, 1.5]}, "Literal[0.5, 1.5]", ()),
    ({"type": "string", "enum": ["a", None]}, "Literal['a'] | None", ()),
    ({"enum": ['x"]; import os', "y'"]}, "Literal['x\"]; import os', \"y'\"]", ()),
    ({"type": "string", "enum": [1, "a"]}, "str", (P.LOSSY_ENUM_NOT_LITERAL,)),
    ({"type": "number", "enum": [1.0, float("nan")]}, "float", (P.LOSSY_ENUM_NOT_LITERAL,)),
    ({"type": "string", "enum": [{"a": 1}]}, "str", (P.LOSSY_ENUM_NOT_LITERAL,)),
    ({"type": "string", "enum": [f"m{i}" for i in range(51)]}, "str", (P.LOSSY_ENUM_NOT_LITERAL,)),
    ({"enum": [1, "a"]}, "Any", (P.LOSSY_ENUM_NOT_LITERAL, P.LOSSY_TYPE_MISSING)),
    ({"type": "array", "items": {"type": "integer"}}, "list[int]", ()),
    ({"type": "array", "items": {"type": "string", "enum": ["a"]}}, "list[Literal['a']]", ()),
    ({"type": "array", "items": {"type": "integer", "nullable": True}}, "list[int | None]", ()),
    ({"type": "array"}, "list[Any]", (P.LOSSY_ARRAY_ITEMS_UNSPECIFIED,)),
    ({"type": "array", "items": {"type": "array", "items": {"type": "integer"}}}, "list[list[int]]", ()),
    ({"type": "array", "items": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}}},
     "list[list[Any]]", (P.LOSSY_ARRAY_DEPTH_EXCEEDED,)),
    ({"oneOf": [{"type": "integer"}, {"type": "string"}]}, "int | str", ()),
    ({"anyOf": [{"type": "integer"}, {"type": "null"}]}, "int | None", ()),
    ({"oneOf": [{"type": "string", "enum": ["a"]}, {"type": "integer"}]}, "Literal['a'] | int", ()),
    ({"oneOf": [{"type": "integer"}, {"type": "array", "items": {"type": "integer"}}]}, "Any",
     (P.LOSSY_COMPOSED_SCHEMA,)),
    ({"anyOf": [{"type": "integer"}, {}]}, "Any", (P.LOSSY_COMPOSED_SCHEMA,)),
    ({"allOf": [{"type": "integer"}]}, "int", ()),
    ({"allOf": [{"type": "integer"}, {"minimum": 1}]}, "Any", (P.LOSSY_COMPOSED_SCHEMA,)),
    ({}, "Any", (P.LOSSY_TYPE_MISSING,)),
    ({"type": "file"}, "Any", (P.LOSSY_TYPE_UNKNOWN,)),
    ({"$ref": "#/components/schemas/X"}, "Any", (P.LOSSY_UNRESOLVED_REF_SCHEMA,)),
    ({"type": "null"}, "None", ()),
])
def test_type_hints(schema, py_type, lossy) -> None:
    p = one(schema)
    assert (p.py_type, p.lossy) == (py_type, lossy)


def test_allow_reserved_is_recorded_as_lossy() -> None:
    assert one({"type": "string"}, allowReserved=True).lossy == (P.LOSSY_ALLOW_RESERVED_IGNORED,)


def test_long_literals_become_module_level_aliases() -> None:
    members = [f"temperature_{i}m" for i in range(12)]
    op = plan([q("hourly", {"type": "array", "items": {"type": "string", "enum": members}}),
               q("unit", {"type": "string", "enum": [*members, None]}),
               q("Unit", {"type": "string", "enum": members})], alias_prefix="GetForecast")
    assert [p.py_type for p in op.params] == [
        "list[GetForecastHourlyValue]", "GetForecastUnitValue | None", "GetForecastUnitValue2"]
    literal = "Literal[" + ", ".join(repr(m) for m in members) + "]"
    assert op.aliases == {"GetForecastHourlyValue": literal, "GetForecastUnitValue": literal,
                          "GetForecastUnitValue2": literal}
    assert op.typing_imports == {"Literal"}


def test_lossy_list_and_jsonable_contract() -> None:
    op = plan([{"name": "id", "in": "path", "schema": {"type": "integer"}}, q("raw", {})])
    assert op.lossy == [{"name": "raw", "in": "query", "reason": P.LOSSY_TYPE_MISSING}]
    assert op.typing_imports == {"Any"}
    assert op.to_jsonable() == [
        {"wire_name": "id", "local_name": "id", "location": "path", "required": True, "py_type": "int", "lossy": []},
        {"wire_name": "raw", "local_name": "raw", "location": "query", "required": False, "py_type": "Any",
         "lossy": [P.LOSSY_TYPE_MISSING]},
    ]


def test_spec_defaults_descriptions_and_examples_are_never_rendered() -> None:
    marker = "SPEC_TEXT_MARKER"
    op = plan([q("v", {"type": "string", "default": marker, "example": marker, "description": marker},
                 description=marker, example=marker)])
    query_src, header_src = P.render_wire_args(op)
    assert marker not in P.render_signature(op, None) + query_src + header_src + repr(op.aliases)


# --- serialisation modes -------------------------------------------------------------------------

@pytest.mark.parametrize("param,mode", [
    (q("s"), "scalar"),
    (q("a", {"type": "array", "items": {"type": "string"}}), "repeat"),
    (q("a", {"type": "array", "items": {"type": "string"}}, explode=False), "comma"),
    (q("a", {"type": "array", "items": {"type": "string"}}, style="spaceDelimited"), "space"),
    (q("a", {"type": "array", "items": {"type": "string"}}, style="pipeDelimited"), "pipe"),
    (q("a", {"type": "array", "items": {"type": "string"}}, style="pipeDelimited", explode=True), "repeat"),
    ({"name": "H", "in": "header", "schema": {"type": "array", "items": {"type": "string"}}}, "comma"),
    ({"name": "p", "in": "path", "schema": {"type": "array", "items": {"type": "string"}}}, "comma"),
])
def test_serialisation_mode_follows_openapi_style_and_explode(param, mode) -> None:
    assert plan([param]).params[0].serialisation == mode


# --- assembled modules: signature, wire, inertness -----------------------------------------------

class Model:
    def model_dump(self, **kwargs):
        return {}


def _assemble(op, body=None) -> str:
    query_src, header_src = P.render_wire_args(op)
    path_src = "[" + ", ".join(f"_relay_path_value({p.local_name})" for p in op.params if p.location == "path") + "]"
    head = '"""Assembled test module."""\nfrom __future__ import annotations\n\n'
    if op.typing_imports:
        head += f"from typing import {', '.join(sorted(op.typing_imports))}\n"
    aliases = "".join(f"{name} = {literal}\n" for name, literal in op.aliases.items())
    return (head + P.SERIALISER_SOURCE + "\n" + aliases + "\n\nclass Client:\n"
            f"    def call(self{P.render_signature(op, body)}):\n"
            f"        return {path_src}, _relay_query_string({query_src}), _relay_header_dict({header_src})\n")


def _load(src: str) -> dict:
    assert_generated_source_inert(src)
    ns: dict = {}
    exec(compile(src, "assembled.py", "exec"), ns)
    return ns


def _sig_op():
    return plan([
        {"name": "id", "in": "path", "schema": {"type": "integer"}},
        q("q", required=True),
        q("nul", {"type": "integer", "nullable": True}, required=True),
        q("opt", {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}}),
        {"name": "X-Any", "in": "header", "schema": {}},
    ])


def test_signature_shape_and_type_hints() -> None:
    op = _sig_op()
    rendered = P.render_signature(op, ("Model", True))
    assert rendered == (", id: int, body: Model, *, q: str, nul: int | None, "
                        "opt: list[Literal['a', 'b']] | None = None, param_4: Any | None = None")
    ns = _load(_assemble(op, ("Model", True)))
    sig = inspect.signature(ns["Client"].call)
    kinds = {n: (p.kind.name, p.default is inspect.Parameter.empty) for n, p in sig.parameters.items()}
    assert kinds == {"self": ("POSITIONAL_OR_KEYWORD", True), "id": ("POSITIONAL_OR_KEYWORD", True),
                     "body": ("POSITIONAL_OR_KEYWORD", True), "q": ("KEYWORD_ONLY", True),
                     "nul": ("KEYWORD_ONLY", True), "opt": ("KEYWORD_ONLY", False),
                     "param_4": ("KEYWORD_ONLY", False)}
    hints = typing.get_type_hints(ns["Client"].call, globalns=ns, localns={"Model": Model})
    assert hints == {"id": int, "body": Model, "q": str, "nul": int | None,
                     "opt": list[Literal["a", "b"]] | None, "param_4": Any | None}


def test_signature_without_keyword_only_params_has_no_star() -> None:
    op = plan([{"name": "id", "in": "path", "schema": {"type": "integer"}}])
    assert P.render_signature(op, None) == ", id: int"
    assert P.render_signature(op, ("Model", True)) == ", id: int, body: Model"
    assert P.render_signature(op, ("Model", False)) == ", id: int, *, body: Model | None = None"
    assert P.render_signature(plan(), None) == ""


def test_alias_module_resolves_hints() -> None:
    members = [f"temperature_{i}m" for i in range(12)]
    op = plan([q("hourly", {"type": "array", "items": {"type": "string", "enum": members}})], alias_prefix="F")
    ns = _load(_assemble(op))
    hints = typing.get_type_hints(ns["Client"].call, globalns=ns)
    assert hints["hourly"] == list[Literal[tuple(members)]] | None


def _wire(op, **kwargs):
    path, query, headers = _load(_assemble(op))["Client"]().call(**kwargs)
    url = requests.Request("GET", "https://api.example.test/x", params=query).prepare().url
    return path, url, headers


def _arr(name, **kw):
    return q(name, {"type": "array", "items": {"type": "string"}}, **kw)


def test_wire_booleans_and_none() -> None:
    op = plan([q("t", {"type": "boolean"}), q("f", {"type": "boolean"}), q("n"),
               q("bs", {"type": "array", "items": {"type": "boolean"}})])
    assert _wire(op, t=True, f=False, bs=[True, False])[1] == "https://api.example.test/x?t=true&f=false&bs=true&bs=false"


def test_wire_list_modes() -> None:
    op = plan([_arr("r"), _arr("c", explode=False), _arr("s", style="spaceDelimited"),
               _arr("p", style="pipeDelimited")])
    v = ["a", "b"]
    assert _wire(op, r=v, c=v, s=v, p=v)[1] == "https://api.example.test/x?r=a&r=b&c=a,b&s=a%20b&p=a%7Cb"


def test_wire_comma_inside_a_value_is_encoded_but_the_separator_is_not() -> None:
    op = plan([_arr("c", explode=False), q("one")])
    assert _wire(op, c=["a,b", "c"], one="x,y")[1] == "https://api.example.test/x?c=a%2Cb,c&one=x%2Cy"


def test_wire_special_characters_are_quoted() -> None:
    op = plan([q("page[size]"), q("v")])
    url = _wire(op, param_0="10", v="a b&c=d/é?#")[1]
    assert url == "https://api.example.test/x?page%5Bsize%5D=10&v=a%20b%26c%3Dd%2F%C3%A9%3F%23"


def test_wire_headers_and_path_values() -> None:
    op = plan([{"name": "id", "in": "path", "schema": {"type": "string"}},
               {"name": "ids", "in": "path", "schema": {"type": "array", "items": {"type": "integer"}}},
               {"name": "X-List", "in": "header", "schema": {"type": "array", "items": {"type": "string"}}},
               {"name": "X-Flag", "in": "header", "schema": {"type": "boolean"}},
               {"name": "X-None", "in": "header", "schema": {"type": "string"}}])
    path, url, headers = _wire(op, id="a/b c", ids=[1, 2], param_2=["a", "b"], param_3=True)
    assert path == ["a%2Fb%20c", "1,2"]
    assert url == "https://api.example.test/x"
    assert headers == {"X-List": "a,b", "X-Flag": "true"}


# --- hostile names and enum members --------------------------------------------------------------

_RUN = r'''
import importlib.util, inspect, sys
spec = importlib.util.spec_from_file_location("assembled", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
call = mod.Client().call
kwargs = {n: "v" for n, p in inspect.signature(call).parameters.items() if p.default is p.empty}
call(**kwargs)
'''


@pytest.mark.parametrize("payload", list(PAYLOADS.values()), ids=list(PAYLOADS))
def test_hostile_names_and_enum_members_stay_inert(payload: str, tmp_path: Path) -> None:
    long_enum = [payload, *(f"filler_value_{i}" for i in range(8))]
    op = plan([
        {"name": payload, "in": "path", "schema": {"type": "string", "enum": [payload]}},
        q(payload, {"type": "string", "enum": [payload, "ok"]}, required=True),
        q(payload + "2", {"type": "array", "items": {"type": "string", "enum": long_enum}}),
        {"name": payload, "in": "header", "schema": {"type": "string", "enum": [payload]}},
    ], alias_prefix="Op")
    assert op.skip_reason is None and len(op.params) == 4
    assert op.aliases  # the long enum went through a module-level alias
    src = _assemble(op)
    compile(src, "assembled.py", "exec")
    assert_generated_source_inert(src)
    module = tmp_path / "assembled.py"
    module.write_text(src)
    canary = tmp_path / "canary"
    proc = subprocess.run([sys.executable, "-c", _RUN, str(module)], capture_output=True, text=True,
                          env={"CANARY_FILE": str(canary), "PATH": "/usr/bin:/bin"}, timeout=60)
    assert proc.returncode == 0, proc.stderr[-500:]
    assert not canary.exists()
