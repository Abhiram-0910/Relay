"""Live-call planning (piece 5a): decide whether Relay calls a generated operation live, and with
which values for its REQUIRED parameters. Pure: no network. Every value comes from the spec itself
(example, examples, schema example, default, const, enum, numeric bounds) and only ever becomes a
kwargs value passed to the generated client at run time, never source text. Nothing is invented:
a required parameter with no usable spec value skips the live call.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field

from app.params import merged_parameters

SKIP_NOT_GET = "not_get"
SKIP_REQUIRES_AUTH = "requires_auth"
SKIP_POSSIBLY_STATE_CHANGING = "possibly_state_changing"
SKIP_NO_SAMPLE_VALUE = "no_sample_value"  # reported as f"{SKIP_NO_SAMPLE_VALUE}:{local_name}"
SKIP_LIVE_BUDGET_EXCEEDED = "live_budget_exceeded"  # applied by ci_runner, not by plan_live_call

STATE_CHANGING_TOKENS = frozenset({
    "logout", "signout", "delete", "remove", "destroy", "revoke", "cancel", "unsubscribe", "reset",
    "purge", "clear", "deactivate", "disable",
})
MAX_VALUE_CHARS = 256

_WORD = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
_MISSING = object()


@dataclass
class LivePlan:
    kwargs: dict = field(default_factory=dict)          # {local_name: value}
    value_sources: dict = field(default_factory=dict)   # {local_name: tier label}
    skip_reason: str | None = None


def _tokens(text: str) -> list[str]:
    """Whole-word tokens: split on non-alphanumerics, then on camelCase boundaries, lowercased."""
    return [w.lower() for chunk in re.split(r"[^A-Za-z0-9]+", text) for w in _WORD.findall(chunk)]


def possibly_state_changing(path: str, operation_id) -> bool:
    """A denylisted token in the path or operationId. Adjacent token pairs are joined too, so a
    camelCase `signOut`/`logOut` matches its one-word form; `presetValues` still does not."""
    for text in (path, operation_id if isinstance(operation_id, str) else ""):
        toks = _tokens(text)
        if STATE_CHANGING_TOKENS & {*toks, *(a + b for a, b in zip(toks, toks[1:]))}:
            return True
    return False


def requires_auth(spec: dict, operation: dict) -> bool:
    """Effective security: the operation's `security` overrides the global one; absent or `[]`
    means none; a list holding an empty object `{}` makes auth optional."""
    requirements = operation["security"] if "security" in operation else spec.get("security")
    if not requirements:
        return False
    return not any(isinstance(r, dict) and not r for r in requirements)


def _declared_types(schema: dict) -> set[str]:
    t = schema.get("type")
    return {x for x in (t if isinstance(t, list) else [t]) if isinstance(x, str)} - {"null"}


def _type_ok(value, types: set[str]) -> bool:
    is_int = isinstance(value, int) and not isinstance(value, bool)
    is_num = (is_int or isinstance(value, float)) and math.isfinite(value)
    checks = {"integer": is_int, "number": is_num, "string": isinstance(value, str),
              "boolean": isinstance(value, bool)}
    if not types:  # no declared type: any plain scalar
        return isinstance(value, (str, bool)) or is_num
    return any(checks.get(t, False) for t in types)


def _clean(value) -> bool:
    text = value if isinstance(value, str) else str(value)
    return len(text) <= MAX_VALUE_CHARS and not any(unicodedata.category(c) == "Cc" for c in text)


def _bound(value):
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    return value if ok else None


def _constraint(schema: dict):
    """Numeric bounds -> a value inside them. Exclusive bounds are usable for integers only."""
    types = _declared_types(schema)
    integer = types == {"integer"}
    if not types or not types <= {"integer", "number"}:
        return _MISSING
    lo, hi = _bound(schema.get("minimum")), _bound(schema.get("maximum"))
    ex_lo, ex_hi = schema.get("exclusiveMinimum"), schema.get("exclusiveMaximum")
    # 3.1: numeric exclusive bounds; 3.0: boolean flags on minimum/maximum.
    lo_excl = _bound(ex_lo) if _bound(ex_lo) is not None else (lo if ex_lo is True else None)
    hi_excl = _bound(ex_hi) if _bound(ex_hi) is not None else (hi if ex_hi is True else None)
    if (lo_excl is not None or hi_excl is not None) and not integer:
        return _MISSING
    if lo_excl is not None:
        lo = math.floor(lo_excl) + 1
    elif lo is not None and integer:
        lo = math.ceil(lo)
    if hi_excl is not None:
        hi = math.ceil(hi_excl) - 1
    elif hi is not None and integer:
        hi = math.floor(hi)
    if lo is not None and hi is not None:
        if lo > hi:
            return _MISSING
        return (lo + hi) // 2 if integer else (lo + hi) / 2
    return lo if lo is not None else (hi if hi is not None else _MISSING)


def _schema_candidates(schema: dict):
    """(label, value) from the schema itself, in tier order."""
    example = schema.get("example", _MISSING)
    if example is _MISSING and isinstance(schema.get("examples"), list) and schema["examples"]:
        example = schema["examples"][0]  # 3.1 schema-level examples
    yield "schema_example", example
    yield "default", schema.get("default", _MISSING)
    yield "const", schema.get("const", _MISSING)
    members = [m for m in schema.get("enum") or [] if m is not None]
    yield "enum", members[0] if members else _MISSING
    yield "constraint", _constraint(schema)


def _param_candidates(param: dict):
    """(label, value) for one parameter, in tier order."""
    yield "example", param.get("example", _MISSING)
    examples = param.get("examples")
    first = next((e["value"] for e in (examples.values() if isinstance(examples, dict) else [])
                  if isinstance(e, dict) and "value" in e), _MISSING)
    yield "examples", first
    yield from _schema_candidates(param.get("schema") or {})


def _pick(param: dict) -> tuple[str, object]:
    """The first usable (label, value); else (rejected label or None, _MISSING)."""
    schema = param.get("schema") or {}
    is_array = "array" in _declared_types(schema)
    item_schema = schema.get("items") if isinstance(schema.get("items"), dict) else {}
    types = _declared_types(item_schema) if is_array else _declared_types(schema)
    rejected = None

    def usable(value) -> bool:
        return _type_ok(value, types) and _clean(value)

    candidates = list(_param_candidates(param))
    if is_array:  # one-element list: a param-level list's first member, then the items' own tiers
        candidates += [(label, v) for label, v in _schema_candidates(item_schema)]
    for label, value in candidates:
        if value is _MISSING:
            continue
        if is_array and isinstance(value, list):
            value = value[0] if value else _MISSING
            if value is _MISSING:
                continue
        if usable(value):
            return label, [value] if is_array else value
        rejected = rejected or f"{label}_rejected"
    return rejected, _MISSING


def plan_live_call(spec: dict, path: str, method: str, params_table: list[dict],
                   presets: dict | None = None) -> LivePlan:
    """Gates in order (not_get, requires_auth, possibly_state_changing, no_sample_value), then
    kwargs for every REQUIRED parameter in `params_table` (app.params' to_jsonable()), keyed by
    local_name. Optional parameters are omitted. `presets` ({wire_name: value}, ci_runner's
    DEMO_PRESETS) fills a parameter only when no tier does, labelled `demo_preset`."""
    if method.upper() != "GET":
        return LivePlan(skip_reason=SKIP_NOT_GET)
    path_item = spec["paths"][path]
    operation = path_item[method.lower()]
    if requires_auth(spec, operation):
        return LivePlan(skip_reason=SKIP_REQUIRES_AUTH)
    if possibly_state_changing(path, operation.get("operationId")):
        return LivePlan(skip_reason=SKIP_POSSIBLY_STATE_CHANGING)
    raw = {(p.get("name"), p.get("in")): p for p in merged_parameters(path_item, operation)}
    plan = LivePlan()
    for entry in params_table:
        if not entry["required"]:
            continue
        local = entry["local_name"]
        label, value = _pick(raw[(entry["wire_name"], entry["location"])])
        if value is _MISSING and entry["wire_name"] in (presets or {}):
            label, value = "demo_preset", presets[entry["wire_name"]]
        if value is _MISSING:
            if label:
                plan.value_sources[local] = label
            plan.skip_reason = f"{SKIP_NO_SAMPLE_VALUE}:{local}"
            return plan
        plan.kwargs[local] = value
        plan.value_sources[local] = label
    return plan
