"""Hermetic tests for app.synthesis (piece 5a): the live-call gates and the value tiers."""
from __future__ import annotations

import pytest

from app.naming import _safe_identifier
from app.params import plan_parameters
from app.synthesis import plan_live_call, possibly_state_changing, requires_auth


def _plan(parameters: list[dict], *, path: str = "/x", method: str = "get", operation: dict | None = None,
          spec_security=None, path_item_parameters: list[dict] = (), presets: dict | None = None):
    operation = {"parameters": parameters, **(operation or {})}
    path_item = {method: operation, "parameters": list(path_item_parameters)}
    spec = {"paths": {path: path_item}}
    if spec_security is not None:
        spec["security"] = spec_security
    table = plan_parameters(path_item, operation, alias_prefix="Op", sanitise=_safe_identifier).to_jsonable()
    return plan_live_call(spec, path, method, table, presets)


def _q(schema: dict | None = None, name: str = "p", **extra) -> dict:
    param = {"name": name, "in": "query", "required": True, **extra}
    if schema is not None:
        param["schema"] = schema
    return param


def _one(param: dict):
    plan = _plan([param])
    return plan.kwargs.get(param["name"]), plan.value_sources.get(param["name"]), plan.skip_reason


# --- gates, in order ----------------------------------------------------------------------------

@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_not_get(method) -> None:
    assert _plan([], method=method).skip_reason == "not_get"


def test_not_get_wins_over_every_later_gate() -> None:
    plan = _plan([_q({"type": "string"})], path="/users/{id}/remove", method="delete",
                 operation={"security": [{"key": []}]})
    assert plan.skip_reason == "not_get"


@pytest.mark.parametrize("spec_security, op_security, expected", [
    (None, None, False),                                  # no security anywhere
    ([{"key": []}], None, True),                          # global applies
    ([{"key": []}], [], False),                           # operation `security: []` overrides to none
    (None, [{"key": []}], True),                          # operation-level only
    ([{"key": []}], [{}, {"key": []}], False),            # {} alternative: auth optional
    ([{}], None, False),                                  # global {} only
    ([{"key": []}], [{"oauth": ["read"]}], True),         # override to another scheme still requires
    ([], None, False),                                    # global empty list
])
def test_requires_auth_rules(spec_security, op_security, expected) -> None:
    spec = {} if spec_security is None else {"security": spec_security}
    operation = {} if op_security is None else {"security": op_security}
    assert requires_auth(spec, operation) is expected


def test_requires_auth_gate_beats_state_changing_and_values() -> None:
    plan = _plan([_q({"type": "string"})], path="/logout", spec_security=[{"key": []}])
    assert plan.skip_reason == "requires_auth"


@pytest.mark.parametrize("path, operation_id, expected", [
    ("/user/logout", None, True),
    ("/x", "deleteUser", True),
    ("/users/{id}/remove", None, True),
    ("/sessions/revoke-all", None, True),
    ("/x", "Cancel_Order", True),
    ("/x", "signOut", True),                  # camelCase pair joined
    ("/x", "DELETEUser", True),               # acronym boundary
    ("/x", "getPetByStatus", False),
    ("/pet/findByStatus", None, False),
    ("/x", "presetValues", False),            # "reset" inside another word
    ("/clearance/levels", None, False),       # "clear" inside another word
    ("/x", "getUnsubscribedCount", False),    # token is "unsubscribed", not "unsubscribe"
    ("/x", None, False),
    ("/x", 42, False),                        # malformed operationId ignored
])
def test_state_changing_denylist(path, operation_id, expected) -> None:
    assert possibly_state_changing(path, operation_id) is expected


def test_state_changing_gate_beats_no_sample_value() -> None:
    assert _plan([_q({"type": "string"})], path="/cache/purge").skip_reason == "possibly_state_changing"


def test_no_sample_value_names_the_sanitised_local_name_only() -> None:
    plan = _plan([_q({"type": "string"}, name="page-size")])
    assert plan.skip_reason == "no_sample_value:param_0"
    assert plan.kwargs == {}


