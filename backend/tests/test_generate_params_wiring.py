"""Piece 4d end to end: generate_endpoint_client -> generated client -> the exact request requests
would send (Session.send stubbed, no network)."""
from __future__ import annotations

import inspect
import json
import typing
from pathlib import Path

import pytest

from app.generate import generate_all_endpoints, generate_endpoint_client
from tests.generated_client import BASE_URL, capture, load_client
from tests.source_inert import assert_generated_source_inert

_OK = {"200": {"description": "ok", "content": {"application/json": {"schema": {"type": "object"}}}}}


def _array(**kw):
    return {"type": "array", "items": {"type": "string"}, **kw}


SPEC = {
    "openapi": "3.0.3",
    "info": {"title": "Wire", "version": "1"},
    "paths": {"/things/{id}": {
        "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
        "get": {
            "operationId": "getThing",
            "parameters": [
                {"name": "opt", "in": "query", "schema": {"type": "string"}},
                {"name": "req", "in": "query", "required": True, "schema": {"type": "string"}},
                {"name": "flag", "in": "query", "schema": {"type": "boolean"}},
                {"name": "tags", "in": "query", "schema": _array()},
                {"name": "ids", "in": "query", "explode": False, "schema": _array()},
                {"name": "self", "in": "query", "schema": {"type": "string"}},
                {"name": "body", "in": "query", "schema": {"type": "string"}},
                {"name": "requests", "in": "query", "schema": {"type": "string"}},
                {"name": "page-size", "in": "query", "schema": {"type": "integer"}},
                {"name": "X-Flag", "in": "header", "schema": {"type": "boolean"}},
                {"name": "X-Tags", "in": "header", "schema": _array()},
            ],
            "responses": _OK,
        },
    }},
}


@pytest.fixture
def item(tmp_path: Path) -> dict:
    result = generate_endpoint_client(SPEC, "/things/{id}", "get", tmp_path)
    assert_generated_source_inert(result["models_code"])
    assert_generated_source_inert(result["client_code"])
    return result


def _local(item: dict, wire: str, location: str = "query") -> str:
    return next(p["local_name"] for p in item["params"] if (p["wire_name"], p["location"]) == (wire, location))


def test_signature_order_and_local_names(item, tmp_path: Path) -> None:
    method = getattr(load_client(item, tmp_path), item["method_name"])
    params = inspect.signature(method).parameters
    assert [(n, p.kind.name, p.default is p.empty) for n, p in params.items()] == [
        ("id", "POSITIONAL_OR_KEYWORD", True),   # path-item-level path param
        ("req", "KEYWORD_ONLY", True),           # required query declared after an optional one
        ("opt", "KEYWORD_ONLY", False), ("flag", "KEYWORD_ONLY", False), ("tags", "KEYWORD_ONLY", False),
        ("ids", "KEYWORD_ONLY", False), ("self_query", "KEYWORD_ONLY", False),
        ("body_query", "KEYWORD_ONLY", False), ("requests_query", "KEYWORD_ONLY", False),
        ("param_9", "KEYWORD_ONLY", False), ("param_10", "KEYWORD_ONLY", False),
        ("param_11", "KEYWORD_ONLY", False),
    ]
    assert (_local(item, "page-size"), _local(item, "X-Flag", "header"), _local(item, "X-Tags", "header")) == (
        "param_9", "param_10", "param_11")


def test_full_request_on_the_wire(item, tmp_path: Path) -> None:
    sent = capture(item, tmp_path, "a/b c", req="r", opt="o", flag=True, tags=["x", "y"], ids=["1", "2,3"],
                   self_query="s", body_query="b", requests_query="q", param_9=10,
                   param_10=False, param_11=["a", "b"])
    # query order on the wire is spec order (the signature moves required params first, the wire doesn't)
    assert sent.url == (f"{BASE_URL}/things/a%2Fb%20c?opt=o&req=r&flag=true&tags=x&tags=y&ids=1,2%2C3"
                        "&self=s&body=b&requests=q&page-size=10")
    assert (sent.headers["X-Flag"], sent.headers["X-Tags"]) == ("false", "a,b")


