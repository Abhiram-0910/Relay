"""assert_generated_source_inert: a pure, structural check that a generated Python module does
nothing at import time beyond defining things.

Seeds the corrector-patch validator (Phase 3 step 6). It is defense in depth, not proof: it says
nothing about what the defined functions do when called (beyond banning dunder access), and the
sandbox stays the real boundary.
"""
from __future__ import annotations

import ast

# Every module generate.py's client template and datamodel-code-generator's pydantic-v2 output were
# observed to import (formats, enums, unions, recursion, root models, Open-Meteo, Petstore v3).
# (level, module): level 1 is the generated package's own relative `from .models import ...`.
ALLOWED_IMPORTS = frozenset({
    (0, "__future__"),
    (0, "requests"),
    (0, "pydantic"),
    (0, "typing"),
    (0, "enum"),
    (0, "datetime"),
    (0, "decimal"),
    (0, "ipaddress"),
    (0, "uuid"),
    (1, "models"),
})


def _is_dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def _is_model_rebuild(stmt: ast.stmt, defined: set[str]) -> bool:
    """`X.model_rebuild()` with no arguments, X a class defined earlier in this module: the one
    module-level call datamodel-code-generator emits (for recursive schemas)."""
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
        return False
    call = stmt.value
    return (not call.args and not call.keywords and isinstance(call.func, ast.Attribute)
            and call.func.attr == "model_rebuild" and isinstance(call.func.value, ast.Name)
            and call.func.value.id in defined)


def _calls_outside_annotations(node: ast.AST) -> list[ast.Call]:
    """Call nodes under `node`, skipping annotation subtrees (only evaluated lazily under
    `from __future__ import annotations`)."""
    found: list[ast.Call] = []

    def visit(n: ast.AST) -> None:
        if isinstance(n, ast.Call):
            found.append(n)
        for field, value in ast.iter_fields(n):
            if field in ("annotation", "returns"):
                continue
            for child in value if isinstance(value, list) else [value]:
                if isinstance(child, ast.AST):
                    visit(child)

    visit(node)
    return found


def assert_generated_source_inert(src: str) -> None:
    tree = ast.parse(src)

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and _is_dunder(node.attr):
            raise AssertionError(f"dunder attribute access: .{node.attr} (line {node.lineno})")
        if isinstance(node, ast.Name) and _is_dunder(node.id):
            raise AssertionError(f"dunder name: {node.id} (line {node.lineno})")

    defined: set[str] = set()
    for i, stmt in enumerate(tree.body):
        where = f"line {stmt.lineno}"
        if isinstance(stmt, ast.Expr):
            if i == 0 and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                continue  # module docstring
            if _is_model_rebuild(stmt, defined):
                continue
            raise AssertionError(f"module-level expression statement ({where}): {ast.unparse(stmt)[:80]}")
        if isinstance(stmt, ast.ImportFrom):
            if (stmt.level, stmt.module) not in ALLOWED_IMPORTS:
                raise AssertionError(f"import not allowlisted ({where}): {ast.unparse(stmt)}")
            continue
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                if (0, alias.name) not in ALLOWED_IMPORTS:
                    raise AssertionError(f"import not allowlisted ({where}): {alias.name}")
            continue
        if isinstance(stmt, (ast.ClassDef, ast.FunctionDef)):
            # The body runs later (class body: at definition, but only Field()/ConfigDict()-style
            # calls appear there); what runs at import is decorators, bases, keywords and defaults.
            header = [*stmt.decorator_list]
            if isinstance(stmt, ast.ClassDef):
                header += [*stmt.bases, *stmt.keywords]
            else:
                header += [*stmt.args.defaults, *[d for d in stmt.args.kw_defaults if d is not None]]
            for part in header:
                if _calls_outside_annotations(part):
                    raise AssertionError(f"call in {stmt.name} definition header ({where})")
            defined.add(stmt.name)
            continue
        if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            if not all(isinstance(t, ast.Name) for t in targets):
                raise AssertionError(f"non-simple assignment target ({where})")
            if stmt.value is not None and _calls_outside_annotations(stmt.value):
                raise AssertionError(f"call in module-level assignment ({where})")
            defined.update(t.id for t in targets)
            continue
        raise AssertionError(f"disallowed module-level statement {type(stmt).__name__} ({where})")
