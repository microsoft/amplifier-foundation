"""Tests for spawn_utils module - provider preferences and model resolution."""

from __future__ import annotations

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from amplifier_foundation.spawn_utils import ProviderPreference
from amplifier_foundation.spawn_utils import _apply_single_override
from amplifier_foundation.spawn_utils import _build_provider_lookup
from amplifier_foundation.spawn_utils import _find_provider_index
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
    async def test_pattern_no_matches_returns_pattern(self) -> None:
        """Test that unmatched patterns are returned as-is."""
        mock_provider = AsyncMock()
        mock_provider.list_models = AsyncMock(return_value=["gpt-4o", "gpt-4o-mini"])

        mock_coordinator = MagicMock()
        mock_coordinator.get.return_value = {"provider-openai": mock_provider}

        result = await resolve_model_pattern(
            "claude-*",  # No Claude models in OpenAI
            "openai",
            mock_coordinator,
        )

        assert result.resolved_model == "claude-*"
        assert result.matched_models == []


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