def test_no_type_dummy_is_ever_invented() -> None:
    for schema in ({"type": "string"}, {"type": "integer"}, {"type": "number"}, {"type": "boolean"},
                   {"type": "array", "items": {"type": "string"}}, {}):
        assert _one(_q(schema))[2] == "no_sample_value:p", schema


def test_optional_params_are_omitted_and_need_no_value() -> None:
    plan = _plan([{"name": "opt", "in": "query", "schema": {"type": "string"}}])
    assert (plan.kwargs, plan.value_sources, plan.skip_reason) == ({}, {}, None)


# --- tiers, in order ----------------------------------------------------------------------------

_ALL_TIERS = {"example": "e", "examples": {"a": {"externalValue": "http://x"}, "b": {"value": "x"}},
              "schema": {"type": "string", "example": "s", "default": "d", "const": "c", "enum": [None, "n"]}}


@pytest.mark.parametrize("drop, expected", [
    ((), ("e", "example")),
    (("example",), ("x", "examples")),
    (("example", "examples"), ("s", "schema_example")),
])
def test_param_level_tiers_in_order(drop, expected) -> None:
    param = _q(**{k: v for k, v in _ALL_TIERS.items() if k not in drop})
    assert _one(param)[:2] == expected


@pytest.mark.parametrize("keep, expected", [
    (("default", "const", "enum"), ("d", "default")),
    (("const", "enum"), ("c", "const")),
    (("enum",), ("n", "enum")),
])
def test_schema_tiers_in_order(keep, expected) -> None:
    schema = {"type": "string", **{k: _ALL_TIERS["schema"][k] for k in keep}}
    assert _one(_q(schema))[:2] == expected


def test_examples_skip_external_value_only_entries() -> None:
    param = _q({"type": "string"}, examples={"ext": {"externalValue": "http://e"}})
    assert _one(param)[2] == "no_sample_value:p"


def test_31_schema_examples_list_counts_as_schema_example() -> None:
    assert _one(_q({"type": "string", "examples": ["v"]}))[:2] == ("v", "schema_example")


@pytest.mark.parametrize("schema, expected", [
    ({"type": "integer", "minimum": 1, "maximum": 10}, 5),            # midpoint, floored
    ({"type": "number", "minimum": 1, "maximum": 2}, 1.5),
    ({"type": "integer", "minimum": 3}, 3),
    ({"type": "integer", "maximum": 7}, 7),
    ({"type": "number", "minimum": -90, "maximum": 90}, 0.0),
    ({"type": "integer", "minimum": 0, "exclusiveMinimum": True, "maximum": 2}, 1),   # 3.0 flag
    ({"type": "integer", "exclusiveMinimum": 0, "exclusiveMaximum": 3}, 1),          # 3.1 numeric
    ({"type": "integer", "exclusiveMaximum": 5}, 4),
    ({"type": "integer", "minimum": 1.5, "maximum": 3.5}, 2),         # non-integer bounds on an integer
])
def test_constraint_tier(schema, expected) -> None:
    value, label, skip = _one(_q(schema))
    assert (value, label, skip) == (expected, "constraint", None)
    assert type(value) is type(expected)


@pytest.mark.parametrize("schema", [
    {"type": "number", "exclusiveMinimum": 0},                         # exclusive on a number: unusable
    {"type": "number", "minimum": 0, "exclusiveMinimum": True},
    {"type": "integer", "exclusiveMinimum": 1, "exclusiveMaximum": 2},  # empty integer range
    {"type": "integer", "minimum": 5, "maximum": 1},
    {"type": "string", "minimum": 1, "maximum": 3},                    # numeric only
    {"type": "integer", "minimum": True},                              # bool is not a bound
    {"type": "number", "minimum": float("inf")},
])
def test_constraint_unusable(schema) -> None:
    assert _one(_q(schema))[2] == "no_sample_value:p"


# --- type checks --------------------------------------------------------------------------------

