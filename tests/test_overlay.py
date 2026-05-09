"""Tests for RuntimeOverlay - Phase 1 (S1 session baseline overlap + S2 mode-only).

S1 (overlap with session baseline) and S2 (mode-only) for agent/context/skill categories.
S3/S4 deferred to Phase 3.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_foundation.configurator._overlay import RuntimeOverlay, TransitionResult


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_coordinator(initial_agents=None, initial_capabilities=None):
    """Build a MagicMock coordinator following test_configurator.py pattern.

    Returns a MagicMock with:
        .config = {'agents': dict(initial_agents or {})}
        .register_capability / .get_capability backed by a capability_store dict
        .hooks = MagicMock() with .hooks.emit = AsyncMock()
    """
    coordinator = MagicMock()
    coordinator.config = {"agents": dict(initial_agents or {})}

    capability_store: dict = dict(initial_capabilities or {})

    def _register_capability(name, value):
        capability_store[name] = value

    def _get_capability(name):
        return capability_store.get(name)

    coordinator.register_capability = MagicMock(side_effect=_register_capability)
    coordinator.get_capability = MagicMock(side_effect=_get_capability)
    coordinator.hooks = MagicMock()
    coordinator.hooks.emit = AsyncMock()

    return coordinator


@pytest.fixture
def coordinator():
    """Bare coordinator with no initial agents."""
    return _make_coordinator()


@pytest.fixture
def overlay(coordinator):
    """RuntimeOverlay bound to coordinator with canonical mode event names."""
    return RuntimeOverlay(
        coordinator,
        success_event="mode:transition_completed",
        failure_event="mode:activation_failed",
    )


# ---------------------------------------------------------------------------
# S2 tests: mode-only agent contributions (no session baseline overlap)
# ---------------------------------------------------------------------------


class TestS2AgentPath:
    """S2 tests: mode-only agent contributions (no session baseline overlap)."""

    @pytest.mark.asyncio
    async def test_s2_agent_apply_mounts_into_coordinator_config(
        self, overlay: RuntimeOverlay, coordinator: MagicMock
    ) -> None:
        """apply() for agents category mounts agent into coordinator.config['agents']."""
        contributions = {
            "agents": {"mode-author": {"description": "A mode-author agent"}}
        }
        result: TransitionResult = await overlay.apply("mode:demo", contributions)

        assert result.success is True
        assert "mode-author" in coordinator.config["agents"]
        assert (
            coordinator.config["agents"]["mode-author"]["description"]
            == "A mode-author agent"
        )

    @pytest.mark.asyncio
    async def test_s2_agent_revoke_unmounts(
        self, overlay: RuntimeOverlay, coordinator: MagicMock
    ) -> None:
        """revoke() removes previously applied agent from coordinator.config['agents']."""
        contributions = {
            "agents": {"mode-author": {"description": "A mode-author agent"}}
        }

        await overlay.apply("mode:demo", contributions)
        assert "mode-author" in coordinator.config["agents"]

        result = await overlay.revoke("mode:demo")

        assert result.success is True
        assert "mode-author" not in coordinator.config["agents"]

    @pytest.mark.asyncio
    async def test_revoke_unknown_scope_is_noop(self, overlay: RuntimeOverlay) -> None:
        """revoke() on a scope that was never applied returns success with empty unmounted list."""
        result = await overlay.revoke("mode:never-applied")

        assert result.success is True
        assert result.unmounted == []


# ---------------------------------------------------------------------------
# S1 tests: agent declared in both session baseline and mode contribution
# ---------------------------------------------------------------------------


class TestS1AgentPath:
    """S1 tests: agent overlap with session baseline.

    S1 invariant: when the same agent name is declared in the session baseline
    AND contributed by a mode, the refcount goes 1→2 on apply (no remount, no
    config replacement) and 2→1 on revoke (no unmount, agent stays).  §7.
    """

    @pytest.mark.asyncio
    async def test_s1_agent_in_session_and_mode_no_unmount_on_revoke(self) -> None:
        """S1: mode apply does not replace session-level config; revoke does not unmount.

        Trace:
          _capture_baseline → refcounts[('agents','mode-author')] = 1
          apply → _increment → before=1, rc=2, _mount NOT called (before != 0)
          revoke → _decrement → before=2, rc=1, _unmount NOT called (new_rc != 0)
        """
        coordinator = _make_coordinator(
            initial_agents={"mode-author": {"description": "session-level"}}
        )
        overlay = RuntimeOverlay(
            coordinator,
            success_event="mode:transition_completed",
            failure_event="mode:activation_failed",
        )

        # Apply mode with same agent name but a different (mode-level) description
        result = await overlay.apply(
            "mode:demo",
            {"agents": {"mode-author": {"description": "mode-level"}}},
        )
        assert result.success is True

        # S1 invariant: session-level config must NOT be replaced by mode contribution
        assert (
            coordinator.config["agents"]["mode-author"]["description"]
            == "session-level"
        )

        # Revoke: refcount went 2→1 — agent must still be present with session-level desc
        await overlay.revoke("mode:demo")
        assert "mode-author" in coordinator.config["agents"]
        assert (
            coordinator.config["agents"]["mode-author"]["description"]
            == "session-level"
        )

    @pytest.mark.asyncio
    async def test_s1_baseline_agent_not_added_by_mode_apply_alone(self) -> None:
        """S1: baseline agent persists after apply+revoke of mode with empty agents.

        A mode that contributes no agents must not disturb the session baseline.
        """
        coordinator = _make_coordinator(
            initial_agents={"baseline-agent": {"description": "baseline"}}
        )
        overlay = RuntimeOverlay(
            coordinator,
            success_event="mode:transition_completed",
            failure_event="mode:activation_failed",
        )

        # Apply a mode that contributes nothing to agents
        await overlay.apply("mode:demo", {"agents": {}})
        # Revoke that mode
        await overlay.revoke("mode:demo")

        # Baseline agent must still be present
        assert "baseline-agent" in coordinator.config["agents"]


# ---------------------------------------------------------------------------
# Context capability tests
# ---------------------------------------------------------------------------


class TestContextCapability:
    """Tests: mode_overlay_context capability via apply / revoke."""

    @pytest.mark.asyncio
    async def test_context_apply_registers_capability(
        self, overlay: RuntimeOverlay, coordinator: MagicMock
    ) -> None:
        """apply() with context paths registers mode_overlay_context capability."""
        contributions = {
            "context": ["@modes:context/schema.md", "@modes:context/anti-patterns.md"]
        }
        await overlay.apply("mode:demo", contributions)

        cap = coordinator.get_capability("mode_overlay_context")
        assert cap == ["@modes:context/schema.md", "@modes:context/anti-patterns.md"]

    @pytest.mark.asyncio
    async def test_context_revoke_clears_capability(
        self, overlay: RuntimeOverlay, coordinator: MagicMock
    ) -> None:
        """revoke() clears mode_overlay_context capability."""
        contributions = {"context": ["@modes:context/schema.md"]}
        await overlay.apply("mode:demo", contributions)
        await overlay.revoke("mode:demo")

        cap = coordinator.get_capability("mode_overlay_context") or []
        assert cap == []

    @pytest.mark.asyncio
    async def test_context_revoke_keeps_paths_referenced_by_other_scope(
        self, overlay: RuntimeOverlay, coordinator: MagicMock
    ) -> None:
        """revoke() only removes paths whose refcount drops to zero.

        A shared path applied under two scopes survives when only one revokes.
        """
        shared_path = "@modes:context/shared.md"
        await overlay.apply("mode:m1", {"context": [shared_path]})
        await overlay.apply("mode:m2", {"context": [shared_path]})

        await overlay.revoke("mode:m1")

        cap = coordinator.get_capability("mode_overlay_context") or []
        assert shared_path in cap


# ---------------------------------------------------------------------------
# Skills capability tests
# ---------------------------------------------------------------------------


class TestSkillsCapability:
    """Tests: mode_overlay_skills capability via apply / revoke."""

    @pytest.mark.asyncio
    async def test_skills_apply_registers_capability(
        self, overlay: RuntimeOverlay, coordinator: MagicMock
    ) -> None:
        """apply() with skills paths registers mode_overlay_skills capability."""
        contributions = {"skills": ["@modes:skills/mode-design-discipline"]}
        await overlay.apply("mode:demo", contributions)

        cap = coordinator.get_capability("mode_overlay_skills")
        assert cap == ["@modes:skills/mode-design-discipline"]

    @pytest.mark.asyncio
    async def test_skills_revoke_clears_capability(
        self, overlay: RuntimeOverlay, coordinator: MagicMock
    ) -> None:
        """revoke() clears mode_overlay_skills capability."""
        contributions = {"skills": ["@modes:skills/mode-design-discipline"]}
        await overlay.apply("mode:demo", contributions)
        await overlay.revoke("mode:demo")

        cap = coordinator.get_capability("mode_overlay_skills") or []
        assert cap == []

    @pytest.mark.asyncio
    async def test_mixed_categories_in_one_apply(
        self, overlay: RuntimeOverlay, coordinator: MagicMock
    ) -> None:
        """apply() with agents+context+skills mounts all three atomically; revoke unwinds all."""
        contributions = {
            "agents": {"mode-author": {"description": "x"}},
            "context": ["@modes:context/schema.md"],
            "skills": ["@modes:skills/mode-design-discipline"],
        }
        await overlay.apply("mode:demo", contributions)

        # All three categories mounted
        assert "mode-author" in coordinator.config["agents"]
        assert coordinator.get_capability("mode_overlay_context") == [
            "@modes:context/schema.md"
        ]
        assert coordinator.get_capability("mode_overlay_skills") == [
            "@modes:skills/mode-design-discipline"
        ]

        # Revoke unwinds all three atomically
        await overlay.revoke("mode:demo")

        assert "mode-author" not in coordinator.config["agents"]
        assert (coordinator.get_capability("mode_overlay_context") or []) == []
        assert (coordinator.get_capability("mode_overlay_skills") or []) == []


# ---------------------------------------------------------------------------
# Rollback tests (§6 step 9, §12)
# ---------------------------------------------------------------------------


class TestRollback:
    """Atomic rollback: prior mounts unwound in reverse when apply() fails mid-way."""

    @pytest.mark.asyncio
    async def test_apply_rollback_on_invalid_agent_payload(
        self, overlay: RuntimeOverlay, coordinator: MagicMock
    ) -> None:
        """apply() failure mid-way rolls back prior mounts and leaves scope unapplied.

        Sequence:
          1. context contribution processed first (succeeds → mounted)
          2. agents contribution processed next → _normalise_agents raises ValueError
             for 'bad-agent': 'not-a-dict'
          3. rollback unwinds context mount in reverse → context capability drops to []
          4. scope NOT added to _scope_claims → retry with valid payload succeeds
        """
        bad_contributions = {
            "context": ["@modes:context/ok.md"],
            "agents": {"bad-agent": "not-a-dict"},
        }
        result = await overlay.apply("mode:bad", bad_contributions)

        # Result must indicate failure with a descriptive error
        assert result.success is False
        assert result.error is not None
        assert "bad-agent" in result.error or "dict" in result.error.lower()

        # Context capability must have been rolled back (it was mounted, then unwound)
        cap = coordinator.get_capability("mode_overlay_context") or []
        assert cap == []

        # bad-agent must not be in coordinator.config['agents']
        assert "bad-agent" not in coordinator.config["agents"]

        # Scope must NOT be marked applied — retry with valid payload must succeed
        result2 = await overlay.apply(
            "mode:bad", {"agents": {"good-agent": {"description": "x"}}}
        )
        assert result2.success is True
        assert "good-agent" in coordinator.config["agents"]

    @pytest.mark.asyncio
    async def test_apply_rollback_emits_failure_event(self) -> None:
        """apply() failure emits the failure event and does NOT emit the success event."""
        coordinator = _make_coordinator()
        fresh_overlay = RuntimeOverlay(
            coordinator,
            success_event="mode:transition_completed",
            failure_event="mode:activation_failed",
        )

        await fresh_overlay.apply("mode:bad", {"agents": {"x": "not-a-dict"}})

        emitted_events = [
            call.args[0] for call in coordinator.hooks.emit.call_args_list
        ]
        assert "mode:activation_failed" in emitted_events
        assert "mode:transition_completed" not in emitted_events
