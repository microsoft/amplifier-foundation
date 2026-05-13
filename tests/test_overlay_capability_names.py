"""Regression test: overlay capability name constants are producer-neutral.

Verifies that:
  1. RUNTIME_SKILL_OVERLAY_CAPABILITY and RUNTIME_CONTEXT_OVERLAY_CAPABILITY
     are importable from the amplifier_foundation top level.
  2. Their values are the producer-neutral strings, not the legacy "mode_overlay_*" names.
  3. They match what RuntimeOverlay actually registers on the coordinator
     (i.e., the internal _CAP_OVERLAY_* constants in _overlay.py).

These tests MUST be RED on the old "mode_overlay_*" names and GREEN after the rename.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestCapabilityNameExports:
    """Top-level import and string-value assertions."""

    def test_runtime_skill_overlay_capability_importable(self) -> None:
        """RUNTIME_SKILL_OVERLAY_CAPABILITY must be importable from amplifier_foundation."""
        from amplifier_foundation import RUNTIME_SKILL_OVERLAY_CAPABILITY  # noqa: F401

    def test_runtime_context_overlay_capability_importable(self) -> None:
        """RUNTIME_CONTEXT_OVERLAY_CAPABILITY must be importable from amplifier_foundation."""
        from amplifier_foundation import RUNTIME_CONTEXT_OVERLAY_CAPABILITY  # noqa: F401

    def test_skill_capability_value_is_producer_neutral(self) -> None:
        """RUNTIME_SKILL_OVERLAY_CAPABILITY must use the producer-neutral string."""
        from amplifier_foundation import RUNTIME_SKILL_OVERLAY_CAPABILITY

        assert RUNTIME_SKILL_OVERLAY_CAPABILITY == "runtime_skill_overlay", (
            f"Expected 'runtime_skill_overlay', got {RUNTIME_SKILL_OVERLAY_CAPABILITY!r}. "
            "Capability names must not embed the producer ('mode_*') — they describe "
            "the runtime surface, not who populated it."
        )

    def test_context_capability_value_is_producer_neutral(self) -> None:
        """RUNTIME_CONTEXT_OVERLAY_CAPABILITY must use the producer-neutral string."""
        from amplifier_foundation import RUNTIME_CONTEXT_OVERLAY_CAPABILITY

        assert RUNTIME_CONTEXT_OVERLAY_CAPABILITY == "runtime_context_overlay", (
            f"Expected 'runtime_context_overlay', got {RUNTIME_CONTEXT_OVERLAY_CAPABILITY!r}. "
            "Capability names must not embed the producer ('mode_*') — they describe "
            "the runtime surface, not who populated it."
        )


class TestOverlayRegistersCorrectCapabilityNames:
    """Verify RuntimeOverlay registers under the renamed capability names."""

    @staticmethod
    def _make_coordinator() -> MagicMock:
        coordinator = MagicMock()
        coordinator.config = {"agents": {}}
        capability_store: dict = {}

        def _register_capability(name: str, value: object) -> None:
            capability_store[name] = value

        def _get_capability(name: str) -> object:
            return capability_store.get(name)

        coordinator.register_capability = MagicMock(side_effect=_register_capability)
        coordinator.get_capability = MagicMock(side_effect=_get_capability)
        coordinator.hooks = MagicMock()
        coordinator.hooks.emit = AsyncMock()
        return coordinator

    @pytest.mark.asyncio
    async def test_skills_registered_under_new_cap_name(self) -> None:
        """apply() must register contributed skills under 'runtime_skill_overlay'."""
        from amplifier_foundation import RUNTIME_SKILL_OVERLAY_CAPABILITY, RuntimeOverlay

        coord = self._make_coordinator()
        overlay = RuntimeOverlay(
            coord,
            success_event="mode:transition_completed",
            failure_event="mode:activation_failed",
        )
        contributions = {"skills": ["@bundle:skills/foo.md"]}
        result = await overlay.apply("mode:test", contributions)

        assert result.success is True
        registered = coord.get_capability(RUNTIME_SKILL_OVERLAY_CAPABILITY)
        assert registered is not None, (
            f"RuntimeOverlay must register skills under "
            f"{RUNTIME_SKILL_OVERLAY_CAPABILITY!r}, but nothing was found there."
        )
        assert "@bundle:skills/foo.md" in registered

    @pytest.mark.asyncio
    async def test_context_registered_under_new_cap_name(self) -> None:
        """apply() must register contributed context under 'runtime_context_overlay'."""
        from amplifier_foundation import RUNTIME_CONTEXT_OVERLAY_CAPABILITY, RuntimeOverlay

        coord = self._make_coordinator()
        overlay = RuntimeOverlay(
            coord,
            success_event="mode:transition_completed",
            failure_event="mode:activation_failed",
        )
        contributions = {"context": ["@bundle:context/foo.md"]}
        result = await overlay.apply("mode:test", contributions)

        assert result.success is True
        registered = coord.get_capability(RUNTIME_CONTEXT_OVERLAY_CAPABILITY)
        assert registered is not None, (
            f"RuntimeOverlay must register context under "
            f"{RUNTIME_CONTEXT_OVERLAY_CAPABILITY!r}, but nothing was found there."
        )
        assert "@bundle:context/foo.md" in registered

    @pytest.mark.asyncio
    async def test_legacy_mode_overlay_skills_name_not_used(self) -> None:
        """RuntimeOverlay must NOT register skills under the old 'mode_overlay_skills' name."""
        from amplifier_foundation import RuntimeOverlay

        coord = self._make_coordinator()
        overlay = RuntimeOverlay(
            coord,
            success_event="mode:transition_completed",
            failure_event="mode:activation_failed",
        )
        contributions = {"skills": ["@bundle:skills/foo.md"]}
        await overlay.apply("mode:test", contributions)

        legacy_value = coord.get_capability("mode_overlay_skills")
        assert legacy_value is None, (
            "RuntimeOverlay must NOT register skills under the legacy name "
            f"'mode_overlay_skills' (found {legacy_value!r}). "
            "The rename to 'runtime_skill_overlay' is required."
        )

    @pytest.mark.asyncio
    async def test_legacy_mode_overlay_context_name_not_used(self) -> None:
        """RuntimeOverlay must NOT register context under the old 'mode_overlay_context' name."""
        from amplifier_foundation import RuntimeOverlay

        coord = self._make_coordinator()
        overlay = RuntimeOverlay(
            coord,
            success_event="mode:transition_completed",
            failure_event="mode:activation_failed",
        )
        contributions = {"context": ["@bundle:context/foo.md"]}
        await overlay.apply("mode:test", contributions)

        legacy_value = coord.get_capability("mode_overlay_context")
        assert legacy_value is None, (
            "RuntimeOverlay must NOT register context under the legacy name "
            f"'mode_overlay_context' (found {legacy_value!r}). "
            "The rename to 'runtime_context_overlay' is required."
        )