@pytest.mark.parametrize("schema, example, ok", [
    ({"type": "integer"}, 3, True),
    ({"type": "integer"}, True, False),        # True is not an int
    ({"type": "integer"}, 3.0, False),
    ({"type": "integer"}, "3", False),
    ({"type": "number"}, 3, True),
    ({"type": "number"}, 2.5, True),
    ({"type": "number"}, False, False),
    ({"type": "number"}, float("nan"), False),
    ({"type": "boolean"}, True, True),
    ({"type": "boolean"}, 1, False),
    ({"type": "boolean"}, "true", False),
    ({"type": "string"}, "s", True),
    ({"type": "string"}, 5, False),
    ({"type": ["string", "null"]}, "s", True),   # 3.1 type list
    ({"type": ["string", "null"]}, None, False),
])
def test_example_type_check(schema, example, ok) -> None:
    value, label, skip = _one(_q(schema, example=example))
    assert (skip is None) is ok
    if ok:
        assert (value, label) == (example, "example")


def test_type_mismatching_example_falls_through_to_the_next_tier() -> None:
    assert _one(_q({"type": "integer", "default": 7}, example="seven"))[:3] == (7, "default", None)


def test_rejected_tier_is_recorded_only_when_nothing_later_wins() -> None:
    plan = _plan([_q({"type": "integer"}, example="seven")])
    assert plan.skip_reason == "no_sample_value:p"
    assert plan.value_sources == {"p": "example_rejected"}


@pytest.mark.parametrize("bad", ["a\nb", "tab\there", "nul\x00", "del\x7f", "c1\x85", "x" * 257])
def test_control_characters_and_oversized_candidates_are_rejected(bad) -> None:
    assert _one(_q({"type": "string"}, example=bad))[2] == "no_sample_value:p"
    assert _one(_q({"type": "string", "default": "ok"}, example=bad))[:2] == ("ok", "default")


def test_256_chars_is_allowed() -> None:
    assert _one(_q({"type": "string"}, example="x" * 256))[0] == "x" * 256


# --- arrays -------------------------------------------------------------------------------------

@pytest.mark.parametrize("param, expected", [
    (_q({"type": "array", "items": {"type": "string"}}, example=["a", "b"]), (["a"], "example")),
    (_q({"type": "array", "items": {"type": "string"}}, example="a"), (["a"], "example")),
    (_q({"type": "array", "items": {"type": "string", "enum": ["t1", "t2"]}}), (["t1"], "enum")),
    (_q({"type": "array", "items": {"type": "integer", "minimum": 2, "maximum": 4}}), ([3], "constraint")),
    (_q({"type": "array", "items": {"type": "integer"}, "default": [9]}), ([9], "default")),
])
def test_array_params_take_a_one_element_list(param, expected) -> None:
    assert _one(param)[:2] == expected


def test_array_items_are_type_checked() -> None:
    param = _q({"type": "array", "items": {"type": "integer", "enum": [5]}}, example=["x"])
    assert _one(param)[:2] == ([5], "enum")
    assert _one(_q({"type": "array", "items": {"type": "integer"}}, example=[]))[2] == "no_sample_value:p"


# --- hostile values only ever become kwargs -----------------------------------------------------

@pytest.mark.parametrize("hostile", ['"; import os; os.system("x") #', "{__import__('os')}", "'''",
                                     "${{ secrets.X }}", "__import__('os').system('id')"])
def test_hostile_example_is_a_value_never_source(hostile, tmp_path) -> None:
    from app.generate import generate_endpoint_client
    from tests.source_inert import assert_generated_source_inert

    operation = {"operationId": "getX", "parameters": [_q({"type": "string"}, name="q", example=hostile)],
                 "responses": {"200": {"description": "ok", "content": {"application/json": {
                     "schema": {"type": "object", "properties": {"id": {"type": "integer"}}}}}}}}
    spec = {"openapi": "3.0.3", "info": {"title": "t", "version": "1"}, "paths": {"/x": {"get": operation}}}
    item = generate_endpoint_client(spec, "/x", "get", tmp_path)
    plan = plan_live_call(spec, "/x", "get", item["params"])
    assert plan.kwargs == {"q": hostile} and plan.value_sources == {"q": "example"}
    for code in (item["client_code"], item["models_code"]):
        assert hostile not in code
        assert_generated_source_inert(code)


