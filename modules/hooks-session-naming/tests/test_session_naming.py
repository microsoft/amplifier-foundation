"""Tests for hooks-session-naming async behavior and model role/provider preferences."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from amplifier_foundation.spawn_utils import ProviderPreference

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
    model_role_resolver=None,
) -> MagicMock:
    """Return a coordinator mock wired for session-naming tests.

    ``model_role_resolver`` is the duck-typed capability the consumer code
    looks up via ``coordinator.get_capability("model_role_resolver")``.
    Pass ``None`` (default) to simulate "no routing bundle installed".
    """
    coordinator = MagicMock()
    coordinator.session_state = {}
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
    capabilities: dict = {"model_role_resolver": model_role_resolver}
    coordinator.get_capability = MagicMock(side_effect=capabilities.get)
    return coordinator


def _make_hook(
    *,
    providers: dict | None = None,
    model_role_resolver=None,
    model_role: str | None = None,
    initial_trigger_turn: int = 2,
) -> SessionNamingHook:
    """Return a SessionNamingHook with mocked coordinator."""
    coordinator = _make_coordinator(
        providers=providers,
        model_role_resolver=model_role_resolver,
    )
    config = SessionNamingConfig(
        initial_trigger_turn=initial_trigger_turn,
        model_role=model_role,
    )
    return SessionNamingHook(coordinator, config)


def _make_resolver(
    *,
    return_value: list | None = None,
    name: str = "test-matrix",
):
    """Build a duck-typed ``model_role_resolver`` mock.

    The new capability contract is:
        async def resolve(model_role: str | list[str]) -> list[ProviderPreference]
    Tests pass the mock via ``_make_hook(model_role_resolver=resolver)``.
    """
    resolver = MagicMock()
    resolver.name = name
    resolver.resolve = AsyncMock(return_value=return_value if return_value is not None else [])
    return resolver


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
    """SessionNamingConfig must accept model_role."""

    def test_model_role_defaults_to_fast(self) -> None:
        """model_role defaults to 'fast' so naming uses a cheap model automatically."""
        config = SessionNamingConfig()
        assert config.model_role == "fast"

    def test_model_role_can_be_set(self) -> None:
        """model_role can be set to a role name string."""
        config = SessionNamingConfig(model_role="fast")
        assert config.model_role == "fast"

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

        resolver = _make_resolver(
            return_value=[
                ProviderPreference(provider="anthropic", model="claude-haiku-4-5", config={}),
            ]
        )
        hook = _make_hook(
            providers=providers,
            model_role_resolver=resolver,
            model_role="fast",
        )
        await hook._call_provider("name this session")

        assert anthropic_provider.complete.called, (
            "Expected anthropic provider to be called based on model_role resolution"
        )
        assert not openai_provider.complete.called

        resolver.resolve.assert_called_once()
        call_args = resolver.resolve.call_args
        assert call_args[0][0] == "fast", "Must pass model_role as a string"

        call_kwargs = anthropic_provider.complete.call_args
        request = call_kwargs[0][0]
        assert request.model == "claude-haiku-4-5"
    @pytest.mark.asyncio
    async def test_model_role_falls_back_when_no_resolver_capability(self) -> None:
        """No model_role_resolver capability → falls back to priority provider."""
        priority_provider = _make_mock_provider()
        providers = {"provider-priority": priority_provider}

        resolver = _make_resolver(return_value=[])
        hook = _make_hook(
            providers=providers,
            model_role_resolver=None,
            model_role="fast",
        )
        await hook._call_provider("name this session")

        assert priority_provider.complete.called, (
            "Must fall back to priority provider when model_role_resolver capability is absent"
        )
        resolver.resolve.assert_not_called()
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
# Task 7: Background naming call must not leak llm:stream_* events
# =============================================================================


class TestNoStreamingEvents:
    """_call_provider must set metadata={'stream': False} so the provider takes
    the non-streaming branch and emits no llm:stream_block_* events.

    Root cause: without this flag the shared Anthropic provider uses
    use_streaming=True, emitting llm:stream_block_start/delta/end on the hook
    bus.  Those events carry no session_id, so the streaming-UI overlay treats
    them as foreground output and renders the naming JSON to the terminal.
    """

    _STREAM_EVENTS = frozenset({
        "llm:stream_block_start",
        "llm:stream_block_delta",
        "llm:stream_block_end",
    })

    def _make_streaming_simulator(self, emitted: list) -> MagicMock:
        """Mock provider that conditionally emits stream events.

        Mirrors the real AnthropicProvider's logic after the fix:
          - metadata={'stream': False}  → non-streaming path, NO events
          - anything else               → streaming path, emits llm:stream_*

        This lets the test discriminate: before the fix the naming hook sends
        no metadata flag so events fire; after the fix the flag suppresses them.
        """
        from amplifier_core import ChatRequest

        async def _complete(request: ChatRequest) -> MagicMock:
            force_no_stream = (
                request.metadata is not None
                and request.metadata.get("stream") is False
            )
            if not force_no_stream:
                emitted.append("llm:stream_block_start")
                emitted.append("llm:stream_block_delta")
                emitted.append("llm:stream_block_end")
            return _make_mock_provider().complete.return_value

        provider = MagicMock()
        provider.complete = _complete
        return provider

    @pytest.mark.asyncio
    async def test_call_provider_sets_stream_false_in_metadata(self) -> None:
        """ChatRequest passed to provider.complete() must have metadata['stream']=False."""
        provider = _make_mock_provider()
        hook = _make_hook(providers={"p": provider})

        await hook._call_provider("name this session")

        assert provider.complete.called
        request = provider.complete.call_args[0][0]
        assert request.metadata is not None, (
            "ChatRequest.metadata must not be None — fix: pass metadata={'stream': False}"
        )
        assert request.metadata.get("stream") is False, (
            f"Expected metadata['stream']=False, got metadata={request.metadata!r}"
        )

    @pytest.mark.asyncio
    async def test_naming_call_emits_no_stream_events(self) -> None:
        """No llm:stream_* events on the hook bus during the naming call."""
        emitted: list = []
        provider = self._make_streaming_simulator(emitted)
        hook = _make_hook(providers={"p": provider})

        await hook._call_provider("name this session")

        stream_events = [e for e in emitted if e in self._STREAM_EVENTS]
        assert not stream_events, (
            f"Naming call leaked llm:stream_* events: {stream_events}. "
            "Fix: set metadata={'stream': False} in _call_provider()."
        )

    @pytest.mark.asyncio
    async def test_discriminator_detects_streaming_without_flag(self) -> None:
        """Control group: without the metadata flag, stream events ARE detected."""
        from amplifier_core import ChatRequest

        emitted: list = []
        provider = self._make_streaming_simulator(emitted)

        # Call directly with no metadata flag (pre-fix scenario)
        request_no_flag = ChatRequest(messages=[])
        await provider.complete(request_no_flag)

        stream_events = [e for e in emitted if e in self._STREAM_EVENTS]
        assert stream_events, (
            "DISCRIMINATOR BROKEN: expected stream events for request without flag"
        )

    @pytest.mark.asyncio
    async def test_call_provider_sets_small_max_output_tokens(self) -> None:
        """ChatRequest must carry a small max_output_tokens to avoid Anthropic's
        'streaming required for long operations' guard.

        The naming call returns only a tiny JSON object; it has no need for a large
        token budget.  Without an explicit cap the request inherits the provider's
        large default, which trips Anthropic's guard when stream=False.

        The cap must be set and must be <= 1024 (well below the threshold).
        """
        provider = _make_mock_provider()
        hook = _make_hook(providers={"p": provider})

        await hook._call_provider("name this session")

        assert provider.complete.called
        request = provider.complete.call_args[0][0]
        assert request.max_output_tokens is not None, (
            "ChatRequest.max_output_tokens must be explicitly set for the naming call. "
            "Without it the request inherits the provider's large default, which trips "
            "Anthropic's 'streaming required for long operations' guard when stream=False."
        )
        assert request.max_output_tokens <= 1024, (
            f"max_output_tokens={request.max_output_tokens} is too large. "
            "The naming call returns a tiny JSON object; cap it at <= 1024 "
            "(256 is the recommended value)."
        )
