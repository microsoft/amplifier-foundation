"""Generic AST scanning helpers shared by resolver_priority_tripwire.py.

Kept in a companion module so the main tripwire script stays under its
<300-line budget; these are pure tree-walking utilities with no knowledge
of the resolver-priority detection rules themselves.
"""

from __future__ import annotations

import ast


def walk_own(node: ast.AST):
    """Descendants of node, never crossing into nested function/lambda scopes."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield child
        yield from walk_own(child)


def iter_functions(node: ast.AST, prefix: str = ""):
    """Yield (qualname, def) for every function at any nesting depth."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            yield from iter_functions(child, f"{prefix}{child.name}.")
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = f"{prefix}{child.name}"
            yield qualname, child
            yield from iter_functions(child, f"{qualname}.")
        else:
            yield from iter_functions(child, prefix)


def const_str(node: ast.AST | None) -> str | None:
    return (
        node.value
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        else None
    )


def flatten_or(test: ast.expr) -> list[ast.expr]:
    if isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
        return [c for v in test.values for c in flatten_or(v)]
    return [test]


def has_return_or_break(stmts: list[ast.stmt]) -> bool:
    return any(
        isinstance(n, (ast.Return, ast.Break)) for s in stmts for n in ast.walk(s)
    )


def has_tiebreak_call(func: ast.AST, tiebreak_calls: set[str]) -> bool:
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (isinstance(f, ast.Name) and f.id in tiebreak_calls) or (
            isinstance(f, ast.Attribute) and f.attr in tiebreak_calls
        ):
            return True
    return False
