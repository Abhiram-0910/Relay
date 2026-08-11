import re
from pathlib import Path

import pytest
import requests

from app.generate import generate_all_endpoints, generate_endpoint_client

PET_SCHEMA = {
    "type": "object",
    "properties": {"id": {"type": "integer"}, "name": {"type": "string"}},
    "required": ["id", "name"],
}

SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Petstore Mini"},
    "paths": {
        "/pet/{petId}": {
            "get": {
                "operationId": "getPetById",
                "parameters": [
                    {"name": "petId", "in": "path", "required": True, "schema": {"type": "integer"}},
                ],
                "responses": {
                    "200": {"description": "ok", "content": {"application/json": {"schema": PET_SCHEMA}}}
                },
            }
        },
        "/pet/findByStatus": {
            "get": {
                "operationId": "findPetsByStatus",
                "parameters": [
                    {"name": "status", "in": "query", "required": False, "schema": {"type": "string"}},
                ],
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {"application/json": {"schema": {"type": "array", "items": PET_SCHEMA}}},
                    }
                },
            }
        },
        "/pet": {
            "post": {
                "operationId": "addPet",
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": PET_SCHEMA}},
                },
                "responses": {
                    "200": {"description": "ok", "content": {"application/json": {"schema": PET_SCHEMA}}}
                },
            }
        },
        "/pet/{petId}/uploadImage": {
            "post": {
                "operationId": "uploadFile",
                "parameters": [
                    {"name": "petId", "in": "path", "required": True, "schema": {"type": "integer"}},
                ],
                "responses": {"200": {"description": "ok"}},  # no JSON content -> must be skipped
            }
        },
    },
}


def test_path_param_endpoint_compiles(tmp_path: Path) -> None:
    result = generate_endpoint_client(SPEC, "/pet/{petId}", "get", tmp_path)

    compile(result["models_code"], "models.py", "exec")
    compile(result["client_code"], "client.py", "exec")

    assert "class GetPetByIdResponse(BaseModel)" in result["models_code"]
    assert "def getPetById(self, petId: int) -> GetPetByIdResponse" in result["client_code"]
    assert 'f"{self.base_url}/pet/{petId}"' in result["client_code"]


def test_query_param_endpoint_compiles(tmp_path: Path) -> None:
    result = generate_endpoint_client(SPEC, "/pet/findByStatus", "get", tmp_path)

    compile(result["models_code"], "models.py", "exec")
    compile(result["client_code"], "client.py", "exec")

    assert "status: str | None = None" in result["client_code"]
    # the wire-visible key is now repr()'d (single-quoted), not raw-spliced double-quoted text
    assert "params={k: v for k, v in {'status': status}.items() if v is not None}" in result["client_code"]


def test_request_body_endpoint_compiles(tmp_path: Path) -> None:
    result = generate_endpoint_client(SPEC, "/pet", "post", tmp_path)

    compile(result["models_code"], "models.py", "exec")
    compile(result["client_code"], "client.py", "exec")

    assert "class AddPetRequest(BaseModel)" in result["models_code"]
    assert "def addPet(self, body: AddPetRequest) -> AddPetResponse" in result["client_code"]
    assert "json=body.model_dump(mode='json', exclude_none=True)" in result["client_code"]


# --- Step 6 (security review, F5): spec-supplied identifiers must never become live code --------

_INJECTION_RESPONSE = {
    "200": {"description": "ok", "content": {"application/json": {"schema": PET_SCHEMA}}}
}


def test_malicious_operation_id_cannot_break_out_of_the_def_line(tmp_path: Path) -> None:
    """operationId used to be spliced verbatim into `def {{ method_name }}(...)` -- a crafted
    value could close that def and append arbitrary statements to the generated module."""
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Evil"},
        "paths": {"/x": {"get": {
            "operationId": "foo(self): pass\n    def evil",
            "responses": _INJECTION_RESPONSE,
        }}},
    }
    result = generate_endpoint_client(spec, "/x", "get", tmp_path)
    compile(result["client_code"], "client.py", "exec")  # must still be ONE valid method, not two
    assert "def evil" not in result["client_code"]
    assert "def get_x" in result["client_code"]  # falls back to the safe method+path-derived name


