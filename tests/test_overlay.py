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
    """Tests: runtime_context_overlay capability via apply / revoke."""

    @pytest.mark.asyncio
    async def test_context_apply_registers_capability(
        self, overlay: RuntimeOverlay, coordinator: MagicMock
    ) -> None:
        """apply() with context paths registers runtime_context_overlay capability."""
        from amplifier_foundation import RUNTIME_CONTEXT_OVERLAY_CAPABILITY

        contributions = {
            "context": ["@modes:context/schema.md", "@modes:context/anti-patterns.md"]
        }
        await overlay.apply("mode:demo", contributions)

        cap = coordinator.get_capability(RUNTIME_CONTEXT_OVERLAY_CAPABILITY)
        assert cap == ["@modes:context/schema.md", "@modes:context/anti-patterns.md"]

    @pytest.mark.asyncio
    async def test_context_revoke_clears_capability(
        self, overlay: RuntimeOverlay, coordinator: MagicMock
    ) -> None:
        """revoke() clears runtime_context_overlay capability."""
        from amplifier_foundation import RUNTIME_CONTEXT_OVERLAY_CAPABILITY

        contributions = {"context": ["@modes:context/schema.md"]}
        await overlay.apply("mode:demo", contributions)
        await overlay.revoke("mode:demo")

        cap = coordinator.get_capability(RUNTIME_CONTEXT_OVERLAY_CAPABILITY) or []
        assert cap == []

    @pytest.mark.asyncio
    async def test_context_revoke_keeps_paths_referenced_by_other_scope(
        self, overlay: RuntimeOverlay, coordinator: MagicMock
    ) -> None:
        """revoke() only removes paths whose refcount drops to zero.

        A shared path applied under two scopes survives when only one revokes.
        """
        from amplifier_foundation import RUNTIME_CONTEXT_OVERLAY_CAPABILITY

        shared_path = "@modes:context/shared.md"
        await overlay.apply("mode:m1", {"context": [shared_path]})
        await overlay.apply("mode:m2", {"context": [shared_path]})

        await overlay.revoke("mode:m1")

        cap = coordinator.get_capability(RUNTIME_CONTEXT_OVERLAY_CAPABILITY) or []
        assert shared_path in cap


# ---------------------------------------------------------------------------
# Skills capability tests
# ---------------------------------------------------------------------------


class TestSkillsCapability:
    """Tests: runtime_skill_overlay capability via apply / revoke."""

    @pytest.mark.asyncio
    async def test_skills_apply_registers_capability(
        self, overlay: RuntimeOverlay, coordinator: MagicMock
    ) -> None:
        """apply() with skills paths registers runtime_skill_overlay capability."""
        from amplifier_foundation import RUNTIME_SKILL_OVERLAY_CAPABILITY

        contributions = {"skills": ["@modes:skills/mode-design-discipline"]}
        await overlay.apply("mode:demo", contributions)

        cap = coordinator.get_capability(RUNTIME_SKILL_OVERLAY_CAPABILITY)
        assert cap == ["@modes:skills/mode-design-discipline"]

    @pytest.mark.asyncio
    async def test_skills_revoke_clears_capability(
        self, overlay: RuntimeOverlay, coordinator: MagicMock
    ) -> None:
        """revoke() clears runtime_skill_overlay capability."""
        from amplifier_foundation import RUNTIME_SKILL_OVERLAY_CAPABILITY

        contributions = {"skills": ["@modes:skills/mode-design-discipline"]}
        await overlay.apply("mode:demo", contributions)
        await overlay.revoke("mode:demo")

        cap = coordinator.get_capability(RUNTIME_SKILL_OVERLAY_CAPABILITY) or []
        assert cap == []

    @pytest.mark.asyncio
    async def test_mixed_categories_in_one_apply(
        self, overlay: RuntimeOverlay, coordinator: MagicMock
    ) -> None:
        """apply() with agents+context+skills mounts all three atomically; revoke unwinds all."""
        from amplifier_foundation import (
            RUNTIME_CONTEXT_OVERLAY_CAPABILITY,
            RUNTIME_SKILL_OVERLAY_CAPABILITY,
        )

        contributions = {
            "agents": {"mode-author": {"description": "x"}},
            "context": ["@modes:context/schema.md"],
            "skills": ["@modes:skills/mode-design-discipline"],
        }
        await overlay.apply("mode:demo", contributions)

        # All three categories mounted
        assert "mode-author" in coordinator.config["agents"]
        assert coordinator.get_capability(RUNTIME_CONTEXT_OVERLAY_CAPABILITY) == [
            "@modes:context/schema.md"
        ]
        assert coordinator.get_capability(RUNTIME_SKILL_OVERLAY_CAPABILITY) == [
            "@modes:skills/mode-design-discipline"
        ]

        # Revoke unwinds all three atomically
        await overlay.revoke("mode:demo")

        assert "mode-author" not in coordinator.config["agents"]
        assert (coordinator.get_capability(RUNTIME_CONTEXT_OVERLAY_CAPABILITY) or []) == []
        assert (coordinator.get_capability(RUNTIME_SKILL_OVERLAY_CAPABILITY) or []) == []


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
        from amplifier_foundation import RUNTIME_CONTEXT_OVERLAY_CAPABILITY

        cap = coordinator.get_capability(RUNTIME_CONTEXT_OVERLAY_CAPABILITY) or []
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


