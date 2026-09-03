"""Tests for hooks-session-naming async behavior and model role/provider preferences."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
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


def _make_vendor_provider(vendor: str, *, priority: int | None = None) -> MagicMock:
    """A mock provider that answers the kernel's ``get_info().id`` contract.

    ``vendor`` is the provider id ("anthropic", "openai", ...) — two mount
    names sharing an id are the same vendor.
    """
    provider = _make_mock_provider()
    provider.get_info.return_value.id = vendor
    if priority is not None:
        provider.priority = priority
    return provider


def _make_pin(current: str | None):
    """Duck-typed ``conversation.provider_pin`` capability mock."""
    pin = MagicMock()
    pin.current = MagicMock(return_value=current)
    return pin


def _make_coordinator(
    *,
    providers: dict | None = None,
    model_role_resolver=None,
    provider_pin: str | None = None,
) -> MagicMock:
    """Return a coordinator mock wired for session-naming tests.

    ``model_role_resolver`` is the duck-typed capability the consumer code
    looks up via ``coordinator.get_capability("model_role_resolver")``.
    Pass ``None`` (default) to simulate "no routing bundle installed".

    ``provider_pin`` is the mount name the ``conversation.provider_pin``
    capability reports as pinned. ``None`` (default) means unpinned, which
    is what a session without an explicit pin looks like.
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
    capabilities: dict = {
        "model_role_resolver": model_role_resolver,
        "conversation.provider_pin": (
            _make_pin(provider_pin) if provider_pin is not None else None
        ),
    }
    coordinator.get_capability = MagicMock(side_effect=capabilities.get)
    return coordinator


