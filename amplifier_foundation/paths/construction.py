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
    """Construct path to a context file.

    The name is a path relative to the context/ directory within the bundle.
    Supports any file extension and arbitrary directory depth.

    Examples:
        'IMPLEMENTATION_PHILOSOPHY.md' -> context/IMPLEMENTATION_PHILOSOPHY.md
        'shared/common-agent-base.md'  -> context/shared/common-agent-base.md
        'examples/config.yaml'         -> context/examples/config.yaml

    Args:
        base: Base directory (bundle root).
        name: Path to context file relative to context/ directory.

    Returns:
        Path to context file.
    """
    return base / "context" / name