# ---------------------------------------------------------------------------
# Event emission contract tests
# ---------------------------------------------------------------------------


class TestEventEmission:
    """Tests: event-emission contract for apply() and revoke()."""

    @pytest.mark.asyncio
    async def test_apply_emits_success_event_with_payload(
        self, overlay: RuntimeOverlay, coordinator: MagicMock
    ) -> None:
        """apply() emits success_event with scope, success=True, and mounted delta."""
        await overlay.apply("mode:demo", {"agents": {"a1": {"description": "x"}}})

        success_calls = [
            c
            for c in coordinator.hooks.emit.call_args_list
            if c.args[0] == "mode:transition_completed"
        ]
        assert len(success_calls) == 1
        payload = success_calls[0].args[1]
        assert payload["scope"] == "mode:demo"
        assert payload["success"] is True
        assert ("agents", "a1") in payload["mounted"]

    @pytest.mark.asyncio
    async def test_revoke_emits_success_event_with_unmounted(
        self, overlay: RuntimeOverlay, coordinator: MagicMock
    ) -> None:
        """revoke() emits success_event with scope and unmounted delta."""
        await overlay.apply("mode:demo", {"agents": {"a1": {"description": "x"}}})
        coordinator.hooks.emit.reset_mock()
        await overlay.revoke("mode:demo")

        success_calls = [
            c
            for c in coordinator.hooks.emit.call_args_list
            if c.args[0] == "mode:transition_completed"
        ]
        assert len(success_calls) == 1
        payload = success_calls[0].args[1]
        assert payload["scope"] == "mode:demo"
        assert ("agents", "a1") in payload["unmounted"]

    @pytest.mark.asyncio
    async def test_event_emit_failure_does_not_break_apply(self) -> None:
        """emit-side failures are swallowed (best-effort) and apply still succeeds."""
        coordinator = _make_coordinator()
        coordinator.hooks.emit.side_effect = RuntimeError("hook bus on fire")
        fresh_overlay = RuntimeOverlay(
            coordinator,
            success_event="mode:transition_completed",
            failure_event="mode:activation_failed",
        )
        result = await fresh_overlay.apply(
            "mode:demo", {"agents": {"a1": {"description": "x"}}}
        )
        assert result.success is True
        assert "a1" in coordinator.config["agents"]


# ---------------------------------------------------------------------------
# Package-level export tests
# ---------------------------------------------------------------------------


def test_runtime_overlay_exported_from_configurator_package() -> None:
    """RuntimeOverlay and TransitionResult are importable from the configurator package root."""
    from amplifier_foundation.configurator import RuntimeOverlay, TransitionResult

    assert RuntimeOverlay is not None
    assert TransitionResult is not None


# ---------------------------------------------------------------------------
# Item 1: revoke() failure event emission
# ---------------------------------------------------------------------------