def _make_hook(
    *,
    providers: dict | None = None,
    model_role_resolver=None,
    model_role: str | None = None,
    initial_trigger_turn: int = 2,
    provider_pin: str | None = None,
) -> SessionNamingHook:
    """Return a SessionNamingHook with mocked coordinator."""
    coordinator = _make_coordinator(
        providers=providers,
        model_role_resolver=model_role_resolver,
        provider_pin=provider_pin,
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

    @pytest.mark.asyncio
    async def test_resolver_empty_result_falls_back_to_priority_provider(
        self, caplog
    ) -> None:
        """Resolver present but resolves to [] must fall back to the session's
        priority provider AND log a WARNING identifying the unresolved role
        and the provider substituted for it -- naming must still run.

        This is the load-bearing regression test for the bug: a resolver that
        resolves to no candidates for a configured model_role (e.g. no "fast"
        model configured for the active provider) is a *stable configuration
        gap*, not a transient error -- retrying later changes nothing. Skipping
        silently in that case means session naming is a feature that quietly
        never runs. Naming must still happen, using the session's own default
        provider, with a loud warning explaining why a role-based routing
        preference was not honored.
        """
        priority_provider = _make_mock_provider()
        providers = {"provider-priority": priority_provider}

        resolver = _make_resolver(return_value=[])
        hook = _make_hook(
            providers=providers,
            model_role_resolver=resolver,
            model_role="fast",
        )

        with caplog.at_level("WARNING"):
            result = await hook._call_provider("name this session", "session-abc")

        assert result is not None, (
            "Naming must still run against the fallback provider when "
            "model_role resolves to no candidates, not abort"
        )
        assert priority_provider.complete.called, (
            "Must fall back to the priority provider when an explicitly-"
            "configured model_role resolves to no candidates, rather than "
            "silently skipping naming for the turn"
        )
        call_kwargs = priority_provider.complete.call_args
        request = call_kwargs[0][0]
        assert request.model is None, (
            "Fallback must not invent a model override -- it uses whatever "
            "model the fallback provider is already configured with"
        )
        resolver.resolve.assert_called_once()

        warnings = [r for r in caplog.records if r.levelno >= 30]
        assert warnings, (
            "Expected a WARNING log identifying the unresolved role and the "
            "fallback provider substituted for it"
        )
        assert any("fast" in r.getMessage() for r in warnings), (
            "Warning should name the model_role that failed to resolve"
        )
        assert any("provider-priority" in r.getMessage() for r in warnings), (
            "Warning should name the provider actually used"
        )

    @pytest.mark.asyncio
    async def test_resolver_empty_result_warns_once_per_session(self, caplog) -> None:
        """The no-candidates fallback warning fires once per session, then
        drops to DEBUG on subsequent occurrences within the same session.

        Naming retries every few turns for the life of a session, so without
        this, a stable config gap (role never resolves) would re-emit the
        identical WARNING on every retry -- noise that drowns out the one
        occurrence a reader actually needs to see.
        """
        providers = {"provider-priority": _make_mock_provider()}
        resolver = _make_resolver(return_value=[])
        hook = _make_hook(
            providers=providers,
            model_role_resolver=resolver,
            model_role="fast",
        )

        with caplog.at_level("DEBUG"):
            await hook._call_provider("name this session", "session-xyz")
            first_pass_warnings = [r for r in caplog.records if r.levelno >= 30]
            caplog.clear()
            await hook._call_provider("name this session", "session-xyz")
            second_pass_warnings = [r for r in caplog.records if r.levelno >= 30]
            second_pass_debugs = [r for r in caplog.records if r.levelno == 10]

        assert first_pass_warnings, "First occurrence in a session must warn"
        assert not second_pass_warnings, (
            "Second occurrence in the SAME session must not re-warn"
        )
        assert second_pass_debugs, (
            "Second occurrence should still be logged, just at DEBUG"
        )

    @pytest.mark.asyncio
    async def test_resolver_exception_aborts_without_calling_provider(self) -> None:
        """Resolver present but resolve() raises must abort, NOT propagate and
        NOT fall back to the priority provider."""
        priority_provider = _make_mock_provider()
        providers = {"provider-priority": priority_provider}

        resolver = _make_resolver()
        resolver.resolve = AsyncMock(side_effect=RuntimeError("boom"))
        hook = _make_hook(
            providers=providers,
            model_role_resolver=resolver,
            model_role="fast",
        )

        result = await hook._call_provider("name this session")

        assert result is None, (
            "Must abort (return None) when the resolver raises, not propagate"
        )
        assert not priority_provider.complete.called, (
            "Must NOT silently fall back to the priority provider when the "
            "resolver raises"
        )

    @pytest.mark.asyncio
    async def test_resolver_empty_result_logs_warning(self, caplog) -> None:
        """Falling back due to an empty resolution must be logged at WARNING."""
        hook = _make_hook(
            providers={"provider-priority": _make_mock_provider()},
            model_role_resolver=_make_resolver(return_value=[]),
            model_role="fast",
        )

        with caplog.at_level("WARNING"):
            await hook._call_provider("name this session", "session-1")

        warnings = [r for r in caplog.records if r.levelno >= 30]
        assert warnings, (
            "Expected a WARNING log when model_role resolves to no candidates"
        )

    @pytest.mark.asyncio
    async def test_resolver_exception_logs_warning(self, caplog) -> None:
        """Aborting due to a resolver exception must be logged at WARNING."""
        resolver = _make_resolver()
        resolver.resolve = AsyncMock(side_effect=RuntimeError("boom"))
        hook = _make_hook(
            providers={"provider-priority": _make_mock_provider()},
            model_role_resolver=resolver,
            model_role="fast",
        )

        with caplog.at_level("WARNING"):
            await hook._call_provider("name this session")

        warnings = [r for r in caplog.records if r.levelno >= 30]
        assert warnings, "Expected a WARNING log when the resolver raises"


# =============================================================================
# Cross-provider purity: naming never calls a provider this session didn't pick
# =============================================================================


class TestProviderPurity:
    """A session pinned to provider X must never emit a naming call on Y.

    Measured leak this pins shut (model_performance-egh): the routing matrix
    defaults to openai, so in an Anthropic-pinned session ``model_role="fast"``
    resolved to an openai candidate, and the unmatched-candidate path fell
    through to ``next(iter(providers.values()))`` — an order-dependent,
    SILENT borrow of whichever provider instance happened to be first in the
    mount dict. 321 foreign responses across 12 capture roots came from here.
    """

    @pytest.mark.asyncio
    async def test_pinned_session_never_calls_foreign_vendor(self, caplog) -> None:
        """Anthropic-pinned session + openai-resolving role → anthropic only.

        The mount dict deliberately lists openai FIRST, so the old
        ``next(iter(providers))`` fallback would have picked openai even
        without the resolver ever matching.
        """
        openai_provider = _make_vendor_provider("openai")
        anthropic_provider = _make_vendor_provider("anthropic")
        providers = {
            "openai-gpt-5": openai_provider,
            "anthropic-sonnet": anthropic_provider,
        }

        resolver = _make_resolver(
            return_value=[
                ProviderPreference(provider="openai", model="gpt-5-mini", config={}),
            ]
        )
        hook = _make_hook(
            providers=providers,
            model_role_resolver=resolver,
            model_role="fast",
            provider_pin="anthropic-sonnet",
        )

        with caplog.at_level("WARNING"):
            result = await hook._call_provider("name this session", "session-pin")

        assert not openai_provider.complete.called, (
            "A session pinned to anthropic must NEVER emit a naming call on "
            "openai — this is the cross-provider leak"
        )
        assert anthropic_provider.complete.called, (
            "Naming must run on the session's own pinned provider"
        )
        assert result is not None

        request = anthropic_provider.complete.call_args[0][0]
        assert request.model is None, (
            "A refused foreign candidate must not leave its model override "
            "behind on the session's own provider"
        )

        warnings = [r.getMessage() for r in caplog.records if r.levelno >= 30]
        assert warnings, "Refusing a foreign provider must be loud, not silent"
        assert any("openai" in m for m in warnings), (
            "The warning must name the provider that was refused"
        )
        assert any("anthropic-sonnet" in m for m in warnings), (
            "The warning must name the provider actually used"
        )

    @pytest.mark.asyncio
    async def test_same_vendor_sibling_is_allowed_with_model_override(self) -> None:
        """anthropic-haiku for an anthropic-pinned session is NOT a leak.

        Two mount names sharing a ``get_info().id`` are the same vendor, so
        routing a cheap chore to a cheaper sibling model stays allowed — the
        purity rule is about vendors, not about mount names.
        """
        sonnet = _make_vendor_provider("anthropic")
        haiku = _make_vendor_provider("anthropic")
        providers = {"anthropic-sonnet": sonnet, "anthropic-haiku": haiku}

        resolver = _make_resolver(
            return_value=[
                ProviderPreference(
                    provider="anthropic-haiku", model="claude-haiku-4-5", config={}
                ),
            ]
        )
        hook = _make_hook(
            providers=providers,
            model_role_resolver=resolver,
            model_role="fast",
            provider_pin="anthropic-sonnet",
        )
        await hook._call_provider("name this session", "session-sibling")

        assert haiku.complete.called, "Same-vendor sibling must still be usable"
        assert not sonnet.complete.called
        assert haiku.complete.call_args[0][0].model == "claude-haiku-4-5"

    @pytest.mark.asyncio
    async def test_unknown_vendor_candidate_is_refused(self, caplog) -> None:
        """Fail closed: a candidate whose vendor cannot be established is refused.

        ``get_info()`` is the only contract for vendor identity. If it is
        missing or unreadable, sameness cannot be PROVEN, and an unprovable
        sameness is exactly how the leak got in.
        """
        session_provider = _make_vendor_provider("anthropic")
        mystery = _make_mock_provider()
        mystery.get_info = MagicMock(side_effect=RuntimeError("no info"))
        providers = {
            "anthropic-sonnet": session_provider,
            "mystery-provider": mystery,
        }

        resolver = _make_resolver(
            return_value=[
                ProviderPreference(provider="mystery", model="who-knows", config={}),
            ]
        )
        hook = _make_hook(
            providers=providers,
            model_role_resolver=resolver,
            model_role="fast",
            provider_pin="anthropic-sonnet",
        )

        with caplog.at_level("WARNING"):
            await hook._call_provider("name this session", "session-unknown")

        assert not mystery.complete.called, (
            "An unprovable-vendor candidate must be refused, not borrowed"
        )
        assert session_provider.complete.called
        assert [r for r in caplog.records if r.levelno >= 30]

    @pytest.mark.asyncio
    async def test_resolved_provider_not_mounted_is_refused(self, caplog) -> None:
        """A candidate naming a provider that is not mounted here is refused."""
        session_provider = _make_vendor_provider("anthropic")
        providers = {"anthropic-sonnet": session_provider}

        resolver = _make_resolver(
            return_value=[
                ProviderPreference(provider="gemini", model="flash", config={}),
            ]
        )
        hook = _make_hook(
            providers=providers,
            model_role_resolver=resolver,
            model_role="fast",
        )

        with caplog.at_level("WARNING"):
            await hook._call_provider("name this session", "session-unmounted")

        assert session_provider.complete.called
        assert session_provider.complete.call_args[0][0].model is None
        warnings = [r.getMessage() for r in caplog.records if r.levelno >= 30]
        assert any("gemini" in m for m in warnings), (
            "The warning must name the unmounted provider that was refused"
        )

    @pytest.mark.asyncio
    async def test_unpinned_session_uses_priority_not_dict_order(self) -> None:
        """Unpinned selection follows the orchestrator's priority rule.

        The mount dict lists openai first; anthropic carries the better
        (lower) priority, so the conversation is answered by anthropic — and
        so must naming be. ``next(iter(providers))`` would have picked openai.
        """
        openai_provider = _make_vendor_provider("openai", priority=100)
        anthropic_provider = _make_vendor_provider("anthropic", priority=10)
        providers = {
            "openai-gpt-5": openai_provider,
            "anthropic-sonnet": anthropic_provider,
        }

        hook = _make_hook(providers=providers)
        await hook._call_provider("name this session", "session-priority")

        assert anthropic_provider.complete.called, (
            "Naming must follow the same priority rule the orchestrator uses "
            "to pick the conversation provider"
        )
        assert not openai_provider.complete.called

    @pytest.mark.asyncio
    async def test_stale_pin_refuses_instead_of_borrowing(self, caplog) -> None:
        """A pin whose provider is gone must skip naming, not pick another."""
        openai_provider = _make_vendor_provider("openai")
        providers = {"openai-gpt-5": openai_provider}

        hook = _make_hook(providers=providers, provider_pin="anthropic-sonnet")

        with caplog.at_level("WARNING"):
            result = await hook._call_provider("name this session", "session-stale")

        assert result is None
        assert not openai_provider.complete.called, (
            "A stale pin must never fall through to whatever else is mounted"
        )
        warnings = [r.getMessage() for r in caplog.records if r.levelno >= 30]
        assert any("anthropic-sonnet" in m for m in warnings)

    @pytest.mark.asyncio
    async def test_cross_provider_refusal_warns_once_per_session(
        self, caplog
    ) -> None:
        """The refusal warning fires once per session, then drops to DEBUG."""
        providers = {
            "openai-gpt-5": _make_vendor_provider("openai"),
            "anthropic-sonnet": _make_vendor_provider("anthropic"),
        }
        resolver = _make_resolver(
            return_value=[
                ProviderPreference(provider="openai", model="gpt-5-mini", config={}),
            ]
        )
        hook = _make_hook(
            providers=providers,
            model_role_resolver=resolver,
            model_role="fast",
            provider_pin="anthropic-sonnet",
        )

        with caplog.at_level("DEBUG"):
            await hook._call_provider("name this session", "session-repeat")
            first = [r for r in caplog.records if r.levelno >= 30]
            caplog.clear()
            await hook._call_provider("name this session", "session-repeat")
            second = [r for r in caplog.records if r.levelno >= 30]
            second_debug = [r for r in caplog.records if r.levelno == 10]

        assert first, "First refusal in a session must warn"
        assert not second, "Second refusal in the SAME session must not re-warn"
        assert second_debug, "Repeat refusals must still be logged at DEBUG"


# =============================================================================
# Attribution: naming's own llm:* events must be distinguishable from root work
# =============================================================================


class _EmittingProvider:
    """A provider that emits llm:* the way real providers do.

    Real providers emit through ``self.coordinator.hooks.emit`` — an attribute
    read bound to their own instance — which is why attribution has to happen
    on a provider view rather than via a forwarding proxy.
    """

    def __init__(self, vendor: str = "anthropic") -> None:
        self.coordinator = None
        self._vendor = vendor
        self.complete_calls: list = []

    def get_info(self):
        return SimpleNamespace(id=self._vendor)

    async def complete(self, request, **kwargs):
        self.complete_calls.append(request)
        await self.coordinator.hooks.emit(
            "llm:request", {"provider": self._vendor, "model": "test-model"}
        )
        await self.coordinator.hooks.emit(
            "llm:response",
            {"provider": self._vendor, "model": "test-model", "status": "ok"},
        )
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    text='{"action": "set", "name": "N", "description": "D"}'
                )
            ]
        )


