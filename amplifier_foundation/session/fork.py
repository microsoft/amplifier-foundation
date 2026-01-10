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

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .events import slice_events_for_fork
from .slice import (
    add_synthetic_tool_results,
    count_turns,
    find_orphaned_tool_calls,
    get_turn_boundaries,
    slice_to_turn,
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
        handle_orphaned_tools: How to handle tool_use without tool_result:
            - "complete": Add synthetic error result (default)
            - "remove": Remove the orphaned tool_use content
            - "error": Raise ValueError

    Returns:
        ForkResult with new session details.

    Raises:
        FileNotFoundError: If parent session doesn't exist or lacks transcript.
        ValueError: If turn number is invalid.

    Example:
        >>> result = fork_session(
        ...     Path("~/.amplifier/projects/myproj/sessions/abc123"),
        ...     turn=3,
        ... )
        >>> print(f"Forked to {result.session_id} at turn {result.forked_from_turn}")
    """
    parent_session_dir = Path(parent_session_dir).resolve()

    # Validate parent exists
    transcript_path = parent_session_dir / "transcript.jsonl"
    metadata_path = parent_session_dir / "metadata.json"
    events_path = parent_session_dir / "events.jsonl"

    if not transcript_path.exists():
        raise FileNotFoundError(
            f"No transcript.jsonl in {parent_session_dir}. "
            "This doesn't appear to be a valid session directory."
        )

    # Load parent data
    messages = _load_transcript(transcript_path)
    parent_metadata = _load_metadata(metadata_path) if metadata_path.exists() else {}
    parent_id = parent_metadata.get("session_id", parent_session_dir.name)

    # Determine turn
    max_turns = count_turns(messages)
    if max_turns == 0:
        raise ValueError("Cannot fork: session has no user messages")

    if turn is None:
        turn = max_turns

    if turn < 1 or turn > max_turns:
        raise ValueError(
            f"Turn {turn} out of range. Valid range: 1-{max_turns}"
        )

    # Slice messages to turn
    sliced = slice_to_turn(messages, turn, handle_orphaned_tools=handle_orphaned_tools)

    # Generate new session ID
    session_id = new_session_id or str(uuid.uuid4())

    # Determine target directory
    if target_dir is None:
        # Create sibling to parent
        base_dir = parent_session_dir.parent
    else:
        base_dir = Path(target_dir).resolve()

    new_session_dir = base_dir / session_id

    # Create new session directory
    new_session_dir.mkdir(parents=True, exist_ok=True)

    # Write forked transcript
    _write_transcript(new_session_dir / "transcript.jsonl", sliced)

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
        "profile": parent_metadata.get("profile"),
        "model": parent_metadata.get("model"),
    }
    _write_metadata(new_session_dir / "metadata.json", new_metadata)

    # Handle events.jsonl
    events_count = 0
    if include_events and events_path.exists():
        try:
            events_count = slice_events_for_fork(
                events_path,
                transcript_path,
                turn,
                new_session_dir / "events.jsonl",
            )
        except Exception:
            # Events slicing is best-effort - don't fail the fork
            # Just create empty events file
            (new_session_dir / "events.jsonl").write_text("")
    elif include_events:
        # Create empty events file for consistency
        (new_session_dir / "events.jsonl").write_text("")

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
    handle_orphaned_tools: str = "complete",
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
    max_turns = count_turns(messages)

    if turn is None:
        turn = max_turns if max_turns > 0 else 0

    if max_turns == 0:
        # Empty conversation - return empty fork
        return ForkResult(
            session_id=str(uuid.uuid4()),
            session_dir=None,
            parent_id=parent_id or "unknown",
            forked_from_turn=0,
            message_count=0,
            messages=[],
        )

    # Slice messages
    sliced = slice_to_turn(messages, turn, handle_orphaned_tools=handle_orphaned_tools)

    return ForkResult(
        session_id=str(uuid.uuid4()),
        session_dir=None,
        parent_id=parent_id or "unknown",
        forked_from_turn=turn,
        message_count=len(sliced),
        messages=sliced,
    )


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
    transcript_path = parent_session_dir / "transcript.jsonl"
    metadata_path = parent_session_dir / "metadata.json"

    if not transcript_path.exists():
        raise FileNotFoundError(f"No transcript.jsonl in {parent_session_dir}")

    messages = _load_transcript(transcript_path)
    parent_metadata = _load_metadata(metadata_path) if metadata_path.exists() else {}
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
    metadata_path = session_dir / "metadata.json"
    if metadata_path.exists():
        parent_metadata = _load_metadata(metadata_path)
        parent_id = parent_metadata.get("session_id", session_dir.name)
    else:
        parent_id = session_dir.name

    forks = []

    # Scan all session directories
    for child_dir in sessions_root.iterdir():
        if not child_dir.is_dir():
            continue

        child_metadata_path = child_dir / "metadata.json"
        if not child_metadata_path.exists():
            continue

        try:
            child_metadata = _load_metadata(child_metadata_path)
            if child_metadata.get("parent_id") == parent_id:
                forks.append({
                    "session_id": child_metadata.get("session_id", child_dir.name),
                    "session_dir": child_dir,
                    "forked_from_turn": child_metadata.get("forked_from_turn"),
                    "forked_at": child_metadata.get("forked_at"),
                    "turn_count": child_metadata.get("turn_count", 0),
                })
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
    metadata_path = session_dir / "metadata.json"
    if metadata_path.exists():
        metadata = _load_metadata(metadata_path)
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
            parent_meta_path = parent_dir / "metadata.json"
            if parent_meta_path.exists():
                parent_meta = _load_metadata(parent_meta_path)
                current_parent_id = parent_meta.get("parent_id")
            else:
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


def _load_transcript(path: Path) -> list[dict[str, Any]]:
    """Load JSONL transcript file."""
    messages = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return messages


def _write_transcript(path: Path, messages: list[dict[str, Any]]) -> None:
    """Write JSONL transcript file."""
    with open(path, "w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")


def _load_metadata(path: Path) -> dict[str, Any]:
    """Load JSON metadata file."""
    return json.loads(path.read_text(encoding="utf-8"))


def _write_metadata(path: Path, metadata: dict[str, Any]) -> None:
    """Write JSON metadata file."""
    path.write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


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
