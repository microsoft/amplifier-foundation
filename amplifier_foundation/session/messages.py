"""Layer Level 1 (Message Algebra): pure list[dict] -> list[dict], zero I/O.

This module provides pure functions for operating on conversation message lists.
All functions are side-effect free and perform no file I/O.

A "turn" is defined as a user message plus all subsequent non-user messages
until the next user message. Turns are 1-indexed for user-facing operations.
"""

from __future__ import annotations

import json
from typing import Any

_DEFAULT_SYNTHETIC_TOOL_CONTENT: str = json.dumps(
    {
        "error": "Tool execution interrupted by session fork",
        "forked": True,
        "message": (
            "This tool call was in progress when the session was forked. "
            "The result is not available in this forked session."
        ),
    }
)

_DEFAULT_SYNTHETIC_ASSISTANT_CONTENT: str = (
    "The previous tool calls were interrupted by a session fork. "
    "Results are not available in this forked session."
)


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


def is_real_user_message(entry: dict[str, Any]) -> bool:
    """Return True if entry represents a genuine human user message.

    A 'real user message' is:
    - role is 'user'
    - no tool_call_id field
    - content is NOT wrapped in <system-reminder> tags

    Content can be a string or a list of content blocks.
    Assistant messages and tool role messages return False.

    Args:
        entry: A message dict with at least a 'role' field.

    Returns:
        True if this is a real user-authored message, False otherwise.
    """
    if entry.get("role") != "user":
        return False

    if "tool_call_id" in entry:
        return False

    content = entry.get("content", "")

    # Check for system-reminder tags in string content
    if isinstance(content, str):
        if content.strip().startswith("<system-reminder>"):
            return False

    # Check for system-reminder tags in list content
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                text = block.get("text", "")
                if isinstance(text, str) and text.strip().startswith(
                    "<system-reminder>"
                ):
                    return False

    return True


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
            f"Turn {turn} exceeds max turns ({max_turns}). Valid range: 1-{max_turns}"
        )

    # Find end index: start of turn N+1, or end of messages
    if turn < max_turns:
        end_idx = boundaries[turn]  # Start of next turn (0-indexed)
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
    *,
    synthetic_content: str | None = None,
) -> list[dict[str, Any]]:
    """Add synthetic error results for orphaned tool calls.

    When forking a session mid-turn, some tool calls may not have results.
    This adds synthetic error results inserted immediately after the assistant
    message that made the tool calls, so the conversation remains valid.

    Args:
        messages: List of conversation messages.
        orphaned_ids: List of tool_call IDs needing synthetic results.
        synthetic_content: Optional custom content string for the synthetic tool
            results. If None, uses the default fork-interruption JSON content.

    Returns:
        New message list with synthetic tool results inserted at correct positions.
    """
    if not orphaned_ids:
        return messages

    content_str = (
        synthetic_content
        if synthetic_content is not None
        else _DEFAULT_SYNTHETIC_TOOL_CONTENT
    )
    orphaned_set = set(orphaned_ids)

    # Build mapping of tool_call_id -> tool_name from assistant messages
    tool_names: dict[str, str] = {}
    for msg in messages:
        if msg.get("role") == "assistant":
            # Handle OpenAI format: tool_calls array
            for tc in msg.get("tool_calls", []):
                tc_id = tc.get("id", "")
                tc_name = tc.get("function", {}).get("name", "") or tc.get("name", "")
                if tc_id and tc_name:
                    tool_names[tc_id] = tc_name
            # Handle Anthropic format: content blocks with tool_use
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tc_id = block.get("id", "")
                        tc_name = block.get("name", "")
                        if tc_id and tc_name:
                            tool_names[tc_id] = tc_name

    # Walk messages and insert synthetics at correct positions
    result: list[dict[str, Any]] = []
    for i, msg in enumerate(messages):
        result.append(msg)

        # After each assistant message, check if any of its tool_calls are orphaned
        if msg.get("role") == "assistant":
            # Collect orphaned tool_call IDs from this assistant message (OpenAI format)
            msg_orphans: list[str] = []
            for tc in msg.get("tool_calls", []):
                tc_id = tc.get("id", "")
                if tc_id in orphaned_set:
                    msg_orphans.append(tc_id)
            # Also check Anthropic format
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tc_id = block.get("id", "")
                        if tc_id in orphaned_set:
                            msg_orphans.append(tc_id)

            if msg_orphans:
                # Insert synthetic results immediately after this assistant message
                for tc_id in msg_orphans:
                    tool_name = tool_names.get(tc_id)
                    synthetic: dict[str, Any] = {
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": content_str,
                    }
                    if tool_name:
                        synthetic["name"] = tool_name
                    result.append(synthetic)

                # FM3 handling: if the next original message is a user message, insert
                # a synthetic assistant response to close the interrupted turn first
                next_idx = i + 1
                if (
                    next_idx < len(messages)
                    and messages[next_idx].get("role") == "user"
                ):
                    result.append(
                        {
                            "role": "assistant",
                            "content": _DEFAULT_SYNTHETIC_ASSISTANT_CONTENT,
                        }
                    )

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
                    tc
                    for tc in new_msg["tool_calls"]
                    if tc.get("id") not in orphaned_set
                ]
                if not new_msg["tool_calls"]:
                    del new_msg["tool_calls"]

            # Filter content blocks (Anthropic format)
            content = new_msg.get("content")
            if isinstance(content, list):
                new_content = [
                    block
                    for block in content
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
