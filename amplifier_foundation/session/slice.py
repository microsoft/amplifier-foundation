"""Message slicing utilities for session fork operations.

This module provides pure functions for slicing conversation messages
at turn boundaries. A "turn" is defined as a user message plus all
subsequent non-user messages until the next user message.

Turns are 1-indexed for user-facing operations (turn 1 = first exchange).
"""

from __future__ import annotations

import json
from typing import Any


def get_turn_boundaries(messages: list[dict[str, Any]]) -> list[int]:
    """Return indices where each turn starts (user message positions).

    A turn begins with each user message. This returns the 0-indexed
    positions of all user messages in the conversation.

    Args:
        messages: List of conversation messages with 'role' field.

    Returns:
        List of indices where user messages appear.

    Example:
        >>> messages = [
        ...     {"role": "user", "content": "Q1"},
        ...     {"role": "assistant", "content": "A1"},
        ...     {"role": "user", "content": "Q2"},
        ... ]
        >>> get_turn_boundaries(messages)
        [0, 2]
    """
    return [i for i, msg in enumerate(messages) if msg.get("role") == "user"]


def count_turns(messages: list[dict[str, Any]]) -> int:
    """Count the number of turns in a conversation.

    Args:
        messages: List of conversation messages.

    Returns:
        Number of turns (user messages) in the conversation.
    """
    return len(get_turn_boundaries(messages))


def slice_to_turn(
    messages: list[dict[str, Any]],
    turn: int,
    *,
    handle_orphaned_tools: str = "complete",
) -> list[dict[str, Any]]:
    """Slice messages to include only up to turn N (1-indexed).

    Turn N includes the Nth user message and all responses until the
    next user message (or end of conversation).

    Args:
        messages: Full message list from transcript.
        turn: Turn number (1-indexed). Turn 1 = first user message + response.
        handle_orphaned_tools: How to handle tool_use without tool_result:
            - "complete": Add synthetic error result (default)
            - "remove": Remove the orphaned tool_use content
            - "error": Raise ValueError

    Returns:
        Sliced message list with orphaned tools handled.

    Raises:
        ValueError: If turn < 1 or turn > max_turns, or if handle_orphaned_tools
            is "error" and orphaned tools are found.

    Example:
        >>> messages = [
        ...     {"role": "user", "content": "Q1"},
        ...     {"role": "assistant", "content": "A1"},
        ...     {"role": "user", "content": "Q2"},
        ...     {"role": "assistant", "content": "A2"},
        ... ]
        >>> sliced = slice_to_turn(messages, 1)
        >>> len(sliced)
        2
    """
    if turn < 1:
        raise ValueError(f"Turn must be >= 1, got {turn}")

    boundaries = get_turn_boundaries(messages)
    max_turns = len(boundaries)

    if max_turns == 0:
        raise ValueError("No user messages found in conversation")

    if turn > max_turns:
        raise ValueError(
            f"Turn {turn} exceeds max turns ({max_turns}). "
            f"Valid range: 1-{max_turns}"
        )

    # Find end index: start of turn N+1, or end of messages
    if turn < max_turns:
        end_idx = boundaries[turn]  # Start of next turn (0-indexed, so turn N+1 = boundaries[turn])
    else:
        end_idx = len(messages)  # Include all messages

    sliced = messages[:end_idx]

    # Handle orphaned tool calls
    orphaned = find_orphaned_tool_calls(sliced)
    if orphaned:
        if handle_orphaned_tools == "error":
            raise ValueError(
                f"Orphaned tool calls at fork boundary: {orphaned}. "
                "These tool_use blocks have no matching tool_result."
            )
        elif handle_orphaned_tools == "remove":
            sliced = _remove_orphaned_tool_calls(sliced, orphaned)
        else:  # "complete" is default
            sliced = add_synthetic_tool_results(sliced, orphaned)

    return sliced


def find_orphaned_tool_calls(messages: list[dict[str, Any]]) -> list[str]:
    """Find tool_call IDs that have no corresponding tool result.

    Scans messages for tool_calls in assistant messages and tool results,
    returning IDs of calls that don't have matching results.

    Args:
        messages: List of conversation messages.

    Returns:
        List of orphaned tool_call IDs.
    """
    # Collect all tool_call IDs from assistant messages
    called_ids: set[str] = set()
    for msg in messages:
        if msg.get("role") == "assistant":
            # Check tool_calls array (standard format)
            if "tool_calls" in msg:
                for tc in msg["tool_calls"]:
                    if "id" in tc:
                        called_ids.add(tc["id"])
            # Check content blocks (Anthropic format)
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        if "id" in block:
                            called_ids.add(block["id"])

    # Collect all tool_call_ids from tool results
    result_ids: set[str] = set()
    for msg in messages:
        if msg.get("role") == "tool" and "tool_call_id" in msg:
            result_ids.add(msg["tool_call_id"])

    return list(called_ids - result_ids)


