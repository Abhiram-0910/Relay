"""Spec-text-to-identifier helpers, shared by generate.py and params.py (moved verbatim from
generate.py so params.py can use them without importing generate.py, which will import params.py)."""
import keyword
import re

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_identifier(name: str, fallback: str) -> str:
    """A spec-supplied name, made safe to use as a Python identifier in generated source. Spec
    text is attacker-controlled input, not code — anything that isn't already a bare identifier
    (and not a Python keyword) is replaced with a deterministic fallback rather than trusted
    verbatim."""
    if _IDENT_RE.match(name) and not keyword.iskeyword(name):
        return name
    return fallback


def _to_pascal_case(name: str) -> str:
    parts = [p for p in re.split(r"[^a-zA-Z0-9]+", name) if p]
    return "".join(p[:1].upper() + p[1:] for p in parts)
