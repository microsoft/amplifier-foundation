"""File and bundle discovery utilities."""

from __future__ import annotations

from pathlib import Path


async def find_files(
    base: Path,
    pattern: str,
    recursive: bool = True,
) -> list[Path]:
    """Find files matching a glob pattern.

    Args:
        base: Base directory to search from.
        pattern: Glob pattern (e.g., "*.md", "**/*.yaml").
        recursive: If True, search recursively.

    Returns:
        List of matching file paths.
    """
    if recursive and not pattern.startswith("**"):
        pattern = f"**/{pattern}"

    return sorted(base.glob(pattern))


async def find_bundle_root(start: Path) -> Path | None:
    """Find the bundle root directory containing bundle.md.

    Searches from start directory upward until finding bundle.md
    or hitting filesystem root.

    Args:
        start: Starting directory.

    Returns:
        Path to bundle root directory, or None if not found.
    """
    current = start.resolve()

    while current != current.parent:
        if (current / "bundle.md").exists():
            return current
        if (current / "bundle.yaml").exists():
            return current
        current = current.parent

    # Check root too
    if (current / "bundle.md").exists():
        return current
    if (current / "bundle.yaml").exists():
        return current

    return None
