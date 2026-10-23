"""Deterministic generation of typed Python clients from an OpenAPI spec.

No LLM involvement here — types come from datamodel-code-generator, client
methods from a Jinja2 template. Each endpoint with a JSON response schema gets
its own standalone models.py + client.py pair; endpoints without a JSON
response are skipped (reported, not generated). Path, query and header params (planned by
app.params) and JSON request bodies are supported; other content types are not.
"""
import json
import re
from pathlib import Path
from typing import Iterator

import jinja2
from datamodel_code_generator import DataModelType, InputFileType
from datamodel_code_generator import generate as generate_models

from app.naming import _IDENT_RE, _safe_identifier, _to_pascal_case  # noqa: F401 (re-exported)
from app.params import SERIALISER_SOURCE, plan_parameters, render_signature, render_wire_args

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


class UnsupportedOperation(ValueError):
    """An operation this generator deliberately skips (e.g. no JSON response). Its message is ours,
    so it is reported verbatim; any other exception is reported by type only."""


# SECURITY: operationId, parameter names, and the path itself all come straight from the
# untrusted spec and get spliced into GENERATED PYTHON SOURCE — an identifier position for the
# first two, and (see _safe_path_fstring) a live f-string expression position for the path.
# Neither is validated by the OpenAPI schema itself, so a crafted spec is attacker-controlled code
# unless every one of these is routed through the helpers below (and app.naming) before reaching
# CLIENT_TEMPLATE.
_BRACE_GROUP_RE = re.compile(r"\{([^{}]*)\}")


def _safe_class_name(name: str, prefix: str) -> str:
    """PascalCase of spec text (ASCII letters/digits only), safe as a Python class name. The only
    way that can still be invalid is a leading digit ("3D Api", "1st-op"), which gets `prefix`."""
    pascal = _to_pascal_case(name)
    return prefix + pascal if pascal[:1].isdigit() else pascal


def _safe_docstring_text(text: str, limit: int = 80) -> str:
    """Spec text made inert inside a generated triple-quoted docstring: non-printable characters
    (newlines, NUL, bidi/format controls, lone surrogates) become spaces, and quotes/backslashes —
    the only characters that can end or escape the literal — are neutralised."""
    text = "".join(c if c.isprintable() else " " for c in str(text))
    return text.replace("\\", "/").replace('"', "'")[:limit]


def _fstring_literal(text: str) -> str:
    """Escape spec text for the literal part of a double-quoted f-string: braces doubled, quote and
    backslash escaped, non-printable characters as \\U escapes. Without this a `"` in a path closes
    the URL string and the rest of the path runs as code in the generated method."""
    out = []
    for c in text:
        if c in "{}":
            out.append(c * 2)
        elif c in '"\\':
            out.append("\\" + c)
        elif not c.isprintable():
            out.append(f"\\U{ord(c):08x}")
        else:
            out.append(c)
    return "".join(out)


def _safe_path_fstring(path: str, path_param_locals: dict[str, str]) -> str:
    """Render `path` as the f-string body used in the generated client. OpenAPI's `{param}` path
    templating is reused here AS a live Python f-string substitution by design, so without this,
    an attacker's spec can put an arbitrary Python expression in a path segment (e.g.
    `/x{__import__('os').system(...)}`) and have it evaluated every time the generated method
    runs. Only a `{name}` matching a DECLARED path parameter becomes a live substitution, and
    always as `_relay_path_value(<local>)` on that parameter's already-validated local identifier
    (percent-encoded on the wire) — never the raw spec text, even when the declared name happens to
    itself be a valid identifier. Everything else (undeclared brace groups, stray braces, quotes,
    backslashes, control characters) is an escaped literal."""
    out, pos = [], 0
    for m in _BRACE_GROUP_RE.finditer(path):
        local = path_param_locals.get(m.group(1))
        if local:
            out.append(_fstring_literal(path[pos:m.start()]) + "{_relay_path_value(" + local + ")}")
            pos = m.end()
    out.append(_fstring_literal(path[pos:]))
    return "".join(out)

CLIENT_TEMPLATE = jinja2.Template(
    '''"""Generated client for {{ title }}."""
from __future__ import annotations

import requests
{{ typing_import_line }}
from .models import {{ model_imports }}

{{ serialiser }}
{{ alias_lines }}

class {{ class_name }}:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def {{ method_name }}(self{{ params_sig }}) -> {{ response_model }}:
        response = requests.{{ http_method }}(
            f"{self.base_url}{{ path_fstring }}",
            {{ params_kwarg_line }}
            {{ headers_kwarg_line }}
            {{ json_kwarg_line }}
        )
        response.raise_for_status()
        return {{ response_model }}.model_validate(response.json())
'''
)


def _response_schema(operation: dict) -> dict | None:
    for status, resp in operation.get("responses", {}).items():
        if status.startswith("2"):
            schema = resp.get("content", {}).get("application/json", {}).get("schema")
            if schema:
                return schema
    return None


