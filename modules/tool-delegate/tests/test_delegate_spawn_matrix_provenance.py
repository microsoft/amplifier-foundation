"""Matrix provenance on spawn telemetry (``delegate:agent_spawned``).

WHAT THIS PROTECTS. ``delegate:agent_spawned`` has always recorded the
``provider_preferences`` a delegation resolved to, but never WHICH routing
matrix file produced them. A user file in ``~/.amplifier/routing/`` silently
outranks the bundle's own same-named matrix, so a surprising resolution in
the event stream was indistinguishable from a shadowed matrix, a shipped
matrix change, or no routing at all.

THE RESOLVER IS DUCK-TYPED AND ITS PROVENANCE ATTRIBUTES ARE OPTIONAL.
amplifier-foundation does not depend on any routing bundle -- that is the
point of the ``model_role_resolver`` capability -- so these tests use a
stand-in that mirrors, attribute for attribute, what hooks-routing's
``MatrixModelRoleResolver`` publishes:

    matrix_path:     str | None   -- WHICH FILE is actually running
    matrix_source:   str | None   -- "user" | "bundle"
    shadowed_paths:  tuple[str, ...] -- every same-named file it outranked

Absent/None means "this strategy does not report a source", NOT "no
shadowing" -- an alternate strategy (cost-aware, latency-aware) or an older
routing bundle has no notion of a matrix file at all. The tests below pin
that distinction, because collapsing it is precisely the failure that would
silently clear a shadowed session.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from amplifier_foundation.spawn_utils import ProviderPreference
from amplifier_module_tool_delegate import DelegateTool, _matrix_provenance

# =============================================================================
# Helpers
# =============================================================================

USER_MATRIX = "/home/u/.amplifier/routing/anthropic.yaml"
BUNDLE_MATRIX = "/opt/bundles/routing-matrix/routing/anthropic.yaml"


class _FakeMatrixResolver:
    """Stand-in for hooks-routing's ``MatrixModelRoleResolver``.

    Mirrors the published attribute contract exactly. ``_unset`` sentinels
    let a test model an OLDER routing bundle (or an alternate strategy) that
    never defines these attributes at all -- distinct from defining them as
    ``None``.
    """

    _UNSET = object()

    def __init__(
        self,
        *,
        name: str = "anthropic",
        matrix_path: Any = _UNSET,
        matrix_source: Any = _UNSET,
        shadowed_paths: Any = _UNSET,
        resolves_to: list[ProviderPreference] | None = None,
    ) -> None:
        self.name = name
        if matrix_path is not self._UNSET:
            self.matrix_path = matrix_path
        if matrix_source is not self._UNSET:
            self.matrix_source = matrix_source
        if shadowed_paths is not self._UNSET:
            self.shadowed_paths = shadowed_paths
        self._resolves_to = resolves_to if resolves_to is not None else []

    async def resolve(self, model_role: str) -> list[ProviderPreference]:
        return list(self._resolves_to)


def _shadowed_resolver(**kw: Any) -> _FakeMatrixResolver:
    """A user file in ~/.amplifier/routing/ shadowing the shipped matrix."""
    return _FakeMatrixResolver(
        matrix_path=USER_MATRIX,
        matrix_source="user",
        shadowed_paths=(BUNDLE_MATRIX,),
        **kw,
    )


def _bundle_resolver(**kw: Any) -> _FakeMatrixResolver:
    """The shipped matrix, nothing shadowed."""
    return _FakeMatrixResolver(
        matrix_path=BUNDLE_MATRIX,
        matrix_source="bundle",
        shadowed_paths=(),
        **kw,
    )


def _make_hooks() -> MagicMock:
    hooks = MagicMock()
    hooks.emit = AsyncMock()
    return hooks


def _make_tool(
    *,
    hooks: MagicMock,
    model_role_resolver: Any = None,
    agents: dict | None = None,
) -> DelegateTool:
    coordinator = MagicMock()
    coordinator.session_id = "parent-session-123"
    coordinator.config = {"agents": agents or {"test-agent": {"description": "t"}}}
    coordinator.session_state = {}

    capabilities: dict = {
        "session.spawn": AsyncMock(
            return_value={
                "output": "done",
                "session_id": "child-001",
                "status": "success",
                "turn_count": 1,
                "metadata": {},
            }
        ),
        "session.resume": AsyncMock(return_value={}),
        "self_delegation_depth": 0,
        "model_role_resolver": model_role_resolver,
    }
    coordinator.get_capability = lambda name: capabilities.get(name)

    # coordinator.get("hooks") -> hooks; coordinator.get("providers") -> {}
    coordinator.get = MagicMock(
        side_effect=lambda key: hooks if key == "hooks" else None
    )

    parent_session = MagicMock()
    parent_session.session_id = "parent-session-123"
    parent_session.config = {"session": {"orchestrator": {}}}
    coordinator.session = parent_session

    return DelegateTool(
        coordinator, {"features": {}, "settings": {"exclude_tools": []}}
    )


def _emitted(hooks: MagicMock, event: str) -> list[dict]:
    return [args[1] for args, _ in hooks.emit.call_args_list if args[0] == event]


async def _spawn(tool: DelegateTool, **overrides: Any) -> None:
    payload: dict[str, Any] = {
        "agent": "test-agent",
        "instruction": "do a thing",
        "context_depth": "none",
    }
    payload.update(overrides)
    await tool.execute(payload)


# =============================================================================
# _matrix_provenance -- the pure read-what-ell-publishes function
# =============================================================================


class TestMatrixProvenanceReader:
    def test_none_resolver_reports_nothing(self):
        assert _matrix_provenance(None) is None

    def test_resolver_without_provenance_attrs_reports_nothing(self):
        """An older routing bundle, or a non-matrix strategy.

        Must be None (-> key omitted), never a dict of nulls: a dict would
        read as the positive claim "we looked, there is no shadowing".
        """
        assert _matrix_provenance(_FakeMatrixResolver()) is None

    def test_explicit_none_attrs_report_nothing(self):
        resolver = _FakeMatrixResolver(
            matrix_path=None, matrix_source=None, shadowed_paths=()
        )
        assert _matrix_provenance(resolver) is None

    def test_shadowed_resolver_is_read_verbatim(self):
        assert _matrix_provenance(_shadowed_resolver()) == {
            "matrix_name": "anthropic",
            "matrix_path": USER_MATRIX,
            "matrix_source": "user",
            "shadowed_paths": [BUNDLE_MATRIX],
        }

    def test_bundle_resolver_reports_empty_shadow_list(self):
        assert _matrix_provenance(_bundle_resolver()) == {
            "matrix_name": "anthropic",
            "matrix_path": BUNDLE_MATRIX,
            "matrix_source": "bundle",
            "shadowed_paths": [],
        }

    def test_string_shadowed_paths_is_not_iterated_character_wise(self):
        """A str is a sequence -- iterating one yields letters, silently.

        A malformed strategy publishing a bare str must degrade to "no
        shadowed paths reported", never to ['/', 'o', 'p', 't', ...].
        """
        resolver = _FakeMatrixResolver(
            matrix_path=USER_MATRIX,
            matrix_source="user",
            shadowed_paths=BUNDLE_MATRIX,  # a str, not a tuple
        )
        result = _matrix_provenance(resolver)
        assert result is not None
        assert result["shadowed_paths"] == []

    def test_non_string_entries_are_dropped_not_coerced(self):
        resolver = _FakeMatrixResolver(
            matrix_path=USER_MATRIX,
            matrix_source="user",
            shadowed_paths=(BUNDLE_MATRIX, None, 42, ""),
        )
        result = _matrix_provenance(resolver)
        assert result is not None
        assert result["shadowed_paths"] == [BUNDLE_MATRIX]

    def test_shadowing_alone_is_enough_to_report(self):
        """path/source absent but shadowing known -> still reportable."""
        resolver = _FakeMatrixResolver(shadowed_paths=(BUNDLE_MATRIX,))
        assert _matrix_provenance(resolver) == {
            "matrix_name": "anthropic",
            "matrix_path": None,
            "matrix_source": None,
            "shadowed_paths": [BUNDLE_MATRIX],
        }


# =============================================================================
# delegate:agent_spawned -- the spawn telemetry record
# =============================================================================


class TestSpawnRecordsMatrixProvenance:
    @pytest.mark.asyncio
    async def test_spawn_records_the_matrix_that_produced_its_preferences(self):
        prefs = [ProviderPreference(provider="anthropic", model="claude-haiku-3.5")]
        hooks = _make_hooks()
        tool = _make_tool(
            hooks=hooks, model_role_resolver=_bundle_resolver(resolves_to=prefs)
        )

        await _spawn(tool, model_role="fast")

        payload = _emitted(hooks, "delegate:agent_spawned")[0]
        # The preferences and the file that produced them, in one record.
        assert payload["provider_preferences"] == [p.to_dict() for p in prefs]
        assert payload["routing_matrix"]["matrix_path"] == BUNDLE_MATRIX
        assert payload["routing_matrix"]["matrix_source"] == "bundle"
        assert payload["routing_matrix"]["matrix_name"] == "anthropic"

    @pytest.mark.asyncio
    async def test_shadowed_load_records_the_shadowing_file_not_the_shipped_one(self):
        """THE headline case.

        When a user file outranks the shipped matrix, the spawn record must
        name the USER file as the matrix in effect. The shipped file appears
        only as something that was shadowed -- never as the answer to "which
        matrix produced these preferences".
        """
        prefs = [ProviderPreference(provider="openai", model="gpt-5.6-sol")]
        hooks = _make_hooks()
        tool = _make_tool(
            hooks=hooks, model_role_resolver=_shadowed_resolver(resolves_to=prefs)
        )

        await _spawn(tool, model_role="fast")

        matrix = _emitted(hooks, "delegate:agent_spawned")[0]["routing_matrix"]
        assert matrix["matrix_path"] == USER_MATRIX, (
            "spawn recorded the shipped matrix while a user file was in effect"
        )
        assert matrix["matrix_source"] == "user"
        assert matrix["shadowed_paths"] == [BUNDLE_MATRIX]
        # The shipped file must not be presented as the effective matrix.
        assert matrix["matrix_path"] != BUNDLE_MATRIX

    @pytest.mark.asyncio
    async def test_unresolved_role_still_records_which_matrix_failed(self):
        """Silent-substitution event names the matrix that had no candidate."""
        hooks = _make_hooks()
        tool = _make_tool(
            hooks=hooks, model_role_resolver=_shadowed_resolver(resolves_to=[])
        )

        await _spawn(tool, model_role="fast")

        payload = _emitted(hooks, "delegate:model_role_unresolved")[0]
        assert payload["routing_matrix"]["matrix_path"] == USER_MATRIX
        assert payload["routing_matrix"]["shadowed_paths"] == [BUNDLE_MATRIX]


# =============================================================================
# Default behaviour byte-identical / backward compatibility
# =============================================================================

# Every key delegate:agent_spawned carried BEFORE this change. Pinned as a
# literal so a rename or drop fails here rather than silently breaking every
# analyzer reading the event stream.
_PRE_EXISTING_SPAWN_KEYS = {
    "agent",
    "sub_session_id",
    "parent_session_id",
    "context_depth",
    "context_scope",
    "tool_call_id",
    "parallel_group_id",
    "model_role",
    "provider_preferences",
}


class TestDefaultBehaviourUnchanged:
    @pytest.mark.asyncio
    async def test_no_model_role_payload_is_byte_identical(self):
        """The overwhelmingly common spawn: no routing requested at all."""
        hooks = _make_hooks()
        tool = _make_tool(hooks=hooks, model_role_resolver=_shadowed_resolver())

        await _spawn(tool)

        payload = _emitted(hooks, "delegate:agent_spawned")[0]
        assert set(payload) == _PRE_EXISTING_SPAWN_KEYS
        assert "routing_matrix" not in payload

    @pytest.mark.asyncio
    async def test_no_routing_bundle_installed_payload_is_byte_identical(self):
        hooks = _make_hooks()
        tool = _make_tool(hooks=hooks, model_role_resolver=None)

        await _spawn(tool, model_role="fast")

        payload = _emitted(hooks, "delegate:agent_spawned")[0]
        assert set(payload) == _PRE_EXISTING_SPAWN_KEYS

    @pytest.mark.asyncio
    async def test_resolver_without_provenance_payload_is_byte_identical(self):
        """An older routing bundle predating the published attributes."""
        prefs = [ProviderPreference(provider="anthropic", model="claude-haiku-3.5")]
        hooks = _make_hooks()
        tool = _make_tool(
            hooks=hooks,
            model_role_resolver=_FakeMatrixResolver(resolves_to=prefs),
        )

        await _spawn(tool, model_role="fast")

        payload = _emitted(hooks, "delegate:agent_spawned")[0]
        assert set(payload) == _PRE_EXISTING_SPAWN_KEYS
        assert payload["provider_preferences"] == [p.to_dict() for p in prefs]

    @pytest.mark.asyncio
    async def test_explicit_pin_records_no_matrix(self):
        """An explicit provider_preferences pin never consults the matrix.

        Recording a matrix here would be a lie: the resolver was not asked,
        so no matrix produced these preferences.
        """
        hooks = _make_hooks()
        tool = _make_tool(hooks=hooks, model_role_resolver=_shadowed_resolver())

        await _spawn(
            tool,
            model_role="fast",
            provider_preferences=[{"provider": "openai", "model": "gpt-5.6-terra"}],
        )

        payload = _emitted(hooks, "delegate:agent_spawned")[0]
        assert "routing_matrix" not in payload
        assert payload["provider_preferences"] == [
            ProviderPreference(provider="openai", model="gpt-5.6-terra").to_dict()
        ]

    @pytest.mark.asyncio
    async def test_pre_existing_keys_are_unchanged_when_matrix_is_recorded(self):
        """Additive means additive: no existing key's name or value moves."""
        prefs = [ProviderPreference(provider="anthropic", model="claude-haiku-3.5")]
        hooks = _make_hooks()
        tool = _make_tool(
            hooks=hooks, model_role_resolver=_shadowed_resolver(resolves_to=prefs)
        )

        await _spawn(tool, model_role="fast")

        payload = _emitted(hooks, "delegate:agent_spawned")[0]
        assert set(payload) == _PRE_EXISTING_SPAWN_KEYS | {"routing_matrix"}
        assert payload["agent"] == "test-agent"
        assert payload["model_role"] == "fast"
        assert payload["provider_preferences"] == [p.to_dict() for p in prefs]

    @pytest.mark.asyncio
    async def test_old_capture_reader_pattern_still_works(self):
        """An analyzer reading OLD captures must not crash and must not
        conclude 'no shadowing' from an absent field.

        Simulates the two capture generations side by side: every capture on
        disk today lacks the key entirely.
        """
        old_capture_payload = {
            "agent": "explorer",
            "model_role": "fast",
            "provider_preferences": [{"provider": "openai", "model": "gpt-5.6-sol"}],
        }

        hooks = _make_hooks()
        tool = _make_tool(
            hooks=hooks,
            model_role_resolver=_shadowed_resolver(
                resolves_to=[ProviderPreference(provider="openai", model="gpt-5.6-sol")]
            ),
        )
        await _spawn(tool, model_role="fast")
        new_capture_payload = _emitted(hooks, "delegate:agent_spawned")[0]

        def matrix_of(payload: dict) -> str:
            """The correct reader: absent -> UNKNOWN, never 'not shadowed'."""
            matrix = payload.get("routing_matrix")
            if matrix is None:
                return "unknown"
            return "shadowed" if matrix["shadowed_paths"] else "clean"

        assert matrix_of(old_capture_payload) == "unknown"
        assert matrix_of(new_capture_payload) == "shadowed"
