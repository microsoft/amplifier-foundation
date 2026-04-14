"""Tests for backward-compatibility shim in amplifier_foundation.session.slice.

Verifies that:
1. Importing from slice.py issues a DeprecationWarning
2. All 7 functions re-exported from messages.py work correctly via the shim
3. __all__ exposes all expected names
"""

from __future__ import annotations

import importlib
import sys
import warnings


class TestSliceDeprecationWarning:
    """slice.py must fire a DeprecationWarning on import."""

    def test_import_fires_deprecation_warning(self):
        """Importing from slice module raises DeprecationWarning."""
        # Remove any cached module so re-import fires the warning
        for key in list(sys.modules.keys()):
            if "amplifier_foundation.session.slice" in key:
                del sys.modules[key]

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            importlib.import_module("amplifier_foundation.session.slice")

        deprecation_warnings = [
            w for w in caught if issubclass(w.category, DeprecationWarning)
        ]
        assert len(deprecation_warnings) >= 1, (
            "Expected at least one DeprecationWarning when importing "
            "amplifier_foundation.session.slice, got none."
        )

    def test_deprecation_message_mentions_messages_module(self):
        """The DeprecationWarning message must point to messages.py."""
        for key in list(sys.modules.keys()):
            if "amplifier_foundation.session.slice" in key:
                del sys.modules[key]

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            importlib.import_module("amplifier_foundation.session.slice")

        deprecation_warnings = [
            w for w in caught if issubclass(w.category, DeprecationWarning)
        ]
        assert deprecation_warnings, "No DeprecationWarning was raised."
        msg = str(deprecation_warnings[0].message)
        assert "messages" in msg, (
            f"DeprecationWarning message should mention 'messages', got: {msg!r}"
        )


class TestSliceShimExports:
    """slice.py shim must re-export all 7 functions from messages.py."""

    def _get_slice_module(self):
        """Import slice module, ignoring deprecation warnings."""
        for key in list(sys.modules.keys()):
            if "amplifier_foundation.session.slice" in key:
                del sys.modules[key]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            return importlib.import_module("amplifier_foundation.session.slice")

    def test_all_contains_expected_functions(self):
        """__all__ must list all 7 function names."""
        mod = self._get_slice_module()
        expected = {
            "add_synthetic_tool_results",
            "count_turns",
            "find_orphaned_tool_calls",
            "get_turn_boundaries",
            "get_turn_summary",
            "is_real_user_message",
            "slice_to_turn",
        }
        assert hasattr(mod, "__all__"), "slice module must define __all__"
        assert set(mod.__all__) == expected, (
            f"__all__ mismatch.\n  Expected: {sorted(expected)}\n  Got: {sorted(mod.__all__)}"
        )

    def test_get_turn_boundaries_works(self):
        """get_turn_boundaries imported from slice shim works correctly."""
        mod = self._get_slice_module()
        msgs = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
        ]
        assert mod.get_turn_boundaries(msgs) == [0, 2]

    def test_count_turns_works(self):
        """count_turns imported from slice shim works correctly."""
        mod = self._get_slice_module()
        msgs = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
        ]
        assert mod.count_turns(msgs) == 1

    def test_is_real_user_message_works(self):
        """is_real_user_message imported from slice shim works correctly."""
        mod = self._get_slice_module()
        assert mod.is_real_user_message({"role": "user", "content": "hi"}) is True
        assert mod.is_real_user_message({"role": "assistant", "content": "hi"}) is False

    def test_slice_to_turn_works(self):
        """slice_to_turn imported from slice shim works correctly."""
        mod = self._get_slice_module()
        msgs = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
            {"role": "assistant", "content": "A2"},
        ]
        sliced = mod.slice_to_turn(msgs, 1)
        assert len(sliced) == 2
        assert sliced[0]["content"] == "Q1"

    def test_find_orphaned_tool_calls_works(self):
        """find_orphaned_tool_calls imported from slice shim works correctly."""
        mod = self._get_slice_module()
        msgs = [
            {"role": "user", "content": "use tool"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call_x", "function": {"name": "f", "arguments": "{}"}}
                ],
            },
            # No tool result — call_x is orphaned
        ]
        orphaned = mod.find_orphaned_tool_calls(msgs)
        assert "call_x" in orphaned

    def test_add_synthetic_tool_results_works(self):
        """add_synthetic_tool_results imported from slice shim works correctly."""
        mod = self._get_slice_module()
        msgs = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call_y", "function": {"name": "g", "arguments": "{}"}}
                ],
            }
        ]
        result = mod.add_synthetic_tool_results(msgs, ["call_y"])
        assert len(result) == 2  # original + synthetic
        assert result[1]["role"] == "tool"

    def test_get_turn_summary_works(self):
        """get_turn_summary imported from slice shim works correctly."""
        mod = self._get_slice_module()
        msgs = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "World"},
        ]
        summary = mod.get_turn_summary(msgs, 1)
        assert summary["turn"] == 1
        assert summary["user_content"] == "Hello"
