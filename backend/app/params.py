"""Parameter planning for one OpenAPI operation: which parameters the generated method takes, their
Python names and type hints, and how each value goes on the wire.

Pure and deterministic: spec dicts in, plain data and source strings out. Nothing here is spec text
spliced into source: wire names are only ever emitted through repr(), local names are sanitised
identifiers, enum members are repr()'d literals, and defaults, descriptions and examples are never
rendered. Type hints are static only — nothing validates at runtime, so a wrong enum in the spec
surfaces as a live finding, never as a client-side rejection.

Not wired into generate.py yet (piece 4d).
"""
from __future__ import annotations

import keyword
import math
from dataclasses import dataclass, field
from typing import Callable, Iterable

from app.naming import _IDENT_RE, _to_pascal_case

LOCATIONS = ("path", "query", "header")
_LOCATION_RANK = {loc: i for i, loc in enumerate(LOCATIONS)}

# Names the generated method body itself uses (CLIENT_TEMPLATE): a parameter with one of these
# names would shadow it. The operation's model class names are added per call (`reserved=`), and
# everything 4d adds to the template starts with RESERVED_PREFIX.
TEMPLATE_LOCALS = frozenset({"self", "body", "requests", "response"})
RESERVED_PREFIX = "_relay_"

# Per OpenAPI 3.x: header parameters with these names are ignored (they're controlled elsewhere).
IGNORED_HEADERS = frozenset({"authorization", "accept", "content-type"})

_SCALARS = {"string": "str", "integer": "int", "number": "float", "boolean": "bool"}
_ENUM_MEMBER_TYPES = (str, int, float, bool)
MAX_LITERAL_MEMBERS = 50
MAX_INLINE_LITERAL = 100
MAX_ARRAY_DEPTH = 2

# Lossy reason codes: the hint is weaker than the spec, but the operation still generates.
LOSSY_ENUM_NOT_LITERAL = "enum_not_literal"
LOSSY_ARRAY_ITEMS_UNSPECIFIED = "array_items_unspecified"
LOSSY_ARRAY_DEPTH_EXCEEDED = "array_depth_exceeded"
LOSSY_COMPOSED_SCHEMA = "composed_schema_to_any"
LOSSY_TYPE_MISSING = "type_missing"
LOSSY_TYPE_UNKNOWN = "type_unknown"
LOSSY_UNRESOLVED_REF_SCHEMA = "unresolved_ref_schema"
LOSSY_ALLOW_RESERVED_IGNORED = "allow_reserved_ignored"

# Ignored reasons: the parameter is left out of the method, recorded, and the operation generates.
IGNORED_OPTIONAL_COOKIE = "optional_cookie"
IGNORED_RESERVED_HEADER = "reserved_header"
IGNORED_OPTIONAL_CONTENT = "optional_content_parameter"

# Skip reasons: the operation cannot be called correctly, so it is not generated.
SKIP_UNRESOLVED_REF = "unresolved_ref_parameter"
SKIP_INVALID_PARAMETER = "invalid_parameter"
SKIP_REQUIRED_COOKIE = "required_cookie_parameter"
SKIP_UNSUPPORTED_LOCATION = "unsupported_location"
SKIP_REQUIRED_CONTENT = "required_content_parameter"
SKIP_UNSUPPORTED_STYLE = "unsupported_style"
SKIP_OBJECT_PARAMETER = "object_parameter"

_DEFAULT_STYLE = {"path": "simple", "query": "form", "header": "simple"}
_SUPPORTED_STYLES = {"path": {"simple"}, "query": {"form", "spaceDelimited", "pipeDelimited"},
                     "header": {"simple"}}


@dataclass(frozen=True)
class ParamSpec:
    wire_name: str
    local_name: str
    location: str           # "path" | "query" | "header"
    required: bool
    py_type: str            # annotation source, e.g. "int", "list[str] | None", an alias name
    style: str
    explode: bool
    serialisation: str      # "scalar" | "repeat" | "comma" | "space" | "pipe"
    lossy: tuple[str, ...]


@dataclass
class OperationParams:
    params: list[ParamSpec] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)       # alias name -> "Literal[...]"
    typing_imports: set[str] = field(default_factory=set)
    ignored: list[dict] = field(default_factory=list)           # {name, in, reason}
    lossy: list[dict] = field(default_factory=list)             # {name, in, reason}
    skip_reason: str | None = None

    def to_jsonable(self) -> list[dict]:
        """The contract consumed by the generator template, ci_runner's synthesis and MCP output."""
        return [{"wire_name": p.wire_name, "local_name": p.local_name, "location": p.location,
                 "required": p.required, "py_type": p.py_type, "lossy": list(p.lossy)}
                for p in self.params]


