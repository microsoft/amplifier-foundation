"""Session capability helpers for modules.

This module provides helper functions for accessing session-scoped capabilities
that are registered by foundation during session creation.

These helpers provide a consistent interface for modules to access session context
without depending on specific capability names or fallback logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    pass  # Coordinator type would go here if we had it


# Capability name constants
WORKING_DIR_CAPABILITY = "session.working_dir"


def get_working_dir(coordinator: Any, fallback: Path | str | None = None) -> Path:
    """Get the session's working directory from coordinator capability.

    This is the canonical way for modules to determine where file operations
    should be rooted. The working directory is set by the app layer during
    session creation and may be updated during the session (e.g., if the
    assistant changes directories).

    Args:
        coordinator: The session coordinator (has get_capability method).
        fallback: Optional fallback path if capability not set.
            If None, falls back to Path.cwd() for backward compatibility.

    Returns:
        Working directory as a Path object.

    Example:
        # In a tool module's mount() or tool implementation
        working_dir = get_working_dir(coordinator)
        file_path = working_dir / "relative/path/file.txt"

        # With explicit fallback
        working_dir = get_working_dir(coordinator, fallback=Path("/workspace"))

        # Using config as fallback (for backward compatibility with existing modules)
        config_dir = config.get("working_dir", ".")
        working_dir = get_working_dir(coordinator, fallback=config_dir)
    """
    # Try to get from capability first
    working_dir = coordinator.get_capability(WORKING_DIR_CAPABILITY)

    if working_dir is not None:
        return Path(working_dir)

    # Fall back to provided fallback or cwd
    if fallback is not None:
        return Path(fallback)

    return Path.cwd()


def set_working_dir(coordinator: Any, path: Path | str) -> None:
    """Update the session's working directory capability.

    This allows dynamic working directory changes during a session,
    such as when the assistant "cd"s into a subdirectory.

    Args:
        coordinator: The session coordinator.
        path: New working directory path.

    Note:
        This re-registers the capability, overwriting the previous value.
        The path should be absolute for consistent behavior.
    """
    # Resolve to absolute path
    resolved = Path(path).resolve()
    coordinator.register_capability(WORKING_DIR_CAPABILITY, str(resolved))
