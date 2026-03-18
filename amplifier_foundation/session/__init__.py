"""Session utilities for Amplifier.

This module provides utilities for session management, including fork, slice,
lineage, diagnosis, and repair operations.

Key concepts:

- **Fork**: Create a new session from an existing session at a specific turn.
  The forked session preserves conversation history up to that turn and is
  independently resumable.

- **Turn**: A user message plus all subsequent non-user messages (assistant
  responses, tool results) until the next user message. Turns are 1-indexed.

- **Lineage**: Parent-child relationships between sessions, tracked via
  `parent_id` in session metadata. Enables tracing session history.

- **Diagnosis**: Structured analysis of a transcript to detect failure modes
  such as missing tool results, ordering violations, and incomplete assistant
  turns. Produces a ``DiagnosisResult`` with recommended repair action.

Module layers:

- **Level 1 — Message Algebra** (``messages``, ``diagnosis``): Pure functions
  operating on ``list[dict]`` with zero I/O.  All inputs and outputs are plain
  Python data structures.

- **Level 2 — JSONL Store** (``store``): Knows file formats and session file
  naming conventions; performs file I/O but has no knowledge of where sessions
  live on disk.

- **Level 3 — Session Operations** (``fork``, ``events``): Combines Levels 1
  and 2 to provide end-to-end session fork, slice, and event operations.

Example usage::

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

    # Diagnose and repair a broken transcript
    from amplifier_foundation.session import (
        load_transcript_with_lines,
        diagnose_transcript,
        repair_transcript,
    )

    entries = load_transcript_with_lines(session_dir)
    diagnosis = diagnose_transcript(entries)
    if diagnosis["status"] == "broken":
        repaired = repair_transcript(entries, diagnosis)

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

# Message algebra utilities (Level 1, zero I/O)
from .messages import (
    add_synthetic_tool_results,
    count_turns,
    find_orphaned_tool_calls,
    get_turn_boundaries,
    get_turn_summary,
    is_real_user_message,
    slice_to_turn,
)

# Diagnosis and repair utilities (Level 1, zero I/O)
from .diagnosis import (
    DiagnosisResult,
    IncompleteTurn,
    build_tool_index,
    diagnose_transcript,
    repair_transcript,
    rewind_transcript,
)

# JSONL store utilities (Level 2, file I/O)
from .store import (
    EVENTS_FILENAME,
    METADATA_FILENAME,
    TRANSCRIPT_FILENAME,
    backup,
    load_metadata,
    load_transcript,
    load_transcript_with_lines,
    read_jsonl,
    write_jsonl,
    write_metadata,
    write_transcript,
)

# Capability helpers (for modules to access session context)
from .capabilities import (
    WORKING_DIR_CAPABILITY,
    get_working_dir,
    set_working_dir,
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
    "count_events",
    "get_event_summary",
    "get_last_timestamp_for_turn",
    "slice_events_for_fork",
    "slice_events_to_timestamp",
    # Message algebra utilities
    "add_synthetic_tool_results",
    "count_turns",
    "find_orphaned_tool_calls",
    "get_turn_boundaries",
    "get_turn_summary",
    "is_real_user_message",
    "slice_to_turn",
    # Diagnosis and repair utilities
    "DiagnosisResult",
    "IncompleteTurn",
    "build_tool_index",
    "diagnose_transcript",
    "repair_transcript",
    "rewind_transcript",
    # JSONL store utilities
    "EVENTS_FILENAME",
    "METADATA_FILENAME",
    "TRANSCRIPT_FILENAME",
    "backup",
    "load_metadata",
    "load_transcript",
    "load_transcript_with_lines",
    "read_jsonl",
    "write_jsonl",
    "write_metadata",
    "write_transcript",
    # Capability helpers
    "WORKING_DIR_CAPABILITY",
    "get_working_dir",
    "set_working_dir",
]
