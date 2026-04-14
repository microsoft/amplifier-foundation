"""Path construction utilities for bundle resources."""

from __future__ import annotations

from pathlib import Path


def construct_agent_path(base: Path, name: str) -> Path:
    """Construct path to an agent file.

    Looks for agent in agents/ subdirectory with .md extension.

    Args:
        base: Base directory (bundle root).
        name: Agent name.

    Returns:
        Path to agent file.
    """
    # Try with and without .md extension
    if name.endswith(".md"):
        return base / "agents" / name
    return base / "agents" / f"{name}.md"


def construct_context_path(base: Path, name: str) -> Path:
    """Construct path to a bundle resource file.

    The name is a path relative to the bundle root directory.
    Supports any file extension and arbitrary directory depth.
    Paths should be explicit - no implicit prefixes are added.

    Examples:
        'context/IMPLEMENTATION_PHILOSOPHY.md' -> context/IMPLEMENTATION_PHILOSOPHY.md
        'context/shared/common-agent-base.md'  -> context/shared/common-agent-base.md
        'providers/anthropic.yaml'             -> providers/anthropic.yaml
        'agents/explorer.md'                   -> agents/explorer.md
        '' or '/' -> base (bundle root)

    Args:
        base: Base directory (bundle root).
        name: Path to file relative to bundle root (explicit, no implicit prefix).

    Returns:
        Path to file (or base if name is empty/root).
    """
    # Strip leading "/" to prevent path from becoming absolute
    # (Python's Path("/base") / "/" = Path("/") which is wrong)
    # Also handle empty string for bundle root access
    name = name.lstrip("/")
    if not name:
        return base
    return base / name
