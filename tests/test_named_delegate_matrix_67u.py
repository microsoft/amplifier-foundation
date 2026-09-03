"""Pins the answer to model_performance-67u.

Two independent questions were tangled together in the original report
("no delegate lands on the model its matrix declares"). This file separates
them and pins each, so neither gets re-filed as folklore.

QUESTION 1 -- does an EXPLICITLY-NAMED delegate bypass matrix resolution?
    No. There is no separate "named-agent" spawn path. ``DelegateTool.execute``
    has exactly ONE resolver call site, guarded by
    ``if raw_model_role and provider_preferences is None`` -- and
    ``raw_model_role`` is read ONLY from the tool input. The agent's own
    declared ``model_role`` is never consulted here. So a delegate call that
    names an agent and supplies no ``model_role`` argument resolves nothing
    and falls through to the session default, BY DESIGN (the fall-through is
    documented in-module and is opt-out via ``strict_model_role``).
    The "organic" path differs only in that the calling model put
    ``model_role`` in the tool arguments. Same code, different arguments.
    ``TestNamedDelegateIsCharacterisation`` pins this. These are
    CHARACTERIZATION tests: they assert intended behaviour, not a defect.

QUESTION 2 -- a real defect found while answering question 1.
    When several instances of ONE provider module are mounted (exactly the
    shape the routing matrix asks for, since a matrix addresses providers by
    bare module type), this file's helpers disagreed about which instance
    "anthropic" means:

        _find_provider_instance  -> highest priority
        _find_provider_index     -> first declared
        _build_provider_lookup   -> last declared (dict last-write-wins)

    ``apply_provider_preferences_with_resolution`` calls two of them in one
    pass: it resolves the model glob against the instance the FIRST picks and
    then promotes the index the THIRD returns. Right model, wrong instance --
    and silently. ``TestProviderInstanceSelectionIsConsistent`` fails before
    the fix and passes after.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from amplifier_foundation.spawn_utils import ProviderPreference
from amplifier_foundation.spawn_utils import _build_provider_lookup
from amplifier_foundation.spawn_utils import _find_provider_index
from amplifier_foundation.spawn_utils import _find_provider_instance
from amplifier_foundation.spawn_utils import apply_provider_preferences_with_resolution

# ---------------------------------------------------------------------------
# The roster that produced the original report.
#
# Verbatim shape of the eval harness's provider mounts (10 instances, 2 module
# types, distinct ``id``s, one forced to priority 0 to pin the cell). Model
# lists are trimmed to what the matrix globs in play actually need.
# ---------------------------------------------------------------------------

H7N_ROSTER: list[dict[str, Any]] = [
    {"id": "sol", "module": "provider-openai", "config": {"priority": 2}},
    {"id": "terra", "module": "provider-openai", "config": {"priority": 3}},
    {"id": "opus-4.8", "module": "provider-anthropic", "config": {"priority": 1}},
    # The forced cell: highest priority (lowest number) of any anthropic mount.
    {"id": "opus", "module": "provider-anthropic", "config": {"priority": 0}},
    {"id": "sonnet", "module": "provider-anthropic", "config": {"priority": 4}},
    {"id": "openai", "module": "provider-openai", "config": {"priority": 6}},
    {"id": "haiku", "module": "provider-anthropic", "config": {"priority": 5}},
    {"id": "fable", "module": "provider-anthropic", "config": {"priority": 7}},
    {"id": "luna", "module": "provider-openai", "config": {"priority": 8}},
    {"id": "luna-max", "module": "provider-openai", "config": {"priority": 9}},
]

ANTHROPIC_MODELS = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"]
OPENAI_MODELS = ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.6"]


def _index_of(providers: list[dict[str, Any]], instance_id: str) -> int:
    return next(i for i, p in enumerate(providers) if p["id"] == instance_id)


class _FakeProvider:
    """Provider double exposing only what pattern resolution touches."""

    def __init__(self, models: list[str]) -> None:
        self._models = list(models)

    async def list_models(self) -> list[str]:
        return list(self._models)


def _make_coordinator(
    providers: list[dict[str, Any]],
    *,
    models_by_module: dict[str, list[str]] | None = None,
) -> Any:
    """Coordinator double whose runtime providers mirror ``providers``.

    Runtime providers are keyed by instance id (how a multi-instance mount
    plan is actually keyed), and ``coordinator.config["providers"]`` carries
    the mount-plan specs that :func:`_find_provider_instance` falls back to
    when a bare module type matches no key directly.
    """
    models_by_module = models_by_module or {
        "provider-anthropic": ANTHROPIC_MODELS,
        "provider-openai": OPENAI_MODELS,
    }
    # A distinct provider object per instance so the promoted instance can be
    # told apart from the one whose model list was consulted.
    runtime = {
        p["id"]: _FakeProvider(models_by_module.get(p["module"], [])) for p in providers
    }
    coordinator = MagicMock()
    coordinator.config = {"providers": providers}
    coordinator.get = MagicMock(
        side_effect=lambda key: runtime if key == "providers" else None
    )
    return coordinator


# =============================================================================
# QUESTION 2 -- the defect
# =============================================================================


class TestProviderInstanceSelectionIsConsistent:
    """One answer to "which instance does a bare module type mean?"."""

    def test_lookup_picks_highest_priority_instance_not_last_declared(self) -> None:
        """``anthropic`` means the highest-priority anthropic mount.

        Fails before the fix: the plain-dict build made the LAST declared
        anthropic mount (``fable``, priority 7) win over the forced cell
        (``opus``, priority 0).
        """
        lookup = _build_provider_lookup(H7N_ROSTER)
        assert lookup["anthropic"] == _index_of(H7N_ROSTER, "opus")
        assert lookup["provider-anthropic"] == _index_of(H7N_ROSTER, "opus")

    def test_lookup_agrees_with_find_provider_instance(self) -> None:
        """The two helpers used in ONE pass must name the same instance.

        This is the split-brain itself: ``apply_provider_preferences_with_
        resolution`` resolves the glob against ``_find_provider_instance``'s
        pick and promotes ``_build_provider_lookup``'s index.
        """
        coordinator = _make_coordinator(H7N_ROSTER)
        runtime = coordinator.get("providers")

        for bare in ("anthropic", "openai"):
            promoted_idx = _build_provider_lookup(H7N_ROSTER)[bare]
            promoted_instance = runtime[H7N_ROSTER[promoted_idx]["id"]]
            consulted_instance = _find_provider_instance(runtime, bare, coordinator)
            assert promoted_instance is consulted_instance, (
                f"bare type {bare!r}: model list read from one instance, "
                f"promotion written to another"
            )

    def test_explicit_instance_id_beats_module_type_collision(self) -> None:
        """An id that collides with a module-type name still addresses itself.

        The roster mounts ``provider-openai`` five times and names one of
        them ``openai`` outright. ``openai`` must mean that instance.
        """
        lookup = _build_provider_lookup(H7N_ROSTER)
        assert lookup["openai"] == _index_of(H7N_ROSTER, "openai")
        # ...and every other instance still addresses itself by id.
        for provider in H7N_ROSTER:
            assert lookup[provider["id"]] == _index_of(H7N_ROSTER, provider["id"])

    def test_find_provider_index_agrees_with_lookup(self) -> None:
        """The third helper answers the same way as the other two."""
        lookup = _build_provider_lookup(H7N_ROSTER)
        for bare in ("anthropic", "openai", "provider-anthropic", "provider-openai"):
            assert _find_provider_index(H7N_ROSTER, bare) == lookup[bare]

    def test_missing_priority_keeps_declaration_order(self) -> None:
        """Plans that never set ``priority`` keep resolving first-declared."""
        providers = [
            {"id": "first", "module": "provider-anthropic", "config": {}},
            {"id": "second", "module": "provider-anthropic", "config": {}},
        ]
        assert _build_provider_lookup(providers)["anthropic"] == 0
        assert _find_provider_index(providers, "anthropic") == 0

    def test_unparseable_priority_does_not_raise(self) -> None:
        """A junk ``priority`` sorts as 0 rather than exploding the spawn."""
        providers = [
            {"id": "junk", "module": "provider-anthropic", "config": {"priority": "x"}},
            {"id": "ten", "module": "provider-anthropic", "config": {"priority": 10}},
        ]
        assert _build_provider_lookup(providers)["anthropic"] == 0

    def test_single_instance_plans_are_unchanged(self) -> None:
        """The common single-instance case keeps every key it always had."""
        providers = [
            {"module": "provider-anthropic", "config": {}},
            {"module": "provider-openai", "config": {}},
        ]
        lookup = _build_provider_lookup(providers)
        assert lookup["anthropic"] == 0
        assert lookup["provider-anthropic"] == 0
        assert lookup["openai"] == 1
        assert lookup["provider-openai"] == 1

    @pytest.mark.asyncio
    async def test_promotion_lands_on_the_instance_that_resolved_the_glob(
        self,
    ) -> None:
        """End-to-end: the promoted mount is the forced cell, not ``fable``."""
        coordinator = _make_coordinator(H7N_ROSTER)
        plan = {"providers": H7N_ROSTER}

        new_plan = await apply_provider_preferences_with_resolution(
            plan,
            [
                ProviderPreference(
                    provider="anthropic",
                    model="claude-opus-*",
                    config={"reasoning_effort": "high"},
                )
            ],
            coordinator,
        )

        promoted = [
            p for p in new_plan["providers"] if p["config"].get("priority") == 0
        ]
        assert len(promoted) == 1
        assert promoted[0]["id"] == "opus"
        assert promoted[0]["config"]["default_model"] == "claude-opus-5"
        # The matrix candidate's own config rides along with the promotion --
        # this is how a matrix effort reaches a delegate.
        assert promoted[0]["config"]["reasoning_effort"] == "high"


# =============================================================================
# The economy-matrix case -- ordered fallback, not a provider mix-up
# =============================================================================


class TestOrderedCandidateFallback:
    """Why Anthropic-glob roles landed on OpenAI models under ``economy``.

    ``economy.yaml`` lists an Anthropic candidate FIRST and an OpenAI
    candidate SECOND for both ``reasoning`` (``claude-sonnet-*`` then
    ``gpt-?.?-terra*``) and ``coding`` (``claude-haiku-*`` then
    ``gpt-?.?-luna*``). When the Anthropic glob resolves against no
    installed model, resolution advances to the next candidate -- which the
    matrix author wrote as OpenAI. The observed
    architect->``gpt-5.6-terra`` / builder->``gpt-5.6-luna`` split is that
    ordered fallback, per role. It is not a provider mix-up, and a session
    default cannot produce it (a session default gives both children the
    SAME model).
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("anthropic_glob", "openai_glob", "expected"),
        [
            ("claude-sonnet-*", "gpt-?.?-terra*", "gpt-5.6-terra"),  # reasoning
            ("claude-haiku-*", "gpt-?.?-luna*", "gpt-5.6-luna"),  # coding
        ],
    )
    async def test_unresolvable_first_candidate_advances_to_the_next(
        self, anthropic_glob: str, openai_glob: str, expected: str
    ) -> None:
        providers = [
            {"id": "opus", "module": "provider-anthropic", "config": {"priority": 0}},
            {"id": "terra", "module": "provider-openai", "config": {"priority": 3}},
        ]
        # The cell pins the anthropic mount to a single model, so neither
        # claude-sonnet-* nor claude-haiku-* has anything to match.
        coordinator = _make_coordinator(
            providers,
            models_by_module={
                "provider-anthropic": ["claude-opus-5"],
                "provider-openai": OPENAI_MODELS,
            },
        )

        new_plan = await apply_provider_preferences_with_resolution(
            {"providers": providers},
            [
                ProviderPreference(provider="anthropic", model=anthropic_glob),
                ProviderPreference(provider="openai", model=openai_glob),
            ],
            coordinator,
        )

        promoted = [
            p for p in new_plan["providers"] if p["config"].get("priority") == 0
        ]
        assert len(promoted) == 1
        assert promoted[0]["id"] == "terra"
        assert promoted[0]["config"]["default_model"] == expected

    @pytest.mark.asyncio
    async def test_no_candidate_resolves_leaves_the_plan_untouched(self) -> None:
        """The documented silent-substitution path: session default is kept.

        Nothing is promoted and no unresolved glob is written into the plan,
        so the child simply runs whatever the session already ranked first.
        """
        providers = [
            {"id": "opus", "module": "provider-anthropic", "config": {"priority": 0}},
        ]
        coordinator = _make_coordinator(
            providers, models_by_module={"provider-anthropic": ["claude-opus-5"]}
        )
        plan = {"providers": providers}

        new_plan = await apply_provider_preferences_with_resolution(
            plan,
            [ProviderPreference(provider="anthropic", model="claude-sonnet-*")],
            coordinator,
        )

        assert new_plan == plan
        assert "default_model" not in new_plan["providers"][0]["config"]


