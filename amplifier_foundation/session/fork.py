"""Session fork operations for Amplifier.

This module provides file-based and in-memory session forking with turn-aware
slicing and lineage tracking.

Key concepts:
- Fork: Create a new session from an existing session at a specific turn
- Turn: A user message + all subsequent responses until the next user message
- Lineage: Parent-child relationship tracked via parent_id in metadata

The forked session is independently resumable and addressable.
"""

from __future__ import annotations

import os
import inspect
import uuid
from collections.abc import Callable
from copy import deepcopy
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .events import slice_events_for_fork
from .jsonl import _private_windows
from .messages import (
    count_turns,
    find_orphaned_tool_calls,
    get_turn_boundaries,
    slice_to_turn,
)
from .store import (
    EVENTS_FILENAME,
    METADATA_FILENAME,
    TRANSCRIPT_FILENAME,
    load_metadata,
    load_transcript,
    write_jsonl,
    write_metadata,
)


@dataclass
class ForkResult:
    """Result of a fork operation.

    Attributes:
        session_id: The new session's unique identifier.
        session_dir: Path to the new session directory (None for in-memory forks).
        parent_id: The parent session's ID for lineage tracking.
        forked_from_turn: The turn number where the fork occurred.
        message_count: Number of messages in the forked session.
        messages: The forked messages (only populated for in-memory forks).
        events_count: Number of events copied (only for file-based forks).
    """

    session_id: str
    session_dir: Path | None
    parent_id: str
    forked_from_turn: int
    message_count: int
    messages: list[dict[str, Any]] | None = None
    events_count: int = 0