# --- types ---------------------------------------------------------------------------------------

@dataclass
class _Hint:
    src: str
    lossy: list[str] = field(default_factory=list)
    nullable: bool = False
    imports: set[str] = field(default_factory=set)
    scalar: bool = False  # a plain scalar or Literal: eligible as a oneOf/anyOf union branch

    @property
    def final(self) -> str:
        return f"{self.src} | None" if self.nullable and self.src != "None" else self.src


def _any(reason: str) -> _Hint:
    return _Hint("Any", [reason], imports={"Any"})


def _enum_literal(members: list) -> str | None:
    """Literal[...] source if every member is the same one of str/int/float(finite)/bool, else None.
    Members are repr()'d, never spliced."""
    if not members or len(members) > MAX_LITERAL_MEMBERS:
        return None
    kinds = {type(m) for m in members}
    if len(kinds) != 1 or kinds.pop() not in _ENUM_MEMBER_TYPES:
        return None
    if any(isinstance(m, float) and not math.isfinite(m) for m in members):
        return None
    return "Literal[" + ", ".join(repr(m) for m in dict.fromkeys(members)) + "]"


def _schema_hint(schema, alias_for: Callable[[str], str], depth: int = 0) -> _Hint:
    """Static type hint for a parameter schema. `alias_for(literal_src)` names a Literal that is too
    long to inline; it returns the module-level alias to use instead."""
    if not isinstance(schema, dict):
        return _any(LOSSY_TYPE_MISSING)
    if "$ref" in schema:
        return _any(LOSSY_UNRESOLVED_REF_SCHEMA)
    nullable = schema.get("nullable") is True

    if "enum" in schema and isinstance(schema["enum"], list):
        members = [m for m in schema["enum"] if m is not None]
        nullable = nullable or len(members) < len(schema["enum"])
        literal = _enum_literal(members)
        if literal is not None:
            src = alias_for(literal) if len(literal) > MAX_INLINE_LITERAL else literal
            return _Hint(src, [], nullable, {"Literal"}, scalar=True)
        base = _schema_hint({k: v for k, v in schema.items() if k != "enum"}, alias_for, depth)
        return _Hint(base.src, [LOSSY_ENUM_NOT_LITERAL, *base.lossy], nullable or base.nullable,
                     base.imports, base.scalar)

    for key in ("oneOf", "anyOf"):
        if key in schema:
            branches = schema[key] if isinstance(schema[key], list) else []
            hints = [_schema_hint(b, alias_for, depth) for b in branches]
            real = [h for h in hints if h.src != "None"]
            if real and all(h.scalar and not h.lossy and not h.nullable for h in real):
                return _Hint(" | ".join(dict.fromkeys(h.src for h in real)), [],
                             nullable or len(real) < len(hints), set().union(*(h.imports for h in real)))
            return _any(LOSSY_COMPOSED_SCHEMA)
    if "allOf" in schema:
        parts = schema["allOf"] if isinstance(schema["allOf"], list) else []
        if len(parts) == 1:
            inner = _schema_hint(parts[0], alias_for, depth)
            return _Hint(inner.src, inner.lossy, nullable or inner.nullable, inner.imports, inner.scalar)
        return _any(LOSSY_COMPOSED_SCHEMA)

    t = schema.get("type")
    if isinstance(t, list):
        non_null = [x for x in t if x != "null"]
        nullable = nullable or len(non_null) < len(t)
        if not non_null:
            return _Hint("None")
        hints = [_schema_hint({**schema, "type": x}, alias_for, depth) for x in non_null]
        return _Hint(" | ".join(dict.fromkeys(h.src for h in hints)), [r for h in hints for r in h.lossy],
                     nullable, set().union(*(h.imports for h in hints)), all(h.scalar for h in hints))
    if t is None:
        return _any(LOSSY_TYPE_MISSING)
    if t == "null":
        return _Hint("None")
    if t in _SCALARS:
        return _Hint(_SCALARS[t], [], nullable, scalar=True)
    if t == "array":
        if "items" not in schema:
            return _Hint("list[Any]", [LOSSY_ARRAY_ITEMS_UNSPECIFIED], nullable, {"Any"})
        if depth + 1 >= MAX_ARRAY_DEPTH and _is_array(schema["items"]):
            return _Hint("list[Any]", [LOSSY_ARRAY_DEPTH_EXCEEDED], nullable, {"Any"})
        item = _schema_hint(schema["items"], alias_for, depth + 1)
        return _Hint(f"list[{item.final}]", item.lossy, nullable, item.imports)
    return _any(LOSSY_TYPE_UNKNOWN)