def _llm_events(coordinator) -> list[tuple[str, dict]]:
    """(event, data) pairs for llm:* events emitted on a coordinator mock."""
    return [
        (call.args[0], call.args[1])
        for call in coordinator.hooks.emit.call_args_list
        if call.args and str(call.args[0]).startswith("llm:")
    ]


class TestNamingEventAttribution:
    """The hook's own LLM calls must never look like the root agent's.

    Providers write llm:request/llm:response into the SESSION's event stream,
    and the kernel stamps session_id/parent_id defaults onto every event
    (amplifier_core/session.py: set_default_fields(session_id, parent_id)).
    Pre-fix, a naming call was therefore recorded with parent_id: null and no
    marker at all — 321 such responses were counted as root agent work by
    every scorer in the model_performance program.
    """

    @pytest.mark.asyncio
    async def test_naming_llm_events_carry_purpose_marker(self) -> None:
        """Every llm:* event a naming call emits carries data.purpose."""
        provider = _EmittingProvider()
        hook = _make_hook(providers={"anthropic-sonnet": provider})
        provider.coordinator = hook.coordinator

        result = await hook._call_provider("name this session", "session-attr")
        assert result is not None

        events = _llm_events(hook.coordinator)
        assert [name for name, _ in events] == ["llm:request", "llm:response"], (
            "The naming call must actually have emitted provider events"
        )
        for name, data in events:
            assert data.get("purpose") == "session-naming", (
                f"{name} emitted by session naming must be excludable by a "
                f"scorer; got {data!r}"
            )
            assert data.get("origin_module") == "hooks-session-naming"

    @pytest.mark.asyncio
    async def test_original_provider_is_not_mutated(self) -> None:
        """Foreground calls through the same provider stay unstamped.

        The stamp must live on a naming-only view. If it were applied to the
        shared provider instance, the root agent's own events would start
        claiming to be session naming — the same attribution bug, inverted.
        """
        provider = _EmittingProvider()
        hook = _make_hook(providers={"anthropic-sonnet": provider})
        root_coordinator = hook.coordinator
        provider.coordinator = root_coordinator

        await hook._call_provider("name this session", "session-attr")

        assert provider.coordinator is root_coordinator, (
            "The shared provider instance must be left exactly as it was"
        )

        root_coordinator.hooks.emit.reset_mock()
        await provider.complete(object())
        for _, data in _llm_events(root_coordinator):
            assert "purpose" not in data, (
                "A non-naming call through the same provider must not be "
                "stamped as session naming"
            )

    @pytest.mark.asyncio
    async def test_stamped_view_is_built_once_per_provider(self) -> None:
        """Providers create SDK clients lazily; don't build a view per turn."""
        provider = _EmittingProvider()
        hook = _make_hook(providers={"anthropic-sonnet": provider})
        provider.coordinator = hook.coordinator

        await hook._call_provider("name this session", "session-attr")
        await hook._call_provider("name this session", "session-attr")

        assert len(hook._stamped_providers) == 1
        assert hook._stamped_provider(provider) is hook._stamped_provider(provider)

    @pytest.mark.asyncio
    async def test_unstampable_provider_skips_rather_than_leaks(
        self, caplog
    ) -> None:
        """If events cannot be stamped, skip the call — loudly.

        An unattributable naming call is worse than a missing session name:
        it silently contaminates whatever reads the event stream.
        """

        class _FrozenProvider:
            """Read-only ``coordinator`` — the copy cannot be re-pointed."""

            def __init__(self) -> None:
                self._coordinator = None
                self.complete_calls: list = []

            @property
            def coordinator(self):
                return self._coordinator

            def get_info(self):
                return SimpleNamespace(id="anthropic")

            async def complete(self, request, **kwargs):  # pragma: no cover
                self.complete_calls.append(request)
                raise AssertionError("must not be called")

        provider = _FrozenProvider()
        hook = _make_hook(providers={"anthropic-sonnet": provider})
        provider._coordinator = hook.coordinator

        with caplog.at_level("WARNING"):
            result = await hook._call_provider("name this session", "session-frozen")

        assert result is None
        assert not provider.complete_calls, (
            "Must not issue the call at all when its events cannot be stamped"
        )
        warnings = [r.getMessage() for r in caplog.records if r.levelno >= 30]
        assert any("session-naming" in m for m in warnings), (
            "Skipping for lack of attribution must be loud, not silent"
        )

    @pytest.mark.asyncio
    async def test_provider_without_coordinator_still_names(self) -> None:
        """A provider that emits nothing has nothing to leak — don't skip it."""
        provider = _make_mock_provider()
        provider.coordinator = None
        hook = _make_hook(providers={"provider-1": provider})

        result = await hook._call_provider("name this session", "session-none")

        assert result is not None
        assert provider.complete.called


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