def fork_session(
    parent_session_dir: Path,
    *,
    turn: int | None = None,
    new_session_id: str | None = None,
    target_dir: Path | None = None,
    include_events: bool = True,
    handle_orphaned_tools: str = "complete",
    windows_policy: str = "legacy",
    media_adopter: Callable[..., list[dict[str, Any]]] | None = None,
    media_checkpoint: Callable[..., None] | None = None,
) -> ForkResult:
    """Fork a stored session from a specific turn.

    Creates a new session directory with:
    - transcript.jsonl sliced to turn N
    - metadata.json with parent lineage information
    - events.jsonl sliced to turn boundary (if include_events=True)

    Args:
        parent_session_dir: Path to parent session directory.
        turn: Turn number to fork from (1-indexed). None = latest turn (full copy).
        new_session_id: ID for new session. Auto-generated UUID if None.
        target_dir: Base directory to create new session in.
                    Defaults to sibling of parent session.
        include_events: Whether to copy and slice events.jsonl (default: True).
        windows_policy: "legacy" by default; "private" explicitly opts into
            private Windows reads and create-only destination reservation.
        media_adopter: Explicit host callback for authorized selected-prefix
            media closure. Receives messages, session_id and session_dir; assets
            must be published before returning target-owned canonical messages.
        media_checkpoint: Host callback after the actual target canonical write,
            receiving the same arguments. Must verify/acknowledge the exact
            saved rows, not save a second transcript or manufacture a report.
        handle_orphaned_tools: How to handle tool_use without tool_result:
            - "complete": Add synthetic error result (default)
            - "remove": Remove the orphaned tool_use content
            - "error": Raise ValueError

    Returns:
        ForkResult with new session details.

    Raises:
        FileNotFoundError: If parent session doesn't exist or lacks transcript.
        FileExistsError: In private Windows mode, if the destination already exists.
        ValueError: If turn number is invalid.

    Example:
        >>> result = fork_session(
        ...     Path("~/.amplifier/projects/myproj/sessions/abc123"),
        ...     turn=3,
        ... )
        >>> print(f"Forked to {result.session_id} at turn {result.forked_from_turn}")
    """
    private_windows = _private_windows(windows_policy)
    if private_windows:
        from ._windows_ownership import _absolute

        # Do not resolve away a reparse ancestor before the private reader sees it.
        parent_session_dir = _absolute(Path(parent_session_dir))
    else:
        parent_session_dir = Path(parent_session_dir).resolve()

    # Validate parent exists
    transcript_path = parent_session_dir / TRANSCRIPT_FILENAME
    metadata_path = parent_session_dir / METADATA_FILENAME
    events_path = parent_session_dir / EVENTS_FILENAME

    if not transcript_path.exists():
        raise FileNotFoundError(
            f"No transcript.jsonl in {parent_session_dir}. "
            "This doesn't appear to be a valid session directory."
        )

    # Load parent data
    if private_windows:
        from .history import SessionHistoryStore

        parent = SessionHistoryStore(parent_session_dir, windows_policy=windows_policy).load(include_events=False)
        messages, parent_metadata = parent.messages, parent.metadata
    else:
        messages = load_transcript(parent_session_dir)
        parent_metadata = (
            load_metadata(parent_session_dir) if metadata_path.exists() else {}
        )
    parent_id = parent_metadata.get("session_id", parent_session_dir.name)

    # Determine turn
    max_turns = count_turns(messages)
    if max_turns == 0:
        raise ValueError("Cannot fork: session has no user messages")

    if turn is None:
        turn = max_turns

    if turn < 1 or turn > max_turns:
        raise ValueError(f"Turn {turn} out of range. Valid range: 1-{max_turns}")

    # Slice messages to turn
    sliced = slice_to_turn(messages, turn, handle_orphaned_tools=handle_orphaned_tools)
    has_media = _has_media(sliced)
    if has_media and (media_adopter is None or media_checkpoint is None):
        raise ValueError(
            "Cannot fork media references without host adoption and checkpoint handling"
        )
    if has_media and os.name != "posix":
        raise ValueError("Media fork durability is not qualified on this platform")

    # Generate new session ID
    session_id = new_session_id or str(uuid.uuid4())
    if has_media:
        import re

        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,220}", session_id):
            raise ValueError("Media fork ID must be a safe session identifier")
    if private_windows and (
        Path(session_id).name != session_id or session_id in {".", ".."}
    ):
        raise ValueError("Windows fork ID must be a single path component")

    # Determine target directory
    if target_dir is None:
        # Create sibling to parent
        base_dir = parent_session_dir.parent
    else:
        base_dir = (
            _absolute(Path(target_dir)) if private_windows else Path(target_dir).resolve()
        )

    new_session_dir = base_dir / session_id

    if has_media:
        # Reserve before any callback publishes assets. A failed adoption/save
        # leaves an explicit partial destination, never an apparently successful
        # fork or an excuse to overwrite/reclaim its assets on retry.
        new_session_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
        sliced = _adopt_media(
            sliced, media_adopter, session_id=session_id, session_dir=new_session_dir
        )
    elif not private_windows:
        # Default legacy path is unchanged on both Windows and POSIX.
        new_session_dir.mkdir(parents=True, exist_ok=True)
    if not private_windows:
        write_jsonl(new_session_dir / TRANSCRIPT_FILENAME, sliced)

    # Write metadata with lineage
    now = datetime.now(timezone.utc).isoformat()
    new_metadata = {
        "session_id": session_id,
        "parent_id": parent_id,
        "forked_from_turn": turn,
        "forked_at": now,
        "created": now,
        "turn_count": count_turns(sliced),
        # Preserve parent metadata
        "bundle": parent_metadata.get("bundle"),
        "model": parent_metadata.get("model"),
    }
    with ExitStack() as publication:
        if private_windows:
            from ._windows_history import reserve_new_session

            # Create-only reservation, not exists-then-save: reject private
            # destinations, self-forks and concurrent same-ID contenders alike.
            publication.enter_context(reserve_new_session(new_session_dir))
            SessionHistoryStore(new_session_dir, windows_policy=windows_policy).save(
                sliced, new_metadata, preserve_system=True
            )
        else:
            write_metadata(new_session_dir, new_metadata)
        if has_media:
            # The target transcript exists only after independently owned assets.
            # Failure is propagated; it never reexecutes the producer or deletes
            # the completed partial canonical evidence.
            outcome = media_checkpoint(
                sliced, session_id=session_id, session_dir=new_session_dir
            )
            if outcome is not None:
                if inspect.iscoroutine(outcome):
                    outcome.close()
                if inspect.isgenerator(outcome):
                    outcome.close()
                raise ValueError(
                    "Media fork checkpoint callback must be synchronous and return None"
                )

        # Handle events.jsonl; Windows retains the reservation pins throughout.
        events_count = 0
        if include_events and events_path.exists():
            try:
                events_count = slice_events_for_fork(
                    events_path,
                    transcript_path,
                    turn,
                    new_session_dir / EVENTS_FILENAME,
                    new_session_id=session_id,
                    parent_session_id=parent_id,
                )
            except Exception:
                # Events slicing is best-effort - don't fail the fork
                # Just create empty events file
                (new_session_dir / EVENTS_FILENAME).write_text("")
        elif include_events:
            # Create empty events file for consistency
            (new_session_dir / EVENTS_FILENAME).write_text("")

    return ForkResult(
        session_id=session_id,
        session_dir=new_session_dir,
        parent_id=parent_id,
        forked_from_turn=turn,
        message_count=len(sliced),
        messages=None,  # Don't include in file-based result
        events_count=events_count,
    )