def _is_array(schema) -> bool:
    return isinstance(schema, dict) and (schema.get("type") == "array"
                                         or (isinstance(schema.get("type"), list) and "array" in schema["type"]))


def _has_object(schema, depth: int = 0) -> bool:
    """An object-valued parameter (or array of objects): no supported serialisation."""
    if not isinstance(schema, dict) or depth > MAX_ARRAY_DEPTH + 1:
        return False
    t = schema.get("type")
    types = t if isinstance(t, list) else [t]
    if "object" in types or "properties" in schema or "additionalProperties" in schema:
        return True
    return "items" in schema and _has_object(schema["items"], depth + 1)


# --- planning ------------------------------------------------------------------------------------

def _merged(path_item: dict, operation: dict) -> list[dict]:
    """Path-item-level parameters, overridden by operation-level ones with the same (name, in)."""
    merged: dict = {}
    for p in [*(path_item.get("parameters") or []), *(operation.get("parameters") or [])]:
        key = (p.get("name"), p.get("in")) if isinstance(p, dict) and "$ref" not in p else ("$ref", id(p))
        merged[key] = p
    return list(merged.values())


def _serialisation(location: str, style: str, explode: bool, is_array: bool) -> str:
    if not is_array:
        return "scalar"
    if location != "query":
        return "comma"  # simple style: arrays are comma-separated whatever `explode` says
    if explode:
        return "repeat"
    return {"form": "comma", "spaceDelimited": "space", "pipeDelimited": "pipe"}[style]


def _allocate(natural: str, location: str, taken: set[str]) -> str:
    candidate = natural
    if candidate in taken:
        candidate = f"{natural}_{location}"
    n = 2
    while candidate in taken:
        candidate = f"{natural}_{location}_{n}"
        n += 1
    return candidate


def plan_parameters(path_item: dict, operation: dict, *, alias_prefix: str,
                    sanitise: Callable[[str, str], str], reserved: Iterable[str] = ()) -> OperationParams:
    """Plan the method parameters for one operation. `sanitise(name, fallback)` turns a wire name
    into an identifier (generate.py passes _safe_identifier). `reserved` adds names the caller's
    template uses that only it knows (the operation's model class names)."""
    out = OperationParams()
    candidates = []
    for p in _merged(path_item, operation):
        if not isinstance(p, dict) or "$ref" in p:
            out.skip_reason = SKIP_UNRESOLVED_REF
            return out
        name, location = p.get("name"), p.get("in")
        if not isinstance(name, str) or not isinstance(location, str):
            out.skip_reason = SKIP_INVALID_PARAMETER
            return out
        required = bool(p.get("required")) or location == "path"
        if location == "cookie":
            if required:
                out.skip_reason = SKIP_REQUIRED_COOKIE
                return out
            out.ignored.append({"name": name, "in": location, "reason": IGNORED_OPTIONAL_COOKIE})
            continue
        if location not in LOCATIONS:
            out.skip_reason = SKIP_UNSUPPORTED_LOCATION
            return out
        if location == "header" and name.lower() in IGNORED_HEADERS:
            out.ignored.append({"name": name, "in": location, "reason": IGNORED_RESERVED_HEADER})
            continue
        if "schema" not in p and "content" in p:
            if required:
                out.skip_reason = SKIP_REQUIRED_CONTENT
                return out
            out.ignored.append({"name": name, "in": location, "reason": IGNORED_OPTIONAL_CONTENT})
            continue
        style = p.get("style", _DEFAULT_STYLE[location])
        if style not in _SUPPORTED_STYLES[location]:
            out.skip_reason = SKIP_UNSUPPORTED_STYLE
            return out
        schema = p.get("schema", {})
        if _has_object(schema):
            out.skip_reason = SKIP_OBJECT_PARAMETER
            return out
        candidates.append((p, name, location, required, style))

    candidates.sort(key=lambda c: _LOCATION_RANK[c[2]])  # stable: spec order within a location
    taken = set(TEMPLATE_LOCALS) | set(reserved) | set(keyword.kwlist)
    for i, (p, name, location, required, style) in enumerate(candidates):
        natural = sanitise(name, f"param_{i}")
        if natural.startswith(("__", RESERVED_PREFIX)):  # name-mangled / template-reserved
            natural = "p" + natural
        local = _allocate(natural, location, taken)
        taken.add(local)

        def alias_for(literal: str, base: str = f"{alias_prefix}{_to_pascal_case(local)}Value") -> str:
            alias, n = base, 2
            while alias in out.aliases:
                alias, n = f"{base}{n}", n + 1
            if not _IDENT_RE.match(alias):
                raise ValueError("alias_prefix must make a valid identifier")
            out.aliases[alias] = literal
            return alias

        schema = p.get("schema", {})
        hint = _schema_hint(schema, alias_for)
        lossy = list(dict.fromkeys(hint.lossy))
        if location == "query" and p.get("allowReserved"):
            lossy.append(LOSSY_ALLOW_RESERVED_IGNORED)
        out.typing_imports |= hint.imports
        py_type = hint.final

        explode = p.get("explode", style == "form")
        out.params.append(ParamSpec(
            wire_name=name, local_name=local, location=location, required=required, py_type=py_type,
            style=style, explode=bool(explode),
            serialisation=_serialisation(location, style, bool(explode), _is_array(schema)),
            lossy=tuple(lossy)))
        out.lossy.extend({"name": name, "in": location, "reason": r} for r in lossy)
    return out


