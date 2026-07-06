#!/usr/bin/env python3
"""CI tripwire: detects first-match-wins provider/module resolution with no
priority tie-break -- a function that iterates a collection, matches a
caller-supplied name against id/type fields, and returns on first match with
no min()/sorted()/max() tie-break among multiple structurally possible
matches. This is the anti-pattern fixed in amplifier-foundation PR #267,
amplifier-bundle-routing-matrix PR #31, and amplifier-app-cli PR #214/#215.
Fix pattern: docs/PATTERNS.md#priority-based-provider-resolution.

Stdlib-only: ast, argparse, pathlib, json, sys, re, fnmatch, subprocess.
Companion modules _yaml_lite.py and _ast_scan.py hold generic, rule-agnostic
helpers so this file stays under its <300-line budget.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from _ast_scan import (
    const_str,
    flatten_or,
    has_return_or_break,
    has_tiebreak_call,
    iter_functions,
    walk_own,
)
from _yaml_lite import load_config as _load_config

ID_FIELD_NAMES = ["id", "instance_id", "name"]
TYPE_FIELD_NAMES = ["module", "type", "provider_type", "kind"]
TIEBREAK_FIELD_NAMES = ["priority", "rank", "precedence"]
DEFAULT_EXCLUDE_GLOBS = [".venv/**", "**/tests/**", "**/test_*.py"]
TIEBREAK_CALLS = {"min", "max", "sorted", "sort"}
STR_TRANSFORMS = {
    "replace",
    "removeprefix",
    "removesuffix",
    "strip",
    "lstrip",
    "rstrip",
    "lower",
    "upper",
}
DOC_REFERENCE = "docs/PATTERNS.md#priority-based-provider-resolution"
CONFIG_FILENAME = ".resolver-priority-tripwire.yaml"


@dataclass
class Finding:
    file: str
    function: str
    line: int
    tier: str  # "ERROR" | "WARN"
    reason: str

    @property
    def key(self) -> str:
        return f"{self.file}:{self.function}:{self.line}"


def load_config(repo_root: Path) -> dict:
    defaults = {
        "id_field_names": ID_FIELD_NAMES,
        "type_field_names": TYPE_FIELD_NAMES,
        "tiebreak_field_names": TIEBREAK_FIELD_NAMES,
        "exclude_globs": DEFAULT_EXCLUDE_GLOBS,
    }
    return _load_config(repo_root, CONFIG_FILENAME, defaults)


def _analyze_function(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    qualname: str,
    relpath: str,
    id_fields: set[str],
    type_fields: set[str],
    file_has_tiebreak_vocab: bool,
) -> list[Finding]:
    """Apply the resolver-priority-tripwire detection algorithm to one function."""
    params = {a.arg for a in func.args.args if a.arg not in ("self", "cls")} | {
        a.arg for a in func.args.kwonlyargs
    }
    if not params:
        return []
    own = list(walk_own(func))
    if not any(isinstance(n, (ast.For, ast.AsyncFor)) for n in own):
        return []

    all_fields = id_fields | type_fields
    field_alias: dict[str, str] = {}
    param_alias: set[str] = set(params)

    def resolve_field(expr: ast.AST | None) -> str | None:
        if expr is None:
            return None
        if isinstance(expr, ast.IfExp):
            return resolve_field(expr.body) or resolve_field(expr.orelse)
        if isinstance(expr, ast.Name):
            return field_alias.get(expr.id)
        if isinstance(expr, ast.Attribute):
            return expr.attr if expr.attr in all_fields else resolve_field(expr.value)
        if isinstance(expr, ast.Subscript):
            key = const_str(expr.slice)
            return key if key in all_fields else None
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute):
            if expr.func.attr == "get" and expr.args:
                key = const_str(expr.args[0])
                return key if key in all_fields else None
            if expr.func.attr in STR_TRANSFORMS:
                return resolve_field(expr.func.value)
            return next((r for a in expr.args if (r := resolve_field(a))), None)
        return None

    def resolve_param(expr: ast.AST) -> bool:
        if isinstance(expr, ast.Name):
            return expr.id in param_alias
        if isinstance(expr, ast.JoinedStr):
            return any(
                isinstance(v, ast.FormattedValue) and resolve_param(v.value)
                for v in expr.values
            )
        if isinstance(expr, ast.Call):
            return any(resolve_param(a) for a in expr.args)
        return False

    for node in own:  # Build field/param alias maps (single-hop, source order).
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target = node.targets[0].id
            if field := resolve_field(node.value):
                field_alias[target] = field
            elif isinstance(node.value, ast.Name) and resolve_param(node.value):
                param_alias.add(target)

    def tier_of(field: str) -> str | None:
        return "id" if field in id_fields else "type" if field in type_fields else None

    def classify(clause: ast.expr) -> tuple[str, str] | None:
        if not isinstance(clause, ast.Compare) or len(clause.ops) != 1:
            return None
        op, left, right = clause.ops[0], clause.left, clause.comparators[0]
        if (
            isinstance(op, ast.In)
            and isinstance(right, (ast.Tuple, ast.List))
            and resolve_param(left)
        ):
            elts = right.elts
            loopish = any(
                isinstance(e, ast.Name)
                or (
                    isinstance(e, ast.Call)
                    and isinstance(e.func, ast.Attribute)
                    and e.func.attr in STR_TRANSFORMS
                )
                for e in elts
            )
            if loopish and any(resolve_param(e) for e in elts):
                return (
                    "tuple_idiom",
                    "dict-key tuple-of-variants idiom (key, key.replace(...), f'provider-{x}')",
                )
        if isinstance(op, (ast.In, ast.Eq)):
            for field_side, param_side in ((left, right), (right, left)):
                if (
                    (f := resolve_field(field_side))
                    and resolve_param(param_side)
                    and (t := tier_of(f))
                ):
                    return (t, f)
        return None

    findings: list[Finding] = []
    for node in own:
        if not isinstance(node, ast.If):
            continue
        cats = [c for c in (classify(c) for c in flatten_or(node.test)) if c]
        if not cats or not has_return_or_break(node.body):
            continue
        kinds, evidence = {c[0] for c in cats}, ", ".join(sorted({c[1] for c in cats}))
        if "tuple_idiom" in kinds or ("id" in kinds and "type" in kinds):
            findings.append(Finding(relpath, qualname, node.lineno, "ERROR", evidence))
        elif kinds == {"type"}:
            tier = "ERROR" if file_has_tiebreak_vocab else "WARN"
            findings.append(
                Finding(
                    relpath,
                    qualname,
                    node.lineno,
                    tier,
                    f"type-only match on '{evidence}', first-match-wins",
                )
            )

    return [] if findings and has_tiebreak_call(func, TIEBREAK_CALLS) else findings


def analyze_file(path: Path, repo_root: Path, cfg: dict) -> list[Finding]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return []
    relpath = path.resolve().relative_to(repo_root.resolve()).as_posix()
    id_fields, type_fields = set(cfg["id_field_names"]), set(cfg["type_field_names"])
    has_vocab = any(
        re.search(rf"\b{re.escape(w)}\b", source, re.IGNORECASE)
        for w in cfg["tiebreak_field_names"]
    )
    return [
        finding
        for qualname, func in iter_functions(tree)
        for finding in _analyze_function(
            func, qualname, relpath, id_fields, type_fields, has_vocab
        )
    ]


def _all_py_files(repo_root: Path, exclude_globs: list[str]) -> list[Path]:
    return [
        p
        for p in repo_root.rglob("*.py")
        if not any(
            fnmatch.fnmatch(p.relative_to(repo_root).as_posix(), pat)
            for pat in exclude_globs
        )
    ]


def _diff_files(repo_root: Path, base: str) -> list[Path]:
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", f"{base}...HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"warning: git diff against {base!r} failed: {exc}", file=sys.stderr)
        return []
    return [
        repo_root / line
        for line in proc.stdout.splitlines()
        if line.endswith(".py") and (repo_root / line).exists()
    ]


def render_report(findings: list[Finding], baseline: set[str]) -> tuple[str, dict]:
    errors = [f for f in findings if f.tier == "ERROR" and f.key not in baseline]
    warnings = [f for f in findings if f.tier == "WARN" and f.key not in baseline]
    baselined = [f for f in findings if f.key in baseline]

    lines = [
        f"resolver-priority-tripwire: {len(errors)} error(s), {len(warnings)} warning(s), {len(baselined)} baselined"
    ]
    for f in errors + warnings + baselined:
        status = "BASELINED" if f.key in baseline else f.tier
        lines.append(f"[{status}] {f.key}: {f.reason}")
        if status != "BASELINED":
            lines.append(f"    Fix pattern: see {DOC_REFERENCE}")

    payload = {
        "summary": {
            "errors": len(errors),
            "warnings": len(warnings),
            "baselined": len(baselined),
        },
        "doc_reference": DOC_REFERENCE,
        "findings": [
            {**asdict(f), "key": f.key, "baselined": f.key in baseline}
            for f in findings
        ],
    }
    return "\n".join(lines), payload


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="resolver_priority_tripwire",
        description="Detects first-match-wins provider/module resolution with no priority tie-break.",
    )
    ap.add_argument("--repo-root", default=".", help="Repository root (default: cwd)")
    ap.add_argument(
        "--all", action="store_true", help="Scan the full repo (default mode)"
    )
    ap.add_argument(
        "--diff-only", action="store_true", help="Scope to files changed vs --base"
    )
    ap.add_argument("--base", default="origin/main", help="Base ref for --diff-only")
    ap.add_argument(
        "--baseline", help="Baseline JSON path suppressing pre-accepted findings"
    )
    ap.add_argument(
        "--json-out", help="Path for the machine-readable JSON report ('-' for stdout)"
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    repo_root = Path(args.repo_root).expanduser().resolve()
    cfg = load_config(repo_root)
    files = (
        _diff_files(repo_root, args.base)
        if args.diff_only
        else _all_py_files(repo_root, cfg["exclude_globs"])
    )

    baseline: set[str] = set()
    if args.baseline and (bp := Path(args.baseline).expanduser()).exists():
        baseline = set(json.loads(bp.read_text(encoding="utf-8")).get("baseline", []))

    findings = [f for file in files for f in analyze_file(file, repo_root, cfg)]
    text, payload = render_report(findings, baseline)
    print(text)
    if args.json_out == "-":
        print(json.dumps(payload, indent=2))
    elif args.json_out:
        Path(args.json_out).write_text(json.dumps(payload, indent=2), encoding="utf-8")

    return (
        1 if any(f.tier == "ERROR" and f.key not in baseline for f in findings) else 0
    )


if __name__ == "__main__":
    sys.exit(main())