def fork_session_in_memory(
    messages: list[dict[str, Any]],
    *,
    turn: int | None = None,
    parent_id: str | None = None,
    new_session_id: str | None = None,
    handle_orphaned_tools: str = "complete",
    media_adopter: Callable[..., list[dict[str, Any]]] | None = None,
) -> ForkResult:
    """Fork messages in memory without file I/O.

    Useful for:
    - Testing fork logic without filesystem
    - Preview before committing to disk
    - In-process forking via ContextManager

    Args:
        messages: Source messages to fork from.
        turn: Turn to fork at (1-indexed). None = all turns (full copy).
        parent_id: Parent session ID for lineage tracking.
        new_session_id: Optional target identity chosen before adoption. Hosts
            reserve this exact target and must not rename it after refs are bound.
        handle_orphaned_tools: How to handle orphaned tool calls.

    Returns:
        ForkResult with messages included (session_dir will be None).

    Raises:
        ValueError: If turn is out of range.

    Example:
        >>> messages = await context.get_messages()
        >>> result = fork_session_in_memory(messages, turn=2)
        >>> await new_context.set_messages(result.messages)
    """
    import re

    if new_session_id is not None and (
        not isinstance(new_session_id, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,220}", new_session_id)
    ):
        raise ValueError("Fork ID must be a safe session identifier")
    session_id = new_session_id if new_session_id is not None else str(uuid.uuid4())
    max_turns = count_turns(messages)

    if turn is None:
        turn = max_turns if max_turns > 0 else 0

    if max_turns == 0:
        # Empty conversation - return empty fork
        return ForkResult(
            session_id=session_id,
            session_dir=None,
            parent_id=parent_id or "unknown",
            forked_from_turn=0,
            message_count=0,
            messages=[],
        )

    # Slice messages
    sliced = slice_to_turn(messages, turn, handle_orphaned_tools=handle_orphaned_tools)
    if _has_media(sliced):
        sliced = _adopt_media(
            sliced, media_adopter, session_id=session_id, session_dir=None
        )

    return ForkResult(
        session_id=session_id,
        session_dir=None,
        parent_id=parent_id or "unknown",
        forked_from_turn=turn,
        message_count=len(sliced),
        messages=sliced,
    )


def _has_media(messages: list[dict[str, Any]]) -> bool:
    """Inspect only the canonical namespace, never strings or nested results."""
    found = False
    for message in messages:
        metadata = message.get("metadata")
        if not isinstance(metadata, dict) or "experimental.media.v1" not in metadata:
            continue
        carrier = metadata["experimental.media.v1"]
        if (not isinstance(carrier, dict) or set(carrier) != {"refs"}
                or not isinstance(carrier["refs"], list)):
            raise ValueError("Invalid canonical media carrier in fork")
        for ref in carrier["refs"]:
            if not isinstance(ref, dict):
                raise ValueError("Invalid canonical media reference in fork")
            found = True
    return found


