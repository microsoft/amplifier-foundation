"""Protocol for @mention resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


class MentionResolverProtocol(Protocol):
    """Protocol for resolving @mentions to file paths.

    Foundation provides BaseMentionResolver with minimal patterns.
    Apps extend with additional shortcuts like @user:, @project:.
    """

    def resolve(self, mention: str) -> Path | None:
        """Resolve an @mention to a file path.

        Args:
            mention: The mention string (including @ prefix).

        Returns:
            Path to the resolved file, or None if not found.
        """
        ...


@runtime_checkable
class RelativeMentionResolverProtocol(Protocol):
    """Optional per-call context for resolvers used by the recursive loader.

    Legacy ``resolve(mention)`` implementations remain supported. Implement this
    extension to anchor local paths to the referring file without changing the
    resolver's workspace, namespace roots, or state between calls.
    """

    def resolve_relative(self, mention: str, relative_to: Path) -> Path | None:
        """Resolve local paths relative to ``relative_to``; retain shortcut roots."""
        ...