def _request_body_schema(operation: dict) -> dict | None:
    return operation.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")


def _generate_model(schema: dict, class_name: str, tmp_dir: Path, filename: str) -> str:
    out = tmp_dir / filename
    generate_models(
        json.dumps(schema),
        input_file_type=InputFileType.JsonSchema,
        output=out,
        output_model_type=DataModelType.PydanticV2BaseModel,
        class_name=class_name,
        disable_timestamp=True,  # byte-reproducible output (receipts hash it)
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

    Raises UnsupportedOperation (a ValueError) if the operation has no JSON response schema —
    callers should treat that as "skip this endpoint", not a hard failure.
    """
    operation = spec["paths"][path][method]
    response_schema = _response_schema(operation)
    if response_schema is None:
        raise UnsupportedOperation(f"{method.upper()} {path} has no JSON response schema to generate a model from")

    prefix = _safe_class_name(operation.get("operationId") or f"{method}_{path}", "Op") or "Operation"
    response_model = f"{prefix}Response"
    models_code = _generate_model(response_schema, response_model, tmp_dir, "response_models.py")

    request_schema = _request_body_schema(operation)
    body_required = operation.get("requestBody", {}).get("required", False)

    request_model = None
    if request_schema is not None:
        request_model = f"{prefix}Request"
        request_models_code = _generate_model(request_schema, request_model, tmp_dir, "request_models.py")
        models_code = _merge_python_modules([models_code, request_models_code])

    # SECURITY: parameter names, types and enum members are spec text; app.params turns them into
    # sanitised identifiers, repr()'d literals and fixed type names before any reach the template.
    op_params = plan_parameters(
        spec["paths"][path], operation, alias_prefix=prefix, sanitise=_safe_identifier,
        reserved={response_model, request_model} - {None},
    )
    if op_params.skip_reason:
        raise UnsupportedOperation(f"{method.upper()} {path} skipped: {op_params.skip_reason}")

    params_sig = render_signature(op_params, (request_model, body_required) if request_model else None)
    query_items, header_items = render_wire_args(op_params)
    has = {loc: any(p.location == loc for p in op_params.params) for loc in ("query", "header")}
    params_kwarg_line = f"params=_relay_query_string({query_items})," if has["query"] else ""
    headers_kwarg_line = f"headers=_relay_header_dict({header_items})," if has["header"] else ""
    dump = "body.model_dump(mode='json', exclude_none=True)"
    # An omitted optional body sends no body at all (json=None: no payload, no Content-Type).
    json_kwarg_line = ("" if not request_model else f"json={dump}," if body_required
                       else f"json=None if body is None else {dump},")
    typing_import_line = (f"from typing import {', '.join(sorted(op_params.typing_imports))}\n"
                          if op_params.typing_imports else "")
    alias_lines = "".join(f"{name} = {literal}\n" for name, literal in op_params.aliases.items())

    method_name = _safe_identifier(
        operation.get("operationId") or "",
        f"{method}_" + re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_"),
    )
    class_name = _safe_class_name(spec.get("info", {}).get("title", "Api"), "Api") + "Client"
    model_imports = response_model + (f", {request_model}" if request_model else "")
    path_param_locals = {p.wire_name: p.local_name for p in op_params.params if p.location == "path"}

    client_code = CLIENT_TEMPLATE.render(
        title=_safe_docstring_text(spec.get("info", {}).get("title", "")),
        response_model=response_model,
        model_imports=model_imports,
        class_name=class_name,
        method_name=method_name,
        params_sig=params_sig,
        http_method=method,
        path_fstring=_safe_path_fstring(path, path_param_locals),
        params_kwarg_line=params_kwarg_line,
        headers_kwarg_line=headers_kwarg_line,
        json_kwarg_line=json_kwarg_line,
        typing_import_line=typing_import_line,
        serialiser=SERIALISER_SOURCE,
        alias_lines=alias_lines,
    )
    # Output that doesn't compile is skipped here, not shipped: SyntaxError reaches
    # generate_all_endpoints' generator_error skip.
    compile(models_code, "models.py", "exec")
    compile(client_code, "client.py", "exec")

    return {
        "endpoint": {"method": method.upper(), "path": path},
        "model_name": response_model,
        "class_name": class_name,
        "method_name": method_name,
        "models_code": models_code,
        "client_code": client_code,
        "params": op_params.to_jsonable(),
        "lossy": op_params.lossy,
        "ignored": op_params.ignored,
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
        except Exception as exc:
            # One bad operation must not take down the rest. Only the exception TYPE goes in the
            # reason: the message can carry raw spec text, and the reason is logged publicly.
            reason = str(exc) if isinstance(exc, UnsupportedOperation) else f"generator_error: {type(exc).__name__}"
            yield {
                "index": index,
                "total": total,
                "status": "skipped",
                "endpoint": {"method": method.upper(), "path": path},
                "reason": reason,
            }