def _adopt_media(messages, adopter, *, session_id, session_dir):
    if adopter is None:
        raise ValueError("Cannot fork media references without host adoption handling")
    # A callback cannot mutate the original selected parent rows on failure.
    result = adopter(
        deepcopy(messages), session_id=session_id, session_dir=session_dir
    )
    if (not isinstance(result, list) or len(result) != len(messages)
            or any(not isinstance(row, dict) or row.get("role") != original.get("role")
                   for row, original in zip(result, messages))):
        raise ValueError("Media adoption must preserve canonical rows and roles")
    _has_media(result)  # Validate the exact carrier, including an empty carrier.
    for row in result:
        metadata = row.get("metadata", {})
        carrier = metadata.get("experimental.media.v1", {}) if isinstance(metadata, dict) else {}
        for ref in carrier.get("refs", []):
            if type(ref.get("v")) is not int or ref["v"] != 1 or ref.get("session_id") != session_id:
                raise ValueError("Media adoption returned a foreign or unsupported reference")
    return result


def get_fork_preview(
    parent_session_dir: Path,
    turn: int,
) -> dict[str, Any]:
    """Get a preview of what a fork would produce.

    Useful for displaying to users before they confirm a fork operation.

    Args:
        parent_session_dir: Path to parent session directory.
        turn: Turn number to preview fork at.

    Returns:
        Dictionary with preview information:
        - parent_id: Parent session ID
        - turn: Requested turn
        - max_turns: Total turns in parent
        - message_count: Messages that would be in fork
        - has_orphaned_tools: Whether fork would have orphaned tool calls
        - orphaned_tool_count: Number of orphaned tool calls
        - last_user_message: Preview of last user message in fork
        - last_assistant_message: Preview of last assistant response

    Raises:
        FileNotFoundError: If parent session doesn't exist.
        ValueError: If turn is out of range.
    """
    parent_session_dir = Path(parent_session_dir).resolve()
    transcript_path = parent_session_dir / TRANSCRIPT_FILENAME
    metadata_path = parent_session_dir / METADATA_FILENAME

    if not transcript_path.exists():
        raise FileNotFoundError(f"No transcript.jsonl in {parent_session_dir}")

    messages = load_transcript(parent_session_dir)
    parent_metadata = (
        load_metadata(parent_session_dir) if metadata_path.exists() else {}
    )
    parent_id = parent_metadata.get("session_id", parent_session_dir.name)

    max_turns = count_turns(messages)
    if turn < 1 or turn > max_turns:
        raise ValueError(f"Turn {turn} out of range (1-{max_turns})")

    # Get sliced messages without handling orphaned tools (to detect them)
    boundaries = get_turn_boundaries(messages)
    end_idx = boundaries[turn] if turn < max_turns else len(messages)
    sliced = messages[:end_idx]

    orphaned = find_orphaned_tool_calls(sliced)

    # Find last user and assistant messages
    last_user = ""
    last_assistant = ""
    for msg in reversed(sliced):
        role = msg.get("role")
        content = msg.get("content", "")

        if role == "user" and not last_user:
            last_user = _extract_text_content(content)[:100]
        elif role == "assistant" and not last_assistant:
            last_assistant = _extract_text_content(content)[:100]

        if last_user and last_assistant:
            break

    return {
        "parent_id": parent_id,
        "turn": turn,
        "max_turns": max_turns,
        "message_count": len(sliced),
        "has_orphaned_tools": len(orphaned) > 0,
        "orphaned_tool_count": len(orphaned),
        "last_user_message": last_user,
        "last_assistant_message": last_assistant,
    }


