#!/usr/bin/env python3
"""Lint script for Amplifier module observability patterns.

Detects two anti-patterns:
1. Fire-and-forget event emission: asyncio.create_task(hooks.emit(...))
2. Events emitted via hooks.emit() that are not registered via
   register_capability("observability.events", ...) or
   register_contributor("observability.events", ...) in the module.

Canonical amplifier_core events (session:*, llm:*, provider:*, tool:*,
execution:*, orchestrator:*) are excluded — they don't need registration.

Usage:
    python scripts/lint_observability.py modules/tool-recipes/
    python scripts/lint_observability.py path/to/module.py

Exit codes:
    0 - No errors (warnings are OK)
    1 - Errors found (unregistered events)
"""

from __future__ import annotations

import argparse
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
# Compiled regex patterns
# ---------------------------------------------------------------------------

# Matches hooks.emit("event:name", ...) — covers both hooks.emit and self.hooks.emit
_EMIT_RE = re.compile(r'hooks\.emit\(\s*["\']([^"\']+)["\']')

# Matches asyncio.create_task( on a line
_CREATE_TASK_RE = re.compile(r"asyncio\.create_task\s*\(")

# Matches register_capability or register_contributor for observability.events
_REGISTER_RE = re.compile(
    r'register_(?:capability|contributor)\s*\(\s*["\']observability\.events["\']'
)

# Matches string literals that look like event names ("namespace:event")
# Namespace and event parts: lowercase letters, digits, underscores
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


def scan_fire_and_forget(content: str, filepath: Path) -> list[tuple[str, int, str]]:
    """Scan *content* for asyncio.create_task(hooks.emit(...)) anti-pattern.

    Both same-line and multi-line (create_task on one line, emit on next)
    forms are detected.

    Returns a list of (severity, line_number, message) tuples.
    """
    issues: list[tuple[str, int, str]] = []
    lines = content.splitlines()

    for i, line in enumerate(lines):
        if not _CREATE_TASK_RE.search(line):
            continue

        # Build a small window: this line plus the next few, to catch
        # multi-line asyncio.create_task(\n    hooks.emit(...)\n) patterns.
        window_end = min(i + 6, len(lines))
        window = "\n".join(lines[i:window_end])

        if "hooks.emit" in window:
            line_no = i + 1  # 1-based
            issues.append(
                (
                    "WARNING",
                    line_no,
                    "fire-and-forget event emission: asyncio.create_task(hooks.emit(...))",
                )
            )

    return issues


def scan_emitted_events(content: str) -> dict[str, int]:
    """Scan *content* for hooks.emit() calls and extract event names.

    Handles both ``hooks.emit(...)`` and ``self.hooks.emit(...)`` (or any
    attribute-access variant ending in ``hooks.emit``).

    Returns a dict mapping event name → first line number (1-based) where it
    is emitted.
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

    Handles two common patterns:

    Pattern 1 — inline list (single or multi-line)::

        coordinator.register_capability(
            "observability.events", ["ns:event1", "ns:event2"]
        )

    Pattern 2 — variable extend + register (as used in tool-delegate)::

        obs = coordinator.get_capability("observability.events") or []
        obs.extend(["ns:event1", "ns:event2"])
        coordinator.register_capability("observability.events", obs)

    For each ``register_capability``/``register_contributor`` call found, the
    function inspects a window of up to 50 lines *before* the call and up to
    10 lines *after* (for multi-line argument lists).  Lines containing
    ``hooks.emit(`` are excluded to prevent emitted event names from being
    mistaken for registered ones.

    Returns a set of registered event name strings.
    """
    registered: set[str] = set()
    lines = content.splitlines()

    # Identify lines that contain hooks.emit calls — exclude these from the
    # registered-events scan so emitted names aren't mistaken for registered ones.
    emit_line_indices: set[int] = {
        i for i, line in enumerate(lines) if "hooks.emit" in line
    }

    # Locate register_capability/register_contributor calls for observability.events.
    # The call may span multiple lines, e.g.:
    #   coordinator.register_capability(
    #       "observability.events", [...]
    #   )
    # so we check a 3-line sliding window joined into one string.
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
        # Look back up to 50 lines (catches the extend-then-register pattern)
        # and up to 10 lines ahead (catches multi-line argument lists)
        start = max(0, reg_idx - 50)
        end = min(len(lines), reg_idx + 11)

        for j in range(start, end):
            if j in emit_line_indices:
                continue  # skip lines with hooks.emit — those are not registrations
            for match in _EVENT_LITERAL_RE.finditer(lines[j]):
                candidate = match.group(1)
                if candidate == "observability.events":
                    continue
                registered.add(candidate)

    return registered


def lint_file(filepath: Path) -> list[tuple[str, int | None, str]]:
    """Lint a single Python file for observability anti-patterns.

    Returns a list of ``(severity, line_or_None, message)`` tuples where
    severity is ``"WARNING"`` or ``"ERROR"``.
    """
    try:
        content = filepath.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [("ERROR", None, f"Could not read file: {exc}")]

    issues: list[tuple[str, int | None, str]] = []

    # --- Check 1: fire-and-forget event emission ---
    for severity, line_no, message in scan_fire_and_forget(content, filepath):
        issues.append((severity, line_no, message))

    # --- Check 2: emitted events not registered in observability.events ---
    emitted = scan_emitted_events(content)
    if emitted:
        registered = scan_registered_events(content)
        unregistered = sorted(
            name
            for name in emitted
            if not is_canonical_event(name) and name not in registered
        )
        if unregistered:
            issues.append(
                (
                    "ERROR",
                    None,
                    f"events emitted but not registered: {', '.join(unregistered)}",
                )
            )

    return issues


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point.

    Returns exit code: 0 if no errors (warnings OK), 1 if any errors found.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Lint Amplifier module Python files for observability anti-patterns.\n\n"
            "Detects:\n"
            "  1. asyncio.create_task(hooks.emit(...)) — fire-and-forget emission\n"
            "  2. hooks.emit() calls for events not registered via observability.events"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths",
        nargs="+",
        metavar="PATH",
        help="File or directory paths to lint (directories are scanned recursively)",
    )
    args = parser.parse_args(argv)

    files = find_python_files(args.paths)
    if not files:
        print("No Python files found.", file=sys.stderr)
        return 0

    has_errors = False
    for filepath in files:
        issues = lint_file(filepath)
        for severity, line_no, message in issues:
            if line_no is not None:
                print(f"{severity}: {filepath}:{line_no} \u2014 {message}")
            else:
                print(f"{severity}: {filepath} \u2014 {message}")
            if severity == "ERROR":
                has_errors = True

    return 1 if has_errors else 0


if __name__ == "__main__":
    sys.exit(main())