# --- keyed by local name, presets, determinism --------------------------------------------------

def test_kwargs_are_keyed_by_local_name_across_locations() -> None:
    plan = _plan([
        {"name": "id", "in": "query", "required": True, "example": "q"},
        {"name": "page-size", "in": "query", "required": True, "example": 10},
        {"name": "X-Req", "in": "header", "required": True, "example": "h"},
        {"name": "session", "in": "cookie", "required": False, "example": "c"},
    ], path="/x/{id}", path_item_parameters=[{"name": "id", "in": "path", "required": True, "example": 7}])
    assert plan.kwargs == {"id": 7, "id_query": "q", "param_2": 10, "param_3": "h"}
    assert set(plan.value_sources.values()) == {"example"}


def test_presets_fill_only_what_no_tier_fills() -> None:
    params = [_q({"type": "string"}, name="latitude"), _q({"type": "string"}, name="longitude", example="1")]
    plan = _plan(params, presets={"latitude": "52.52", "longitude": "13.41"})
    assert plan.kwargs == {"latitude": "52.52", "longitude": "1"}
    assert plan.value_sources == {"latitude": "demo_preset", "longitude": "example"}


def test_presets_never_bypass_a_gate() -> None:
    plan = _plan([_q({"type": "string"}, name="latitude")], spec_security=[{"k": []}],
                 presets={"latitude": "52.52"})
    assert plan.skip_reason == "requires_auth"


def test_same_input_same_plan() -> None:
    params = [_q({"type": "integer", "minimum": 1, "maximum": 9}, name="a"),
              _q({"type": "string", "enum": ["x", "y"]}, name="b")]
    plans = [_plan(params) for _ in range(3)]
    assert all(p == plans[0] for p in plans)
    assert plans[0].kwargs == {"a": 5, "b": "x"}


# --- integration through ci_runner (sandbox stubbed) --------------------------------------------

def _get(params: list[dict] | None = None, **extra) -> dict:
    return {"parameters": params or [], **extra, "responses": {"200": {"description": "ok", "content": {
        "application/json": {"schema": {"type": "object", "properties": {"id": {"type": "integer"}}}}}}}}


# One operation per gate, in spec order; the budget is set to 3 so the 4th live-eligible GET is cut.
GATES_SPEC = {
    "openapi": "3.0.3", "info": {"title": "gates", "version": "1"},
    "servers": [{"url": "https://api.example.com"}],
    "components": {"securitySchemes": {"key": {"type": "apiKey", "in": "header", "name": "X-Key"}}},
    "paths": {
        "/items": {"get": _get(), "post": _get()},
        "/items/{item-id}": {"get": _get([{"name": "item-id", "in": "path", "required": True,
                                           "schema": {"type": "integer", "minimum": 1, "maximum": 100}}])},
        "/search": {"get": _get([{"name": "q", "in": "query", "required": True, "example": "relay",
                                  "schema": {"type": "string"}},
                                 {"name": "X-Lang", "in": "header", "required": True,
                                  "schema": {"type": "string", "enum": ["en", "de"]}}])},
        "/private": {"get": _get(security=[{"key": []}])},
        "/session/logout": {"get": _get()},
        "/thing": {"get": _get([{"name": "token", "in": "query", "required": True, "schema": {"type": "string"}}])},
        "/elsewhere": {"get": _get(servers=[{"url": "ftp://files.example"}])},
        "/public": {"get": _get(security=[{}, {"key": []}])},
    },
}
GATES_EXPECTED = [
    ("GET", "/items", "verified_pass", None),
    ("POST", "/items", "generated_only", "not_get"),
    ("GET", "/items/{item-id}", "verified_pass", None),
    ("GET", "/search", "verified_pass", None),
    ("GET", "/private", "generated_only", "requires_auth"),
    ("GET", "/session/logout", "generated_only", "possibly_state_changing"),
    ("GET", "/thing", "generated_only", "no_sample_value:token"),
    ("GET", "/elsewhere", "generated_only", "server_url_invalid"),
    ("GET", "/public", "generated_only", "live_budget_exceeded"),
]


