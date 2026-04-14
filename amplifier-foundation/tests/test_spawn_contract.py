"""Tests for spawn contract reconciliation (Fixes 4 & 5).

Fix 4: Example spawn_capability should accept all kwargs tool-delegate sends.
Fix 5: PreparedBundle.spawn() should accept self_delegation_depth and register
       it as a coordinator capability on the child session.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from amplifier_core.hooks import HookRegistry

from amplifier_foundation.bundle import Bundle, PreparedBundle

# Resolve the example file relative to this test file
_EXAMPLE_07 = (
    Path(__file__).resolve().parent.parent / "examples" / "07_full_workflow.py"
)


# =============================================================================
# Helpers (same pattern as test_spawn_enrichment.py)
# =============================================================================


def _make_child_bundle() -> Bundle:
    """Create a minimal child bundle for testing."""
    return Bundle(name="test-child", version="0.0.1")


def _make_prepared_bundle() -> PreparedBundle:
    """Create a PreparedBundle with minimal mocks."""
    parent_bundle = Bundle(name="test-parent", version="0.0.1")
    resolver = MagicMock()
    return PreparedBundle(mount_plan={}, bundle=parent_bundle, resolver=resolver)


def _make_mock_coordinator(hooks: HookRegistry) -> MagicMock:
    """Create a mock coordinator with a real HookRegistry."""
    coordinator = MagicMock()
    coordinator.hooks = hooks
    coordinator.mount = AsyncMock()
    coordinator.register_capability = MagicMock()
    coordinator.get = MagicMock(return_value=None)
    # get_capability returns None by default (no capabilities registered yet)
    coordinator.get_capability = MagicMock(return_value=None)
    return coordinator


def _make_mock_session(
    hooks: HookRegistry,
    session_id: str = "test-child-123",
) -> MagicMock:
    """Create a mock AmplifierSession with a real HookRegistry."""
    session = MagicMock()
    session.session_id = session_id
    session.coordinator = _make_mock_coordinator(hooks)
    session.initialize = AsyncMock()
    session.cleanup = AsyncMock()
    session.execute = AsyncMock(return_value="Done")
    return session


# =============================================================================
# Fix 4: Example spawn_capability signature tests
# =============================================================================


def test_example_spawn_capability_accepts_all_delegate_kwargs():
    """The example spawn_capability should accept all kwargs tool-delegate sends."""
    # Import the example module to inspect the function
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "example_07",
        str(_EXAMPLE_07),
    )
    mod = importlib.util.module_from_spec(spec)

    # The example imports amplifier_foundation, which should be available
    spec.loader.exec_module(mod)

    # Get the spawn_capability from register_spawn_capability's closure
    # Instead, check the inner function's signature by inspecting the source
    source = inspect.getsource(mod.register_spawn_capability)

    # These are the kwargs tool-delegate sends that were previously missing:
    assert "provider_preferences" in source, (
        "spawn_capability should accept provider_preferences"
    )
    assert "**kwargs" in source, (
        "spawn_capability should accept **kwargs for forward-compatibility"
    )


def test_example_spawn_capability_has_kwargs_catchall():
    """The example should have **kwargs to catch future additions."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "example_07",
        str(_EXAMPLE_07),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    source = inspect.getsource(mod.register_spawn_capability)

    # The function should accept these tool-delegate kwargs without crashing
    for param in ["tool_inheritance", "hook_inheritance", "self_delegation_depth"]:
        assert param in source, f"spawn_capability should accept or document {param}"


# =============================================================================
# Fix 5: PreparedBundle.spawn() self_delegation_depth tests
# =============================================================================


