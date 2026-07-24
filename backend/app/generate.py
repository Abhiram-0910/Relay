"""Deterministic generation of typed Python clients from an OpenAPI spec.

No LLM involvement here — types come from datamodel-code-generator, client
methods from a Jinja2 template. Each endpoint with a JSON response schema gets
its own standalone models.py + client.py pair; endpoints without a JSON
response are skipped (reported, not generated). Path params, query params,
and JSON request bodies are supported; other content types are not.
"""
import json
import re
from pathlib import Path
from typing import Iterator

import jinja2
from datamodel_code_generator import DataModelType, InputFileType
from datamodel_code_generator import generate as generate_models

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}
JSON_TYPE_TO_PY = {"integer": "int", "string": "str", "number": "float", "boolean": "bool"}

CLIENT_TEMPLATE = jinja2.Template(
    '''"""Generated client for {{ title }}."""
from __future__ import annotations

import requests

from .models import {{ model_imports }}


class {{ class_name }}:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def {{ method_name }}(self{{ params_sig }}) -> {{ response_model }}:
        response = requests.{{ http_method }}(
            f"{self.base_url}{{ path_fstring }}",
            {{ params_kwarg_line }}
            {{ json_kwarg_line }}
        )
        response.raise_for_status()
        return {{ response_model }}.model_validate(response.json())
'''
)


def _to_pascal_case(name: str) -> str:
    parts = [p for p in re.split(r"[^a-zA-Z0-9]+", name) if p]
    return "".join(p[:1].upper() + p[1:] for p in parts)


def _py_type(schema: dict) -> str:
    return JSON_TYPE_TO_PY.get(schema.get("type"), "str")


def _response_schema(operation: dict) -> dict | None:
    for status, resp in operation.get("responses", {}).items():
        if status.startswith("2"):
            schema = resp.get("content", {}).get("application/json", {}).get("schema")
            if schema:
                return schema
    return None


def _request_body_schema(operation: dict) -> dict | None:
    return operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")


def _params_by_location(operation: dict, location: str) -> list[dict]:
    return [p for p in operation.get("parameters", []) if p.get("in") == location]


def _generate_model(schema: dict, class_name: str, tmp_dir: Path, filename: str) -> str:
    out = tmp_dir / filename
    generate_models(
        json.dumps(schema),
        input_file_type=InputFileType.JsonSchema,
        output=out,
        output_model_type=DataModelType.PydanticV2BaseModel,
        class_name=class_name,
    )
    return out.read_text()


def _merge_python_modules(sources: list[str]) -> str:
    """Hoist top-level imports (incl. `from __future__ import ...`) to the top, deduped, in
    first-seen order — required because Python only allows a single future-import block at
    the very start of a file, so naive concatenation of two generated modules breaks."""
    import_lines: list[str] = []
    seen: set[str] = set()
    bodies: list[str] = []
    for source in sources:
        body_lines = []
        for line in source.splitlines():
            is_top_level_import = line == line.strip() and (line.startswith("from ") or line.startswith("import "))
            if is_top_level_import:
                if line not in seen:
                    seen.add(line)
                    import_lines.append(line)
            else:
                body_lines.append(line)
        bodies.append("\n".join(body_lines).strip("\n"))
    return "\n".join(import_lines) + "\n\n\n" + "\n\n\n".join(bodies) + "\n"


def generate_endpoint_client(spec: dict, path: str, method: str, tmp_dir: Path) -> dict:
    """Generate models.py + client.py source for one path+method.

    Raises ValueError if the operation has no JSON response schema — callers
    should treat that as "skip this endpoint", not a hard failure.
    """
    operation = spec["paths"][path][method]
    response_schema = _response_schema(operation)
    if response_schema is None:
        raise ValueError(f"{method.upper()} {path} has no JSON response schema to generate a model from")

    prefix = _to_pascal_case(operation.get("operationId") or f"{method}_{path}") or "Operation"
    response_model = f"{prefix}Response"
    models_code = _generate_model(response_schema, response_model, tmp_dir, "response_models.py")

    path_params = _params_by_location(operation, "path")
    query_params = _params_by_location(operation, "query")
    request_schema = _request_body_schema(operation)
    body_required = operation.get("requestBody", {}).get("required", False)

    request_model = None
    if request_schema is not None:
        request_model = f"{prefix}Request"
        request_models_code = _generate_model(request_schema, request_model, tmp_dir, "request_models.py")
        models_code = _merge_python_modules([models_code, request_models_code])

    # ponytail: required-before-optional param ordering, no dedup of shared nested schema
    # names across endpoints — good enough for compile-checked standalone files, revisit if
    # these ever get merged into one shared client module.
    sig_parts = [f", {p['name']}: {_py_type(p.get('schema', {}))}" for p in path_params]
    if request_model and body_required:
        sig_parts.append(f", body: {request_model}")
    sig_parts += [f", {p['name']}: {_py_type(p.get('schema', {}))} | None = None" for p in query_params]
    if request_model and not body_required:
        sig_parts.append(f", body: {request_model} | None = None")
    params_sig = "".join(sig_parts)

    params_kwarg_line = ""
    if query_params:
        items = ", ".join(f'"{p["name"]}": {p["name"]}' for p in query_params)
        params_kwarg_line = "params={k: v for k, v in {" + items + "}.items() if v is not None},"

    json_kwarg_line = f"json=body.model_dump(mode='json', exclude_none=True)," if request_model else ""

    method_name = operation.get("operationId") or f"{method}_" + re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_")
    class_name = _to_pascal_case(spec.get("info", {}).get("title", "Api")) + "Client"
    model_imports = response_model + (f", {request_model}" if request_model else "")

    client_code = CLIENT_TEMPLATE.render(
        title=spec.get("info", {}).get("title", ""),
        response_model=response_model,
        model_imports=model_imports,
        class_name=class_name,
        method_name=method_name,
        params_sig=params_sig,
        http_method=method,
        path_fstring=path,
        params_kwarg_line=params_kwarg_line,
        json_kwarg_line=json_kwarg_line,
    )

    return {
        "endpoint": {"method": method.upper(), "path": path},
        "model_name": response_model,
        "class_name": class_name,
        "method_name": method_name,
        "models_code": models_code,
        "client_code": client_code,
    }


def generate_all_endpoints(spec: dict, tmp_dir: Path) -> Iterator[dict]:
    """Yield one progress dict per path+method, in order, each either generated or skipped."""
    operations = [
        (path, method)
        for path, methods in spec.get("paths", {}).items()
        for method in methods
        if method.lower() in HTTP_METHODS
    ]
    total = len(operations)
    for index, (path, method) in enumerate(operations, start=1):
        try:
            result = generate_endpoint_client(spec, path, method.lower(), tmp_dir)
            yield {"index": index, "total": total, "status": "generated", **result}
        except ValueError as exc:
            yield {
                "index": index,
                "total": total,
                "status": "skipped",
                "endpoint": {"method": method.upper(), "path": path},
                "reason": str(exc),
            }
