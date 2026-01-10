"""Events.jsonl slicing utilities for session fork operations.

This module provides functions for slicing events.jsonl files when forking
sessions. Events are correlated to turns via timestamps - we find the last
event timestamp for the target turn and include all events up to that point.

Note: events.jsonl is primarily an audit log and is NOT required for session
resume. The transcript.jsonl is the source of truth for conversation state.
However, preserving events provides complete audit trail for forked sessions.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


def slice_events_to_timestamp(
    events_path: Path,
    cutoff_timestamp: str,
    output_path: Path,
) -> int:
    """Slice events.jsonl to include only events up to a timestamp.

    Reads events line by line (memory efficient for large files) and writes
    events with timestamp <= cutoff_timestamp to the output file.

    Args:
        events_path: Path to source events.jsonl file.
        cutoff_timestamp: ISO format timestamp. Events with ts <= this are included.
        output_path: Path to write sliced events.

    Returns:
        Number of events written.

    Raises:
        FileNotFoundError: If events_path doesn't exist.
    """
    if not events_path.exists():
        raise FileNotFoundError(f"Events file not found: {events_path}")

    cutoff_dt = _parse_timestamp(cutoff_timestamp)
    count = 0

    with open(events_path, "r", encoding="utf-8") as infile:
        with open(output_path, "w", encoding="utf-8") as outfile:
            for line in infile:
                line = line.strip()
                if not line:
                    continue

                try:
                    event = json.loads(line)
                    event_ts = event.get("ts") or event.get("timestamp")

                    if event_ts:
                        event_dt = _parse_timestamp(event_ts)
                        if event_dt <= cutoff_dt:
                            outfile.write(line + "\n")
                            count += 1
                    else:
                        # Events without timestamp are included (shouldn't happen)
                        outfile.write(line + "\n")
                        count += 1

                except json.JSONDecodeError:
                    # Skip malformed lines
                    continue

    return count


def get_last_timestamp_for_turn(
    transcript_path: Path,
    turn: int,
) -> str | None:
    """Get the timestamp of the last message in a turn.

    Reads the transcript to find the last message belonging to the specified
    turn and returns its timestamp.

    Args:
        transcript_path: Path to transcript.jsonl file.
        turn: Turn number (1-indexed).

    Returns:
        ISO timestamp string of the last message in the turn, or None if
        no timestamp is found.

    Raises:
        FileNotFoundError: If transcript_path doesn't exist.
        ValueError: If turn is out of range.
    """
    if not transcript_path.exists():
        raise FileNotFoundError(f"Transcript not found: {transcript_path}")

    messages = list(_read_jsonl(transcript_path))

    # Find turn boundaries
    boundaries = [i for i, msg in enumerate(messages) if msg.get("role") == "user"]

    if not boundaries:
        raise ValueError("No user messages found in transcript")

    max_turns = len(boundaries)
    if turn < 1 or turn > max_turns:
        raise ValueError(f"Turn {turn} out of range (1-{max_turns})")

    # Find end of turn
    start_idx = boundaries[turn - 1]
    end_idx = boundaries[turn] if turn < max_turns else len(messages)

    # Get timestamp from last message in turn
    turn_messages = messages[start_idx:end_idx]

    # Search backwards for a message with timestamp
    for msg in reversed(turn_messages):
        ts = msg.get("timestamp") or msg.get("ts")
        if ts:
            return ts

    return None


def slice_events_for_fork(
    events_path: Path,
    transcript_path: Path,
    turn: int,
    output_path: Path,
) -> int:
    """Slice events.jsonl for a fork at a specific turn.

    This is a convenience function that:
    1. Finds the last timestamp for the target turn
    2. Slices events to that timestamp

    Args:
        events_path: Path to source events.jsonl file.
        transcript_path: Path to transcript.jsonl file.
        turn: Turn number to fork at (1-indexed).
        output_path: Path to write sliced events.

    Returns:
        Number of events written.

    Raises:
        FileNotFoundError: If source files don't exist.
        ValueError: If turn is out of range or no timestamp found.
    """
    cutoff_ts = get_last_timestamp_for_turn(transcript_path, turn)

    if cutoff_ts is None:
        # No timestamp found - copy all events up to a reasonable point
        # This shouldn't happen in practice, but handle gracefully
        # by creating an empty events file
        output_path.write_text("")
        return 0

    return slice_events_to_timestamp(events_path, cutoff_ts, output_path)


def count_events(events_path: Path) -> int:
    """Count the number of events in an events.jsonl file.

    Args:
        events_path: Path to events.jsonl file.

    Returns:
        Number of valid JSON lines in the file.
    """
    if not events_path.exists():
        return 0

    count = 0
    for _ in _read_jsonl(events_path):
        count += 1
    return count


def get_event_summary(events_path: Path) -> dict[str, Any]:
    """Get a summary of events in an events.jsonl file.

    Args:
        events_path: Path to events.jsonl file.

    Returns:
        Dictionary with:
        - total_events: Total count
        - event_types: Dict of event type -> count
        - first_timestamp: First event timestamp
        - last_timestamp: Last event timestamp
    """
    if not events_path.exists():
        return {
            "total_events": 0,
            "event_types": {},
            "first_timestamp": None,
            "last_timestamp": None,
        }

    event_types: dict[str, int] = {}
    first_ts: str | None = None
    last_ts: str | None = None
    total = 0

    for event in _read_jsonl(events_path):
        total += 1

        # Count event types
        event_type = event.get("event", "unknown")
        event_types[event_type] = event_types.get(event_type, 0) + 1

        # Track timestamps
        ts = event.get("ts") or event.get("timestamp")
        if ts:
            if first_ts is None:
                first_ts = ts
            last_ts = ts

    return {
        "total_events": total,
        "event_types": event_types,
        "first_timestamp": first_ts,
        "last_timestamp": last_ts,
    }


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Read a JSONL file yielding parsed objects.

    Args:
        path: Path to JSONL file.

    Yields:
        Parsed JSON objects from each line.
    """
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def _parse_timestamp(ts: str) -> datetime:
    """Parse an ISO format timestamp string.

    Handles various ISO formats with and without timezone info.

    Args:
        ts: ISO format timestamp string.

    Returns:
        datetime object (may or may not have tzinfo).
    """
    # Handle various ISO formats
    # Try common formats
    formats = [
        "%Y-%m-%dT%H:%M:%S.%f%z",  # Full with microseconds and tz
        "%Y-%m-%dT%H:%M:%S%z",      # Without microseconds, with tz
        "%Y-%m-%dT%H:%M:%S.%fZ",    # With Z suffix
        "%Y-%m-%dT%H:%M:%SZ",       # Without microseconds, Z suffix
        "%Y-%m-%dT%H:%M:%S.%f",     # Without timezone
        "%Y-%m-%dT%H:%M:%S",        # Basic ISO
    ]

    # Normalize Z to +00:00
    ts_normalized = ts.replace("Z", "+00:00")

    # Handle +00:00 format (add colon if missing)
    if "+" in ts_normalized and ":" not in ts_normalized.split("+")[1]:
        parts = ts_normalized.split("+")
        tz_part = parts[1]
        if len(tz_part) == 4:  # e.g., "0000"
            ts_normalized = parts[0] + "+" + tz_part[:2] + ":" + tz_part[2:]

    for fmt in formats:
        try:
            return datetime.strptime(ts_normalized, fmt)
        except ValueError:
            continue

    # Fallback: use fromisoformat (Python 3.11+)
    try:
        return datetime.fromisoformat(ts_normalized)
    except ValueError:
        pass

    # Last resort: just use the original string comparison will work for ISO
    raise ValueError(f"Cannot parse timestamp: {ts}")