class TestSpawnSelfDelegationDepth:
    """Tests for self_delegation_depth parameter on PreparedBundle.spawn()."""

    def test_spawn_signature_accepts_self_delegation_depth(self):
        """spawn() should accept self_delegation_depth as a keyword argument."""
        sig = inspect.signature(PreparedBundle.spawn)
        assert "self_delegation_depth" in sig.parameters, (
            "PreparedBundle.spawn() should accept self_delegation_depth parameter"
        )

    @pytest.mark.asyncio
    async def test_spawn_registers_depth_as_coordinator_capability(self):
        """spawn() should register self_delegation_depth as a coordinator capability."""
        hooks = HookRegistry()
        child_bundle = _make_child_bundle()
        prepared = _make_prepared_bundle()

        mock_session = _make_mock_session(hooks)

        with patch(
            "amplifier_core.AmplifierSession",
            return_value=mock_session,
        ):
            await prepared.spawn(
                child_bundle,
                "Do something",
                compose=False,
                self_delegation_depth=3,
            )

        # self_delegation_depth should be registered as a coordinator capability
        mock_session.coordinator.register_capability.assert_any_call(
            "self_delegation_depth", 3
        )

    @pytest.mark.asyncio
    async def test_spawn_default_depth_does_not_register_capability(self):
        """When self_delegation_depth is 0 (default), don't register it."""
        hooks = HookRegistry()
        child_bundle = _make_child_bundle()
        prepared = _make_prepared_bundle()

        mock_session = _make_mock_session(hooks)

        with patch(
            "amplifier_core.AmplifierSession",
            return_value=mock_session,
        ):
            # Don't pass self_delegation_depth (uses default 0)
            await prepared.spawn(child_bundle, "Do something", compose=False)

        # self_delegation_depth should NOT be registered for depth=0
        capability_calls = [
            call[0][0]
            for call in mock_session.coordinator.register_capability.call_args_list
        ]
        assert "self_delegation_depth" not in capability_calls

    @pytest.mark.asyncio
    async def test_spawn_depth_does_not_pollute_orchestrator_config(self):
        """self_delegation_depth should NOT be in orchestrator config."""
        hooks = HookRegistry()
        child_bundle = _make_child_bundle()
        prepared = _make_prepared_bundle()

        mock_session = _make_mock_session(hooks)
        captured_config = {}

        def capture_session_init(config, **kwargs):
            captured_config.update(config)
            return mock_session

        with patch(
            "amplifier_core.AmplifierSession",
            side_effect=capture_session_init,
        ):
            await prepared.spawn(
                child_bundle,
                "Do something",
                compose=False,
                orchestrator_config={"min_delay_between_calls_ms": 500},
                self_delegation_depth=2,
            )

        orch_config = captured_config.get("orchestrator", {}).get("config", {})
        # orchestrator_config pass-through should still work
        assert orch_config.get("min_delay_between_calls_ms") == 500
        # But self_delegation_depth should NOT be in orchestrator config
        assert "self_delegation_depth" not in orch_config

    @pytest.mark.asyncio
    async def test_spawn_backward_compatible_without_depth(self):
        """Existing callers that don't pass self_delegation_depth still work."""
        hooks = HookRegistry()
        child_bundle = _make_child_bundle()
        prepared = _make_prepared_bundle()

        mock_session = _make_mock_session(hooks)

        with patch(
            "amplifier_core.AmplifierSession",
            return_value=mock_session,
        ):
            # Call without self_delegation_depth - should not raise
            result = await prepared.spawn(child_bundle, "Do something", compose=False)

        assert result["output"] == "Done"
        assert result["session_id"] == "test-child-123"

    @pytest.mark.asyncio
    async def test_spawn_turn_count_defaults_to_1(self):
        """turn_count should default to 1 when not in completion data."""
        hooks = HookRegistry()
        child_bundle = _make_child_bundle()
        prepared = _make_prepared_bundle()

        mock_session = _make_mock_session(hooks)

        with patch(
            "amplifier_core.AmplifierSession",
            return_value=mock_session,
        ):
            result = await prepared.spawn(child_bundle, "Do something", compose=False)

        # When orchestrator:complete doesn't fire (no turn_count in data),
        # the result should default to 1, not None
        assert result["turn_count"] == 1
