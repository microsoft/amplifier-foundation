"""Tests for hooks-session-naming async behavior and model role/provider preferences."""

from __future__ import annotations

import asyncio
import sys
import types
from collections.abc import Callable
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
) -> Callable[[], None]:
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
        mock_resolver_mod.resolve_model_role = resolve_fn  # type: ignore[attr-defined]
    if find_fn is not None:
        mock_resolver_mod.find_provider_by_type = find_fn  # type: ignore[attr-defined]

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


# =============================================================================
# Task 4: Internal provider timeout
# =============================================================================


class TestProviderTimeout:
    """_generate_name must handle a stalled provider call within 10 s."""

    @pytest.mark.asyncio
    async def test_generate_name_returns_on_provider_timeout(
        self, tmp_path: Path
    ) -> None:
        """_generate_name catches asyncio.TimeoutError from stalled _call_provider."""
        hook = _make_hook()

        # Give it real context so it reaches the _call_provider call
        hook._get_conversation_context = AsyncMock(
            return_value="some conversation text"
        )
        hook._load_metadata = MagicMock(return_value={})

        # Replace wait_for with a version that closes the coroutine before
        # raising, so the GC never sees an unawaited coroutine (no RuntimeWarning)
        async def fake_wait_for(coro, timeout=None):  # noqa: RUF029
            coro.close()
            raise asyncio.TimeoutError

        with patch("asyncio.wait_for", new=fake_wait_for):
            # Must not raise — timeout must be caught inside _generate_name
            await hook._generate_name("session-abc123", tmp_path, is_update=False)

        # If we reach here, the timeout was handled correctly — no exception propagated


# =============================================================================
# Task 5: Config dataclass extension
# =============================================================================


class TestSessionNamingConfig:
    """SessionNamingConfig must accept model_role and provider_preferences."""

    def test_defaults_are_none(self) -> None:
        """Both new fields default to None."""
        config = SessionNamingConfig()
        assert config.model_role is None
        assert config.provider_preferences is None

    def test_model_role_can_be_set(self) -> None:
        """model_role can be set to a role name string."""
        config = SessionNamingConfig(model_role="fast")
        assert config.model_role == "fast"

    def test_provider_preferences_can_be_set(self) -> None:
        """provider_preferences can be set to a list of dicts."""
        prefs = [{"provider": "anthropic", "model": "claude-haiku-4-5"}]
        config = SessionNamingConfig(provider_preferences=prefs)
        assert config.provider_preferences == prefs

    def test_existing_fields_still_have_defaults(self) -> None:
        """Adding new fields must not break existing defaults."""
        config = SessionNamingConfig()
        assert config.initial_trigger_turn == 2
        assert config.update_interval_turns == 5
        assert config.max_name_length == 50
        assert config.max_description_length == 200
        assert config.max_retries == 3


# =============================================================================
# Task 6: model_role resolution
# =============================================================================


class TestModelRoleResolution:
    """_call_provider resolves model_role via routing matrix when available."""

    @pytest.mark.asyncio
    async def test_model_role_uses_resolved_provider_and_model(self) -> None:
        """When model_role resolves, the matching provider is called with model override."""
        anthropic_provider = _make_mock_provider()
        openai_provider = _make_mock_provider()
        providers = {
            "provider-anthropic": anthropic_provider,
            "provider-openai": openai_provider,
        }

        routing_matrix = {
            "roles": {
                "fast": {
                    "candidates": [{"provider": "anthropic", "model": "claude-haiku-*"}]
                }
            }
        }

        mock_resolve = AsyncMock(
            return_value=[
                {"provider": "anthropic", "model": "claude-haiku-4-5", "config": {}}
            ]
        )
        cleanup = _install_mock_routing(resolve_fn=mock_resolve)
        try:
            hook = _make_hook(
                providers=providers,
                session_state={"routing_matrix": routing_matrix},
                model_role="fast",
            )
            await hook._call_provider("name this session")

            assert anthropic_provider.complete.called, (
                "Expected anthropic provider to be called based on model_role resolution"
            )
            assert not openai_provider.complete.called

            mock_resolve.assert_called_once()
            call_args = mock_resolve.call_args
            assert call_args[0][0] == ["fast"], "Must pass [model_role] as roles list"

            call_kwargs = anthropic_provider.complete.call_args
            request = call_kwargs[0][0]
            assert request.model == "claude-haiku-4-5"
        finally:
            cleanup()

    @pytest.mark.asyncio
    async def test_model_role_falls_back_when_routing_not_installed(
        self, caplog
    ) -> None:
        """ImportError on hooks-routing → falls back to priority provider, logs warning."""
        priority_provider = _make_mock_provider()
        providers = {"provider-priority": priority_provider}

        for mod_name in (
            "amplifier_module_hooks_routing",
            "amplifier_module_hooks_routing.resolver",
        ):
            sys.modules.pop(mod_name, None)

        hook = _make_hook(
            providers=providers,
            session_state={"routing_matrix": {"roles": {}}},
            model_role="fast",
        )

        import logging

        with caplog.at_level(logging.WARNING):
            await hook._call_provider("name this session")

        assert priority_provider.complete.called, (
            "Must fall back to priority provider when hooks-routing not available"
        )
        assert any(
            "hooks-routing" in msg or "model_role" in msg for msg in caplog.messages
        ), "Must log a warning mentioning model_role or hooks-routing"

    @pytest.mark.asyncio
    async def test_model_role_falls_back_when_no_routing_matrix(self) -> None:
        """No routing_matrix in session_state → falls back to priority provider."""
        priority_provider = _make_mock_provider()
        providers = {"provider-priority": priority_provider}

        mock_resolve = AsyncMock(return_value=[])
        cleanup = _install_mock_routing(resolve_fn=mock_resolve)
        try:
            hook = _make_hook(
                providers=providers,
                session_state={},
                model_role="fast",
            )
            await hook._call_provider("name this session")

            assert priority_provider.complete.called, (
                "Must fall back to priority provider when routing_matrix absent"
            )
            mock_resolve.assert_not_called()
        finally:
            cleanup()

    @pytest.mark.asyncio
    async def test_no_model_role_uses_priority_provider(self) -> None:
        """Without model_role, existing behavior is preserved (priority provider)."""
        priority_provider = _make_mock_provider()
        providers = {"provider-priority": priority_provider}

        hook = _make_hook(providers=providers)
        await hook._call_provider("name this session")

        assert priority_provider.complete.called
        call_kwargs = priority_provider.complete.call_args
        request = call_kwargs[0][0]
        assert request.model is None, "No model override without model_role"


