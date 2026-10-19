"""The spec-version gate: decide from the parsed document's own `swagger`/`openapi` field (never
the URL) whether Relay processes it, before any $ref is resolved.

Relay verifies OpenAPI 3.0.x and 3.1.x. Swagger 2.0 and other OpenAPI versions are a clean
`unsupported_spec_version`; a missing, doubled, non-string or non-version-shaped field is
`spec_invalid`. The public `spec_version` label echoes the field only when it is strictly
version-shaped, so arbitrary spec text never reaches a log or the run result.
"""
from __future__ import annotations

import re

_SUPPORTED_OPENAPI = re.compile(r"3\.[01]\.\d+(-[0-9A-Za-z.-]+)?", re.ASCII)
_VERSION_SHAPED = re.compile(r"\d+\.\d+(\.\d+)?(-[0-9A-Za-z.-]+)?", re.ASCII)
_LABEL_SAFE = re.compile(r"\d{1,2}\.\d{1,3}(\.\d{1,3})?", re.ASCII)
UNRECOGNISED = "unrecognised"


class SpecVersionInvalid(Exception):
    """No usable version field: classified spec_invalid."""


class UnsupportedSpecVersion(Exception):
    """A well-formed version Relay doesn't process: classified unsupported_spec_version."""


def spec_version_label(document) -> str:
    """`swagger 2.0` / `openapi 3.1.0` when exactly one field holds a strictly version-shaped
    string, else `unrecognised`. Safe to log and publish."""
    if not isinstance(document, dict):
        return UNRECOGNISED
    fields = [f for f in ("swagger", "openapi") if f in document]
    if len(fields) != 1:
        return UNRECOGNISED
    value = document[fields[0]]
    if isinstance(value, str) and _LABEL_SAFE.fullmatch(value):
        return f"{fields[0]} {value}"
    return UNRECOGNISED


def check_spec_version(document) -> None:
    """Raise SpecVersionInvalid or UnsupportedSpecVersion unless this is OpenAPI 3.0.x/3.1.x."""
    if not isinstance(document, dict):
        raise SpecVersionInvalid("the spec is not a mapping")
    fields = [f for f in ("swagger", "openapi") if f in document]
    if len(fields) != 1:
        raise SpecVersionInvalid("expected exactly one of `swagger` / `openapi`")
    field, value = fields[0], document[fields[0]]
    # YAML `openapi: 3.1` parses as a float, so a non-string is a malformed field, not a version.
    if not isinstance(value, str) or not _VERSION_SHAPED.fullmatch(value):
        raise SpecVersionInvalid(f"`{field}` is not a version string")
    if field == "openapi" and _SUPPORTED_OPENAPI.fullmatch(value):
        return
    raise UnsupportedSpecVersion(f"{spec_version_label(document)} is not OpenAPI 3.0/3.1")
