"""Tests for the hooks-delegation-batching module.

Covers the 10-case wave-detection table from the Stage 2 ("Option C") spec,
plus an additional flag-off no-op case exercised through the public mount()
entry point (config default OFF -> the module ships dark unless explicitly
composed and enabled).

Each spec case constructs the hook directly and feeds synthetic `tool:post`
payloads, per the spec's test plan (section 8.1).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from amplifier_module_hooks_delegation_batching import (
    DelegationBatchingConfig,
    DelegationBatchingHooks,
    DelegationBatchingState,
    mount,
)


def _delegate_event(group_id) -> dict:
    """Build a synthetic `tool:post` payload for a `delegate` tool call."""
    return {
        "tool_name": "delegate",
        "tool_call_id": f"call-{group_id}",
        "tool_input": {"agent": "foundation:explorer", "instruction": "..."},
        "result": {"success": True},
        "parallel_group_id": group_id,
    }


def _non_delegate_event(tool_name: str = "read_file") -> dict:
    """Build a synthetic `tool:post` payload for a non-delegate tool call."""
    return {
        "tool_name": tool_name,
        "tool_call_id": "call-other",
        "tool_input": {"file_path": "/tmp/foo.py"},
        "result": {"content": "..."},
        "parallel_group_id": None,
    }


def _make_hooks(**config_overrides) -> DelegationBatchingHooks:
    """Helper mirroring the repo's `_make_hooks` convention (see
    hooks-process-guard/tests/test_process_guard.py)."""
    config = DelegationBatchingConfig(**config_overrides)
    return DelegationBatchingHooks(config)


# --- Case 1 ---


class TestCase1SingleNarrowWave:
    """Single wave, 1 member -> no nudge (wave_count == 1 < min_wave_index)."""

    @pytest.mark.asyncio
    async def test_no_nudge_on_first_wave(self):
        hooks = _make_hooks()

        result = await hooks.handle_tool_post("tool:post", _delegate_event("g1"))

        assert result.action == "continue"
        assert hooks.state.wave_count == 1
        assert hooks.state.nudges_issued == 0


# --- Case 2 ---


class TestCase2TwoConsecutiveNarrowWaves:
    """Two consecutive 1-member waves -> exactly one nudge, on the second."""

    @pytest.mark.asyncio
    async def test_nudge_on_second_wave_only(self):
        hooks = _make_hooks()

        first = await hooks.handle_tool_post("tool:post", _delegate_event("g1"))
        second = await hooks.handle_tool_post("tool:post", _delegate_event("g2"))

        assert first.action == "continue"
        assert second.action == "inject_context"
        assert hooks.state.nudges_issued == 1
        assert hooks.state.nudged_groups == {"g2"}


# --- Case 3 ---


class TestCase3WideWavesDedupe:
    """Wave of 3 members, then another wave of 3 -> at most one nudge total;
    never one per member -- asserts `nudged_groups` dedupe."""

    @pytest.mark.asyncio
    async def test_never_more_than_one_nudge_per_wide_wave(self):
        hooks = _make_hooks()

        wave1_results = [
            await hooks.handle_tool_post("tool:post", _delegate_event("g1"))
            for _ in range(3)
        ]
        wave2_results = [
            await hooks.handle_tool_post("tool:post", _delegate_event("g2"))
            for _ in range(3)
        ]

        all_results = wave1_results + wave2_results
        nudge_count = sum(1 for r in all_results if r.action == "inject_context")

        assert nudge_count == 1
        assert hooks.state.nudges_issued == 1


# --- Case 4 ---


class TestCase4InterruptionResetsChain:
    """Wave, then a read_file tool:post, then a wave -> no nudge -- `interrupted`
    resets the chain."""

    @pytest.mark.asyncio
    async def test_intervening_non_delegate_work_resets_wave_chain(self):
        hooks = _make_hooks()

        wave1 = await hooks.handle_tool_post("tool:post", _delegate_event("g1"))
        other = await hooks.handle_tool_post(
            "tool:post", _non_delegate_event("read_file")
        )
        wave2 = await hooks.handle_tool_post("tool:post", _delegate_event("g2"))

        assert wave1.action == "continue"
        assert other.action == "continue"
        assert wave2.action == "continue"
        # The chain reset: wave2 starts a fresh chain at count 1, not 2.
        assert hooks.state.wave_count == 1
        assert hooks.state.interrupted is False
        assert hooks.state.nudges_issued == 0


# --- Case 5 ---


class TestCase5MaxNudgesCap:
    """Six consecutive 1-member waves -> exactly `max_nudges` (3) nudges."""

    @pytest.mark.asyncio
    async def test_nudges_capped_at_max_nudges(self):
        hooks = _make_hooks()

        results = [
            await hooks.handle_tool_post("tool:post", _delegate_event(f"g{i}"))
            for i in range(1, 7)
        ]

        nudge_count = sum(1 for r in results if r.action == "inject_context")

        assert nudge_count == 3
        assert hooks.state.nudges_issued == 3


# --- Case 6 ---


class TestCase6DisabledNeverNudges:
    """`enabled: false` -> never nudges, under any sequence."""

    @pytest.mark.asyncio
    async def test_disabled_config_never_nudges(self):
        hooks = _make_hooks(enabled=False)

        results = [
            await hooks.handle_tool_post("tool:post", _delegate_event(f"g{i}"))
            for i in range(1, 7)
        ]

        assert all(r.action == "continue" for r in results)
        assert hooks.state.nudges_issued == 0
        # Disabled short-circuits before any state mutation whatsoever.
        assert hooks.state.wave_count == 0
        assert hooks.state.seen_groups == set()


# --- Case 7 ---


class TestCase7MissingGroupId:
    """`tool:post` with `parallel_group_id` absent/None -> returns `continue`,
    no crash, no state mutation."""

    @pytest.mark.asyncio
    async def test_absent_group_id_is_a_safe_noop(self):
        hooks = _make_hooks()
        event = _delegate_event("unused")
        del event["parallel_group_id"]

        result = await hooks.handle_tool_post("tool:post", event)

        assert result.action == "continue"
        assert hooks.state.wave_count == 0
        assert hooks.state.seen_groups == set()
        assert hooks.state.group_sizes == {}

    @pytest.mark.asyncio
    async def test_none_group_id_is_a_safe_noop(self):
        hooks = _make_hooks()
        event = _delegate_event("unused")
        event["parallel_group_id"] = None

        result = await hooks.handle_tool_post("tool:post", event)

        assert result.action == "continue"
        assert hooks.state.wave_count == 0
        assert hooks.state.seen_groups == set()


# --- Case 8 ---


class TestCase8HandlerNeverPropagates:
    """Handler raises internally (monkeypatch a member to raise) -> returns
    `HookResult(action="continue")` -- never propagates."""

    @pytest.mark.asyncio
    async def test_internal_exception_is_swallowed(self, monkeypatch):
        hooks = _make_hooks()

        async def _boom(_data):
            raise RuntimeError("simulated internal failure")

        monkeypatch.setattr(hooks, "_process", _boom)

        result = await hooks.handle_tool_post("tool:post", _delegate_event("g1"))

        assert result.action == "continue"


# --- Case 9 ---


class TestCase9InjectionShape:
    """Injection shape: `action == "inject_context"`, `ephemeral is True`,
    `append_to_last_tool_result is True`, `context_injection_role == "user"`.
    """

    @pytest.mark.asyncio
    async def test_nudge_result_shape(self):
        hooks = _make_hooks()
        await hooks.handle_tool_post("tool:post", _delegate_event("g1"))

        result = await hooks.handle_tool_post("tool:post", _delegate_event("g2"))

        assert result.action == "inject_context"
        assert result.ephemeral is True
        assert result.append_to_last_tool_result is True
        assert result.context_injection_role == "user"
        assert result.context_injection is not None
        assert "wave 2" in result.context_injection.lower()


# --- Case 10 ---


class TestCase10NarrowWaveOnlySuppression:
    """`narrow_wave_only=True`, wave of 3 arriving before any nudge ->
    suppressed once `group_sizes >= 2`."""

    @pytest.mark.asyncio
    async def test_wide_wave_suppressed_after_second_member(self):
        # min_wave_index=1 so the very first wave is already nudge-eligible,
        # isolating the narrow_wave_only guard as the case under test.
        hooks = _make_hooks(min_wave_index=1, narrow_wave_only=True)

        first = await hooks.handle_tool_post("tool:post", _delegate_event("g1"))
        second = await hooks.handle_tool_post("tool:post", _delegate_event("g1"))
        third = await hooks.handle_tool_post("tool:post", _delegate_event("g1"))

        # Only the first (narrow, size 1) member nudges.
        assert first.action == "inject_context"
        assert second.action == "continue"
        assert third.action == "continue"
        assert hooks.state.nudges_issued == 1
        assert hooks.state.group_sizes["g1"] == 3


# --- Additional: flag-off no-op via mount() (config flag, default OFF) ---


class TestFlagOffNoOpViaMount:
    """The module ships dark by default: it is never composed into
    `behaviors/agents.yaml` (see `behaviors/delegation-batching.yaml`'s
    header comment), and explicitly setting `enabled: false` in composed
    config -- the operational off-switch -- produces a true no-op end to
    end through the real `mount()` wiring, not just the bare dataclass
    (see Case 6 above for the direct-construction equivalent).
    """

    @pytest.mark.asyncio
    async def test_mount_with_enabled_false_is_a_full_noop(self):
        coordinator = MagicMock()
        coordinator.hooks = MagicMock()
        coordinator.hooks.register = MagicMock()
        coordinator.hooks.emit = AsyncMock()
        coordinator.register_contributor = MagicMock()

        result = await mount(coordinator, {"enabled": False})

        assert result["config"]["enabled"] is False

        # Pull out the registered handler and drive it directly, the way
        # the real orchestrator would via tool:post.
        _, handler_fn = coordinator.hooks.register.call_args[0][:2]

        results = [
            await handler_fn("tool:post", _delegate_event(f"g{i}")) for i in range(1, 7)
        ]

        assert all(r.action == "continue" for r in results)
        coordinator.hooks.emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_mount_registers_contributor_and_hook(self):
        coordinator = MagicMock()
        coordinator.hooks = MagicMock()
        coordinator.hooks.register = MagicMock()
        coordinator.register_contributor = MagicMock()

        result = await mount(coordinator, {})

        coordinator.register_contributor.assert_called_once()
        contrib_args = coordinator.register_contributor.call_args[0]
        assert contrib_args[0] == "observability.events"
        assert contrib_args[1] == "hooks-delegation-batching"
        assert contrib_args[2]() == ["delegate:batching_nudge"]

        coordinator.hooks.register.assert_called_once()
        register_args, register_kwargs = coordinator.hooks.register.call_args
        assert register_args[0] == "tool:post"
        assert register_kwargs["priority"] == 50
        assert register_kwargs["name"] == "hooks-delegation-batching"

        assert result["name"] == "hooks-delegation-batching"
        assert result["config"]["enabled"] is True
        assert result["config"]["min_wave_index"] == 2
        assert result["config"]["max_nudges"] == 3
        assert result["config"]["narrow_wave_only"] is True


# --- Config defaults sanity (mirrors hooks-process-guard's TestXConfig pattern) ---


class TestDelegationBatchingConfigDefaults:
    def test_defaults_match_spec(self):
        config = DelegationBatchingConfig()

        assert config.enabled is True
        assert config.min_wave_index == 2
        assert config.max_nudges == 3
        assert config.narrow_wave_only is True


class TestDelegationBatchingStateDefaults:
    def test_defaults(self):
        state = DelegationBatchingState()

        assert state.wave_count == 0
        assert state.nudges_issued == 0
        assert state.seen_groups == set()
        assert state.group_sizes == {}
        assert state.nudged_groups == set()
        assert state.last_group_id is None
        assert state.interrupted is False


class TestTelemetryEmission:
    """`delegate:batching_nudge` fires with the spec's payload shape when a
    hooks emitter is wired, and never fires when one isn't (unit-test mode).
    """

    @pytest.mark.asyncio
    async def test_emits_expected_payload_when_wired(self):
        emitter = MagicMock()
        emitter.emit = AsyncMock()
        config = DelegationBatchingConfig()
        hooks = DelegationBatchingHooks(config, hooks=emitter)

        await hooks.handle_tool_post("tool:post", _delegate_event("g1"))
        await hooks.handle_tool_post("tool:post", _delegate_event("g2"))

        emitter.emit.assert_awaited_once()
        event_name, payload = emitter.emit.call_args[0]
        assert event_name == "delegate:batching_nudge"
        assert payload == {
            "wave_count": 2,
            "parallel_group_id": "g2",
            "wave_size": 1,
            "nudges_issued": 1,
            "metadata": None,
        }

    @pytest.mark.asyncio
    async def test_no_emitter_wired_does_not_crash(self):
        hooks = _make_hooks()

        first = await hooks.handle_tool_post("tool:post", _delegate_event("g1"))
        second = await hooks.handle_tool_post("tool:post", _delegate_event("g2"))

        assert first.action == "continue"
        assert second.action == "inject_context"
