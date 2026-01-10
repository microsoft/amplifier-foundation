"""Session utilities for Amplifier.

This module provides utilities for session fork, slice, and lineage operations.

Key concepts:
- **Fork**: Create a new session from an existing session at a specific turn.
  The forked session preserves conversation history up to that turn and is
  independently resumable.

- **Turn**: A user message plus all subsequent non-user messages (assistant
  responses, tool results) until the next user message. Turns are 1-indexed.

- **Lineage**: Parent-child relationships between sessions, tracked via
  `parent_id` in session metadata. Enables tracing session history.

Example usage:

    from amplifier_foundation.session import fork_session, ForkResult

    # Fork a stored session at turn 3
    result = fork_session(
        Path("~/.amplifier/projects/myproj/sessions/abc123"),
        turn=3,
    )
    print(f"Forked to {result.session_id}")

    # Fork in memory (for testing or preview)
    from amplifier_foundation.session import fork_session_in_memory

    messages = await context.get_messages()
    result = fork_session_in_memory(messages, turn=2)
    await new_context.set_messages(result.messages)

The kernel (amplifier-core) already provides the mechanism for session forking
via the `parent_id` parameter in AmplifierSession and the `session:fork` event.
These utilities provide the policy layer for actually performing forks.
"""

from __future__ import annotations

# Core fork operations
from .fork import (
    ForkResult,
    fork_session,
    fork_session_in_memory,
    get_fork_preview,
    get_session_lineage,
    list_session_forks,
)

# Events utilities
from .events import (
    count_events,
    get_event_summary,
    get_last_timestamp_for_turn,
    slice_events_for_fork,
    slice_events_to_timestamp,
)

# Slice utilities (for advanced use cases)
from .slice import (
    add_synthetic_tool_results,
    count_turns,
    find_orphaned_tool_calls,
    get_turn_boundaries,
    get_turn_summary,
    slice_to_turn,
)

__all__ = [
    # Core fork operations
    "ForkResult",
    "fork_session",
    "fork_session_in_memory",
    "get_fork_preview",
    "get_session_lineage",
    "list_session_forks",
    # Events utilities
    "slice_events_to_timestamp",
    "slice_events_for_fork",
    "get_last_timestamp_for_turn",
    "count_events",
    "get_event_summary",
    # Slice utilities
    "get_turn_boundaries",
    "count_turns",
    "slice_to_turn",
    "find_orphaned_tool_calls",
    "add_synthetic_tool_results",
    "get_turn_summary",
]
