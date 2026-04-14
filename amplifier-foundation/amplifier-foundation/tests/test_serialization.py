"""Tests for serialization.py JSON sanitization utilities."""

from __future__ import annotations

import json
from typing import Any

from amplifier_foundation.serialization import sanitize_for_json
from amplifier_foundation.serialization import sanitize_message


class TestSanitizeForJson:
    """Tests for sanitize_for_json function."""

    def test_primitives_unchanged(self) -> None:
        """Primitive types pass through unchanged."""
        assert sanitize_for_json(None) is None
        assert sanitize_for_json(True) is True
        assert sanitize_for_json(False) is False
        assert sanitize_for_json(42) == 42
        assert sanitize_for_json(3.14) == 3.14
        assert sanitize_for_json("hello") == "hello"

    def test_dict_sanitization(self) -> None:
        """Dicts are sanitized recursively."""
        data = {"a": 1, "b": {"c": 2}}
        result = sanitize_for_json(data)
        assert result == {"a": 1, "b": {"c": 2}}
        # Verify serializable
        json.dumps(result)

    def test_list_sanitization(self) -> None:
        """Lists are sanitized recursively."""
        data = [1, "two", {"three": 3}]
        result = sanitize_for_json(data)
        assert result == [1, "two", {"three": 3}]
        json.dumps(result)

    def test_tuple_converted_to_list(self) -> None:
        """Tuples are converted to lists."""
        data = (1, 2, 3)
        result = sanitize_for_json(data)
        assert result == [1, 2, 3]
        json.dumps(result)

    def test_nested_structure(self) -> None:
        """Handles deeply nested structures."""
        data = {
            "level1": {
                "level2": {
                    "level3": [1, 2, {"level4": "deep"}],
                },
            },
        }
        result = sanitize_for_json(data)
        assert result == data
        json.dumps(result)

    def test_non_serializable_returns_none(self) -> None:
        """Non-serializable values return None."""

        class NotSerializable:
            """Class that cannot be JSON serialized."""

            __slots__ = ()  # Intentionally non-serializable

        result = sanitize_for_json(NotSerializable())
        assert result is None

    def test_object_with_dict(self) -> None:
        """Objects with __dict__ are converted."""

        class SimpleObject:
            def __init__(self) -> None:
                self.a = 1
                self.b = "two"

        obj = SimpleObject()
        result = sanitize_for_json(obj)
        assert result == {"a": 1, "b": "two"}

    def test_max_depth_protection(self) -> None:
        """Max depth prevents infinite recursion."""
        # Create a deep structure
        data: dict[str, Any] = {}
        current = data
        for _i in range(100):
            current["nested"] = {}
            current = current["nested"]
        current["value"] = "deep"

        # Should not raise, just truncate at max_depth
        result = sanitize_for_json(data, max_depth=10)
        # Result should be truncated
        json.dumps(result)  # Should be serializable

    def test_filters_none_values_in_dict(self) -> None:
        """None values from non-serializable fields are filtered."""

        class NotSerializable:
            """Class that cannot be JSON serialized."""

            __slots__ = ()  # Intentionally non-serializable

        data = {
            "good": "value",
            "bad": NotSerializable(),
        }
        result = sanitize_for_json(data)
        assert result == {"good": "value"}


class TestSanitizeMessage:
    """Tests for sanitize_message function."""

    def test_simple_message(self) -> None:
        """Simple message passes through."""
        message = {"role": "user", "content": "hello"}
        result = sanitize_message(message)
        assert result == {"role": "user", "content": "hello"}

    def test_extracts_thinking_text_from_dict(self) -> None:
        """Extracts thinking text from dict-style thinking_block."""
        message = {
            "role": "assistant",
            "content": "response",
            "thinking_block": {"text": "my thinking"},
        }
        result = sanitize_message(message)
        assert result["thinking_text"] == "my thinking"
        assert "thinking_block" not in result

    def test_extracts_thinking_text_from_object(self) -> None:
        """Extracts thinking text from object-style thinking_block."""

        class ThinkingBlock:
            def __init__(self) -> None:
                self.text = "object thinking"

        message = {
            "role": "assistant",
            "content": "response",
            "thinking_block": ThinkingBlock(),
        }
        result = sanitize_message(message)
        assert result["thinking_text"] == "object thinking"

    def test_removes_content_blocks(self) -> None:
        """Removes content_blocks field."""
        message = {
            "role": "assistant",
            "content": "response",
            "content_blocks": [{"type": "text"}, {"type": "tool_use"}],
        }
        result = sanitize_message(message)
        assert "content_blocks" not in result
        assert result["content"] == "response"

    def test_handles_non_dict_input(self) -> None:
        """Handles non-dict input gracefully."""
        result = sanitize_message("not a dict")  # type: ignore[arg-type]
        assert isinstance(result, dict)

    def test_preserves_standard_fields(self) -> None:
        """Preserves standard message fields."""
        message = {
            "role": "assistant",
            "content": "hello",
            "name": "bot",
            "model": "claude",
        }
        result = sanitize_message(message)
        assert result == message

    def test_result_is_serializable(self) -> None:
        """Result is always JSON serializable."""

        class WeirdObject:
            def __init__(self) -> None:
                self.data = "stuff"

        message = {
            "role": "user",
            "content": "hello",
            "extra": WeirdObject(),
        }
        result = sanitize_message(message)
        # Should not raise
        json.dumps(result)
