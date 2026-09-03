"""Resume must carry the caller's routing, not silently drop it.

Measured defect (rc0, on the wire): a delegate spawned with an explicit
``model_role`` loses that role the moment it is resumed. Two independent
sites had to be fixed; this file covers the amplifier-foundation one.

The tool-delegate resume path called the app layer's ``session.resume``
capability with ``(sub_session_id, instruction)`` ONLY -- neither
``provider_preferences`` nor ``model_role`` crossed the seam, so the app
layer never had the values it needed to promote the caller's provider on
the resumed leg. The app-layer half (amplifier-app-cli #292) already
accepts both kwargs; this repo simply never sent them.

"Wire" at this repo's boundary is the ``session.resume`` capability call:
that call IS the request tool-delegate makes of the app layer, and its
kwargs are what the app layer turns into the resumed session's provider
config. ``TestResumedRequestConfig`` closes the remaining gap by driving
foundation's own ``apply_provider_preferences`` with what crossed the
seam, asserting the resumed leg's effective provider/model -- the thing
the first LLM request is actually built from -- matches the spawn leg's.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from amplifier_module_tool_delegate import DelegateTool

from amplifier_foundation.spawn_utils import (
    ProviderPreference,
    apply_provider_preferences,
)

# =============================================================================
# Helpers
# =============================================================================


def _resume_result(session_id: str = "child-001") -> dict:
    return {
        "output": "resumed",
        "session_id": session_id,
        "status": "success",
        "turn_count": 2,
        "metadata": {},
    }


def _spawn_result(**kwargs) -> dict:
    return {
        "output": "spawned",
        "session_id": kwargs.get("sub_session_id", "child-001"),
        "status": "success",
        "turn_count": 1,
        "metadata": {},
    }


def _make_delegate_tool(
    *,
    spawn_fn=None,
    resume_fn=None,
    agents: dict | None = None,
    model_role_resolver=None,
    settings: dict | None = None,
) -> DelegateTool:
    """DelegateTool over a mocked coordinator exposing spawn + resume."""
    coordinator = MagicMock()
    coordinator.session_id = "parent-session-123"
    coordinator.config = {"agents": agents or {}}
    coordinator.session_state = {}

    capabilities: dict = {
        "session.spawn": spawn_fn
        or AsyncMock(side_effect=lambda **kw: _spawn_result(**kw)),
        "session.resume": resume_fn or AsyncMock(return_value=_resume_result()),
        "agents.list": lambda: agents or {},
        "agents.get": lambda name: (agents or {}).get(name),
        "self_delegation_depth": 0,
        "model_role_resolver": model_role_resolver,
    }

    coordinator.get_capability = lambda name: capabilities.get(name)
    coordinator.get = MagicMock(return_value=None)  # hooks = None

    parent_session = MagicMock()
    parent_session.session_id = "parent-session-123"
    parent_session.config = {"session": {"orchestrator": {}}}
    coordinator.session = parent_session

    config: dict = {
        "features": {},
        "settings": {"exclude_tools": [], **(settings or {})},
    }
    return DelegateTool(coordinator, config)


def _make_resolver(*, return_value: list | None = None, name: str = "test-matrix"):
    resolver = MagicMock()
    resolver.name = name
    resolver.resolve = AsyncMock(
        return_value=return_value if return_value is not None else []
    )
    return resolver


_REASONING = ProviderPreference(provider="anthropic", model="claude-opus-4")
_FAST = ProviderPreference(provider="anthropic", model="claude-haiku-4")


# =============================================================================
# The caller states a role ON the resume call
# =============================================================================


class TestResumeThreadsExplicitRouting:
    @pytest.mark.asyncio
    async def test_resume_sends_model_role_to_resume_capability(self):
        """An explicit model_role on a resume call must reach session.resume."""
        resume_fn = AsyncMock(return_value=_resume_result())
        tool = _make_delegate_tool(
            resume_fn=resume_fn,
            model_role_resolver=_make_resolver(return_value=[_REASONING]),
        )

        await tool.execute(
            {
                "session_id": "child-001",
                "instruction": "Continue",
                "model_role": "reasoning",
            }
        )

        _, kwargs = resume_fn.call_args
        assert kwargs.get("model_role") == "reasoning", (
            "resume path dropped the caller's model_role before it reached the app layer"
        )

    @pytest.mark.asyncio
    async def test_resume_sends_resolved_preferences_to_resume_capability(self):
        """The preferences the role resolved to must reach session.resume too."""
        resume_fn = AsyncMock(return_value=_resume_result())
        tool = _make_delegate_tool(
            resume_fn=resume_fn,
            model_role_resolver=_make_resolver(return_value=[_REASONING]),
        )

        await tool.execute(
            {
                "session_id": "child-001",
                "instruction": "Continue",
                "model_role": "reasoning",
            }
        )

        _, kwargs = resume_fn.call_args
        assert kwargs.get("provider_preferences") == [_REASONING]

    @pytest.mark.asyncio
    async def test_resume_sends_explicit_provider_preferences(self):
        """An explicit provider_preferences pin on resume must reach the app layer."""
        resume_fn = AsyncMock(return_value=_resume_result())
        tool = _make_delegate_tool(resume_fn=resume_fn)

        await tool.execute(
            {
                "session_id": "child-001",
                "instruction": "Continue",
                "provider_preferences": [{"provider": "openai", "model": "gpt-5"}],
            }
        )

        _, kwargs = resume_fn.call_args
        assert kwargs.get("provider_preferences") == [
            ProviderPreference(provider="openai", model="gpt-5")
        ]


# =============================================================================
# The caller stated the role at SPAWN and says nothing on resume
# =============================================================================


class TestResumeInheritsSpawnRouting:
    @pytest.mark.asyncio
    async def test_resume_reuses_spawn_time_routing_when_caller_omits_it(self):
        """Acceptance criterion: a session spawned with a role keeps that role on resume.

        The caller resumes with (session_id, instruction) only -- the shape
        every existing caller uses -- so the routing must come from what
        THIS tool recorded at spawn time.
        """
        spawn_fn = AsyncMock(side_effect=lambda **kw: _spawn_result(**kw))
        resume_fn = AsyncMock(return_value=_resume_result())
        tool = _make_delegate_tool(
            spawn_fn=spawn_fn,
            resume_fn=resume_fn,
            agents={"explorer": {"description": "d"}},
            model_role_resolver=_make_resolver(return_value=[_REASONING]),
        )

        spawned = await tool.execute(
            {
                "agent": "explorer",
                "instruction": "Investigate",
                "context_depth": "none",
                "model_role": "reasoning",
            }
        )
        session_id = spawned.output["session_id"]

        await tool.execute({"session_id": session_id, "instruction": "Keep going"})

        _, spawn_kwargs = spawn_fn.call_args
        _, resume_kwargs = resume_fn.call_args
        assert (
            resume_kwargs.get("provider_preferences")
            == spawn_kwargs["provider_preferences"]
        )
        assert resume_kwargs.get("model_role") == "reasoning"

    @pytest.mark.asyncio
    async def test_explicit_resume_role_overrides_the_spawn_time_role(self):
        """A role stated on the resume call wins over the one recorded at spawn."""
        spawn_fn = AsyncMock(side_effect=lambda **kw: _spawn_result(**kw))
        resume_fn = AsyncMock(return_value=_resume_result())
        resolver = _make_resolver(return_value=[_REASONING])
        tool = _make_delegate_tool(
            spawn_fn=spawn_fn,
            resume_fn=resume_fn,
            agents={"explorer": {"description": "d"}},
            model_role_resolver=resolver,
        )

        spawned = await tool.execute(
            {
                "agent": "explorer",
                "instruction": "Investigate",
                "context_depth": "none",
                "model_role": "reasoning",
            }
        )
        session_id = spawned.output["session_id"]

        resolver.resolve = AsyncMock(return_value=[_FAST])
        await tool.execute(
            {
                "session_id": session_id,
                "instruction": "Just summarise",
                "model_role": "fast",
            }
        )

        _, resume_kwargs = resume_fn.call_args
        assert resume_kwargs.get("model_role") == "fast"
        assert resume_kwargs.get("provider_preferences") == [_FAST]


# =============================================================================
# What the resumed leg's request is actually built from
# =============================================================================


class TestResumedRequestConfig:
    @pytest.mark.asyncio
    async def test_resumed_leg_resolves_to_the_same_provider_and_model_as_spawn(self):
        """Drive the real promotion with what crossed the seam.

        ``apply_provider_preferences`` is the same foundation function the
        app layer calls to build a child session's provider config, so the
        promoted provider/model here is what the resumed session's first
        LLM request would be issued against.
        """
        mount_plan = {
            "providers": [
                {
                    "module": "provider-openai",
                    "config": {"default_model": "gpt-5-mini", "priority": 0},
                },
                {
                    "module": "provider-anthropic",
                    "config": {"default_model": "claude-haiku-4", "priority": 1},
                },
            ]
        }
        effective: dict[str, dict] = {}

        def _record(leg: str, preferences):
            # Lower priority number wins (see _apply_single_override).
            plan = apply_provider_preferences(mount_plan, list(preferences or []))
            top = min(plan["providers"], key=lambda p: p["config"].get("priority", 99))
            effective[leg] = {
                "module": top["module"],
                "model": top["config"]["default_model"],
            }

        async def spawn_capability(**kw):
            _record("spawn", kw.get("provider_preferences"))
            return _spawn_result(**kw)

        async def resume_capability(
            sub_session_id: str,
            instruction: str,
            provider_preferences: list | None = None,
            model_role: str | list[str] | None = None,
        ):
            _record("resume", provider_preferences)
            return _resume_result(sub_session_id)

        tool = _make_delegate_tool(
            spawn_fn=spawn_capability,
            resume_fn=resume_capability,
            agents={"explorer": {"description": "d"}},
            model_role_resolver=_make_resolver(return_value=[_REASONING]),
        )

        spawned = await tool.execute(
            {
                "agent": "explorer",
                "instruction": "Investigate",
                "context_depth": "none",
                "model_role": "reasoning",
            }
        )
        await tool.execute(
            {"session_id": spawned.output["session_id"], "instruction": "Keep going"}
        )

        assert effective["spawn"] == {
            "module": "provider-anthropic",
            "model": "claude-opus-4",
        }
        assert effective["resume"] == effective["spawn"], (
            "the resumed leg would issue its first request against a different "
            f"provider/model than the spawn leg: {effective}"
        )


# =============================================================================
# Backward compatibility: don't crash, don't drop silently
# =============================================================================


class TestResumeCapabilityCompatibility:
    @pytest.mark.asyncio
    async def test_legacy_two_argument_resume_capability_still_works(self):
        """An app layer that predates the routing kwargs must not start crashing."""
        seen: dict = {}

        async def legacy_resume(sub_session_id: str, instruction: str):
            seen["sub_session_id"] = sub_session_id
            seen["instruction"] = instruction
            return _resume_result(sub_session_id)

        tool = _make_delegate_tool(
            resume_fn=legacy_resume,
            model_role_resolver=_make_resolver(return_value=[_REASONING]),
        )

        result = await tool.execute(
            {
                "session_id": "child-001",
                "instruction": "Continue",
                "model_role": "reasoning",
            }
        )

        assert result.success is True
        assert seen["sub_session_id"] == "child-001"

    @pytest.mark.asyncio
    async def test_legacy_capability_drop_is_logged_not_silent(self, caplog):
        """Dropping the caller's routing must be loud -- silence is the original bug."""

        async def legacy_resume(sub_session_id: str, instruction: str):
            return _resume_result(sub_session_id)

        tool = _make_delegate_tool(
            resume_fn=legacy_resume,
            model_role_resolver=_make_resolver(return_value=[_REASONING]),
        )

        with caplog.at_level(logging.WARNING, logger="amplifier_module_tool_delegate"):
            await tool.execute(
                {
                    "session_id": "child-001",
                    "instruction": "Continue",
                    "model_role": "reasoning",
                }
            )

        assert any("model_role" in rec.getMessage() for rec in caplog.records), (
            "expected a warning naming the routing that could not be threaded"
        )

    @pytest.mark.asyncio
    async def test_plain_resume_sends_no_routing_kwargs(self):
        """With nothing to thread, the call stays exactly as it was before this fix."""
        resume_fn = AsyncMock(return_value=_resume_result())
        tool = _make_delegate_tool(resume_fn=resume_fn)

        await tool.execute({"session_id": "child-001", "instruction": "Continue"})

        _, kwargs = resume_fn.call_args
        assert set(kwargs) == {"sub_session_id", "instruction"}


# =============================================================================
# Telemetry: the resumed event must show what the resumed leg was routed to
# =============================================================================


class TestResumedEventRouting:
    @pytest.mark.asyncio
    async def test_agent_resumed_event_carries_role_and_preferences(self):
        emitted: dict = {}

        hooks = MagicMock()

        async def _emit(event, payload):
            emitted[event] = payload

        hooks.emit = _emit

        tool = _make_delegate_tool(
            model_role_resolver=_make_resolver(return_value=[_REASONING]),
        )
        tool.coordinator.get = MagicMock(return_value=hooks)

        await tool.execute(
            {
                "session_id": "child-001",
                "instruction": "Continue",
                "model_role": "reasoning",
            }
        )

        payload = emitted["delegate:agent_resumed"]
        assert payload["model_role"] == "reasoning"
        assert payload["provider_preferences"] == [_REASONING.to_dict()]