def run_gates_spec(monkeypatch, budget: int = 3):
    """ci_runner.run over GATES_SPEC with the network, sandbox, corrector and sleep stubbed."""
    import json
    from unittest.mock import MagicMock

    from app import ci_runner

    monkeypatch.setattr(ci_runner, "resolve_and_validate_host", lambda url: "8.8.8.8")
    resp = MagicMock(text=json.dumps(GATES_SPEC), is_redirect=False, url="https://specs.example/openapi.json")
    resp.raise_for_status.return_value = None
    monkeypatch.setattr(ci_runner.requests, "get", lambda *a, **k: resp)
    monkeypatch.delenv("RELAY_HAS_BYOK", raising=False)
    monkeypatch.setattr(ci_runner, "MAX_LIVE_OPERATIONS", budget)
    calls, sleeps, corrector = [], [], MagicMock(side_effect=AssertionError("no correction"))
    monkeypatch.setattr(ci_runner, "run_in_sandbox",
                        lambda pkg, cs: calls.append(cs) or {"status": "verified_pass", "detail": "stub"})
    monkeypatch.setattr(ci_runner, "self_correct", corrector)
    monkeypatch.setattr(ci_runner.time, "sleep", sleeps.append)
    result = ci_runner.run("https://specs.example/openapi.json", reporter=MagicMock())
    return result, calls, sleeps, corrector


def test_ci_runner_applies_every_gate_in_spec_order(monkeypatch) -> None:
    result, calls, sleeps, corrector = run_gates_spec(monkeypatch)
    got = [(v["endpoint"]["method"], v["endpoint"]["path"], v["status"], v.get("reason")) for v in result["validated"]]
    assert got == GATES_EXPECTED
    corrector.assert_not_called()

    by_path = {c["endpoint"]["path"]: c for c in calls}
    assert list(by_path) == ["/items", "/items/{item-id}", "/search"]
    assert by_path["/items/{item-id}"]["kwargs"] == {"param_0": 50}      # sanitised local name, constraint midpoint
    assert by_path["/search"]["kwargs"] == {"q": "relay", "param_1": "en"}

    sources = {v["endpoint"]["path"]: v["value_sources"] for v in result["validated"] if v["status"] != "generated_only"}
    assert sources == {"/items": {}, "/items/{item-id}": {"param_0": "constraint"},
                       "/search": {"q": "example", "param_1": "enum"}}
    # generated_only entries keep their shape: no value_sources, no values
    assert all(set(v) == {"endpoint", "status", "reason"} for v in result["validated"] if v["status"] == "generated_only")
    # one pause before each live call after the first (same host), each at most the configured pause
    assert len(sleeps) == 2 and all(0 < s <= 1.0 for s in sleeps)


def test_budget_counts_only_operations_actually_called(monkeypatch) -> None:
    result, calls, _, _ = run_gates_spec(monkeypatch, budget=4)
    assert [c["endpoint"]["path"] for c in calls] == ["/items", "/items/{item-id}", "/search", "/public"]
    assert "live_budget_exceeded" not in [v.get("reason") for v in result["validated"]]


def test_budget_zero_calls_nothing(monkeypatch) -> None:
    result, calls, sleeps, _ = run_gates_spec(monkeypatch, budget=0)
    assert calls == [] and sleeps == []
    assert [v["reason"] for v in result["validated"] if v["endpoint"]["method"] == "GET" and
            v.get("reason") == "live_budget_exceeded"] == ["live_budget_exceeded"] * 4