class TestRevokeFailureEvent:
    """Tests: revoke() emits failure_event when unmount raises, not success_event."""

    @pytest.mark.asyncio
    async def test_revoke_emits_failure_event_on_unmount_error(self) -> None:
        """revoke() emits failure_event (not success_event) when _decrement raises.

        The bug: revoke() always emits self._success_event at the end, even
        after setting result.success = False due to an unmount exception.
        """
        coordinator = _make_coordinator()
        fresh_overlay = RuntimeOverlay(
            coordinator,
            success_event="mode:transition_completed",
            failure_event="mode:activation_failed",
        )

        # Apply a scope successfully
        await fresh_overlay.apply(
            "mode:demo",
            {"agents": {"mode-author": {"description": "x"}}},
        )
        coordinator.hooks.emit.reset_mock()

        # Monkey-patch _decrement to raise on the specific agent key
        original_decrement = fresh_overlay._decrement

        def failing_decrement(category: str, key: str) -> None:
            if category == "agents" and key == "mode-author":
                raise RuntimeError("simulated unmount failure")
            return original_decrement(category, key)

        fresh_overlay._decrement = failing_decrement  # type: ignore[method-assign]

        result = await fresh_overlay.revoke("mode:demo")

        assert result.success is False

        emitted_events = [
            call.args[0] for call in coordinator.hooks.emit.call_args_list
        ]
        assert "mode:activation_failed" in emitted_events, (
            f"failure_event not emitted; emitted: {emitted_events}"
        )
        assert "mode:transition_completed" not in emitted_events, (
            f"success_event was incorrectly emitted on failure; emitted: {emitted_events}"
        )


# ---------------------------------------------------------------------------
# Item 4: debug methods — get_refcount and dump_state
# ---------------------------------------------------------------------------


class TestDebugMethods:
    """Tests: get_refcount() and dump_state() introspection helpers."""

    @pytest.mark.asyncio
    async def test_get_refcount_returns_zero_for_unknown_key(
        self, overlay: RuntimeOverlay
    ) -> None:
        """get_refcount() returns 0 for a key that has never been applied (no raise)."""
        assert overlay.get_refcount("agents", "nonexistent") == 0

    @pytest.mark.asyncio
    async def test_get_refcount_reflects_applied_scope(
        self, overlay: RuntimeOverlay
    ) -> None:
        """get_refcount() returns 1 after a scope contributes an agent."""
        await overlay.apply(
            "mode:demo", {"agents": {"mode-author": {"description": "x"}}}
        )
        assert overlay.get_refcount("agents", "mode-author") == 1

    @pytest.mark.asyncio
    async def test_get_refcount_increments_with_two_scopes(
        self, overlay: RuntimeOverlay
    ) -> None:
        """get_refcount() returns 2 when two scopes contribute the same item."""
        await overlay.apply(
            "mode:demo1", {"agents": {"shared-agent": {"description": "x"}}}
        )
        await overlay.apply(
            "mode:demo2", {"agents": {"shared-agent": {"description": "y"}}}
        )
        assert overlay.get_refcount("agents", "shared-agent") == 2

    @pytest.mark.asyncio
    async def test_get_refcount_drops_to_zero_after_revoke(
        self, overlay: RuntimeOverlay
    ) -> None:
        """get_refcount() returns 0 after the only referencing scope is revoked."""
        await overlay.apply(
            "mode:demo", {"agents": {"mode-author": {"description": "x"}}}
        )
        await overlay.revoke("mode:demo")
        assert overlay.get_refcount("agents", "mode-author") == 0

    @pytest.mark.asyncio
    async def test_dump_state_returns_expected_structure(
        self, overlay: RuntimeOverlay
    ) -> None:
        """dump_state() returns scope_claims, refcounts, and owned keys."""
        await overlay.apply(
            "mode:demo",
            {
                "agents": {"mode-author": {"description": "x"}},
                "context": ["@modes:context/schema.md"],
            },
        )
        state = overlay.dump_state()

        assert "scope_claims" in state
        assert "refcounts" in state
        assert "owned" in state

        # scope_claims: mode:demo must be present
        assert "mode:demo" in state["scope_claims"]

        # refcounts: (agents, mode-author) must have refcount >= 1
        assert any("mode-author" in str(k) for k in state["refcounts"])

        # owned: agents key must list mode-author
        assert "mode-author" in state["owned"].get("agents", [])