def add_synthetic_tool_results(
    messages: list[dict[str, Any]],
    orphaned_ids: list[str],
) -> list[dict[str, Any]]:
    """Add synthetic error results for orphaned tool calls.

    When forking a session mid-turn, some tool calls may not have results.
    This adds synthetic error results so the conversation remains valid.

    Args:
        messages: List of conversation messages.
        orphaned_ids: List of tool_call IDs needing synthetic results.

    Returns:
        New message list with synthetic tool results appended.
    """
    if not orphaned_ids:
        return messages

    result = list(messages)
    for tool_id in orphaned_ids:
        result.append({
            "role": "tool",
            "tool_call_id": tool_id,
            "content": json.dumps({
                "error": "Tool execution interrupted by session fork",
                "forked": True,
                "message": "This tool call was in progress when the session was forked. "
                           "The result is not available in this forked session."
            }),
        })
    return result


def _remove_orphaned_tool_calls(
    messages: list[dict[str, Any]],
    orphaned_ids: list[str],
) -> list[dict[str, Any]]:
    """Remove orphaned tool_call entries from messages.

    This modifies assistant messages to remove tool_calls that don't have
    corresponding results. Use with caution as this alters conversation history.

    Args:
        messages: List of conversation messages.
        orphaned_ids: List of tool_call IDs to remove.

    Returns:
        New message list with orphaned tool calls removed.
    """
    orphaned_set = set(orphaned_ids)
    result = []

    for msg in messages:
        if msg.get("role") == "assistant":
            new_msg = dict(msg)

            # Filter tool_calls array
            if "tool_calls" in new_msg:
                new_msg["tool_calls"] = [
                    tc for tc in new_msg["tool_calls"]
                    if tc.get("id") not in orphaned_set
                ]
                if not new_msg["tool_calls"]:
                    del new_msg["tool_calls"]

            # Filter content blocks (Anthropic format)
            content = new_msg.get("content")
            if isinstance(content, list):
                new_content = [
                    block for block in content
                    if not (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("id") in orphaned_set
                    )
                ]
                new_msg["content"] = new_content

            result.append(new_msg)
        else:
            result.append(msg)

    return result


def get_turn_summary(
    messages: list[dict[str, Any]],
    turn: int,
    *,
    max_length: int = 100,
) -> dict[str, Any]:
    """Get a summary of a specific turn for display purposes.

    Args:
        messages: List of conversation messages.
        turn: Turn number (1-indexed).
        max_length: Maximum length for content preview.

    Returns:
        Dictionary with turn information:
        - turn: Turn number
        - user_content: Truncated user message
        - assistant_content: Truncated assistant response
        - tool_count: Number of tool calls in turn
        - message_count: Total messages in turn

    Raises:
        ValueError: If turn is out of range.
    """
    boundaries = get_turn_boundaries(messages)
    max_turns = len(boundaries)

    if turn < 1 or turn > max_turns:
        raise ValueError(f"Turn {turn} out of range (1-{max_turns})")

    start_idx = boundaries[turn - 1]  # 0-indexed
    end_idx = boundaries[turn] if turn < max_turns else len(messages)

    turn_messages = messages[start_idx:end_idx]

    user_content = ""
    assistant_content = ""
    tool_count = 0

    for msg in turn_messages:
        role = msg.get("role")
        content = msg.get("content", "")

        if role == "user":
            if isinstance(content, str):
                user_content = content[:max_length]
                if len(content) > max_length:
                    user_content += "..."
            elif isinstance(content, list):
                # Extract text from content blocks
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        user_content = block.get("text", "")[:max_length]
                        break

        elif role == "assistant":
            if isinstance(content, str):
                if not assistant_content:  # First assistant message
                    assistant_content = content[:max_length]
                    if len(content) > max_length:
                        assistant_content += "..."
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text" and not assistant_content:
                            assistant_content = block.get("text", "")[:max_length]
                        elif block.get("type") == "tool_use":
                            tool_count += 1

            # Count tool_calls
            if "tool_calls" in msg:
                tool_count += len(msg["tool_calls"])

    return {
        "turn": turn,
        "user_content": user_content,
        "assistant_content": assistant_content,
        "tool_count": tool_count,
        "message_count": len(turn_messages),
    }
