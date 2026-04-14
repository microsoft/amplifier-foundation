#!/usr/bin/env python3
"""Generate a DOT graph of event flows in Amplifier module source files.

Introspects Python source files to extract:
- Events emitted via hooks.emit("event:name", ...)
- Events registered via register_capability("observability.events", [...]) or
  register_contributor("observability.events", ...)

Generates a DOT digraph to stdout showing the relationship between modules
and their events, with color coding:
- Module nodes: lightblue boxes
- Registered events: lightgreen ovals
- Unregistered (non-canonical) events: red ovals  (warning — not declared)
- Canonical amplifier_core events: lightyellow ovals (built-in, no registration needed)

Usage:
    python scripts/generate_event_dot.py modules/ > events.dot
    dot -Tsvg events.dot > events.svg

    python scripts/generate_event_dot.py modules/tool-delegate/ > delegate.dot
    python scripts/generate_event_dot.py modules/ --format json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Canonical event prefixes from amplifier_core — no registration required
# ---------------------------------------------------------------------------

CANONICAL_PREFIXES: frozenset[str] = frozenset(
    {
        "session",
        "llm",
        "provider",
        "tool",
        "execution",
        "orchestrator",
    }
)

# ---------------------------------------------------------------------------
# Compiled regex patterns  (reused from lint_observability patterns)
# ---------------------------------------------------------------------------

# Matches hooks.emit("event:name", ...) — covers both hooks.emit and self.hooks.emit
_EMIT_RE = re.compile(r'hooks\.emit\(\s*["\'"]([^"\']+)["\'"]')

# Matches register_capability or register_contributor for observability.events
_REGISTER_RE = re.compile(
    r'register_(?:capability|contributor)\s*\(\s*["\']observability\.events["\']'
)

# Matches string literals that look like event names ("namespace:event")
_EVENT_LITERAL_RE = re.compile(r'["\']([a-z][a-z0-9_]*:[a-z][a-z0-9_:]*)["\']')


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_canonical_event(event_name: str) -> bool:
    """Return True if *event_name* belongs to a canonical amplifier_core namespace.

    Canonical events are built-in to amplifier_core and do not need to be
    registered via observability.events in individual modules.
    """
    prefix = event_name.split(":")[0]
    return prefix in CANONICAL_PREFIXES


def find_python_files(paths: list[str]) -> list[Path]:
    """Collect all .py files from *paths* (files or directories).

    Directories are searched recursively. Results are returned in sorted order
    so output is deterministic across runs.
    """
    result: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_file() and path.suffix == ".py":
            result.append(path)
        elif path.is_dir():
            result.extend(sorted(path.rglob("*.py")))
    return result


def get_module_name(filepath: Path) -> str:
    """Derive a human-readable module name from a file path.

    Uses the immediate parent directory name as the module name, which
    corresponds to the module directory (e.g. ``tool-delegate`` from
    ``modules/tool-delegate/mount.py``).

    Falls back to the file stem when the file has no meaningful parent
    (e.g. a top-level ``module.py`` with parent ``.``).
    """
    parent = filepath.parent
    # If parent is "." (current dir) or has no name, fall back to file stem
    parent_name = parent.name
    if not parent_name or parent_name == ".":
        return filepath.stem
    return parent_name


def scan_emitted_events(content: str) -> dict[str, int]:
    """Scan *content* for hooks.emit() calls and extract event names.

    Returns a dict mapping event name → first line number (1-based).
    """
    emitted: dict[str, int] = {}
    for i, line in enumerate(content.splitlines(), start=1):
        for match in _EMIT_RE.finditer(line):
            event_name = match.group(1)
            if event_name not in emitted:
                emitted[event_name] = i
    return emitted


def scan_registered_events(content: str) -> set[str]:
    """Scan *content* for events registered via observability.events capability.

    Handles both inline list and variable extend+register patterns.
    Returns a set of registered event name strings.
    """
    registered: set[str] = set()
    lines = content.splitlines()

    # Lines containing hooks.emit — exclude from registration scan
    emit_line_indices: set[int] = {
        i for i, line in enumerate(lines) if "hooks.emit" in line
    }

    # Locate register_capability/register_contributor calls for observability.events
    _multi_line_register_re = re.compile(
        r'register_(?:capability|contributor)\s*\(\s*["\']observability\.events["\']',
        re.DOTALL,
    )

    register_indices: list[int] = []
    for i in range(len(lines)):
        window_end = min(i + 3, len(lines))
        window = " ".join(lines[i:window_end])
        if _multi_line_register_re.search(window):
            register_indices.append(i)

    if not register_indices:
        return registered

    for reg_idx in register_indices:
        # Look back up to 50 lines (catches extend-then-register pattern)
        # and up to 10 lines ahead (catches multi-line argument lists)
        start = max(0, reg_idx - 50)
        end = min(len(lines), reg_idx + 11)

        for j in range(start, end):
            if j in emit_line_indices:
                continue
            for match in _EVENT_LITERAL_RE.finditer(lines[j]):
                candidate = match.group(1)
                if candidate == "observability.events":
                    continue
                registered.add(candidate)

    return registered


def scan_module(filepath: Path) -> dict:
    """Scan a single Python file and return module event data.

    Returns a dict::

        {
            "module":     str,          # derived module name
            "emitted":    dict[str, int],  # event_name -> line_number
            "registered": set[str],        # registered event names
        }
    """
    module_name = get_module_name(filepath)
    try:
        content = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {"module": module_name, "emitted": {}, "registered": set()}

    emitted = scan_emitted_events(content)
    registered = scan_registered_events(content) if emitted else set()

    return {"module": module_name, "emitted": emitted, "registered": registered}


# ---------------------------------------------------------------------------
# DOT generation
# ---------------------------------------------------------------------------

# Color constants
_COLOR_MODULE = "lightblue"
_COLOR_REGISTERED = "lightgreen"
_COLOR_UNREGISTERED = "red"
_COLOR_CANONICAL = "lightyellow"


def generate_dot(data: list[dict]) -> str:
    """Generate a DOT digraph from module scan data.

    Parameters
    ----------
    data:
        List of dicts as returned by :func:`scan_module`.

    Returns
    -------
    str
        DOT graph as a string.
    """
    lines: list[str] = []
    lines.append("digraph amplifier_events {")
    lines.append("    rankdir=LR;")
    lines.append('    node [fontname="Helvetica"];')
    lines.append("")

    # Collect all unique modules and events
    modules: list[str] = []
    # event_name -> color
    all_events: dict[str, str] = {}

    for entry in data:
        mod = entry["module"]
        if mod not in modules:
            modules.append(mod)

        emitted: dict[str, int] = entry.get("emitted", {})
        registered: set[str] = entry.get("registered", set())

        for event_name in emitted:
            if event_name in all_events:
                continue  # already categorised by a prior module
            if is_canonical_event(event_name):
                all_events[event_name] = _COLOR_CANONICAL
            elif event_name in registered:
                all_events[event_name] = _COLOR_REGISTERED
            else:
                all_events[event_name] = _COLOR_UNREGISTERED

    # --- Module nodes ---
    if modules:
        lines.append("    // Modules")
        for mod in modules:
            lines.append(
                f'    "{mod}" [shape=box, style=filled, fillcolor={_COLOR_MODULE}];'
            )
        lines.append("")

    # --- Event nodes ---
    if all_events:
        lines.append("    // Events")
        for event_name, color in sorted(all_events.items()):
            lines.append(
                f'    "{event_name}" [shape=oval, style=filled, fillcolor={color}];'
            )
        lines.append("")

    # --- Edges ---
    edges: list[str] = []
    for entry in data:
        mod = entry["module"]
        for event_name in sorted(entry.get("emitted", {})):
            edges.append(f'    "{mod}" -> "{event_name}";')

    if edges:
        lines.append("    // Emissions")
        lines.extend(edges)
        lines.append("")

    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON generation
# ---------------------------------------------------------------------------


def generate_json(data: list[dict]) -> str:
    """Generate a JSON representation of module event data.

    Parameters
    ----------
    data:
        List of dicts as returned by :func:`scan_module`.

    Returns
    -------
    str
        JSON string with a ``modules`` top-level key.
    """
    modules_out = []
    for entry in data:
        modules_out.append(
            {
                "module": entry["module"],
                "emitted": sorted(entry.get("emitted", {}).keys()),
                "registered": sorted(entry.get("registered", set())),
            }
        )

    result = {"modules": modules_out}
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.  Returns exit code 0 on success."""
    parser = argparse.ArgumentParser(
        description=(
            "Generate a DOT or JSON event-flow graph from Amplifier module source files.\n\n"
            "Introspects hooks.emit() calls and observability.events registrations to\n"
            "build a graph showing which modules emit which events."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths",
        nargs="+",
        metavar="PATH",
        help="File or directory paths to scan (directories are scanned recursively)",
    )
    parser.add_argument(
        "--format",
        choices=["dot", "json"],
        default="dot",
        help="Output format: 'dot' (default) or 'json'",
    )
    args = parser.parse_args(argv)

    files = find_python_files(args.paths)
    if not files:
        print("No Python files found.", file=sys.stderr)
        return 0

    # Aggregate by module name — multiple files in the same module dir are merged
    modules_map: dict[str, dict] = {}
    for filepath in files:
        entry = scan_module(filepath)
        mod = entry["module"]
        if mod not in modules_map:
            modules_map[mod] = {
                "module": mod,
                "emitted": {},
                "registered": set(),
            }
        # Merge emitted (keep lowest line number per event)
        for event, lineno in entry["emitted"].items():
            if event not in modules_map[mod]["emitted"]:
                modules_map[mod]["emitted"][event] = lineno
        # Merge registered
        modules_map[mod]["registered"].update(entry["registered"])

    data = list(modules_map.values())

    if args.format == "json":
        print(generate_json(data))
    else:
        print(generate_dot(data))

    return 0


if __name__ == "__main__":
    sys.exit(main())