# --- rendering -----------------------------------------------------------------------------------

def _optional(py_type: str) -> str:
    return py_type if py_type.endswith(" | None") or py_type == "None" else f"{py_type} | None"


def render_signature(op_params: OperationParams, body: tuple[str, bool] | None) -> str:
    """The parameter list after `self`, as `, a: int, *, b: str | None = None`. `body` is
    (model class name, required) or None."""
    path = [p for p in op_params.params if p.location == "path"]
    rest = [p for p in op_params.params if p.location != "path"]
    parts = [f"{p.local_name}: {p.py_type}" for p in path]
    if body and body[1]:
        parts.append(f"body: {body[0]}")
    keyword_only = [f"{p.local_name}: {p.py_type}" for p in rest if p.required]
    keyword_only += [f"{p.local_name}: {_optional(p.py_type)} = None" for p in rest if not p.required]
    if body and not body[1]:
        keyword_only.append(f"body: {body[0]} | None = None")
    if keyword_only:
        parts += ["*", *keyword_only]
    return "".join(f", {part}" for part in parts)


def render_wire_args(op_params: OperationParams) -> tuple[str, str]:
    """Source for the query items list and the header items list fed to the SERIALISER_SOURCE
    helpers. Wire names only via repr(); local names only as variable references."""
    def items(location: str) -> str:
        return "[" + ", ".join(f"({p.wire_name!r}, {p.local_name}, {p.serialisation!r})"
                               for p in op_params.params if p.location == location) + "]"
    return items("query"), items("header")


# Emitted verbatim into every generated client by 4d (the client ships standalone, so it cannot
# import from relay). Fixed text: no spec content ever goes in here.
SERIALISER_SOURCE = '''\
from urllib.parse import quote as _relay_quote


def _relay_wire_str(value):
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def _relay_path_value(value):
    if isinstance(value, (list, tuple)):
        return ",".join(_relay_quote(_relay_wire_str(v), safe="") for v in value)
    return _relay_quote(_relay_wire_str(value), safe="")


def _relay_query_string(items):
    pairs = []
    for name, value, mode in items:
        if value is None:
            continue
        key = _relay_quote(name, safe="")
        if isinstance(value, (list, tuple)):
            parts = [_relay_quote(_relay_wire_str(v), safe="") for v in value if v is not None]
            if mode == "repeat":
                pairs.extend(key + "=" + part for part in parts)
            else:
                delimiter = {"comma": ",", "space": "%20", "pipe": "|"}[mode]
                pairs.append(key + "=" + delimiter.join(parts))
        else:
            pairs.append(key + "=" + _relay_quote(_relay_wire_str(value), safe=""))
    return "&".join(pairs)


def _relay_header_dict(items):
    headers = {}
    for name, value, _mode in items:
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            headers[name] = ",".join(_relay_wire_str(v) for v in value if v is not None)
        else:
            headers[name] = _relay_wire_str(value)
    return headers
'''
