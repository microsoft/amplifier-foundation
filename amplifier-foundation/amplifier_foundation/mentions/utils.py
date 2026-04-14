"""Utilities for @mention handling."""

from __future__ import annotations

from pathlib import Path


def format_directory_listing(path: Path) -> str:
    """Generate formatted directory listing for @mention context.

    Returns a string showing immediate directory contents with DIR/FILE markers.
    This format matches amplifier-module-tool-filesystem for consistency.

    The listing is suitable for injection into LLM context, giving the model
    awareness of available files without loading all their contents.

    Args:
        path: Directory path to list (must exist and be a directory)

    Returns:
        Formatted listing string with header and entries

    Example output:
        Directory: /path/to/dir

          DIR  subdir1
          DIR  subdir2
          FILE config.yaml
          FILE README.md
    """
    if not path.is_dir():
        raise ValueError(f"Path is not a directory: {path}")

    try:
        # Sort: directories first, then files, alphabetically within each group
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        return f"Directory: {path}\n\n  (permission denied)"

    lines = []
    for entry in entries:
        entry_type = "DIR " if entry.is_dir() else "FILE"
        lines.append(f"  {entry_type} {entry.name}")

    listing = "\n".join(lines) if lines else "  (empty directory)"
    return f"Directory: {path}\n\n{listing}"
