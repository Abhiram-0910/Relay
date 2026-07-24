"""Deterministic generation of a typed Python client for one OpenAPI operation.

No LLM involvement here — types come from datamodel-code-generator, the client
class from a Jinja2 template. Only GET operations with a JSON response schema
and simple path params are supported so far; request bodies and query params
are out of scope for this slice.
"""
import json
import re
from pathlib import Path

import jinja2
from datamodel_code_generator import DataModelType, InputFileType
from datamodel_code_generator import generate as generate_models

JSON_TYPE_TO_PY = {"integer": "int", "string": "str", "number": "float", "boolean": "bool"}

CLIENT_TEMPLATE = jinja2.Template(
    '''"""Generated client for {{ title }}."""
from __future__ import annotations

import requests

from .models import {{ response_model }}


class {{ class_name }}:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def {{ method_name }}(self{{ params_sig }}) -> {{ response_model }}:
        response = requests.{{ http_method }}(f"{self.base_url}{{ path_fstring }}")
        response.raise_for_status()
        return {{ response_model }}.model_validate(response.json())
'''
)


def _to_pascal_case(name: str) -> str:
    parts = [p for p in re.split(r"[^a-zA-Z0-9]+", name) if p]
    return "".join(p[:1].upper() + p[1:] for p in parts)


def _response_schema(operation: dict) -> dict | None:
    for status, resp in operation.get("responses", {}).items():
        if status.startswith("2"):
            schema = resp.get("content", {}).get("application/json", {}).get("schema")
            if schema:
                return schema
    return None


def _path_params(operation: dict) -> list[dict]:
    return [p for p in operation.get("parameters", []) if p.get("in") == "path"]


def generate_endpoint_client(spec: dict, path: str, method: str, tmp_dir: Path) -> dict:
    """Generate models.py + client.py source for one path+method, writing models.py under tmp_dir."""
    operation = spec["paths"][path][method]
    response_schema = _response_schema(operation)
    if response_schema is None:
        raise ValueError(f"{method.upper()} {path} has no JSON response schema to generate a model from")

    model_name = _to_pascal_case(response_schema.get("title") or operation.get("operationId") or path) or "Response"

    models_path = tmp_dir / "models.py"
    generate_models(
        json.dumps(response_schema),
        input_file_type=InputFileType.JsonSchema,
        output=models_path,
        output_model_type=DataModelType.PydanticV2BaseModel,
        class_name=model_name,
    )
    models_code = models_path.read_text()

    path_params = _path_params(operation)
    params_sig = "".join(
        f", {p['name']}: {JSON_TYPE_TO_PY.get(p.get('schema', {}).get('type'), 'str')}" for p in path_params
    )
    method_name = operation.get("operationId") or f"{method}_" + re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_")
    class_name = _to_pascal_case(spec.get("info", {}).get("title", "Api")) + "Client"

    client_code = CLIENT_TEMPLATE.render(
        title=spec.get("info", {}).get("title", ""),
        response_model=model_name,
        class_name=class_name,
        method_name=method_name,
        params_sig=params_sig,
        http_method=method,
        path_fstring=path,
    )

    return {
        "endpoint": {"method": method.upper(), "path": path},
        "model_name": model_name,
        "class_name": class_name,
        "method_name": method_name,
        "models_code": models_code,
        "client_code": client_code,
    }