# =============================================================================
# QUESTION 1 -- characterization. Intended behaviour; do not "fix" these.
# =============================================================================


def _make_delegate_tool(
    *,
    spawn_fn: Any,
    agents: dict | None = None,
    model_role_resolver: Any = None,
) -> Any:
    from amplifier_module_tool_delegate import DelegateTool

    coordinator = MagicMock()
    coordinator.session_id = "parent-session-67u"
    coordinator.config = {"agents": agents or {}}
    coordinator.session_state = {}

    capabilities = {
        "session.spawn": spawn_fn,
        "session.resume": AsyncMock(return_value={}),
        "agents.list": lambda: agents or {},
        "agents.get": lambda name: (agents or {}).get(name),
        "self_delegation_depth": 0,
        "model_role_resolver": model_role_resolver,
    }
    coordinator.get_capability = lambda name: capabilities.get(name)
    coordinator.get = MagicMock(return_value=None)  # hooks = None

    parent_session = MagicMock()
    parent_session.session_id = "parent-session-67u"
    parent_session.config = {"session": {"orchestrator": {}}}
    coordinator.session = parent_session

    return DelegateTool(
        coordinator, {"features": {}, "settings": {"exclude_tools": []}}
    )


def _spawn_double() -> AsyncMock:
    return AsyncMock(
        return_value={
            "output": "done",
            "session_id": "child-67u",
            "status": "success",
            "turn_count": 1,
            "metadata": {},
        }
    )


