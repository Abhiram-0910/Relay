from pathlib import Path

from app.generate import generate_endpoint_client

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
                    "200": {
                        "description": "ok",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "title": "Pet",
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "integer"},
                                        "name": {"type": "string"},
                                    },
                                    "required": ["id", "name"],
                                },
                            }
                        },
                    }
                },
            }
        }
    },
}


def test_generate_endpoint_client_produces_compilable_code(tmp_path: Path) -> None:
    result = generate_endpoint_client(SPEC, "/pet/{petId}", "get", tmp_path)

    compile(result["models_code"], "models.py", "exec")
    compile(result["client_code"], "client.py", "exec")

    assert "class Pet(BaseModel)" in result["models_code"]
    assert "def getPetById(self, petId: int) -> Pet" in result["client_code"]
    assert 'f"{self.base_url}/pet/{petId}"' in result["client_code"]


def test_generate_endpoint_client_raises_without_json_response() -> None:
    spec = {
        "info": {"title": "X"},
        "paths": {"/no-response": {"get": {"responses": {"200": {"description": "ok"}}}}},
    }
    try:
        generate_endpoint_client(spec, "/no-response", "get", tmp_dir=Path("/tmp"))
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for endpoint with no JSON response schema")
