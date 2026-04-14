"""Deprecated: message slicing utilities — now in messages.py.

All functions in this module have been moved to
``amplifier_foundation.session.messages``.  This module remains as a
thin backward-compatibility shim so existing imports continue to work,
but it will be removed in a future release.

Migrate your imports::

    # Old (deprecated)
    from amplifier_foundation.session.slice import get_turn_boundaries

    # New
    from amplifier_foundation.session.messages import get_turn_boundaries
"""

from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "amplifier_foundation.session.slice is deprecated. "
    "Import from amplifier_foundation.session.messages instead.",
    DeprecationWarning,
    stacklevel=2,
)

from .messages import add_synthetic_tool_results  # noqa: E402, F401
from .messages import count_turns  # noqa: E402, F401
from .messages import find_orphaned_tool_calls  # noqa: E402, F401
from .messages import get_turn_boundaries  # noqa: E402, F401
from .messages import get_turn_summary  # noqa: E402, F401
from .messages import is_real_user_message  # noqa: E402, F401
from .messages import slice_to_turn  # noqa: E402, F401

__all__ = [
    "add_synthetic_tool_results",
    "count_turns",
    "find_orphaned_tool_calls",
    "get_turn_boundaries",
    "get_turn_summary",
    "is_real_user_message",
    "slice_to_turn",
]