# ---------------------------------------------------------------------------
# Item 5: _owned / _refcounts invariant
# ---------------------------------------------------------------------------


def _assert_owned_refcount_invariant(overlay: RuntimeOverlay, label: str = "") -> None:
    """Assert that _owned and _refcounts are in sync.

    Invariant (for overlays with no session-baseline agents):
      For every category C in overlay._owned:
          set(_owned[C].keys())
          == { k  for (C2, k), rc in _refcounts.items()  if C2 == C and rc > 0 }

    In other words: every key in _owned[C] has a positive refcount, and every
    key with a positive refcount in category C is present in _owned[C].

    Note: this invariant assumes the overlay was created with no initial
    session-baseline agents.  Baseline agents appear in _refcounts (rc=1) but
    NOT in _owned (they were never mounted by the overlay).  The test below
    uses an overlay with an empty agent baseline to avoid that asymmetry.
    """
    for category, owned_dict in overlay._owned.items():
        owned_keys = set(owned_dict.keys())
        positive_rc_keys = {
            k
            for (cat, k), rc in overlay._refcounts.items()
            if cat == category and rc > 0
        }
        tag = f" [{label}]" if label else ""
        assert owned_keys == positive_rc_keys, (
            f"Invariant violated{tag} for category={category!r}: "
            f"_owned keys={owned_keys} != positive-refcount keys={positive_rc_keys}"
        )


class TestOwnedRefcountInvariant:
    """Invariant: _owned and _refcounts stay in sync across apply/revoke sequences."""

    @pytest.mark.asyncio
    async def test_invariant_holds_across_apply_revoke_sequence(self) -> None:
        """After each step in a multi-scope sequence the _owned/_refcount invariant holds.

        Sequence:
          0. initial state (nothing applied)
          1. apply scope-A: agent-x + context-path-1
          2. apply scope-B: agent-x (overlap) + context-path-2
          3. revoke scope-A: agent-x rc 2→1 (stays owned), path-1 rc 1→0 (unmounted)
          4. revoke scope-B: agent-x rc 1→0 (unmounted), path-2 rc 1→0 (unmounted)

        The overlay is created with NO initial agents so the baseline is empty
        and the clean invariant (owned == positive-refcount-keys) holds at all
        steps for all categories.
        """
        # Start with an empty-baseline overlay
        coordinator = _make_coordinator(initial_agents=None)
        ov = RuntimeOverlay(
            coordinator,
            success_event="mode:transition_completed",
            failure_event="mode:activation_failed",
        )

        _assert_owned_refcount_invariant(ov, "initial")

        # Step 1: apply scope-A
        await ov.apply(
            "scope-A",
            {
                "agents": {"agent-x": {"description": "x"}},
                "context": ["@modes:context/path-1.md"],
            },
        )
        _assert_owned_refcount_invariant(ov, "after apply scope-A")

        # Step 2: apply scope-B (agent-x overlaps; path-2 is new)
        await ov.apply(
            "scope-B",
            {
                "agents": {"agent-x": {"description": "y"}},
                "context": ["@modes:context/path-2.md"],
            },
        )
        _assert_owned_refcount_invariant(ov, "after apply scope-B")

        # Step 3: revoke scope-A (agent-x rc 2→1: stays; path-1 rc 1→0: unmounted)
        await ov.revoke("scope-A")
        _assert_owned_refcount_invariant(ov, "after revoke scope-A")

        # Spot-check: agent-x still owned (rc=1), path-1 gone, path-2 still owned
        assert ov.get_refcount("agents", "agent-x") == 1
        assert ov.get_refcount("context", "@modes:context/path-1.md") == 0
        assert ov.get_refcount("context", "@modes:context/path-2.md") == 1

        # Step 4: revoke scope-B (agent-x rc 1→0: unmounted; path-2 rc 1→0: unmounted)
        await ov.revoke("scope-B")
        _assert_owned_refcount_invariant(ov, "after revoke scope-B")

        # Everything fully unmounted
        assert ov.get_refcount("agents", "agent-x") == 0
        assert ov.get_refcount("context", "@modes:context/path-2.md") == 0
