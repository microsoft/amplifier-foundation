#!/usr/bin/env python3
"""Generate and sync PYEOF heredoc blocks in recipe YAML files.

Reads the standalone Python scripts (parse_bundle_composition.py and
analyze_composition_effects.py), escapes recipe-engine template variables
(``{{var}}`` → ``{ {var} }``), and embeds them as ``<< 'PYEOF'`` heredocs
in each recipe YAML file that contains ``parse-composition`` and
``analyze-composition`` bash steps.

Usage:
    python generate_recipe_heredocs.py            # update in-place
    python generate_recipe_heredocs.py --check    # exit 1 on drift
"""

from __future__ import annotations

import difflib
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPTS_DIR = Path(__file__).resolve().parent
RECIPES_DIR = SCRIPTS_DIR.parent

PARSE_SCRIPT = SCRIPTS_DIR / "parse_bundle_composition.py"
ANALYZE_SCRIPT = SCRIPTS_DIR / "analyze_composition_effects.py"

# Recipe files that contain the two bash steps.  Missing files are skipped
# with a warning so the script works even if only one recipe exists.
TARGET_RECIPES: list[Path] = [
    RECIPES_DIR / "bundle-behavioral-model.yaml",
    RECIPES_DIR / "change-spec-to-behavioral-model.yaml",
]

# ---------------------------------------------------------------------------
# Template-variable escaping
# ---------------------------------------------------------------------------


def escape_template_vars(code: str) -> str:
    """Replace ``{{`` / ``}}`` with ``{ {`` / ``} }`` in Python source.

    The recipe engine interprets ``{{name}}`` as template variables.  Inside
    heredoc Python code we must break those sequences so they pass through
    as literal text.  Only *un-escaped* double-braces (i.e. not preceded by
    a backslash) are replaced — regex strings like ``r"\\{\\{"`` are safe.
    """
    code = code.replace("{{", "{ {")
    code = code.replace("}}", "} }")
    return code


# ---------------------------------------------------------------------------
# Source-code surgery helpers
# ---------------------------------------------------------------------------


def strip_main_and_name_guard(code: str) -> str:
    """Remove the module-level ``def main()`` and ``if __name__`` block.

    Cuts from the first ``def main(`` at column 0 through end-of-file.
    Trailing blank lines above the cut are also removed.
    """
    lines = code.split("\n")
    cut: int | None = None
    for i, line in enumerate(lines):
        if re.match(r"^def main\(", line):
            cut = i
            break
    if cut is None:
        return code
    # Also strip preceding blank lines
    while cut > 0 and lines[cut - 1].strip() == "":
        cut -= 1
    result = "\n".join(lines[:cut])
    if not result.endswith("\n"):
        result += "\n"
    return result


def strip_header_and_imports(code: str) -> str:
    """Remove shebang, module docstring, and top-level import lines."""
    lines = code.split("\n")
    i = 0
    n = len(lines)

    # Skip shebang
    if i < n and lines[i].startswith("#!"):
        i += 1

    # Skip blank lines
    while i < n and lines[i].strip() == "":
        i += 1

    # Skip module docstring (triple-quoted)
    if i < n and lines[i].strip().startswith('"""'):
        # Check single-line docstring  ("""...""" on one line, >3 chars)
        rest_after_open = lines[i].strip()[3:]
        if '"""' in rest_after_open:
            i += 1
        else:
            i += 1
            while i < n:
                if '"""' in lines[i]:
                    i += 1
                    break
                i += 1

    # Skip blank lines and import statements
    while i < n:
        s = lines[i].strip()
        if s == "" or s.startswith("import ") or s.startswith("from "):
            i += 1
        else:
            break

    return "\n".join(lines[i:])


# ---------------------------------------------------------------------------
# Combined main() for the analyze-composition step
# ---------------------------------------------------------------------------

COMBINED_MAIN = '''\


def main() -> None:
    """CLI entrypoint: parse + analyze a bundle, output composition effects JSON."""
    if len(sys.argv) != 3:
        error = {"error": f"Usage: {sys.argv[0]} <bundle_name> <registry_path>"}
        print(json.dumps(error))
        sys.exit(1)

    bundle_name = sys.argv[1]
    registry_path = Path(sys.argv[2]).expanduser()

    bundles = load_registry(registry_path)

    if bundle_name not in bundles:
        error = {"error": f"Bundle '{bundle_name}' not found in registry"}
        print(json.dumps(error))
        sys.exit(1)

    manifest = build_manifest(bundle_name, bundles, registry_path=registry_path)
    result = analyze(manifest)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
'''


# ---------------------------------------------------------------------------
# Heredoc generators
# ---------------------------------------------------------------------------


def generate_parse_python() -> str:
    """Return the escaped Python source for the parse-composition heredoc."""
    code = PARSE_SCRIPT.read_text(encoding="utf-8")
    return escape_template_vars(code)


def generate_analyze_python() -> str:
    """Return the combined + escaped Python source for analyze-composition."""
    parse_raw = PARSE_SCRIPT.read_text(encoding="utf-8")
    analyze_raw = ANALYZE_SCRIPT.read_text(encoding="utf-8")

    parse_body = strip_main_and_name_guard(parse_raw)
    analyze_body = strip_header_and_imports(strip_main_and_name_guard(analyze_raw))

    combined = (
        parse_body.rstrip("\n") + "\n\n" + analyze_body.rstrip("\n") + COMBINED_MAIN
    )
    return escape_template_vars(combined)


