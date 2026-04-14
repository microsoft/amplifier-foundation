"""Data models for @mention handling."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class ContextFile:
    """A context file loaded from an @mention.

    Supports multi-path attribution: when the same content is found at multiple
    paths (e.g., @foundation:foo.md and @recipes:bar.md), all paths are tracked
    so users/models know all @mentions that resolved to this content.
    """

    content: str
    content_hash: str  # SHA-256 hash for deduplication
    paths: list[Path]  # All paths where this content was found (for attribution)


@dataclass
class MentionResult:
    """Result of resolving a single @mention."""

    mention: str
    resolved_path: Path | None
    content: str | None
    error: str | None
    is_directory: bool = False  # True if resolved_path is a directory

    @property
    def found(self) -> bool:
        """True if the mention was successfully resolved (file or directory)."""
        return self.resolved_path is not None and (self.content is not None or self.is_directory)
