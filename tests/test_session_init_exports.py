"""Tests for amplifier_foundation.session __init__.py exports.

Verifies that all 38 public symbols are importable directly from the
top-level session package after the __init__.py update.
"""

from __future__ import annotations


# =============================================================================
# RED: These imports should fail before the __init__.py is updated,
# and pass after the update.
# =============================================================================


class TestExistingExports:
    """Verify pre-existing symbols still export correctly."""

    def test_fork_exports(self):
        from amplifier_foundation.session import (  # noqa: F401
            ForkResult,
            fork_session,
            fork_session_in_memory,
            get_fork_preview,
            get_session_lineage,
            list_session_forks,
        )

    def test_events_exports(self):
        from amplifier_foundation.session import (  # noqa: F401
            count_events,
            get_event_summary,
            get_last_timestamp_for_turn,
            slice_events_for_fork,
            slice_events_to_timestamp,
        )

    def test_messages_exports(self):
        """These were previously imported from .slice, now from .messages."""
        from amplifier_foundation.session import (  # noqa: F401
            add_synthetic_tool_results,
            count_turns,
            find_orphaned_tool_calls,
            get_turn_boundaries,
            get_turn_summary,
            slice_to_turn,
        )

    def test_capabilities_exports(self):
        from amplifier_foundation.session import (  # noqa: F401
            WORKING_DIR_CAPABILITY,
            get_working_dir,
            set_working_dir,
        )


class TestNewExports:
    """Verify newly added symbols are exported from the session package."""

    def test_is_real_user_message_export(self):
        from amplifier_foundation.session import is_real_user_message  # noqa: F401

    def test_store_constants_export(self):
        from amplifier_foundation.session import (  # noqa: F401
            EVENTS_FILENAME,
            METADATA_FILENAME,
            TRANSCRIPT_FILENAME,
        )

    def test_store_functions_export(self):
        from amplifier_foundation.session import (  # noqa: F401
            backup,
            load_metadata,
            load_transcript,
            load_transcript_with_lines,
            read_jsonl,
            write_jsonl,
            write_metadata,
            write_transcript,
        )

    def test_diagnosis_types_export(self):
        from amplifier_foundation.session import (  # noqa: F401
            DiagnosisResult,
            IncompleteTurn,
        )

    def test_diagnosis_functions_export(self):
        from amplifier_foundation.session import (  # noqa: F401
            build_tool_index,
            diagnose_transcript,
            repair_transcript,
            rewind_transcript,
        )


class TestDunderAll:
    """Verify __all__ contains all 38 expected names."""

    def test_all_contains_38_names(self):
        import amplifier_foundation.session as session

        assert hasattr(session, "__all__"), "__all__ must be defined"
        assert len(session.__all__) == 38, (
            f"Expected 38 names in __all__, got {len(session.__all__)}: "
            f"{sorted(session.__all__)}"
        )

    def test_all_includes_new_names(self):
        import amplifier_foundation.session as session

        expected_new = {
            "is_real_user_message",
            "TRANSCRIPT_FILENAME",
            "METADATA_FILENAME",
            "EVENTS_FILENAME",
            "read_jsonl",
            "write_jsonl",
            "load_transcript",
            "load_transcript_with_lines",
            "write_transcript",
            "load_metadata",
            "write_metadata",
            "backup",
            "diagnose_transcript",
            "repair_transcript",
            "rewind_transcript",
            "build_tool_index",
            "DiagnosisResult",
            "IncompleteTurn",
        }
        for name in expected_new:
            assert name in session.__all__, f"{name!r} missing from __all__"

    def test_all_symbols_are_importable(self):
        """Every name in __all__ must be accessible as an attribute."""
        import amplifier_foundation.session as session

        for name in session.__all__:
            assert hasattr(session, name), (
                f"Name {name!r} is in __all__ but not importable from the package"
            )
