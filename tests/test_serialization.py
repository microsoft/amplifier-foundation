"""Tests for serialization.py JSON sanitization utilities."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any
import pytest

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


@pytest.mark.parametrize("field", ["metadata", "output"])
def test_canonical_media_null_provenance_survives_real_sanitizer_and_save(tmp_path, field):
    from amplifier_foundation import sanitize_message as public_sanitize
    from amplifier_foundation.session.history import SessionHistoryStore

    ref = {
        "v": 1, "session_id": "session", "sha256": "b" * 64, "mime": "image/png",
        "bytes": 70, "width": 1, "height": 1,
        "provenance": {"kind": "tool", "session_id": "session", "id": "call-1",
                       "created_at": "2026-01-01T00:00:00Z", "expires_at": None},
    }
    message = {
        "role": "tool", "tool_call_id": "call-1", "content": "image label",
        field: {"experimental.media.v1": {"refs": [ref]},
                "other": {"legacy_none": None, "preserve": "unchanged"}},
    }
    original = deepcopy(message)
    clean = public_sanitize(message)
    assert clean[field]["experimental.media.v1"]["refs"] == [ref]
    assert clean[field]["other"] == {"preserve": "unchanged"}
    clean[field]["experimental.media.v1"]["refs"][0]["provenance"]["id"] = "copy"
    assert message == original
    store = SessionHistoryStore(tmp_path / "session")
    store.save([message], {"session_id": "session"}, sanitizer=public_sanitize)
    stored = store.load_messages()[0]
    assert stored[field]["experimental.media.v1"]["refs"] == [ref]
    assert "expires_at" in stored[field]["experimental.media.v1"]["refs"][0]["provenance"]


@pytest.mark.parametrize("bad", [b"pixels", object(), float("nan"), {1: "not-string-key"}])
def test_reserved_media_is_not_lossily_sanitized_or_stringified(bad):
    message = {"role": "tool", "metadata": {
        "experimental.media.v1": {"refs": [{"bad": bad}]}
    }}
    with pytest.raises(ValueError, match="finite JSON"):
        sanitize_message(message)


def test_text_and_unrelated_nested_namespace_are_not_promoted():
    message = {
        "role": "user",
        "content": '{"experimental.media.v1":{"refs":[{"expires_at":null}]}}',
        "metadata": {"other": {"experimental.media.v1": {"legacy": None}}},
    }
    clean = sanitize_message(message)
    assert clean["content"] == message["content"]
    assert clean["metadata"] == {"other": {"experimental.media.v1": {}}}