def build_command_lines(python_code: str) -> list[str]:
    """Build the shell command body lines (un-indented) for a PYEOF heredoc."""
    lines: list[str] = [
        "set -euo pipefail",
        '"${AMPLIFIER_PYTHON:-python3}" - "{{bundle_name}}" "{{registry_path}}" << \'PYEOF\'',
    ]
    for pyline in python_code.rstrip("\n").split("\n"):
        lines.append(pyline)
    lines.append("PYEOF")
    return lines


# ---------------------------------------------------------------------------
# YAML surgery — locate and replace command blocks
# ---------------------------------------------------------------------------


def _find_command_block(lines: list[str], step_id: str) -> tuple[int, int, int]:
    """Return (body_start, body_end, body_indent) for *step_id*'s command block.

    *body_start* is the first line of the command body (line after ``command: |``).
    *body_end*   is the first line **after** the body.
    *body_indent* is the indentation width of the body content.

    Raises ``ValueError`` when the step or its ``command: |`` is not found.
    """
    # 1. Find the step
    step_line: int | None = None
    for i, line in enumerate(lines):
        if re.search(rf'id:\s*["\']?{re.escape(step_id)}["\']?', line):
            step_line = i
            break
    if step_line is None:
        raise ValueError(f"step '{step_id}' not found")

    # 2. Find command: | within the step
    cmd_line: int | None = None
    for i in range(step_line + 1, len(lines)):
        if re.match(r"^\s*command:\s*\|\s*$", lines[i]):
            cmd_line = i
            break
        # Hit another step before finding command:
        if re.match(r"\s*-\s+id:", lines[i]):
            break
    if cmd_line is None:
        raise ValueError(f"no 'command: |' found for step '{step_id}'")

    cmd_indent = len(lines[cmd_line]) - len(lines[cmd_line].lstrip())
    body_indent = cmd_indent + 2

    # 3. Determine body extent
    body_start = cmd_line + 1
    body_end = body_start
    for i in range(body_start, len(lines)):
        if lines[i].strip() == "":
            # Blank line — could be inside or after the block.  Look ahead
            # to see if the next non-blank line is still indented.
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            if j < len(lines):
                next_indent = len(lines[j]) - len(lines[j].lstrip())
                if next_indent >= body_indent:
                    body_end = i + 1
                    continue
            # Either EOF or next non-blank line is at a lower indent → stop.
            break
        curr_indent = len(lines[i]) - len(lines[i].lstrip())
        if curr_indent >= body_indent:
            body_end = i + 1
        else:
            break

    return body_start, body_end, body_indent


def replace_step_command(yaml_text: str, step_id: str, new_body: list[str]) -> str:
    """Replace the ``command: |`` block of *step_id* in *yaml_text*."""
    lines = yaml_text.split("\n")
    start, end, indent = _find_command_block(lines, step_id)

    prefix = " " * indent
    new_lines: list[str] = []
    for bline in new_body:
        if bline:
            new_lines.append(prefix + bline)
        else:
            new_lines.append("")
    # Ensure one trailing blank line after the block (visual separator)
    if end < len(lines) and lines[end].strip() != "":
        new_lines.append("")

    result = lines[:start] + new_lines + lines[end:]
    return "\n".join(result)


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def update_recipe(recipe_path: Path, *, check: bool = False) -> bool:
    """Update (or check) a single recipe file.  Returns True when clean."""
    original = recipe_path.read_text(encoding="utf-8")
    updated = original

    # --- parse-composition ---
    parse_py = generate_parse_python()
    parse_cmd = build_command_lines(parse_py)
    try:
        updated = replace_step_command(updated, "parse-composition", parse_cmd)
    except ValueError as exc:
        print(f"  SKIP parse-composition in {recipe_path.name}: {exc}")

    # --- analyze-composition ---
    analyze_py = generate_analyze_python()
    analyze_cmd = build_command_lines(analyze_py)
    try:
        updated = replace_step_command(updated, "analyze-composition", analyze_cmd)
    except ValueError as exc:
        print(f"  SKIP analyze-composition in {recipe_path.name}: {exc}")

    if original == updated:
        return True  # no changes

    if check:
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{recipe_path.name}",
            tofile=f"b/{recipe_path.name}",
        )
        sys.stderr.writelines(diff)
        return False

    recipe_path.write_text(updated, encoding="utf-8")
    return False  # changes were written


def main() -> None:
    """CLI entrypoint."""
    check = "--check" in sys.argv

    any_drift = False
    for recipe_path in TARGET_RECIPES:
        if not recipe_path.exists():
            print(f"  SKIP {recipe_path.name} (file not found)")
            continue
        clean = update_recipe(recipe_path, check=check)
        if clean:
            print(f"  OK   {recipe_path.name}")
        elif check:
            print(f"  DRIFT {recipe_path.name}")
            any_drift = True
        else:
            print(f"  WROTE {recipe_path.name}")

    if check and any_drift:
        print("\nHeredocs are out of sync.  Run:")
        print("  python recipes/scripts/generate_recipe_heredocs.py")
        sys.exit(1)


if __name__ == "__main__":
    main()
