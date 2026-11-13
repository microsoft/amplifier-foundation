"""Serialization utilities for amplifier-foundation.

Provides sanitization for safely persisting data that may contain
non-serializable objects (common with LLM API responses).

Philosophy: These are pure mechanisms. Apps decide WHAT to persist.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)
_MEDIA_NAMESPACE = "experimental.media.v1"


def _media_json(value: Any, *, depth: int = 12) -> Any:
    """Copy reserved media JSON exactly, including semantic nulls.

    This is a serialization check, not ref ownership, format or policy admission.
    Never coerce objects/paths/bytes into a resolvable-looking reference. A bad
    reserved carrier refuses persistence rather than silently dropping fields.
    """
    import math

    if depth <= 0:
        raise ValueError("Canonical media metadata exceeds serialization depth")
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if type(value) is list:
        return [_media_json(item, depth=depth - 1) for item in value]
    if type(value) is dict and all(type(key) is str for key in value):
        return {key: _media_json(item, depth=depth - 1)
                for key, item in value.items()}
    raise ValueError("Canonical media metadata must contain only finite JSON data")


def _sanitize_carrier(value: dict[str, Any]) -> dict[str, Any]:
    media = value[_MEDIA_NAMESPACE]
    if (type(media) is not dict or set(media) != {"refs"}
            or type(media["refs"]) is not list):
        raise ValueError("Invalid canonical media metadata carrier")
    # Preserve other namespaces' existing sanitization contract unchanged.
    result = sanitize_for_json({key: item for key, item in value.items()
                                if key != _MEDIA_NAMESPACE})
    result[_MEDIA_NAMESPACE] = _media_json(media)
    return result


def sanitize_for_json(value: Any, *, max_depth: int = 50) -> Any:
    """Recursively sanitize a value to ensure it's JSON-serializable.

    Handles common cases from LLM responses:
    - Non-serializable objects (returns None or extracts useful text)
    - Nested dicts and lists
    - Objects with __dict__

    Based on app-cli's `_sanitize_value()` pattern.

    Args:
        value: Any value that may or may not be serializable
        max_depth: Maximum recursion depth (prevents infinite loops)

    Returns:
        Sanitized value that's JSON-serializable

    Example:
        # Sanitize LLM response for persistence
        clean_response = sanitize_for_json(llm_response)
        json.dumps(clean_response)  # Now safe
    """
    if max_depth <= 0:
        return None

    # Handle None and primitives (always serializable)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    # Handle dicts recursively
    if isinstance(value, dict):
        return {
            k: sanitize_for_json(v, max_depth=max_depth - 1)
            for k, v in value.items()
            if sanitize_for_json(v, max_depth=max_depth - 1) is not None
        }

    # Handle lists recursively
    if isinstance(value, list):
        sanitized = []
        for item in value:
            clean_item = sanitize_for_json(item, max_depth=max_depth - 1)
            if clean_item is not None:
                sanitized.append(clean_item)
        return sanitized

    # Handle tuples (convert to list)
    if isinstance(value, tuple):
        return sanitize_for_json(list(value), max_depth=max_depth - 1)

    # Try objects with __dict__ (like Pydantic models)
    if hasattr(value, "__dict__"):
        try:
            return sanitize_for_json(vars(value), max_depth=max_depth - 1)
        except Exception:
            pass

    # Try model_dump for Pydantic v2
    if hasattr(value, "model_dump"):
        try:
            return sanitize_for_json(value.model_dump(), max_depth=max_depth - 1)
        except Exception:
            pass

    # Last resort: try to serialize directly
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        logger.debug(f"Skipping non-serializable value of type {type(value).__name__}")
        return None


def sanitize_message(message: dict[str, Any]) -> dict[str, Any]:
    """Sanitize a chat message for persistence.

    Special handling for known non-serializable fields from LLM APIs:
    - thinking_block: Extracts text content
    - content_blocks: Processes each block

    Based on app-cli's `_sanitize_message()` pattern.

    Args:
        message: Chat message dict (may contain non-serializable fields)

    Returns:
        Sanitized message safe for JSON serialization

    Example:
        # Sanitize assistant message with thinking
        clean_msg = sanitize_message({
            "role": "assistant",
            "content": "Hello",
            "thinking_block": <ThinkingBlock object>
        })
    """
    if not isinstance(message, dict):
        result = sanitize_for_json(message)
        return result if isinstance(result, dict) else {}

    sanitized: dict[str, Any] = {}

    for key, value in message.items():
        if (key in ("metadata", "output") and isinstance(value, dict)
                and _MEDIA_NAMESPACE in value):
            sanitized[key] = _sanitize_carrier(value)
            continue
        # Handle known problematic fields
        if key == "thinking_block":
            # Extract text from thinking block if available
            if isinstance(value, dict) and "text" in value:
                sanitized["thinking_text"] = value["text"]
            elif hasattr(value, "text"):
                sanitized["thinking_text"] = value.text  # pyright: ignore[reportAttributeAccessIssue]
            continue

        if key == "content_blocks":
            # These often contain raw API objects - skip
            continue

        # Sanitize other fields
        clean_value = sanitize_for_json(value)
        if clean_value is not None:
            sanitized[key] = clean_value

    return sanitized