# =============================================================================
# Task 7: provider_preferences resolution
# =============================================================================


class TestProviderPreferencesResolution:
    """_call_provider resolves provider_preferences using find_provider_by_type."""

    @pytest.mark.asyncio
    async def test_provider_preferences_selects_correct_provider_and_model(
        self,
    ) -> None:
        """First matching provider_preference wins; model is passed to ChatRequest."""
        anthropic_provider = _make_mock_provider()
        openai_provider = _make_mock_provider()
        providers = {
            "provider-anthropic": anthropic_provider,
            "provider-openai": openai_provider,
        }

        prefs = [{"provider": "anthropic", "model": "claude-haiku-4-5"}]

        mock_find = MagicMock(return_value=anthropic_provider)
        cleanup = _install_mock_routing(find_fn=mock_find)
        try:
            hook = _make_hook(providers=providers, provider_preferences=prefs)
            await hook._call_provider("name this session")

            assert anthropic_provider.complete.called
            assert not openai_provider.complete.called

            mock_find.assert_called_once_with("anthropic", providers)

            call_kwargs = anthropic_provider.complete.call_args
            request = call_kwargs[0][0]
            assert request.model == "claude-haiku-4-5"
        finally:
            cleanup()

    @pytest.mark.asyncio
    async def test_provider_preferences_skips_to_next_when_first_not_found(
        self,
    ) -> None:
        """If first preference provider not found, tries next preference."""
        openai_provider = _make_mock_provider()
        providers = {"provider-openai": openai_provider}

        prefs = [
            {"provider": "anthropic", "model": "claude-haiku-4-5"},
            {"provider": "openai", "model": "gpt-4o-mini"},
        ]

        def find_by_type(provider_type: str, _providers: dict):
            if provider_type == "anthropic":
                return None
            if provider_type == "openai":
                return openai_provider
            return None

        cleanup = _install_mock_routing(find_fn=find_by_type)
        try:
            hook = _make_hook(providers=providers, provider_preferences=prefs)
            await hook._call_provider("name this session")

            assert openai_provider.complete.called
            call_kwargs = openai_provider.complete.call_args
            request = call_kwargs[0][0]
            assert request.model == "gpt-4o-mini"
        finally:
            cleanup()

    @pytest.mark.asyncio
    async def test_provider_preferences_falls_back_when_routing_not_installed(
        self, caplog
    ) -> None:
        """ImportError on hooks-routing → falls back to priority provider, logs warning."""
        priority_provider = _make_mock_provider()
        providers = {"provider-priority": priority_provider}

        prefs = [{"provider": "anthropic", "model": "claude-haiku-4-5"}]

        for mod_name in (
            "amplifier_module_hooks_routing",
            "amplifier_module_hooks_routing.resolver",
        ):
            sys.modules.pop(mod_name, None)

        hook = _make_hook(providers=providers, provider_preferences=prefs)

        import logging

        with caplog.at_level(logging.WARNING):
            await hook._call_provider("name this session")

        assert priority_provider.complete.called
        assert any(
            "provider_preferences" in msg or "hooks-routing" in msg
            for msg in caplog.messages
        )

    @pytest.mark.asyncio
    async def test_provider_preferences_wins_over_model_role(self) -> None:
        """provider_preferences takes precedence when both are set."""
        anthropic_provider = _make_mock_provider()
        priority_provider = _make_mock_provider()
        providers = {
            "provider-anthropic": anthropic_provider,
            "provider-priority": priority_provider,
        }

        routing_matrix = {"roles": {"fast": {"candidates": []}}}
        mock_resolve = AsyncMock(
            return_value=[{"provider": "priority", "model": "big-model"}]
        )
        mock_find = MagicMock(return_value=anthropic_provider)
        cleanup = _install_mock_routing(resolve_fn=mock_resolve, find_fn=mock_find)
        try:
            hook = _make_hook(
                providers=providers,
                session_state={"routing_matrix": routing_matrix},
                model_role="fast",
                provider_preferences=[
                    {"provider": "anthropic", "model": "claude-haiku-4-5"}
                ],
            )
            await hook._call_provider("name this session")

            assert anthropic_provider.complete.called, (
                "provider_preferences must win over model_role"
            )
            mock_resolve.assert_not_called()
        finally:
            cleanup()