def _resolver_double() -> Any:
    resolver = MagicMock()
    resolver.name = "matrix-double"
    resolver.resolve = AsyncMock(
        return_value=[ProviderPreference(provider="anthropic", model="claude-opus-5")]
    )
    return resolver


# The agent frontmatter shape the original probe delegated to, verbatim from
# the captured parent ``session:config``: a declared model_role list, and --
# importantly -- zero resolved provider_preferences.
ARCHITECT_AGENT = {
    "anchors-amp-dev:architect": {
        "model_role": ["reasoning", "general"],
        "provider_preferences": [],
    }
}


class TestNamedDelegateIsCharacterisation:
    """Naming an agent does not bypass anything; it just supplies no role."""

    @pytest.mark.asyncio
    async def test_no_model_role_argument_never_consults_the_resolver(self) -> None:
        """The guard at the single resolver call site is what "bypass" means."""
        spawn_fn = _spawn_double()
        resolver = _resolver_double()
        tool = _make_delegate_tool(
            spawn_fn=spawn_fn,
            agents=ARCHITECT_AGENT,
            model_role_resolver=resolver,
        )

        result = await tool.execute(
            {"agent": "anchors-amp-dev:architect", "instruction": "Reply A-OK."}
        )

        assert result.success
        resolver.resolve.assert_not_awaited()
        assert spawn_fn.await_args.kwargs["provider_preferences"] is None

    @pytest.mark.asyncio
    async def test_agent_declared_model_role_is_inert_in_tool_delegate(self) -> None:
        """``agents[name]["model_role"]`` is never read on the spawn path.

        The agent above declares ``model_role: [reasoning, general]``. That
        declaration is resolved elsewhere (the routing hook writes resolved
        preferences into agent configs at session:start) -- NOT here. This
        test pins the boundary so the next reader does not go looking for a
        missing lookup in this module.
        """
        spawn_fn = _spawn_double()
        resolver = _resolver_double()
        tool = _make_delegate_tool(
            spawn_fn=spawn_fn,
            agents=ARCHITECT_AGENT,
            model_role_resolver=resolver,
        )

        await tool.execute(
            {"agent": "anchors-amp-dev:architect", "instruction": "Reply A-OK."}
        )

        resolver.resolve.assert_not_awaited()
        assert spawn_fn.await_args.kwargs["provider_preferences"] is None

    @pytest.mark.asyncio
    async def test_agent_level_provider_preferences_are_the_one_fallback(self) -> None:
        """The only agent-level routing input the spawn path reads."""
        spawn_fn = _spawn_double()
        tool = _make_delegate_tool(
            spawn_fn=spawn_fn,
            agents={
                "anchors-amp-dev:architect": {
                    "model_role": ["reasoning", "general"],
                    "provider_preferences": [
                        {"provider": "anthropic", "model": "claude-opus-*"}
                    ],
                }
            },
            model_role_resolver=_resolver_double(),
        )

        await tool.execute(
            {"agent": "anchors-amp-dev:architect", "instruction": "Reply A-OK."}
        )

        prefs = spawn_fn.await_args.kwargs["provider_preferences"]
        assert prefs is not None
        assert [p.model for p in prefs] == ["claude-opus-*"]

    @pytest.mark.asyncio
    async def test_same_call_site_resolves_when_the_caller_supplies_the_role(
        self,
    ) -> None:
        """The "organic" path: identical code, one extra tool argument."""
        spawn_fn = _spawn_double()
        resolver = _resolver_double()
        tool = _make_delegate_tool(
            spawn_fn=spawn_fn,
            agents=ARCHITECT_AGENT,
            model_role_resolver=resolver,
        )

        await tool.execute(
            {
                "agent": "anchors-amp-dev:architect",
                "instruction": "Reply A-OK.",
                "model_role": "reasoning",
            }
        )

        resolver.resolve.assert_awaited_once_with("reasoning")
        prefs = spawn_fn.await_args.kwargs["provider_preferences"]
        assert [p.model for p in prefs] == ["claude-opus-5"]
