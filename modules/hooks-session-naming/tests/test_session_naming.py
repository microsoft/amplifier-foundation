"""Tests for hooks-session-naming async behavior and model role/provider preferences."""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_module_hooks_session_naming import (
    SessionNamingConfig,
    SessionNamingHook,
)


# =============================================================================
# Shared helpers
# =============================================================================


def _make_mock_provider() -> MagicMock:
    """Return a mock provider whose complete() returns a text response."""
    provider = MagicMock()
    text_block = MagicMock()
    text_block.text = (
        '{"action": "set", "name": "Test Session", "description": "A test."}'
    )
    response = MagicMock()
    response.content = [text_block]
    provider.complete = AsyncMock(return_value=response)
    return provider


def _make_coordinator(
    *,
    providers: dict | None = None,
    session_state: dict | None = None,
) -> MagicMock:
    """Return a coordinator mock wired for session-naming tests."""
    coordinator = MagicMock()
    coordinator.session_state = session_state or {}
    coordinator.hooks = MagicMock()
    coordinator.hooks.emit = AsyncMock()
    coordinator.hooks.register = MagicMock()
    coordinator.mount_points = MagicMock()
    coordinator.mount_points.get = MagicMock(return_value=None)

    _providers = (
        providers if providers is not None else {"provider-1": _make_mock_provider()}
    )
    coordinator.get = MagicMock(
        side_effect=lambda key: _providers if key == "providers" else None
    )
    return coordinator


def _make_hook(
    *,
    providers: dict | None = None,
    session_state: dict | None = None,
    model_role: str | None = None,
    provider_preferences: list[dict] | None = None,
    initial_trigger_turn: int = 2,
) -> SessionNamingHook:
    """Return a SessionNamingHook with mocked coordinator."""
    coordinator = _make_coordinator(
        providers=providers,
        session_state=session_state,
    )
    config = SessionNamingConfig(
        initial_trigger_turn=initial_trigger_turn,
        model_role=model_role,
        provider_preferences=provider_preferences,
    )
    return SessionNamingHook(coordinator, config)


def _install_mock_routing(
    *,
    resolve_fn=None,
    find_fn=None,
) -> callable:
    """Inject a mock amplifier_module_hooks_routing.resolver into sys.modules.

    Returns a cleanup() callable — always call it in a finally block.

    Usage:
        cleanup = _install_mock_routing(resolve_fn=AsyncMock(...))
        try:
            ...
        finally:
            cleanup()
    """
    mock_resolver_mod = types.ModuleType("amplifier_module_hooks_routing.resolver")
    if resolve_fn is not None:
        mock_resolver_mod.resolve_model_role = resolve_fn
    if find_fn is not None:
        mock_resolver_mod.find_provider_by_type = find_fn

    mock_routing_mod = types.ModuleType("amplifier_module_hooks_routing")

    originals: dict = {}
    for mod_name in (
        "amplifier_module_hooks_routing",
        "amplifier_module_hooks_routing.resolver",
    ):
        if mod_name in sys.modules:
            originals[mod_name] = sys.modules[mod_name]

    sys.modules["amplifier_module_hooks_routing"] = mock_routing_mod
    sys.modules["amplifier_module_hooks_routing.resolver"] = mock_resolver_mod

    def cleanup() -> None:
        for mod_name in (
            "amplifier_module_hooks_routing",
            "amplifier_module_hooks_routing.resolver",
        ):
            if mod_name in originals:
                sys.modules[mod_name] = originals[mod_name]
            elif mod_name in sys.modules:
                del sys.modules[mod_name]

    return cleanup


# =============================================================================
# Task 2: Async fire-and-forget
# =============================================================================


class TestAsyncFireAndForget:
    """on_orchestrator_complete must return immediately without awaiting the task."""

    @pytest.mark.asyncio
    async def test_returns_hookresult_without_awaiting_generate_name(
        self, tmp_path: Path
    ) -> None:
        """HookResult is returned before _generate_name completes."""
        task_finished = asyncio.Event()

        async def slow_generate(*args, **kwargs) -> None:
            await asyncio.sleep(0.3)
            task_finished.set()

        hook = _make_hook()
        hook._generate_name = slow_generate
        hook._get_session_dir = MagicMock(return_value=tmp_path)
        # turn_count=1 → current_turn=2 → hits initial_trigger_turn=2
        hook._load_metadata = MagicMock(return_value={"turn_count": 1})

        from amplifier_core import HookResult

        result = await hook.on_orchestrator_complete(
            "prompt:complete", {"session_id": "test-session-abc"}
        )

        assert isinstance(result, HookResult)
        assert result.action == "continue"
        # The task should NOT have finished by the time we return
        assert not task_finished.is_set(), (
            "on_orchestrator_complete should NOT await _generate_name"
        )

    @pytest.mark.asyncio
    async def test_pending_tasks_holds_reference_and_discards_on_done(
        self, tmp_path: Path
    ) -> None:
        """Task is added to _pending_tasks and removed when it completes."""
        task_started = asyncio.Event()

        async def quick_generate(*args, **kwargs) -> None:
            task_started.set()

        hook = _make_hook()
        hook._generate_name = quick_generate
        hook._get_session_dir = MagicMock(return_value=tmp_path)
        hook._load_metadata = MagicMock(return_value={"turn_count": 1})

        await hook.on_orchestrator_complete(
            "prompt:complete", {"session_id": "test-session-def"}
        )

        assert len(hook._pending_tasks) == 1, "Task must be tracked immediately"

        # Yield to event loop so the task runs and the done-callback fires
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert len(hook._pending_tasks) == 0, (
            "Task must be removed from _pending_tasks after completion"
        )


# =============================================================================
# Task 3: Session-end drain
# =============================================================================


class TestSessionEndDrain:
    """on_session_end must drain in-flight tasks within the 15 s timeout."""

    @pytest.mark.asyncio
    async def test_on_session_end_awaits_in_flight_task(self) -> None:
        """on_session_end waits for a pending task that completes quickly."""
        hook = _make_hook()
        completed = asyncio.Event()

        async def quick_task() -> None:
            await asyncio.sleep(0.05)
            completed.set()

        task = asyncio.create_task(quick_task())
        hook._pending_tasks.add(task)
        task.add_done_callback(hook._pending_tasks.discard)

        from amplifier_core import HookResult

        result = await hook.on_session_end("session:end", {})

        assert isinstance(result, HookResult)
        assert result.action == "continue"
        assert completed.is_set(), "on_session_end must drain the in-flight task"

    @pytest.mark.asyncio
    async def test_on_session_end_handles_timeout_gracefully(self) -> None:
        """on_session_end returns HookResult even when a task times out (15 s)."""
        hook = _make_hook()

        async def infinite_task() -> None:
            await asyncio.sleep(999)

        task = asyncio.create_task(infinite_task())
        hook._pending_tasks.add(task)
        task.add_done_callback(hook._pending_tasks.discard)

        from amplifier_core import HookResult

        # Patch asyncio.wait_for to immediately raise TimeoutError (no real wait)
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
            result = await hook.on_session_end("session:end", {})

        assert isinstance(result, HookResult)
        assert result.action == "continue"

        # Clean up the dangling task
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_on_session_end_no_pending_returns_immediately(self) -> None:
        """on_session_end with no pending tasks returns HookResult immediately."""
        hook = _make_hook()
        assert len(hook._pending_tasks) == 0

        from amplifier_core import HookResult

        result = await hook.on_session_end("session:end", {})

        assert isinstance(result, HookResult)
        assert result.action == "continue"