# =============================================================================
# Truncated-response parsing (token-cap resilience)
# =============================================================================


class TestParseResponseTruncation:
    """_parse_response must survive a naming response truncated at the token cap.

    The naming call caps output tokens. The requested JSON is
    ``{"action", "name", "description"}`` with ``description`` last and
    free-text, so an over-long description truncates the response mid-string
    and yields invalid JSON. The parser salvages the completed ``action`` and
    ``name`` fields instead of discarding the whole response, and never emits
    a user-facing warning for a best-effort background chore.
    """

    def test_full_valid_response_parses(self) -> None:
        hook = _make_hook()
        result = hook._parse_response(
            '{"action": "set", "name": "My Session", "description": "A test."}'
        )
        assert result == {
            "action": "set",
            "name": "My Session",
            "description": "A test.",
        }

    def test_truncated_in_description_salvages_name(self) -> None:
        hook = _make_hook()
        result = hook._parse_response(
            '{"action": "set", "name": "My Session", '
            '"description": "A very long description that ran past the token c'
        )
        assert result == {"action": "set", "name": "My Session"}

    def test_truncated_in_name_is_clean_miss(self) -> None:
        hook = _make_hook()
        result = hook._parse_response('{"action": "set", "name": "My Sess')
        assert result is None

    def test_truncation_emits_no_warning(self, caplog) -> None:
        hook = _make_hook()
        with caplog.at_level("DEBUG"):
            hook._parse_response(
                '{"action": "set", "name": "My Session", "description": "trunc'
            )
        warnings = [r for r in caplog.records if r.levelno >= 30]  # WARNING+
        assert not warnings, f"unexpected warning(s): {[r.getMessage() for r in warnings]}"

    def test_markdown_wrapped_response_parses(self) -> None:
        hook = _make_hook()
        result = hook._parse_response(
            '```json\n{"action": "set", "name": "X Y", "description": "z"}\n```'
        )
        assert result == {"action": "set", "name": "X Y", "description": "z"}
