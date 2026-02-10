"""Tests for spawn() enrichment with orchestrator:complete metadata."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from amplifier_core.hooks import HookRegistry

from amplifier_foundation.bundle import Bundle, PreparedBundle


# =============================================================================
# Helpers
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
    coordinator.get = MagicMock(return_value=None)  # No context manager
    return coordinator


def _make_mock_session(
    hooks: HookRegistry,
    session_id: str = "test-child-123",
    execute_side_effect=None,
) -> MagicMock:
    """Create a mock AmplifierSession with a real HookRegistry."""
    session = MagicMock()
    session.session_id = session_id
    session.coordinator = _make_mock_coordinator(hooks)
    session.initialize = AsyncMock()
    session.cleanup = AsyncMock()
    if execute_side_effect:
        session.execute = AsyncMock(side_effect=execute_side_effect)
    else:
        session.execute = AsyncMock(return_value="Done")
    return session


# =============================================================================
# Tests
# =============================================================================


@pytest.mark.asyncio
async def test_spawn_returns_status_from_orchestrator_complete():
    """spawn() should include status/turn_count/metadata from orchestrator:complete."""
    hooks = HookRegistry()
    child_bundle = _make_child_bundle()
    prepared = _make_prepared_bundle()

    async def _execute_with_event(instruction):
        """Simulate orchestrator emitting orchestrator:complete during execute."""
        await hooks.emit(
            "orchestrator:complete",
            {
                "status": "success",
                "turn_count": 3,
                "metadata": {"routing_label": "tests_pass"},
            },
        )
        return "The task completed successfully."

    mock_session = _make_mock_session(hooks)
    mock_session.execute = AsyncMock(side_effect=_execute_with_event)

    with patch(
        "amplifier_core.AmplifierSession",
        return_value=mock_session,
    ):
        result = await prepared.spawn(child_bundle, "Do something", compose=False)

    assert result["output"] == "The task completed successfully."
    assert result["session_id"] == "test-child-123"
    assert result["status"] == "success"
    assert result["turn_count"] == 3
    assert result["metadata"] == {"routing_label": "tests_pass"}


@pytest.mark.asyncio
async def test_spawn_returns_defaults_when_no_orchestrator_complete():
    """spawn() should return sensible defaults when orchestrator:complete never fires."""
    hooks = HookRegistry()
    child_bundle = _make_child_bundle()
    prepared = _make_prepared_bundle()

    mock_session = _make_mock_session(hooks, session_id="test-child-456")
    mock_session.execute = AsyncMock(return_value="Some response")

    with patch(
        "amplifier_core.AmplifierSession",
        return_value=mock_session,
    ):
        result = await prepared.spawn(child_bundle, "Do something", compose=False)

    assert result["output"] == "Some response"
    assert result["session_id"] == "test-child-456"
    assert result["status"] == "success"  # default
    assert result["turn_count"] == 1  # default (matches tool-delegate's .get fallback)
    assert result["metadata"] == {}  # default


@pytest.mark.asyncio
async def test_spawn_returns_error_status_on_failed_execution():
    """spawn() should capture error status from orchestrator:complete."""
    hooks = HookRegistry()
    child_bundle = _make_child_bundle()
    prepared = _make_prepared_bundle()

    async def _execute_with_error_event(instruction):
        await hooks.emit(
            "orchestrator:complete",
            {
                "status": "error",
                "turn_count": 1,
                "metadata": {"error": "tool failed"},
            },
        )
        return "I encountered an error."

    mock_session = _make_mock_session(hooks)
    mock_session.execute = AsyncMock(side_effect=_execute_with_error_event)

    with patch(
        "amplifier_core.AmplifierSession",
        return_value=mock_session,
    ):
        result = await prepared.spawn(child_bundle, "Do something", compose=False)

    assert result["status"] == "error"
    assert result["turn_count"] == 1
    assert result["metadata"] == {"error": "tool failed"}


@pytest.mark.asyncio
async def test_spawn_backward_compatible_output_and_session_id():
    """Existing callers that only read 'output' and 'session_id' still work."""
    hooks = HookRegistry()
    child_bundle = _make_child_bundle()
    prepared = _make_prepared_bundle()

    mock_session = _make_mock_session(hooks, session_id="compat-session")
    mock_session.execute = AsyncMock(return_value="backward compat response")

    with patch(
        "amplifier_core.AmplifierSession",
        return_value=mock_session,
    ):
        result = await prepared.spawn(child_bundle, "Do something", compose=False)

    # These are the ONLY two keys existing callers read
    assert result["output"] == "backward compat response"
    assert result["session_id"] == "compat-session"
    # New keys are present but don't break anything
    assert "status" in result
    assert "turn_count" in result
    assert "metadata" in result


@pytest.mark.asyncio
async def test_spawn_cleans_up_hook_on_exception():
    """The temporary hook should be cleaned up even if execute() raises."""
    hooks = HookRegistry()
    child_bundle = _make_child_bundle()
    prepared = _make_prepared_bundle()

    async def _execute_raises(instruction):
        raise RuntimeError("execute failed")

    mock_session = _make_mock_session(hooks, execute_side_effect=_execute_raises)

    with patch(
        "amplifier_core.AmplifierSession",
        return_value=mock_session,
    ):
        with pytest.raises(RuntimeError, match="execute failed"):
            await prepared.spawn(child_bundle, "Do something", compose=False)

    # Verify the temporary hook was cleaned up (no handlers left for the event)
    handlers = hooks.list_handlers("orchestrator:complete")
    handler_names = handlers.get("orchestrator:complete", [])
    assert "_spawn_completion_capture" not in handler_names