def test_optional_none_is_omitted_everywhere(item, tmp_path: Path) -> None:
    sent = capture(item, tmp_path, "7", req="r")
    assert sent.url == f"{BASE_URL}/things/7?req=r"
    assert "X-Flag" not in sent.headers and "X-Tags" not in sent.headers


def test_boolean_false_in_query_and_true_in_header(item, tmp_path: Path) -> None:
    sent = capture(item, tmp_path, "7", req="r", flag=False, param_10=True)
    assert sent.url == f"{BASE_URL}/things/7?req=r&flag=false"
    assert sent.headers["X-Flag"] == "true"


def test_generated_items_carry_the_parameter_table(tmp_path: Path) -> None:
    [generated] = generate_all_endpoints(SPEC, tmp_path)
    assert generated["status"] == "generated"
    assert generated["params"][0] == {"wire_name": "id", "local_name": "id", "location": "path",
                                      "required": True, "py_type": "str", "lossy": []}
    assert generated["lossy"] == [] and generated["ignored"] == []


def test_skip_reason_becomes_an_unsupported_operation(tmp_path: Path) -> None:
    spec = {"openapi": "3.0.3", "info": {"title": "T", "version": "1"}, "paths": {"/x": {"get": {
        "operationId": "getX", "responses": _OK,
        "parameters": [{"name": "s", "in": "cookie", "required": True, "schema": {"type": "string"}}]}}}}
    [skipped] = generate_all_endpoints(spec, tmp_path)
    assert skipped["status"] == "skipped"
    assert skipped["reason"] == "GET /x skipped: required_cookie_parameter"


def test_generation_is_byte_reproducible(tmp_path: Path) -> None:
    first = generate_endpoint_client(SPEC, "/things/{id}", "get", tmp_path / "a")
    second = generate_endpoint_client(SPEC, "/things/{id}", "get", tmp_path / "b")
    assert "timestamp" not in first["models_code"]
    assert (first["models_code"], first["client_code"]) == (second["models_code"], second["client_code"])


def _body_op(required: bool) -> dict:
    return {"openapi": "3.0.3", "info": {"title": "Body", "version": "1"}, "paths": {"/orders": {"post": {
        "operationId": "placeOrder", "responses": _OK,
        "requestBody": {"required": required, "content": {"application/json": {"schema": {
            "type": "object", "properties": {"qty": {"type": "integer"}}}}}}}}}}


def test_optional_body_omitted_sends_no_body(tmp_path: Path) -> None:
    item = generate_endpoint_client(_body_op(False), "/orders", "post", tmp_path)
    assert_generated_source_inert(item["client_code"])
    sent = capture(item, tmp_path)
    assert sent.body is None and "Content-Type" not in sent.headers


def test_optional_body_given_is_sent_as_json(tmp_path: Path) -> None:
    item = generate_endpoint_client(_body_op(False), "/orders", "post", tmp_path)
    body_model = typing.get_type_hints(getattr(load_client(item, tmp_path / "m"), item["method_name"]))["body"]
    model = next(a for a in typing.get_args(body_model) if a is not type(None))
    sent = capture(item, tmp_path, body=model(qty=2))
    assert (json.loads(sent.body), sent.headers["Content-Type"]) == ({"qty": 2}, "application/json")


def test_required_body_is_unchanged(tmp_path: Path) -> None:
    item = generate_endpoint_client(_body_op(True), "/orders", "post", tmp_path)
    method = getattr(load_client(item, tmp_path / "m"), item["method_name"])
    [(name, param)] = inspect.signature(method).parameters.items()
    assert (name, param.kind, param.default) == ("body", param.POSITIONAL_OR_KEYWORD, param.empty)
    assert "json=body.model_dump(mode='json', exclude_none=True)," in item["client_code"]
    sent = capture(item, tmp_path, typing.get_type_hints(method)["body"](qty=3))
    assert (json.loads(sent.body), sent.headers["Content-Type"]) == ({"qty": 3}, "application/json")
