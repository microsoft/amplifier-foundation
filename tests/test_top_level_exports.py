"""Tests for top-level amplifier_foundation package exports.

Ensures that key classes added in Phase 1 (RuntimeOverlay, TransitionResult)
are importable directly from the top-level amplifier_foundation namespace,
not only from amplifier_foundation.configurator.

Regression guard: hooks-mode does
    from amplifier_foundation import RuntimeOverlay
at module load time inside _get_or_create_overlay().  If the top-level
__init__.py does not re-export the class, every mode activation raises
ImportError at runtime — not at import time, because the import is lazy.
"""

from __future__ import annotations


class TestRuntimeOverlayTopLevelExport:
    """RuntimeOverlay must be importable from the top-level package."""

    def test_runtime_overlay_importable_from_top_level(self) -> None:
        from amplifier_foundation import RuntimeOverlay  # noqa: F401

        assert RuntimeOverlay is not None

    def test_transition_result_importable_from_top_level(self) -> None:
        from amplifier_foundation import TransitionResult  # noqa: F401

        assert TransitionResult is not None

    def test_top_level_and_submodule_runtime_overlay_are_same_class(self) -> None:
        """Top-level import and submodule import must resolve to the same class object."""
        from amplifier_foundation import RuntimeOverlay as TopLevel
        from amplifier_foundation.configurator import RuntimeOverlay as SubModule

        assert TopLevel is SubModule

    def test_top_level_and_submodule_transition_result_are_same_class(self) -> None:
        """Top-level import and submodule import must resolve to the same class object."""
        from amplifier_foundation import TransitionResult as TopLevel
        from amplifier_foundation.configurator import TransitionResult as SubModule

        assert TopLevel is SubModule

    def test_runtime_overlay_in_dunder_all(self) -> None:
        """RuntimeOverlay must appear in __all__ so `from amplifier_foundation import *` works."""
        import amplifier_foundation

        assert "RuntimeOverlay" in amplifier_foundation.__all__, (
            "RuntimeOverlay missing from amplifier_foundation.__all__"
        )

    def test_transition_result_in_dunder_all(self) -> None:
        """TransitionResult must appear in __all__."""
        import amplifier_foundation

        assert "TransitionResult" in amplifier_foundation.__all__, (
            "TransitionResult missing from amplifier_foundation.__all__"
        )
