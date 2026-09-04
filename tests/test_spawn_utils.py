"""Tests for spawn_utils module - provider preferences and model resolution."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock
import asyncio
from unittest.mock import MagicMock

import pytest

from amplifier_foundation.spawn_utils import ProviderPreference
from amplifier_foundation.spawn_utils import _apply_single_override
from amplifier_foundation.spawn_utils import _build_provider_lookup
from amplifier_foundation.spawn_utils import _find_provider_index
from amplifier_foundation.spawn_utils import _find_provider_instance
from amplifier_foundation.spawn_utils import apply_provider_preferences
from amplifier_foundation.spawn_utils import apply_provider_preferences_with_resolution
from amplifier_foundation.spawn_utils import is_glob_pattern
from amplifier_foundation.spawn_utils import resolve_model_pattern


class TestProviderPreference:
    """Tests for ProviderPreference dataclass."""

    def test_create_provider_preference(self) -> None:
        """Test creating a ProviderPreference instance."""
        pref = ProviderPreference(provider="anthropic", model="claude-haiku-3")
        assert pref.provider == "anthropic"
        assert pref.model == "claude-haiku-3"

    def test_to_dict(self) -> None:
        """Test converting ProviderPreference to dict."""
        pref = ProviderPreference(provider="openai", model="gpt-4o-mini")
        result = pref.to_dict()
        assert result == {"provider": "openai", "model": "gpt-4o-mini"}

    def test_from_dict(self) -> None:
        """Test creating ProviderPreference from dict."""
        data = {"provider": "azure", "model": "gpt-4"}
        pref = ProviderPreference.from_dict(data)
        assert pref.provider == "azure"
        assert pref.model == "gpt-4"

    def test_from_dict_missing_provider(self) -> None:
        """Test from_dict raises error when provider is missing."""
        with pytest.raises(ValueError, match="requires 'provider' key"):
            ProviderPreference.from_dict({"model": "gpt-4"})

    def test_from_dict_missing_model(self) -> None:
        """Test from_dict raises error when model is missing."""
        with pytest.raises(ValueError, match="requires 'model' key"):
            ProviderPreference.from_dict({"provider": "openai"})


class TestIsGlobPattern:
    """Tests for is_glob_pattern function."""

    def test_not_a_pattern(self) -> None:
        """Test that exact model names are not patterns."""
        assert not is_glob_pattern("claude-3-haiku-20240307")
        assert not is_glob_pattern("gpt-4o-mini")
        assert not is_glob_pattern("claude-sonnet-4-20250514")

    def test_asterisk_pattern(self) -> None:
        """Test asterisk wildcard detection."""
        assert is_glob_pattern("claude-haiku-*")
        assert is_glob_pattern("*-haiku-*")
        assert is_glob_pattern("gpt-4*")

    def test_question_mark_pattern(self) -> None:
        """Test question mark wildcard detection."""
        assert is_glob_pattern("gpt-4?")
        assert is_glob_pattern("claude-?-haiku")

    def test_bracket_pattern(self) -> None:
        """Test bracket character class detection."""
        assert is_glob_pattern("gpt-[45]")
        assert is_glob_pattern("claude-[a-z]-haiku")


class TestApplyProviderPreferences:
    """Tests for apply_provider_preferences function."""

    def test_empty_preferences(self) -> None:
        """Test that empty preferences returns unchanged mount plan."""
        mount_plan = {"providers": [{"module": "provider-anthropic", "config": {}}]}
        result = apply_provider_preferences(mount_plan, [])
        assert result is mount_plan  # Same object, unchanged

    def test_no_providers_in_mount_plan(self) -> None:
        """Test handling of mount plan without providers."""
        mount_plan = {"orchestrator": {"module": "loop-basic"}}
        prefs = [ProviderPreference(provider="anthropic", model="claude-haiku-3")]
        result = apply_provider_preferences(mount_plan, prefs)
        assert result is mount_plan  # Unchanged

    def test_first_preference_matches(self) -> None:
        """Test that first matching preference is used."""
        mount_plan = {
            "providers": [
                {"module": "provider-anthropic", "config": {"priority": 10}},
                {"module": "provider-openai", "config": {"priority": 20}},
            ]
        }
        prefs = [
            ProviderPreference(provider="anthropic", model="claude-haiku-3"),
            ProviderPreference(provider="openai", model="gpt-4o-mini"),
        ]
        result = apply_provider_preferences(mount_plan, prefs)

        # Anthropic should be promoted to priority 0
        assert result["providers"][0]["config"]["priority"] == 0
        assert result["providers"][0]["config"]["default_model"] == "claude-haiku-3"
        # OpenAI should be unchanged
        assert result["providers"][1]["config"]["priority"] == 20

    def test_second_preference_matches_when_first_unavailable(self) -> None:
        """Test fallback to second preference when first is unavailable."""
        mount_plan = {
            "providers": [
                {"module": "provider-openai", "config": {"priority": 10}},
            ]
        }
        prefs = [
            ProviderPreference(provider="anthropic", model="claude-haiku-3"),
            ProviderPreference(provider="openai", model="gpt-4o-mini"),
        ]
        result = apply_provider_preferences(mount_plan, prefs)

        # OpenAI should be promoted since anthropic isn't available
        assert result["providers"][0]["config"]["priority"] == 0
        assert result["providers"][0]["config"]["default_model"] == "gpt-4o-mini"

    def test_no_preferences_match(self) -> None:
        """Test that mount plan is unchanged when no preferences match."""
        mount_plan = {
            "providers": [
                {"module": "provider-azure", "config": {"priority": 10}},
            ]
        }
        prefs = [
            ProviderPreference(provider="anthropic", model="claude-haiku-3"),
            ProviderPreference(provider="openai", model="gpt-4o-mini"),
        ]
        result = apply_provider_preferences(mount_plan, prefs)

        # Should be unchanged
        assert result["providers"][0]["config"]["priority"] == 10
        assert "default_model" not in result["providers"][0]["config"]

    def test_flexible_provider_matching_short_name(self) -> None:
        """Test that short provider names match full module names."""
        mount_plan = {
            "providers": [
                {"module": "provider-anthropic", "config": {}},
            ]
        }
        # Use short name "anthropic" instead of "provider-anthropic"
        prefs = [ProviderPreference(provider="anthropic", model="claude-haiku-3")]
        result = apply_provider_preferences(mount_plan, prefs)

        assert result["providers"][0]["config"]["priority"] == 0
        assert result["providers"][0]["config"]["default_model"] == "claude-haiku-3"

    def test_flexible_provider_matching_full_name(self) -> None:
        """Test that full module names also work."""
        mount_plan = {
            "providers": [
                {"module": "provider-anthropic", "config": {}},
            ]
        }
        prefs = [
            ProviderPreference(provider="provider-anthropic", model="claude-haiku-3")
        ]
        result = apply_provider_preferences(mount_plan, prefs)

        assert result["providers"][0]["config"]["priority"] == 0

    def test_mount_plan_not_mutated(self) -> None:
        """Test that original mount plan is not mutated."""
        mount_plan = {
            "providers": [
                {"module": "provider-anthropic", "config": {"priority": 10}},
            ]
        }
        prefs = [ProviderPreference(provider="anthropic", model="claude-haiku-3")]

        # Store original values
        original_priority = mount_plan["providers"][0]["config"]["priority"]

        result = apply_provider_preferences(mount_plan, prefs)

        # Original should be unchanged
        assert mount_plan["providers"][0]["config"]["priority"] == original_priority
        assert "default_model" not in mount_plan["providers"][0]["config"]

        # Result should have new values
        assert result["providers"][0]["config"]["priority"] == 0
        assert result["providers"][0]["config"]["default_model"] == "claude-haiku-3"


class TestResolveModelPattern:
    """Tests for resolve_model_pattern function."""

    @pytest.mark.asyncio
    async def test_not_a_pattern_returns_as_is(self) -> None:
        """Test that non-patterns are returned unchanged."""
        result = await resolve_model_pattern(
            "claude-3-haiku-20240307",
            "anthropic",
            MagicMock(),
        )
        assert result.resolved_model == "claude-3-haiku-20240307"
        assert result.pattern is None

    @pytest.mark.asyncio
    async def test_pattern_without_provider_returns_as_is(self) -> None:
        """Test that patterns without provider are returned as-is."""
        result = await resolve_model_pattern(
            "claude-haiku-*",
            None,
            MagicMock(),
        )
        assert result.resolved_model == "claude-haiku-*"
        assert result.pattern == "claude-haiku-*"

    @pytest.mark.asyncio
    async def test_pattern_resolves_to_latest(self) -> None:
        """Test that glob patterns resolve to the latest matching model."""
        # Mock coordinator with provider that returns models
        mock_provider = AsyncMock()
        mock_provider.list_models = AsyncMock(
            return_value=[
                "claude-3-haiku-20240101",
                "claude-3-haiku-20240307",
                "claude-3-haiku-20240201",
            ]
        )

        mock_coordinator = MagicMock()
        mock_coordinator.get.return_value = {"provider-anthropic": mock_provider}

        result = await resolve_model_pattern(
            "claude-3-haiku-*",
            "anthropic",
            mock_coordinator,
        )

        # Should resolve to latest (sorted descending)
        assert result.resolved_model == "claude-3-haiku-20240307"
        assert result.pattern == "claude-3-haiku-*"
        assert len(result.matched_models or []) == 3

    @pytest.mark.asyncio
    async def test_pattern_no_matches_returns_none(self) -> None:
        """Regression test (bug: silent-fallback design flaw): when a glob
        pattern finds zero matches against a real, non-empty model list,
        resolve_model_pattern() must NOT disguise this as a successful
        resolution by returning the raw, unresolved pattern string as
        resolved_model. That raw glob (e.g. "claude-*") would otherwise
        flow straight into a mount plan and be sent literally to the
        provider's API, producing a confusing 404 instead of a clear,
        diagnosable "could not resolve a model" signal. Failure must be
        explicit: resolved_model is None.
        """
        mock_provider = AsyncMock()
        mock_provider.list_models = AsyncMock(return_value=["gpt-4o", "gpt-4o-mini"])

        mock_coordinator = MagicMock()
        mock_coordinator.get.return_value = {"provider-openai": mock_provider}

        result = await resolve_model_pattern(
            "claude-*",  # No Claude models in OpenAI
            "openai",
            mock_coordinator,
        )

        assert result.resolved_model is None, (
            "resolve_model_pattern() must signal failure (None) when a glob "
            f"matches nothing, not disguise it as success. Got: {result.resolved_model!r}"
        )
        assert result.matched_models == []

    @pytest.mark.asyncio
    async def test_empty_model_list_returns_none(self) -> None:
        """Regression test (bug: silent-fallback design flaw): when the
        provider has no available models at all (empty list_models()
        result), resolve_model_pattern() must NOT return the raw,
        unresolved pattern string disguised as a successful resolution.
        """
        mock_provider = AsyncMock()
        mock_provider.list_models = AsyncMock(return_value=[])

        mock_coordinator = MagicMock()
        mock_coordinator.get.return_value = {"provider-openai": mock_provider}

        result = await resolve_model_pattern(
            "gpt-4o-*",
            "openai",
            mock_coordinator,
        )

        assert result.resolved_model is None, (
            "resolve_model_pattern() must signal failure (None) when the "
            f"provider has no models, not disguise it as success. Got: {result.resolved_model!r}"
        )
        assert result.matched_models == []

    @pytest.mark.asyncio
    async def test_pattern_matches_case_insensitively(self) -> None:
        """Regression test: glob matching must be case-insensitive and
        OS-independent. Raw fnmatch.filter() uses os.path.normcase, which is
        case-sensitive on Linux/Mac and case-insensitive on Windows -- so a
        mixed-case model id (e.g. real-world 'Qwen3.6-35B-A3B-UD-Q4_K_XL')
        would silently fail to match a lowercase pattern ('qwen3.6-*') on
        Linux/Mac while matching on Windows. Model glob matching must be
        deterministic across platforms, and consistent with the routing-matrix
        resolver's semantics (amplifier_module_hooks_routing.resolver).
        """
        mock_provider = AsyncMock()
        mock_provider.list_models = AsyncMock(
            return_value=["Qwen3.6-35B-A3B-UD-Q4_K_XL"]
        )

        mock_coordinator = MagicMock()
        mock_coordinator.get.return_value = {"provider-ornith": mock_provider}

        result = await resolve_model_pattern(
            "qwen3.6-*",
            "ornith",
            mock_coordinator,
        )

        assert result.resolved_model == "Qwen3.6-35B-A3B-UD-Q4_K_XL", (
            f"Expected case-insensitive match to find the mixed-case model, "
            f"got: {result.resolved_model!r}"
        )
        assert result.matched_models == ["Qwen3.6-35B-A3B-UD-Q4_K_XL"]

    @pytest.mark.asyncio
    async def test_uppercase_pattern_matches_lowercase_model(self) -> None:
        """Symmetric case: an uppercase-leaning pattern must match a
        lowercase model id -- proves the fix lowercases BOTH sides, not just
        the model list."""
        mock_provider = AsyncMock()
        mock_provider.list_models = AsyncMock(
            return_value=["qwen3.6-35b-a3b-ud-q4_k_xl"]
        )

        mock_coordinator = MagicMock()
        mock_coordinator.get.return_value = {"provider-ornith": mock_provider}

        result = await resolve_model_pattern(
            "Qwen3.6-*",
            "ornith",
            mock_coordinator,
        )

        assert result.resolved_model == "qwen3.6-35b-a3b-ud-q4_k_xl"


class TestFindProviderInstanceMultiInstance:
    """Regression tests for _find_provider_instance's bare-module-type fallback.

    Live production bug: a user with 3 separately-configured Anthropic
    instances (each with an explicit `id:` -- anthropic-sonnet,
    anthropic-opus, anthropic-haiku -- none keyed bare "anthropic") hit
    "provider has no models" for every matrix role candidate naming the
    bare type "anthropic" (e.g. fast -> claude-haiku-*). The real Anthropic
    API was independently confirmed to have real models available; the bug
    is purely in provider lookup never falling back to "search all
    instances of this module type" when no single provider is keyed by
    the bare type.
    """

    @staticmethod
    def _provider_specs() -> list[dict]:
        return [
            {
                "module": "provider-anthropic",
                "id": "anthropic-sonnet",
                "config": {"priority": 1},
            },
            {
                "module": "provider-anthropic",
                "id": "anthropic-opus",
                "config": {"priority": 2},
            },
            {
                "module": "provider-anthropic",
                "id": "anthropic-haiku",
                "config": {"priority": 9},
            },
        ]

    def test_bare_type_resolves_to_highest_priority_instance(self) -> None:
        """No provider is keyed bare 'anthropic' -- must fall back to the
        module-type search and pick the default (priority=1) instance,
        not return None."""
        sonnet = MagicMock(name="sonnet-instance")
        opus = MagicMock(name="opus-instance")
        haiku = MagicMock(name="haiku-instance")
        providers = {
            "anthropic-sonnet": sonnet,
            "anthropic-opus": opus,
            "anthropic-haiku": haiku,
        }
        coordinator = MagicMock()
        coordinator.config = {"providers": self._provider_specs()}

        result = _find_provider_instance(providers, "anthropic", coordinator)

        assert result is sonnet, (
            "Bare type 'anthropic' with no bare-keyed instance must resolve "
            "to the highest-priority (lowest priority number) instance of "
            f"that module type. Got: {result!r}"
        )

    def test_returns_none_without_coordinator(self) -> None:
        """Without a coordinator, old exact-match-only behavior is preserved
        (no crash, no fallback attempted)."""
        providers = {"anthropic-sonnet": MagicMock()}
        assert _find_provider_instance(providers, "anthropic") is None

    def test_exact_match_still_wins_over_fallback(self) -> None:
        """A provider keyed literally 'anthropic' is preferred immediately --
        the fallback path is never even consulted."""
        bare = MagicMock(name="bare-instance")
        providers = {"anthropic": bare, "anthropic-haiku": MagicMock()}
        coordinator = MagicMock()
        coordinator.config = {
            "providers": [
                {
                    "module": "provider-anthropic",
                    "id": "anthropic-haiku",
                    "config": {"priority": 1},
                },
                {
                    "module": "provider-anthropic",
                    "id": "anthropic",
                    "config": {"priority": 5},
                },
            ]
        }
        assert _find_provider_instance(providers, "anthropic", coordinator) is bare

    def test_no_matching_module_type_returns_none(self) -> None:
        """Fallback search finds no instance of the requested module type ->
        still returns None (no false positive)."""
        providers = {"openai": MagicMock()}
        coordinator = MagicMock()
        coordinator.config = {
            "providers": [
                {"module": "provider-openai", "id": "openai", "config": {}},
            ]
        }
        assert _find_provider_instance(providers, "anthropic", coordinator) is None


class TestResolveModelPatternMultiInstanceProvider:
    """End-to-end regression test (live prod bug, reproduced directly against
    the real amplifier ecosystem): resolve_model_pattern() must resolve a
    bare-type provider name + glob pattern against the default instance when
    multiple named instances exist and none is keyed by the bare type.
    """

    @pytest.mark.asyncio
    async def test_bare_type_glob_resolves_via_default_instance(self) -> None:
        sonnet_provider = AsyncMock()
        sonnet_provider.list_models = AsyncMock(
            return_value=["claude-haiku-4-5-20251001", "claude-haiku-3-20240307"]
        )
        opus_provider = AsyncMock()
        opus_provider.list_models = AsyncMock(return_value=["claude-opus-4-8"])

        mock_coordinator = MagicMock()
        mock_coordinator.get.return_value = {
            "anthropic-sonnet": sonnet_provider,
            "anthropic-opus": opus_provider,
        }
        mock_coordinator.config = {
            "providers": [
                {
                    "module": "provider-anthropic",
                    "id": "anthropic-sonnet",
                    "config": {"priority": 1},
                },
                {
                    "module": "provider-anthropic",
                    "id": "anthropic-opus",
                    "config": {"priority": 2},
                },
            ]
        }

        result = await resolve_model_pattern(
            "claude-haiku-*", "anthropic", mock_coordinator
        )

        assert result.resolved_model == "claude-haiku-4-5-20251001", (
            "resolve_model_pattern() must resolve a bare-type provider name "
            "against the default (highest-priority) instance when no single "
            "instance is keyed by the bare type. Got: "
            f"{result.resolved_model!r} (before the fix this returned None, "
            "with 'provider has no models' logged -- even though the "
            "provider genuinely has matching models)."
        )
        assert opus_provider.list_models.await_count == 0, (
            "Only the resolved (highest-priority) instance's list_models() "
            "should be queried, not every instance of the type."
        )


class TestApplyProviderPreferencesWithResolution:
    """Tests for apply_provider_preferences_with_resolution function."""

    @pytest.mark.asyncio
    async def test_resolves_glob_pattern(self) -> None:
        """Test that glob patterns are resolved during application."""
        mount_plan = {
            "providers": [
                {"module": "provider-anthropic", "config": {}},
            ]
        }

        # Mock coordinator with provider
        mock_provider = AsyncMock()
        mock_provider.list_models = AsyncMock(
            return_value=[
                "claude-3-haiku-20240101",
                "claude-3-haiku-20240307",
            ]
        )
        mock_coordinator = MagicMock()
        mock_coordinator.get.return_value = {"provider-anthropic": mock_provider}

        prefs = [ProviderPreference(provider="anthropic", model="claude-3-haiku-*")]

        result = await apply_provider_preferences_with_resolution(
            mount_plan, prefs, mock_coordinator
        )

        # Should resolve pattern to latest model
        assert (
            result["providers"][0]["config"]["default_model"]
            == "claude-3-haiku-20240307"
        )

    @pytest.mark.asyncio
    async def test_exact_model_not_resolved(self) -> None:
        """Test that exact model names pass through without resolution."""
        mount_plan = {
            "providers": [
                {"module": "provider-anthropic", "config": {}},
            ]
        }

        mock_coordinator = MagicMock()
        mock_coordinator.get.return_value = {}

        prefs = [
            ProviderPreference(provider="anthropic", model="claude-3-haiku-20240307")
        ]

        result = await apply_provider_preferences_with_resolution(
            mount_plan, prefs, mock_coordinator
        )

        # Exact model should pass through
        assert (
            result["providers"][0]["config"]["default_model"]
            == "claude-3-haiku-20240307"
        )

    @pytest.mark.asyncio
    async def test_fallback_with_resolution(self) -> None:
        """Test fallback chain with pattern resolution."""
        mount_plan = {
            "providers": [
                {"module": "provider-openai", "config": {}},
            ]
        }

        mock_provider = AsyncMock()
        mock_provider.list_models = AsyncMock(return_value=["gpt-4o", "gpt-4o-mini"])
        mock_coordinator = MagicMock()
        mock_coordinator.get.return_value = {"provider-openai": mock_provider}

        prefs = [
            # First preference unavailable
            ProviderPreference(provider="anthropic", model="claude-haiku-*"),
            # Second preference available with pattern
            ProviderPreference(provider="openai", model="gpt-4o*"),
        ]

        result = await apply_provider_preferences_with_resolution(
            mount_plan, prefs, mock_coordinator
        )

        # Should use openai with resolved model (gpt-4o sorts after gpt-4o-mini descending)
        assert result["providers"][0]["config"]["priority"] == 0
        # gpt-4o-mini > gpt-4o when sorted descending
        assert result["providers"][0]["config"]["default_model"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_falls_through_to_next_preference_when_glob_fails(self) -> None:
        """Regression test (bug: silent-fallback design flaw): when the
        FIRST preference's glob pattern fails to resolve against its
        provider's real model list (zero fnmatch matches), the function
        must NOT apply that preference with the raw, unresolved glob
        string as the "resolved" model. It must instead advance to the
        NEXT preference in the ordered list -- mirroring
        resolve_model_role()'s `continue` behavior in the sibling
        routing-matrix resolver -- and apply ITS successfully resolved
        model.
        """
        mount_plan = {
            "providers": [
                {"module": "provider-anthropic", "config": {}},
                {"module": "provider-openai", "config": {}},
            ]
        }

        # Anthropic is present but has no model matching the glob.
        mock_anthropic = AsyncMock()
        mock_anthropic.list_models = AsyncMock(
            return_value=["claude-sonnet-4-20250514"]  # no "claude-haiku-*" match
        )
        # OpenAI is present and DOES have a matching model.
        mock_openai = AsyncMock()
        mock_openai.list_models = AsyncMock(return_value=["gpt-4o-mini"])

        mock_coordinator = MagicMock()
        mock_coordinator.get.return_value = {
            "provider-anthropic": mock_anthropic,
            "provider-openai": mock_openai,
        }

        prefs = [
            # First preference: provider present, but glob matches nothing.
            ProviderPreference(provider="anthropic", model="claude-haiku-*"),
            # Second preference: provider present, glob resolves cleanly.
            ProviderPreference(provider="openai", model="gpt-4o-*"),
        ]

        result = await apply_provider_preferences_with_resolution(
            mount_plan, prefs, mock_coordinator
        )

        # The SECOND preference (openai) must be the one promoted/applied,
        # with its cleanly-resolved model -- NOT anthropic with the raw
        # unresolved glob "claude-haiku-*".
        assert result["providers"][1]["config"]["priority"] == 0, (
            "Expected openai (second preference) to be promoted after "
            "anthropic's glob failed to resolve."
        )
        assert result["providers"][1]["config"]["default_model"] == "gpt-4o-mini"
        # Anthropic (first preference) must be left untouched -- specifically,
        # it must never have been given the raw, unresolved glob as a model.
        assert "default_model" not in result["providers"][0]["config"]
        assert result["providers"][0]["config"].get("priority") != 0

    @pytest.mark.asyncio
    async def test_all_preferences_fail_leaves_mount_plan_unmodified(self) -> None:
        """Regression test: when EVERY preference in the ordered list fails
        to resolve its glob pattern, the mount plan must be returned
        unmodified -- no unresolved pattern string may be written into it
        anywhere, and no provider should be promoted.
        """
        mount_plan = {
            "providers": [
                {"module": "provider-anthropic", "config": {"priority": 10}},
                {"module": "provider-openai", "config": {"priority": 20}},
            ]
        }

        mock_anthropic = AsyncMock()
        mock_anthropic.list_models = AsyncMock(
            return_value=["claude-sonnet-4-20250514"]
        )
        mock_openai = AsyncMock()
        mock_openai.list_models = AsyncMock(return_value=["gpt-4o", "gpt-4o-mini"])

        mock_coordinator = MagicMock()
        mock_coordinator.get.return_value = {
            "provider-anthropic": mock_anthropic,
            "provider-openai": mock_openai,
        }

        prefs = [
            ProviderPreference(
                provider="anthropic", model="claude-haiku-*"
            ),  # no match
            ProviderPreference(provider="openai", model="claude-*"),  # no match either
        ]

        result = await apply_provider_preferences_with_resolution(
            mount_plan, prefs, mock_coordinator
        )

        # Nothing should have been promoted or modified.
        assert result["providers"][0]["config"] == {"priority": 10}
        assert result["providers"][1]["config"] == {"priority": 20}
        # No unresolved glob pattern string should appear anywhere in the result.
        for p in result["providers"]:
            assert "default_model" not in p["config"]


class TestProviderPreferenceConfig:
    """Tests for ProviderPreference config field."""

    def test_provider_preference_config_default_empty(self) -> None:
        """Config defaults to empty dict when not specified."""
        pref = ProviderPreference(provider="openai", model="gpt-5")
        assert pref.config == {}

    def test_provider_preference_with_config(self) -> None:
        """Config field holds provided values."""
        pref = ProviderPreference(
            provider="openai", model="gpt-5", config={"reasoning_effort": "high"}
        )
        assert pref.config == {"reasoning_effort": "high"}

    def test_from_dict_with_config(self) -> None:
        """from_dict populates config from dict key."""
        pref = ProviderPreference.from_dict(
            {
                "provider": "openai",
                "model": "gpt-5",
                "config": {"reasoning_effort": "high"},
            }
        )
        assert pref.config == {"reasoning_effort": "high"}

    def test_from_dict_without_config_key(self) -> None:
        """from_dict defaults config to empty dict when key absent (backward compat)."""
        pref = ProviderPreference.from_dict({"provider": "openai", "model": "gpt-5"})
        assert pref.config == {}

    def test_to_dict_includes_config_when_present(self) -> None:
        """to_dict includes config key when config is non-empty."""
        pref = ProviderPreference(
            provider="openai", model="gpt-5", config={"reasoning_effort": "high"}
        )
        assert pref.to_dict() == {
            "provider": "openai",
            "model": "gpt-5",
            "config": {"reasoning_effort": "high"},
        }

    def test_to_dict_excludes_config_when_empty(self) -> None:
        """to_dict omits config key when config is empty (backward compat)."""
        pref = ProviderPreference(provider="openai", model="gpt-5")
        assert pref.to_dict() == {"provider": "openai", "model": "gpt-5"}

    def test_roundtrip_with_config(self) -> None:
        """Roundtrip through to_dict/from_dict preserves all fields including config."""
        original = ProviderPreference(
            provider="openai", model="gpt-5", config={"reasoning_effort": "high"}
        )
        roundtripped = ProviderPreference.from_dict(original.to_dict())
        assert roundtripped.provider == original.provider
        assert roundtripped.model == original.model
        assert roundtripped.config == original.config


class TestBuildProviderLookupMultiInstance:
    """Tests for _build_provider_lookup with id-based lookup."""

    def test_build_provider_lookup_includes_id(self) -> None:
        """Lookup dict includes id keys when providers have id field."""
        providers = [
            {"module": "provider-anthropic", "id": "anthropic-team-a", "config": {}},
            {"module": "provider-anthropic", "id": "anthropic-team-b", "config": {}},
        ]
        lookup = _build_provider_lookup(providers)
        assert lookup["anthropic-team-a"] == 0
        assert lookup["anthropic-team-b"] == 1


class TestFindProviderIndexMultiInstance:
    """Tests for _find_provider_index with id-based matching."""

    def test_find_provider_index_by_id(self) -> None:
        """Can find a provider by its id field."""
        providers = [
            {"module": "provider-anthropic", "id": "anthropic-team-a", "config": {}},
            {"module": "provider-anthropic", "id": "anthropic-team-b", "config": {}},
        ]
        assert _find_provider_index(providers, "anthropic-team-a") == 0
        assert _find_provider_index(providers, "anthropic-team-b") == 1


class TestApplySingleOverrideConfig:
    """Tests for _apply_single_override pref_config merging and protected keys."""

    def _make_mount_plan(self, provider_config: dict) -> tuple[dict, list]:
        """Build a minimal mount plan with one provider."""
        mount_plan = {
            "providers": [{"module": "provider-openai", "config": provider_config}],
            "session": {"orchestrator": {"module": "loop-basic"}},
        }
        return mount_plan, mount_plan["providers"]

    def test_apply_single_override_merges_pref_config(self) -> None:
        """pref_config keys are merged into provider config."""
        mount_plan, providers = self._make_mount_plan(
            {"api_key": "sk-test", "default_model": "gpt-4", "priority": 10}
        )
        result = _apply_single_override(
            mount_plan,
            providers,
            0,
            "gpt-5",
            pref_config={"reasoning_effort": "high", "temperature": 0.3},
        )
        result_config = result["providers"][0]["config"]
        assert result_config["reasoning_effort"] == "high"
        assert result_config["temperature"] == 0.3

    def test_apply_single_override_protects_credentials(self) -> None:
        """api_key is protected — pref_config cannot override it."""
        mount_plan, providers = self._make_mount_plan(
            {"api_key": "sk-test", "default_model": "gpt-4", "priority": 10}
        )
        result = _apply_single_override(
            mount_plan,
            providers,
            0,
            "gpt-5",
            pref_config={"api_key": "EVIL", "reasoning_effort": "high"},
        )
        result_config = result["providers"][0]["config"]
        assert result_config["api_key"] == "sk-test"
        assert result_config["reasoning_effort"] == "high"

    def test_apply_single_override_protects_base_url(self) -> None:
        """base_url is protected — pref_config cannot override it."""
        mount_plan, providers = self._make_mount_plan(
            {"api_key": "sk-test", "base_url": "http://real.com", "priority": 10}
        )
        result = _apply_single_override(
            mount_plan,
            providers,
            0,
            "gpt-5",
            pref_config={"base_url": "http://evil.com", "temperature": 0.5},
        )
        result_config = result["providers"][0]["config"]
        assert result_config["base_url"] == "http://real.com"
        assert result_config["temperature"] == 0.5

    def test_apply_single_override_no_config_backward_compat(self) -> None:
        """Calling without pref_config works exactly as before."""
        mount_plan, providers = self._make_mount_plan(
            {"api_key": "sk-test", "default_model": "gpt-4", "priority": 10}
        )
        result = _apply_single_override(mount_plan, providers, 0, "gpt-5")
        result_config = result["providers"][0]["config"]
        assert result_config["priority"] == 0
        assert result_config["default_model"] == "gpt-5"
        assert result_config["api_key"] == "sk-test"

    def test_apply_single_override_preference_wins_over_base(self) -> None:
        """pref_config value wins over same key already in provider config."""
        mount_plan, providers = self._make_mount_plan(
            {"api_key": "sk-test", "reasoning_effort": "low", "priority": 10}
        )
        result = _apply_single_override(
            mount_plan,
            providers,
            0,
            "gpt-5",
            pref_config={"reasoning_effort": "high"},
        )
        result_config = result["providers"][0]["config"]
        assert result_config["reasoning_effort"] == "high"

    def test_apply_single_override_protects_azure_auth(self) -> None:
        """Azure auth fields like managed_identity_client_id cannot be overridden."""
        mount_plan, providers = self._make_mount_plan(
            {
                "api_key": "sk-test",
                "default_model": "gpt-4",
                "priority": 10,
                "managed_identity_client_id": "original-id",
            }
        )
        result = _apply_single_override(
            mount_plan,
            providers,
            0,
            "gpt-5",
            pref_config={
                "managed_identity_client_id": "evil-id",
                "reasoning_effort": "high",
            },
        )
        result_config = result["providers"][0]["config"]
        assert (
            result_config["managed_identity_client_id"] == "original-id"
        )  # NOT overridden
        assert result_config["reasoning_effort"] == "high"  # non-protected key merged

    def test_apply_single_override_priority_cannot_be_overridden(self) -> None:
        """priority and default_model are enforced even if pref_config tries to override them."""
        mount_plan, providers = self._make_mount_plan(
            {"api_key": "sk-test", "default_model": "gpt-4", "priority": 10}
        )
        result = _apply_single_override(
            mount_plan,
            providers,
            0,
            "gpt-5",
            pref_config={
                "priority": 99,
                "default_model": "gpt-3.5",
                "reasoning_effort": "high",
            },
        )
        result_config = result["providers"][0]["config"]
        assert result_config["priority"] == 0  # enforced, not 99
        assert result_config["default_model"] == "gpt-5"  # enforced, not gpt-3.5
        assert (
            result_config["reasoning_effort"] == "high"
        )  # non-protected merged normally


class TestProviderPreferenceConfigWiring:
    """Tests that pref.config is wired through apply_provider_preferences callers."""

    def _make_mount_plan(self) -> dict:
        """Build a minimal mount plan with one openai provider."""
        return {
            "providers": [
                {
                    "module": "provider-openai",
                    "config": {"api_key": "sk-test", "priority": 10},
                }
            ],
            "session": {"orchestrator": {"module": "loop-basic"}},
        }

    def test_apply_provider_preferences_passes_config(self) -> None:
        """apply_provider_preferences passes pref.config to _apply_single_override."""
        mount_plan = self._make_mount_plan()
        pref = ProviderPreference(
            provider="openai", model="gpt-5", config={"reasoning_effort": "high"}
        )
        result = apply_provider_preferences(mount_plan, [pref])

        result_config = result["providers"][0]["config"]
        assert result_config["reasoning_effort"] == "high"

    @pytest.mark.asyncio
    async def test_apply_provider_preferences_with_resolution_passes_config(
        self,
    ) -> None:
        """apply_provider_preferences_with_resolution passes pref.config."""
        mount_plan = self._make_mount_plan()
        pref = ProviderPreference(
            provider="openai",
            model="gpt-5",  # exact model, no glob — resolution is a no-op
            config={"reasoning_effort": "high"},
        )

        mock_coordinator = MagicMock()
        mock_coordinator.get.return_value = {}

        result = await apply_provider_preferences_with_resolution(
            mount_plan, [pref], mock_coordinator
        )

        result_config = result["providers"][0]["config"]
        assert result_config["reasoning_effort"] == "high"

    def test_config_flows_end_to_end(self) -> None:
        """Multiple config values flow end-to-end alongside existing provider keys."""
        mount_plan = self._make_mount_plan()
        pref = ProviderPreference(
            provider="openai",
            model="gpt-5",
            config={"reasoning_effort": "high", "temperature": 0.3},
        )
        result = apply_provider_preferences(mount_plan, [pref])

        result_config = result["providers"][0]["config"]
        assert result_config["reasoning_effort"] == "high"
        assert result_config["temperature"] == 0.3
        # Existing protected key untouched
        assert result_config["api_key"] == "sk-test"


class TestListModelsSingleFlightMemoization:
    """Regression tests: concurrent glob-pattern resolutions must coalesce
    provider.list_models() into a single upstream call (per provider, per TTL
    window) instead of firing one GET /v1/models per spawned child session.
    """

    def _make_coordinator(self, provider: AsyncMock) -> MagicMock:
        coordinator = MagicMock()
        coordinator.get.return_value = {"provider-anthropic": provider}
        return coordinator

    @pytest.mark.asyncio
    async def test_concurrent_resolutions_fetch_once(self) -> None:
        """50 parallel spawns resolving the same pattern = 1 upstream call."""
        provider = AsyncMock()
        provider.list_models = AsyncMock(
            return_value=["claude-sonnet-4-5", "claude-haiku-4-5"]
        )
        coordinator = self._make_coordinator(provider)

        results = await asyncio.gather(
            *(
                resolve_model_pattern("claude-haiku-*", "anthropic", coordinator)
                for _ in range(50)
            )
        )

        assert provider.list_models.await_count == 1
        assert all(r.resolved_model == "claude-haiku-4-5" for r in results)

    @pytest.mark.asyncio
    async def test_sequential_resolutions_reuse_within_ttl(self) -> None:
        provider = AsyncMock()
        provider.list_models = AsyncMock(return_value=["claude-sonnet-4-5"])
        coordinator = self._make_coordinator(provider)

        for _ in range(5):
            result = await resolve_model_pattern(
                "claude-sonnet-*", "anthropic", coordinator
            )
            assert result.resolved_model == "claude-sonnet-4-5"

        assert provider.list_models.await_count == 1

    @pytest.mark.asyncio
    async def test_refresh_after_ttl_expiry(self) -> None:
        provider = AsyncMock()
        provider.list_models = AsyncMock(return_value=["claude-sonnet-4-5"])
        coordinator = self._make_coordinator(provider)

        import amplifier_foundation.spawn_utils as su

        original_ttl = su.LIST_MODELS_CACHE_TTL_SECONDS
        su.LIST_MODELS_CACHE_TTL_SECONDS = 0.05
        try:
            await resolve_model_pattern("claude-*", "anthropic", coordinator)
            await asyncio.sleep(0.1)
            await resolve_model_pattern("claude-*", "anthropic", coordinator)
        finally:
            su.LIST_MODELS_CACHE_TTL_SECONDS = original_ttl

        assert provider.list_models.await_count == 2

    @pytest.mark.asyncio
    async def test_exact_name_never_fetches(self) -> None:
        provider = AsyncMock()
        coordinator = self._make_coordinator(provider)

        result = await resolve_model_pattern(
            "claude-sonnet-4-5", "anthropic", coordinator
        )

        assert result.resolved_model == "claude-sonnet-4-5"
        provider.list_models.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_providers_cached_independently(self) -> None:
        provider_a = AsyncMock()
        provider_a.list_models = AsyncMock(return_value=["m-a"])
        coordinator_a = MagicMock()
        coordinator_a.get.return_value = {"provider-anthropic": provider_a}

        provider_b = AsyncMock()
        provider_b.list_models = AsyncMock(return_value=["m-b"])
        coordinator_b = MagicMock()
        coordinator_b.get.return_value = {"provider-anthropic": provider_b}

        await asyncio.gather(
            resolve_model_pattern("m-*", "anthropic", coordinator_a),
            resolve_model_pattern("m-*", "anthropic", coordinator_b),
        )

        assert provider_a.list_models.await_count == 1
        assert provider_b.list_models.await_count == 1

    @pytest.mark.asyncio
    async def test_failures_are_not_cached(self) -> None:
        provider = AsyncMock()
        provider.list_models = AsyncMock(
            side_effect=[RuntimeError("boom"), ["claude-sonnet-4-5"]]
        )
        coordinator = self._make_coordinator(provider)

        first = await resolve_model_pattern("claude-*", "anthropic", coordinator)
        assert first.resolved_model is None

        second = await resolve_model_pattern("claude-*", "anthropic", coordinator)
        assert second.resolved_model == "claude-sonnet-4-5"
        assert provider.list_models.await_count == 2


class TestListModelsCacheHardening:
    """Regression tests for the hardening pass on top of #302's single-flight
    TTL cache (danshapiro): soft-failure non-answers must never poison the
    cache, a failing fetch must be shared once (not re-run per waiter) and
    must not persist past its own delivery, and a waiter must not block
    forever on a hung shared fetch.

    See repro_302.py / repro_302_hol.py for the standalone demonstrations
    these tests mirror.
    """

    def _make_coordinator(
        self, provider: AsyncMock, key: str = "provider-ollama"
    ) -> MagicMock:
        coordinator = MagicMock()
        coordinator.get.return_value = {key: provider}
        return coordinator

    @pytest.mark.asyncio
    async def test_empty_list_not_cached_then_refetches_and_later_caches(
        self,
    ) -> None:
        """Mirrors repro_302.py CASE 1 (ollama-style soft failure -> []).

        An empty result must never be cached -- the next call must refetch
        live -- but once a genuinely non-empty result comes back, THAT one
        is cached normally (no refetch on the following call).
        """
        provider = AsyncMock()
        provider.list_models = AsyncMock(
            side_effect=[[], [], ["qwen3.6-35b-a3b", "llama4:70b"]]
        )
        coordinator = self._make_coordinator(provider)

        first = await resolve_model_pattern("qwen3.6-*", "ollama", coordinator)
        assert first.resolved_model is None
        assert provider.list_models.await_count == 1

        # [] must NOT have been cached -- this call refetches live.
        second = await resolve_model_pattern("qwen3.6-*", "ollama", coordinator)
        assert second.resolved_model is None
        assert provider.list_models.await_count == 2

        # Server "recovers": a genuinely non-empty result comes back.
        third = await resolve_model_pattern("qwen3.6-*", "ollama", coordinator)
        assert third.resolved_model == "qwen3.6-35b-a3b"
        assert provider.list_models.await_count == 3

        # The non-empty result IS cached -- a 4th call must not refetch.
        fourth = await resolve_model_pattern("qwen3.6-*", "ollama", coordinator)
        assert fourth.resolved_model == "qwen3.6-35b-a3b"
        assert provider.list_models.await_count == 3

    @pytest.mark.asyncio
    async def test_degraded_single_model_list_bounded_by_ttl(self) -> None:
        """Mirrors repro_302.py CASE 2 (chat-completions degraded fallback).

        A degraded single-model fallback list (e.g. chat-completions
        returning `[configured_model]` on ANY exception) is NON-empty, so
        it is indistinguishable at this generic, provider-agnostic cache
        layer from a legitimately single-model catalog -- and
        `test_sequential_resolutions_reuse_within_ttl` above requires a
        genuine single-model result to be cached, so this layer cannot
        special-case "list of length 1" without breaking that contract.

        What the hardening DOES guarantee: the poisoning window is bounded
        by the (now-configurable, see TestListModelsConfigurableKnobs) TTL
        instead of being permanent -- once the TTL expires, a subsequent
        real catalog fetch replaces the degraded entry rather than being
        stuck behind it indefinitely.
        """
        provider = AsyncMock()
        provider.list_models = AsyncMock(
            side_effect=[
                ["local-default"],  # degraded fallback while "down"
                ["qwen3.6-35b-a3b", "qwen3.6-8b", "local-default"],  # recovered
            ]
        )
        coordinator = self._make_coordinator(provider)

        import amplifier_foundation.spawn_utils as su

        original_ttl = su.LIST_MODELS_CACHE_TTL_SECONDS
        su.LIST_MODELS_CACHE_TTL_SECONDS = 0.05
        try:
            degraded = await resolve_model_pattern("qwen3.6-*", "ollama", coordinator)
            # The degraded list is non-empty, so it is cached like any other
            # answer -- pattern doesn't match it, so resolution fails, but
            # this is a real answer as far as the cache can tell.
            assert degraded.resolved_model is None
            assert provider.list_models.await_count == 1

            await asyncio.sleep(0.1)  # TTL expires

            recovered = await resolve_model_pattern("qwen3.6-*", "ollama", coordinator)
        finally:
            su.LIST_MODELS_CACHE_TTL_SECONDS = original_ttl

        # Once the (bounded, configurable) TTL has passed, the degraded
        # entry is gone and a fresh, real catalog is fetched and resolved
        # correctly -- not permanently poisoned.
        assert provider.list_models.await_count == 2
        # Two matches ("qwen3.6-35b-a3b", "qwen3.6-8b"); descending sort picks
        # "qwen3.6-8b" ("8" > "3" at the first differing character).
        assert recovered.resolved_model == "qwen3.6-8b"

    @pytest.mark.asyncio
    async def test_concurrent_failure_shared_once_then_fresh_fetch(self) -> None:
        """A failing fetch is shared once across every concurrent waiter --
        not re-run as a full retry campaign per waiter -- and does not
        persist past its own delivery: the next NEW caller after the wave
        settles gets a genuinely fresh fetch.
        """
        provider = AsyncMock()
        started = asyncio.Event()

        async def flaky_list_models() -> list[str]:
            started.set()
            await asyncio.sleep(0.05)
            raise RuntimeError("boom")

        provider.list_models = AsyncMock(side_effect=flaky_list_models)
        coordinator = self._make_coordinator(provider, key="provider-anthropic")

        results = await asyncio.gather(
            *(
                resolve_model_pattern("claude-*", "anthropic", coordinator)
                for _ in range(20)
            )
        )

        # The failure was delivered to every one of the 20 waiters...
        assert all(r.resolved_model is None for r in results)
        # ...but the provider was only actually called once for the whole
        # wave (not once per waiter re-running its own retry campaign).
        assert provider.list_models.await_count == 1

        # The next NEW caller, after the failed wave has settled, starts a
        # genuinely fresh fetch rather than adopting the dead failed task.
        provider.list_models = AsyncMock(return_value=["claude-sonnet-4-5"])
        result = await resolve_model_pattern("claude-*", "anthropic", coordinator)
        assert result.resolved_model == "claude-sonnet-4-5"
        assert provider.list_models.await_count == 1

    @pytest.mark.asyncio
    async def test_waiter_timeout_falls_through_to_direct_call(self) -> None:
        """A caller must not block forever on someone else's in-flight
        fetch -- past the configured wait timeout it falls through to a
        direct, uncached provider.list_models() call of its own.
        """
        provider = AsyncMock()
        release = asyncio.Event()
        calls = 0

        async def hangs_then_direct_succeeds() -> list[str]:
            nonlocal calls
            calls += 1
            if calls == 1:
                # The shared fetch: held open past the wait timeout.
                await release.wait()
                return ["claude-sonnet-4-5"]
            # This caller's own direct fallback call, once it gives up
            # waiting on the (still-hanging) shared fetch.
            return ["claude-haiku-4-5"]

        provider.list_models = AsyncMock(side_effect=hangs_then_direct_succeeds)
        coordinator = self._make_coordinator(provider, key="provider-anthropic")

        import amplifier_foundation.spawn_utils as su

        original_timeout = su.LIST_MODELS_WAIT_TIMEOUT_SECONDS
        su.LIST_MODELS_WAIT_TIMEOUT_SECONDS = 0.05
        try:
            result = await resolve_model_pattern("claude-*", "anthropic", coordinator)
        finally:
            su.LIST_MODELS_WAIT_TIMEOUT_SECONDS = original_timeout
            release.set()
            # Let the still-in-flight shared fetch drain cleanly instead of
            # leaving a pending task for the loop to warn about at teardown.
            entry = su._MODEL_LIST_CACHE.get(provider)
            if entry is not None and entry.task is not None:
                await asyncio.wait_for(entry.task, timeout=1)

        assert result.resolved_model == "claude-haiku-4-5"
        assert calls == 2


class TestListModelsConfigurableKnobs:
    """Regression tests for making the TTL and wait-timeout configurable via
    environment variables while preserving the existing module-global
    monkey-patch surface (`su.LIST_MODELS_CACHE_TTL_SECONDS = ...` etc.).
    """

    def test_ttl_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import amplifier_foundation.spawn_utils as su

        monkeypatch.delenv("AMPLIFIER_LIST_MODELS_CACHE_TTL_SECONDS", raising=False)
        assert su._get_ttl_seconds() == su.LIST_MODELS_CACHE_TTL_SECONDS

        monkeypatch.setenv("AMPLIFIER_LIST_MODELS_CACHE_TTL_SECONDS", "5")
        assert su._get_ttl_seconds() == 5.0

    def test_ttl_module_global_still_monkeypatchable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import amplifier_foundation.spawn_utils as su

        monkeypatch.delenv("AMPLIFIER_LIST_MODELS_CACHE_TTL_SECONDS", raising=False)
        original = su.LIST_MODELS_CACHE_TTL_SECONDS
        try:
            su.LIST_MODELS_CACHE_TTL_SECONDS = 123.0
            assert su._get_ttl_seconds() == 123.0
        finally:
            su.LIST_MODELS_CACHE_TTL_SECONDS = original

    def test_wait_timeout_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import amplifier_foundation.spawn_utils as su

        monkeypatch.delenv("AMPLIFIER_LIST_MODELS_WAIT_TIMEOUT_SECONDS", raising=False)
        assert su._get_wait_timeout_seconds() == su.LIST_MODELS_WAIT_TIMEOUT_SECONDS

        monkeypatch.setenv("AMPLIFIER_LIST_MODELS_WAIT_TIMEOUT_SECONDS", "2.5")
        assert su._get_wait_timeout_seconds() == 2.5

    def test_invalid_env_value_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import amplifier_foundation.spawn_utils as su

        monkeypatch.setenv("AMPLIFIER_LIST_MODELS_CACHE_TTL_SECONDS", "not-a-number")
        assert su._get_ttl_seconds() == su.LIST_MODELS_CACHE_TTL_SECONDS


class TestOverrideOutranksTiedPriorityZeroPrimary:
    """Regression tests for openai_improvement-ejq.

    An override selecting a non-primary provider instance must STRICTLY
    win child provider resolution, even when the user's primary provider
    is declared FIRST at priority=0 (the natural "make this my default
    model" config).

    Before the fix, `_apply_single_override` only ever promoted the
    overridden target to priority=0 and never touched anyone else's
    priority. When the primary was ALSO priority=0 (having been declared
    first -- the ordinary shape of a user's default-provider config), the
    two tied at priority=0 and a stable sort broke the tie by declaration
    order, silently handing resolution back to the primary regardless of
    which instance the override selected. In a 5-run live eval, 100% of
    sub-agent LLM calls ran the primary instead of the role-resolved /
    agent-frontmatter `provider_preferences` selection -- completely
    silently.

    `_pick_default_provider` below simulates the production tie-break
    rule exactly as documented in the bug report, and exactly matching the
    `candidates.sort(key=lambda c: c[0])` stable-sort-by-priority idiom
    `_find_provider_instance` already uses elsewhere in this module: the
    provider with the lowest `config.priority` wins; a tie is broken by
    declaration order (first in the list wins), because Python's
    `sort`/`sorted` are stable.
    """

    @staticmethod
    def _pick_default_provider(mount_plan: dict) -> dict:
        """Simulate production's priority-based default-provider selection."""
        providers = mount_plan["providers"]
        candidates = [
            (p.get("config", {}).get("priority", 0), i) for i, p in enumerate(providers)
        ]
        candidates.sort(key=lambda c: c[0])
        return providers[candidates[0][1]]

    def test_override_second_instance_outranks_priority_zero_primary_same_type(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Two instances of the SAME provider type: the primary is declared
        FIRST at priority=0 (typical user default-model config); the
        override picks the SECOND instance by its explicit `id`. The
        second instance must strictly win resolution -- not merely tie.
        """
        mount_plan = {
            "providers": [
                {
                    "module": "provider-anthropic",
                    "id": "anthropic-primary",
                    "config": {
                        "priority": 0,
                        "default_model": "claude-primary-model",
                    },
                },
                {
                    "module": "provider-anthropic",
                    "id": "anthropic-secondary",
                    "config": {"priority": 10},
                },
            ]
        }
        prefs = [
            ProviderPreference(
                provider="anthropic-secondary", model="claude-secondary-model"
            )
        ]

        with caplog.at_level(logging.DEBUG, logger="amplifier_foundation.spawn_utils"):
            result = apply_provider_preferences(mount_plan, prefs)

        selected = self._pick_default_provider(result)
        assert selected["id"] == "anthropic-secondary", (
            "The overridden (secondary) instance must win child provider "
            "resolution, not silently lose a priority=0 tie to the "
            "first-declared primary."
        )
        assert selected["config"]["default_model"] == "claude-secondary-model"

        # The primary must have been demoted strictly below the target --
        # not merely left tied with it at priority=0.
        primary = result["providers"][0]
        assert primary["id"] == "anthropic-primary"
        assert primary["config"]["priority"] > selected["config"]["priority"]

        # A tie-demotion occurred -- it must be observable.
        assert any(
            "tie-break" in r.message and "anthropic-primary" in r.message
            for r in caplog.records
        ), "Expected a debug-level tie-break log noting the demoted instance"

    @pytest.mark.asyncio
    async def test_override_cross_provider_outranks_priority_zero_primary(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Cross-provider case: primary is a DIFFERENT provider type (openai)
        than the override target (anthropic) -- the mechanism must be
        provider-agnostic, not special-cased to same-module-type
        disambiguation.
        """
        mount_plan = {
            "providers": [
                {
                    "module": "provider-openai",
                    "config": {"priority": 0, "default_model": "gpt-primary-model"},
                },
                {
                    "module": "provider-anthropic",
                    "config": {"priority": 20},
                },
            ]
        }
        prefs = [
            ProviderPreference(provider="anthropic", model="claude-secondary-model")
        ]

        with caplog.at_level(logging.DEBUG, logger="amplifier_foundation.spawn_utils"):
            result = await apply_provider_preferences_with_resolution(
                mount_plan, prefs, coordinator=None
            )

        selected = self._pick_default_provider(result)
        assert selected["module"] == "provider-anthropic", (
            "The overridden anthropic instance must win child provider "
            "resolution over the priority=0, first-declared openai primary."
        )
        assert selected["config"]["default_model"] == "claude-secondary-model"

        primary = result["providers"][0]
        assert primary["module"] == "provider-openai"
        assert primary["config"]["priority"] > selected["config"]["priority"]

        assert any("tie-break" in r.message for r in caplog.records), (
            "Expected a debug-level tie-break log for the cross-provider case too"
        )

    def test_override_selecting_primary_itself_no_demotion_needed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When the override selects the primary itself, there is no tie to
        break (the target IS the priority=0 instance) -- no other provider
        should be touched and no tie-break log should fire.
        """
        mount_plan = {
            "providers": [
                {"module": "provider-anthropic", "config": {"priority": 0}},
                {"module": "provider-openai", "config": {"priority": 20}},
            ]
        }
        prefs = [
            ProviderPreference(provider="anthropic", model="claude-override-model")
        ]

        with caplog.at_level(logging.DEBUG, logger="amplifier_foundation.spawn_utils"):
            result = apply_provider_preferences(mount_plan, prefs)

        selected = self._pick_default_provider(result)
        assert selected["module"] == "provider-anthropic"
        assert selected["config"]["default_model"] == "claude-override-model"
        assert selected["config"]["priority"] == 0

        # Secondary is untouched -- it never tied with the target, so no
        # demotion should have been applied.
        secondary = result["providers"][1]
        assert secondary["config"]["priority"] == 20
        assert "default_model" not in secondary["config"]

        assert not any("tie-break" in r.message for r in caplog.records), (
            "No tie-break demotion should occur when the override target "
            "is already the sole priority=0 instance"
        )


# =============================================================================
# recipes-0ac -- a preference is a (provider, model) PAIR
# =============================================================================
#
# Measured 2026-09-02 on a 14-provider host. Module `provider-anthropic` is
# mounted three times with distinct ids and priorities; the routing matrix
# addresses it by MODULE name ("anthropic") and discriminates with the model
# glob. Before the fix the model half never reached instance resolution, so
# every {anthropic, *} preference landed on whichever anthropic mount ranked
# first and stamped the requested model onto THAT instance's config -- right
# model name, wrong instance, and with it the wrong base_url / context window
# / cache-retention settings. Downstream this put a reasoning-role agent on a
# 65K-context mount and produced 400s.


MEASURED_HOST: list[dict[str, Any]] = [
    {
        "id": "opus",
        "module": "provider-anthropic",
        "config": {"priority": 1, "default_model": "claude-opus-5"},
    },
    {
        "id": "sonnet",
        "module": "provider-anthropic",
        "config": {"priority": 5, "default_model": "claude-sonnet-5"},
    },
    {
        "id": "fable",
        "module": "provider-anthropic",
        "config": {"priority": 6, "default_model": "claude-sonnet-4-5"},
    },
    {
        "id": "gemini",
        "module": "provider-gemini",
        "config": {"priority": 3, "default_model": "gemini-3-pro"},
    },
]


def _measured_host() -> dict[str, Any]:
    """A fresh, deeply-copied copy of the measured mount plan."""
    return {"providers": [{**p, "config": dict(p["config"])} for p in MEASURED_HOST]}


def _promoted(plan: dict[str, Any]) -> dict[str, Any]:
    """The single instance the override promoted to priority 0."""
    winners = [p for p in plan["providers"] if p["config"].get("priority") == 0]
    assert len(winners) == 1, f"expected exactly one promoted mount, got {winners}"
    return winners[0]


def _by_id(plan: dict[str, Any], instance_id: str) -> dict[str, Any]:
    return next(p for p in plan["providers"] if p["id"] == instance_id)


class TestModuleNamedPreferenceResolvesToMatchingInstance:
    """Module-named preferences pick the instance that serves the model."""

    def test_model_glob_selects_matching_instance_not_first_ranked(self) -> None:
        """{anthropic, claude-opus-*} means `opus`, and only `opus`."""
        result = apply_provider_preferences(
            _measured_host(),
            [ProviderPreference(provider="anthropic", model="claude-opus-*")],
        )

        assert _promoted(result)["id"] == "opus"

        # The instance that does NOT serve this model keeps its own config --
        # no stray promotion, no stamped-on model, no borrowed settings.
        fable = _by_id(result, "fable")
        assert fable["config"]["priority"] == 6
        assert fable["config"]["default_model"] == "claude-sonnet-4-5"

    def test_model_selects_lower_priority_instance_that_serves_it(self) -> None:
        """The fix proper: the model half outranks bare priority order.

        Fails before the fix -- `opus` (priority 1, the highest-ranked
        anthropic mount) was promoted and `claude-sonnet-4-5` written onto
        ITS config, even though `fable` is the mount that serves that model.
        """
        result = apply_provider_preferences(
            _measured_host(),
            [ProviderPreference(provider="anthropic", model="claude-sonnet-4-5")],
        )

        promoted = _promoted(result)
        assert promoted["id"] == "fable"
        assert promoted["config"]["default_model"] == "claude-sonnet-4-5"

        opus = _by_id(result, "opus")
        assert opus["config"]["priority"] == 1
        assert opus["config"]["default_model"] == "claude-opus-5"

    def test_no_model_falls_back_to_highest_priority_instance(self) -> None:
        """With nothing to discriminate on, highest priority wins."""
        result = apply_provider_preferences(
            _measured_host(),
            [ProviderPreference(provider="anthropic", model="")],
        )
        assert _promoted(result)["id"] == "opus"

    def test_unmatched_model_still_falls_back_to_highest_priority(self) -> None:
        """A model no mount declares must never turn into a MISS.

        Model metadata in a mount plan is optional and often absent; a hint
        that matches nothing carries no information and must not stop the
        preference from being applied at all.
        """
        result = apply_provider_preferences(
            _measured_host(),
            [ProviderPreference(provider="anthropic", model="claude-unknown-9")],
        )
        assert _promoted(result)["id"] == "opus"

    def test_instance_id_preference_is_exact_and_ignores_model(self) -> None:
        """Naming an instance id addresses that instance, full stop."""
        result = apply_provider_preferences(
            _measured_host(),
            [ProviderPreference(provider="fable", model="claude-sonnet-4-5")],
        )
        assert _promoted(result)["id"] == "fable"

        # Even a model only a SIBLING serves does not redirect an explicit id.
        result = apply_provider_preferences(
            _measured_host(),
            [ProviderPreference(provider="fable", model="claude-opus-5")],
        )
        assert _promoted(result)["id"] == "fable"

    def test_other_module_untouched(self) -> None:
        """Narrowing within one module never reaches across modules."""
        result = apply_provider_preferences(
            _measured_host(),
            [ProviderPreference(provider="anthropic", model="claude-sonnet-4-5")],
        )
        gemini = _by_id(result, "gemini")
        assert gemini["config"]["priority"] == 3
        assert gemini["config"]["default_model"] == "gemini-3-pro"

    def test_single_instance_module_is_unchanged_by_any_model(self) -> None:
        """The common single-mount case resolves regardless of the model."""
        plan = {
            "providers": [
                {
                    "module": "provider-anthropic",
                    "config": {"default_model": "claude-opus-5"},
                },
                {"module": "provider-openai", "config": {}},
            ]
        }
        for model in ("claude-opus-5", "claude-sonnet-4-5", "totally-unknown", ""):
            result = apply_provider_preferences(
                {
                    "providers": [
                        {**p, "config": dict(p["config"])} for p in plan["providers"]
                    ]
                },
                [ProviderPreference(provider="anthropic", model=model)],
            )
            promoted = _promoted(result)
            assert promoted["module"] == "provider-anthropic", f"model={model!r}"
            assert promoted["config"]["default_model"] == model, f"model={model!r}"

    def test_declared_models_list_participates_when_present(self) -> None:
        """A mount that declares a `models` list is selectable by any of them."""
        plan = {
            "providers": [
                {
                    "id": "primary",
                    "module": "provider-anthropic",
                    "config": {"priority": 0, "default_model": "claude-opus-5"},
                },
                {
                    "id": "long-context",
                    "module": "provider-anthropic",
                    "config": {
                        "priority": 9,
                        "default_model": "claude-opus-5",
                        "models": ["claude-opus-5", "claude-opus-5-1m"],
                    },
                },
            ]
        }
        result = apply_provider_preferences(
            plan, [ProviderPreference(provider="anthropic", model="claude-opus-5-1m")]
        )
        assert _promoted(result)["id"] == "long-context"

    def test_model_matching_is_case_insensitive(self) -> None:
        """Model globs fold case, matching resolve_model_pattern()."""
        result = apply_provider_preferences(
            _measured_host(),
            [ProviderPreference(provider="anthropic", model="CLAUDE-SONNET-4-5")],
        )
        assert _promoted(result)["id"] == "fable"


class TestProviderResolutionHelpersAgree:
    """`_find_provider_index` and `_build_provider_lookup` are one function."""

    def test_helpers_agree_on_every_addressable_name(self) -> None:
        lookup = _build_provider_lookup(MEASURED_HOST)
        names = [
            "anthropic",
            "provider-anthropic",
            "gemini",
            "provider-gemini",
            "opus",
            "sonnet",
            "fable",
        ]
        for name in names:
            assert _find_provider_index(MEASURED_HOST, name) == lookup[name], name

    def test_module_name_resolves_to_highest_priority_instance(self) -> None:
        """Never the last-declared one (`fable`, priority 6)."""
        lookup = _build_provider_lookup(MEASURED_HOST)
        assert lookup["anthropic"] == 0
        assert lookup["provider-anthropic"] == 0
        assert _find_provider_index(MEASURED_HOST, "anthropic") == 0

    def test_find_provider_index_honours_the_model_hint(self) -> None:
        """The hint is optional; supplying it narrows to the serving mount."""
        assert _find_provider_index(MEASURED_HOST, "anthropic") == 0
        assert (
            _find_provider_index(MEASURED_HOST, "anthropic", "claude-sonnet-4-5") == 2
        )
        assert _find_provider_index(MEASURED_HOST, "anthropic", "claude-opus-*") == 0

    def test_unknown_name_is_still_a_miss(self) -> None:
        assert _find_provider_index(MEASURED_HOST, "cohere") is None
        assert _find_provider_index(MEASURED_HOST, "cohere", "command-r") is None
        assert "cohere" not in _build_provider_lookup(MEASURED_HOST)


class TestModuleNamedPreferenceWithAsyncResolution:
    """The async path resolves the glob against the instance it promotes."""

    @pytest.mark.asyncio
    async def test_async_path_promotes_the_model_matching_instance(self) -> None:
        provider = MagicMock()
        provider.list_models = AsyncMock(
            return_value=["claude-opus-5", "claude-sonnet-5", "claude-sonnet-4-5"]
        )
        coordinator = MagicMock()
        coordinator.get = MagicMock(return_value={"anthropic": provider})

        result = await apply_provider_preferences_with_resolution(
            _measured_host(),
            [ProviderPreference(provider="anthropic", model="claude-sonnet-4-*")],
            coordinator,
        )

        promoted = _promoted(result)
        assert promoted["id"] == "fable"
        assert promoted["config"]["default_model"] == "claude-sonnet-4-5"

    @pytest.mark.asyncio
    async def test_async_path_preserves_protected_config_keys(self) -> None:
        """PROTECTED_CONFIG_KEYS survive selection by model, as ever."""
        plan = _measured_host()
        _by_id(plan, "fable")["config"]["api_key"] = "fable-secret"

        result = await apply_provider_preferences_with_resolution(
            plan,
            [
                ProviderPreference(
                    provider="anthropic",
                    model="claude-sonnet-4-5",
                    config={"api_key": "injected", "reasoning_effort": "high"},
                )
            ],
            MagicMock(get=MagicMock(return_value={})),
        )

        promoted = _promoted(result)
        assert promoted["id"] == "fable"
        assert promoted["config"]["api_key"] == "fable-secret"
        assert promoted["config"]["reasoning_effort"] == "high"
