"""File I/O with retry logic and atomic writes.

This module provides mechanisms for robust file operations:
- Cloud sync aware reads/writes with retry logic
- Atomic writes (crash-safe using temp file + rename)
- Backup before write pattern

Philosophy: These are pure mechanisms. Apps decide WHEN/WHAT to persist.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


async def read_with_retry(
    path: Path,
    max_retries: int = 3,
    initial_delay: float = 0.1,
) -> str:
    """Read file content with retry logic for cloud sync delays.

    OneDrive, Dropbox, and Google Drive can cause transient I/O errors
    when files aren't locally cached. This function automatically retries
    with exponential backoff.

    Args:
        path: Path to file to read.
        max_retries: Maximum number of retry attempts.
        initial_delay: Initial delay in seconds before first retry.

    Returns:
        File content as string.

    Raises:
        FileNotFoundError: If file doesn't exist.
        OSError: If file can't be read after all retries.
    """
    delay = initial_delay
    last_error: OSError | None = None

    for attempt in range(max_retries):
        try:
            return path.read_text(encoding="utf-8")
        except OSError as e:
            last_error = e
            if e.errno == 5 and attempt < max_retries - 1:
                if attempt == 0:
                    logger.warning(
                        f"File I/O error reading {path} - retrying. "
                        "This may be due to cloud-synced files (OneDrive, Dropbox, etc.). "
                        "Consider enabling 'Always keep on this device' for the data folder."
                    )
                await asyncio.sleep(delay)
                delay *= 2
            else:
                raise

    raise last_error  # type: ignore[misc]


async def write_with_retry(
    path: Path,
    content: str,
    max_retries: int = 3,
    initial_delay: float = 0.1,
) -> None:
    """Write content to file with retry logic for cloud sync delays.

    Args:
        path: Path to file to write.
        content: Content to write.
        max_retries: Maximum number of retry attempts.
        initial_delay: Initial delay in seconds before first retry.

    Raises:
        OSError: If file can't be written after all retries.
    """
    delay = initial_delay
    last_error: OSError | None = None

    for attempt in range(max_retries):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return
        except OSError as e:
            last_error = e
            if e.errno == 5 and attempt < max_retries - 1:
                if attempt == 0:
                    logger.warning(
                        f"File I/O error writing to {path} - retrying. "
                        "This may be due to cloud-synced files (OneDrive, Dropbox, etc.). "
                        "Consider enabling 'Always keep on this device' for the data folder."
                    )
                await asyncio.sleep(delay)
                delay *= 2
            else:
                raise

    raise last_error  # type: ignore[misc]


# -----------------------------------------------------------------------------
# Synchronous atomic write with backup
# Based on battle-tested patterns from amplifier-app-cli's SessionStore
# -----------------------------------------------------------------------------


def _write_atomic(
    path: Path,
    content: str | bytes,
    *,
    mode: str = "w",
    encoding: str | None = "utf-8",
) -> None:
    """Write file atomically using temp file + rename pattern.

    This ensures the file is never partially written - either the
    old content exists or the new content exists, never a mix.

    Args:
        path: Target file path
        content: Content to write
        mode: Write mode ("w" for text, "wb" for binary)
        encoding: Encoding for text mode (ignored for binary)

    Raises:
        OSError: If write or rename fails
    """
    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path: Path | None = None
    try:
        # Create temp file in same directory (same filesystem for atomic rename)
        with tempfile.NamedTemporaryFile(
            mode=mode,
            encoding=encoding if "b" not in mode else None,
            dir=path.parent,
            prefix=f".{path.stem}_",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            temp_path = Path(tmp_file.name)
            tmp_file.write(content)
            tmp_file.flush()

        # File is now closed, safe to rename on Windows
        # Atomic rename (works cross-platform)
        temp_path.replace(path)

    except Exception as e:
        # Clean up temp file on failure
        if temp_path:
            with contextlib.suppress(Exception):
                temp_path.unlink()
        raise OSError(f"Failed to write atomically to {path}: {e}") from e


def write_with_backup(
    path: Path,
    content: str | bytes,
    *,
    backup_suffix: str = ".backup",
    mode: str = "w",
    encoding: str | None = "utf-8",
) -> None:
    """Write file with backup of previous version.

    Creates a backup before writing, enabling recovery if the new
    write is corrupted or interrupted.

    Based on app-cli's SessionStore backup pattern.

    Args:
        path: Target file path
        content: Content to write
        backup_suffix: Suffix for backup file (default: ".backup")
        mode: Write mode ("w" for text, "wb" for binary)
        encoding: Encoding for text mode

    Example:
        # Creates config.json.backup before writing config.json
        write_with_backup(Path("config.json"), json.dumps(config))
    """
    backup_path = path.with_suffix(path.suffix + backup_suffix)

    # Create backup if file exists (best effort - don't fail the write)
    if path.exists():
        with contextlib.suppress(Exception):
            shutil.copy2(path, backup_path)

    # Write atomically
    _write_atomic(path, content, mode=mode, encoding=encoding)
