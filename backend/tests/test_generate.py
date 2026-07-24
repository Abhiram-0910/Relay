from pathlib import Path

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
    assert 'params={k: v for k, v in {"status": status}.items() if v is not None}' in result["client_code"]


def test_request_body_endpoint_compiles(tmp_path: Path) -> None:
    result = generate_endpoint_client(SPEC, "/pet", "post", tmp_path)

    compile(result["models_code"], "models.py", "exec")
    compile(result["client_code"], "client.py", "exec")

    assert "class AddPetRequest(BaseModel)" in result["models_code"]
    assert "def addPet(self, body: AddPetRequest) -> AddPetResponse" in result["client_code"]
    assert "json=body.model_dump(mode='json', exclude_none=True)" in result["client_code"]


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