def list_session_forks(
    session_dir: Path,
    sessions_root: Path | None = None,
) -> list[dict[str, Any]]:
    """List all sessions forked from a given session.

    Scans the sessions directory for sessions with parent_id matching
    the given session.

    Args:
        session_dir: Path to the parent session directory.
        sessions_root: Root directory containing all sessions.
                       Defaults to parent of session_dir.

    Returns:
        List of dictionaries with fork information:
        - session_id: Fork session ID
        - session_dir: Path to fork session
        - forked_from_turn: Turn where fork occurred
        - forked_at: Timestamp of fork
        - turn_count: Current turn count in fork
    """
    session_dir = Path(session_dir).resolve()

    if sessions_root is None:
        sessions_root = session_dir.parent

    # Get parent session ID
    metadata_path = session_dir / METADATA_FILENAME
    if metadata_path.exists():
        parent_metadata = load_metadata(session_dir)
        parent_id = parent_metadata.get("session_id", session_dir.name)
    else:
        parent_id = session_dir.name

    forks = []

    # Scan all session directories
    for child_dir in sessions_root.iterdir():
        if not child_dir.is_dir():
            continue

        child_metadata_path = child_dir / METADATA_FILENAME
        if not child_metadata_path.exists():
            continue

        try:
            child_metadata = load_metadata(child_dir)
            if child_metadata.get("parent_id") == parent_id:
                forks.append(
                    {
                        "session_id": child_metadata.get("session_id", child_dir.name),
                        "session_dir": child_dir,
                        "forked_from_turn": child_metadata.get("forked_from_turn"),
                        "forked_at": child_metadata.get("forked_at"),
                        "turn_count": child_metadata.get("turn_count", 0),
                    }
                )
        except Exception:
            # Skip sessions we can't read
            continue

    # Sort by fork time
    forks.sort(key=lambda x: x.get("forked_at") or "", reverse=True)

    return forks


def get_session_lineage(
    session_dir: Path,
    sessions_root: Path | None = None,
) -> dict[str, Any]:
    """Get the full lineage tree for a session.

    Traces both ancestors (parents) and descendants (forks) of a session.

    Args:
        session_dir: Path to the session directory.
        sessions_root: Root directory containing all sessions.

    Returns:
        Dictionary with lineage information:
        - session_id: This session's ID
        - parent_id: Parent session ID (None if root)
        - forked_from_turn: Turn where this was forked (None if root)
        - ancestors: List of ancestor session IDs (parent chain)
        - children: List of direct child session info
        - depth: Fork depth from root (0 = original session)
    """
    session_dir = Path(session_dir).resolve()

    if sessions_root is None:
        sessions_root = session_dir.parent

    # Load this session's metadata
    metadata_path = session_dir / METADATA_FILENAME
    if metadata_path.exists():
        metadata = load_metadata(session_dir)
    else:
        metadata = {"session_id": session_dir.name}

    session_id = metadata.get("session_id", session_dir.name)
    parent_id = metadata.get("parent_id")
    forked_from_turn = metadata.get("forked_from_turn")

    # Trace ancestors
    ancestors = []
    current_parent_id = parent_id
    while current_parent_id:
        ancestors.append(current_parent_id)
        # Try to find parent session
        parent_dir = sessions_root / current_parent_id
        if parent_dir.exists():
            parent_meta_path = parent_dir / METADATA_FILENAME
            if parent_meta_path.exists():
                parent_meta = load_metadata(parent_dir)
                current_parent_id = parent_meta.get("parent_id")
            else:
                # Parent dir exists but lacks metadata — stop tracing ancestors
                break
        else:
            break

    # Get children (direct forks)
    children = list_session_forks(session_dir, sessions_root)

    return {
        "session_id": session_id,
        "parent_id": parent_id,
        "forked_from_turn": forked_from_turn,
        "ancestors": ancestors,
        "children": children,
        "depth": len(ancestors),
    }


# --- Private helpers ---


def _extract_text_content(content: Any) -> str:
    """Extract text from various content formats."""
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        # Handle content blocks (Anthropic format)
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text", "")
        # Fallback: stringify
        return str(content)
    else:
        return str(content) if content else ""