def test_malicious_path_expression_is_never_evaluated(tmp_path: Path) -> None:
    """The raw path is spliced into a live f-string in the generated client (OpenAPI's {param}
    path templating IS Python f-string substitution here, by design) -- a path segment crafted to
    look like a Python expression used to be evaluated every time the generated method ran."""
    evil_path = "/x{__import__('os').system('id')}"
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Evil"},
        "paths": {evil_path: {"get": {"operationId": "getX", "responses": _INJECTION_RESPONSE}}},
    }
    result = generate_endpoint_client(spec, evil_path, "get", tmp_path)
    compile(result["client_code"], "client.py", "exec")
    # The malicious text can still appear as INERT STRING CONTENT (that's fine) -- what matters is
    # whether it's live code (single braces, evaluated every call) or an escaped literal (double
    # braces, never evaluated). Assert the escaped form specifically, and that no LIVE single-brace
    # occurrence survives (a brace not immediately preceded by another opening brace).
    assert "{{__import__('os').system('id')}}" in result["client_code"]
    assert not re.search(r"(?<!\{)\{__import__", result["client_code"])


def test_malicious_query_param_name_cannot_break_out_of_the_dict_literal(tmp_path: Path) -> None:
    """A query parameter name with an embedded quote used to be spliced straight into a
    hand-written string literal in params_kwarg_line, breaking out of it."""
    evil_name = 'a": os.system("evil"), "b'
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Evil"},
        "paths": {"/x": {"get": {
            "operationId": "getX",
            "parameters": [{"name": evil_name, "in": "query",
                           "required": False, "schema": {"type": "string"}}],
            "responses": _INJECTION_RESPONSE,
        }}},
    }
    result = generate_endpoint_client(spec, "/x", "get", tmp_path)
    compile(result["client_code"], "client.py", "exec")
    # The malicious name must appear ONLY as one correctly quote-escaped Python string literal
    # (repr()) -- never as the old raw-spliced f'"{name}": ...' form, which this exact payload
    # would have broken out of.
    assert repr(evil_name) in result["client_code"]
    assert f'"{evil_name}"' not in result["client_code"]


def test_declared_path_param_still_substitutes_correctly(tmp_path: Path) -> None:
    """The legitimate case must keep working exactly as before: a real path parameter is still a
    live f-string substitution using the caller-supplied argument."""
    result = generate_endpoint_client(SPEC, "/pet/{petId}", "get", tmp_path)
    compile(result["client_code"], "client.py", "exec")
    assert 'f"{self.base_url}/pet/{petId}"' in result["client_code"]


def test_generate_endpoint_client_raises_without_json_response(tmp_path: Path) -> None:
    try:
        generate_endpoint_client(SPEC, "/pet/{petId}/uploadImage", "post", tmp_path)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for endpoint with no JSON response schema")


def test_generate_all_endpoints_generates_and_skips(tmp_path: Path) -> None:
    items = list(generate_all_endpoints(SPEC, tmp_path))

    assert len(items) == 4
    statuses = {item["endpoint"]["path"]: item["status"] for item in items}
    assert statuses["/pet/{petId}"] == "generated"
    assert statuses["/pet/findByStatus"] == "generated"
    assert statuses["/pet"] == "generated"
    assert statuses["/pet/{petId}/uploadImage"] == "skipped"

    for item in items:
        if item["status"] == "generated":
            compile(item["models_code"], "models.py", "exec")
            compile(item["client_code"], "client.py", "exec")

    assert all(item["total"] == 4 for item in items)
    assert [item["index"] for item in items] == [1, 2, 3, 4]


@pytest.mark.live
def test_generate_all_endpoints_against_real_petstore_spec(tmp_path: Path) -> None:
    resp = requests.get("https://petstore3.swagger.io/api/v3/openapi.json", timeout=15)
    resp.raise_for_status()
    spec = resp.json()

    items = list(generate_all_endpoints(spec, tmp_path))
    generated = [item for item in items if item["status"] == "generated"]
    skipped = [item for item in items if item["status"] == "skipped"]

    assert len(items) == 19
    assert len(generated) + len(skipped) == 19
    assert generated
    assert skipped  # DELETE/logout endpoints have no JSON response, expected

    for item in generated:
        compile(item["models_code"], "models.py", "exec")
        compile(item["client_code"], "client.py", "exec")

    assert all(item["total"] == 19 for item in items)
    assert [item["index"] for item in items] == list(range(1, 20))
