"""Tests for spawn_utils module - provider preferences and model resolution."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from amplifier_foundation.spawn_utils import ClassPreference
from amplifier_foundation.spawn_utils import ProviderPreference
from amplifier_foundation.spawn_utils import RoutingConfig
from amplifier_foundation.spawn_utils import apply_provider_preferences
from amplifier_foundation.spawn_utils import apply_provider_preferences_with_resolution
from amplifier_foundation.spawn_utils import is_glob_pattern
from amplifier_foundation.spawn_utils import preference_from_dict
from amplifier_foundation.spawn_utils import resolve_model_class
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


class TestClassPreference:
    """Tests for ClassPreference dataclass."""

    def test_create_class_preference(self) -> None:
        """Test creating a ClassPreference with defaults."""
        pref = ClassPreference(class_name="fast")
        assert pref.class_name == "fast"
        assert pref.required is False

    def test_create_class_preference_required(self) -> None:
        """Test creating a ClassPreference with required=True."""
        pref = ClassPreference(class_name="premium", required=True)
        assert pref.class_name == "premium"
        assert pref.required is True

    def test_to_dict_basic(self) -> None:
        """Test to_dict without required flag."""
        pref = ClassPreference(class_name="fast")
        result = pref.to_dict()
        assert result == {"class": "fast"}
        assert "required" not in result

    def test_to_dict_required(self) -> None:
        """Test to_dict with required=True includes required key."""
        pref = ClassPreference(class_name="premium", required=True)
        result = pref.to_dict()
        assert result == {"class": "premium", "required": True}

    def test_from_dict(self) -> None:
        """Test creating ClassPreference from dict."""
        data = {"class": "fast"}
        pref = ClassPreference.from_dict(data)
        assert pref.class_name == "fast"
        assert pref.required is False

    def test_from_dict_with_required(self) -> None:
        """Test creating ClassPreference from dict with required."""
        data = {"class": "premium", "required": True}
        pref = ClassPreference.from_dict(data)
        assert pref.class_name == "premium"
        assert pref.required is True

    def test_from_dict_missing_class_key(self) -> None:
        """Test from_dict raises ValueError when 'class' key is missing."""
        with pytest.raises(ValueError, match="requires 'class' key"):
            ClassPreference.from_dict({"name": "fast"})

    def test_to_dict_from_dict_roundtrip(self) -> None:
        """Test that to_dict/from_dict round-trips preserve equality."""
        pref = ClassPreference(class_name="premium", required=True)
        assert ClassPreference.from_dict(pref.to_dict()) == pref

        pref_basic = ClassPreference(class_name="fast")
        assert ClassPreference.from_dict(pref_basic.to_dict()) == pref_basic


class TestPreferenceFromDict:
    """Tests for preference_from_dict discriminating deserializer."""

    def test_provider_preference(self) -> None:
        """Test that entries with 'provider' key produce ProviderPreference."""
        data = {"provider": "anthropic", "model": "claude-haiku-3"}
        result = preference_from_dict(data)
        assert isinstance(result, ProviderPreference)
        assert result.provider == "anthropic"
        assert result.model == "claude-haiku-3"

    def test_class_preference(self) -> None:
        """Test that entries with 'class' key produce ClassPreference."""
        data = {"class": "fast"}
        result = preference_from_dict(data)
        assert isinstance(result, ClassPreference)
        assert result.class_name == "fast"

    def test_class_preference_with_required(self) -> None:
        """Test that class entries with required flag are deserialized correctly."""
        data = {"class": "premium", "required": True}
        result = preference_from_dict(data)
        assert isinstance(result, ClassPreference)
        assert result.class_name == "premium"
        assert result.required is True

    def test_both_keys_prefers_class(self) -> None:
        """Test that 'class' takes priority when both 'class' and 'provider' are present."""
        data = {"class": "fast", "provider": "anthropic", "model": "claude-haiku-3"}
        result = preference_from_dict(data)
        assert isinstance(result, ClassPreference)
        assert result.class_name == "fast"

    def test_unknown_preference_raises(self) -> None:
        """Test that entries without 'provider' or 'class' raise ValueError."""
        with pytest.raises(ValueError, match="provider.*class"):
            preference_from_dict({"model": "gpt-4"})


class TestRoutingConfig:
    """Tests for RoutingConfig dataclass."""

    def test_defaults(self) -> None:
        """Test RoutingConfig default values."""
        config = RoutingConfig()
        assert config.strategy == "balanced"
        assert config.max_tier is None
        assert config.classes is None

    def test_custom_strategy(self) -> None:
        """Test RoutingConfig with custom strategy."""
        config = RoutingConfig(strategy="cost")
        assert config.strategy == "cost"

    def test_from_dict_minimal(self) -> None:
        """Test from_dict with empty dict gives defaults."""
        config = RoutingConfig.from_dict({})
        assert config.strategy == "balanced"
        assert config.max_tier is None
        assert config.classes is None

    def test_from_dict_full(self) -> None:
        """Test from_dict with all fields populated."""
        data = {
            "strategy": "quality",
            "max_tier": "tier-2",
            "classes": {"fast": {"max_latency": 100}},
        }
        config = RoutingConfig.from_dict(data)
        assert config.strategy == "quality"
        assert config.max_tier == "tier-2"
        assert config.classes == {"fast": {"max_latency": 100}}

    def test_from_dict_partial(self) -> None:
        """Test from_dict with only some fields."""
        data = {"strategy": "cost", "max_tier": "tier-1"}
        config = RoutingConfig.from_dict(data)
        assert config.strategy == "cost"
        assert config.max_tier == "tier-1"
        assert config.classes is None

    def test_to_dict_defaults(self) -> None:
        """Test to_dict with default values."""
        config = RoutingConfig()
        result = config.to_dict()
        assert result == {"strategy": "balanced"}
        assert "max_tier" not in result
        assert "classes" not in result

    def test_to_dict_full(self) -> None:
        """Test to_dict with all fields populated."""
        config = RoutingConfig(
            strategy="quality",
            max_tier="tier-2",
            classes={"fast": {"max_latency": 100}},
        )
        result = config.to_dict()
        assert result == {
            "strategy": "quality",
            "max_tier": "tier-2",
            "classes": {"fast": {"max_latency": 100}},
        }

    def test_from_dict_invalid_strategy_raises(self) -> None:
        """Test from_dict raises ValueError for invalid strategy values."""
        with pytest.raises(ValueError, match="strategy"):
            RoutingConfig.from_dict({"strategy": "invalid"})

    def test_direct_construction_invalid_strategy_raises(self) -> None:
        """Test that direct construction with invalid strategy raises ValueError."""
        with pytest.raises(ValueError, match="strategy"):
            RoutingConfig(strategy="invalid")  # type: ignore[arg-type]

    def test_to_dict_from_dict_roundtrip(self) -> None:
        """Test that to_dict/from_dict round-trips preserve equality."""
        config = RoutingConfig(
            strategy="cost",
            max_tier="tier-1",
            classes={"fast": {"provider": "openai"}},
        )
        assert RoutingConfig.from_dict(config.to_dict()) == config

        config_default = RoutingConfig()
        assert RoutingConfig.from_dict(config_default.to_dict()) == config_default


class TestResolveModelClass:
    """Tests for resolve_model_class() — dynamic class-to-model resolution."""

    def _make_mock_coordinator(self, providers_dict):
        """Build a mock coordinator with providers that return ModelInfo lists."""
        coordinator = MagicMock()
        coordinator.get.return_value = providers_dict
        coordinator.get_capability.return_value = None  # no routing config
        return coordinator

    def _make_mock_provider(self, name, models):
        """Build a mock provider with list_models()."""
        provider = MagicMock()
        provider.name = name
        provider.list_models = AsyncMock(return_value=models)
        return provider

    @pytest.mark.asyncio
    async def test_reasoning_class_matches_high_cost_tier(self):
        """Reasoning class resolves by cost_tier=high, not capability tags."""
        from amplifier_core import ModelInfo

        provider = self._make_mock_provider(
            "anthropic",
            [
                ModelInfo(
                    id="claude-opus-4-6",
                    display_name="Claude Opus 4.6",
                    context_window=200000,
                    max_output_tokens=64000,
                    capabilities=["tools", "thinking", "streaming"],
                    metadata={"cost_tier": "high"},
                ),
                ModelInfo(
                    id="claude-sonnet-4-6",
                    display_name="Claude Sonnet 4.6",
                    context_window=200000,
                    max_output_tokens=64000,
                    capabilities=["tools", "thinking", "streaming"],
                    metadata={"cost_tier": "medium"},
                ),
            ],
        )
        coordinator = self._make_mock_coordinator({"provider-anthropic": provider})

        results = await resolve_model_class("reasoning", coordinator)
        assert len(results) == 1
        assert results[0].provider == "anthropic"
        assert results[0].model == "claude-opus-4-6"

    @pytest.mark.asyncio
    async def test_reasoning_class_excludes_non_high_tier(self):
        """Reasoning class uses cost_tier=high — medium-tier models are excluded
        even if they have a 'reasoning' capability tag."""
        from amplifier_core import ModelInfo

        provider = self._make_mock_provider(
            "openai",
            [
                ModelInfo(
                    id="gpt-5.2",
                    display_name="GPT-5.2",
                    context_window=400000,
                    max_output_tokens=128000,
                    capabilities=["tools", "reasoning", "streaming"],
                    metadata={"cost_tier": "medium"},
                ),
                ModelInfo(
                    id="gpt-5.2-pro",
                    display_name="GPT-5.2 Pro",
                    context_window=400000,
                    max_output_tokens=128000,
                    capabilities=["tools", "reasoning", "streaming"],
                    metadata={"cost_tier": "high"},
                ),
            ],
        )
        coordinator = self._make_mock_coordinator({"provider-openai": provider})

        results = await resolve_model_class("reasoning", coordinator)
        assert len(results) == 1
        assert results[0].model == "gpt-5.2-pro"

    @pytest.mark.asyncio
    async def test_fast_class_filters_to_fast_models(self):
        """Fast class resolves by cost_tier=low, not capability tags."""
        from amplifier_core import ModelInfo

        provider = self._make_mock_provider(
            "anthropic",
            [
                ModelInfo(
                    id="claude-opus-4-6",
                    display_name="Claude Opus 4.6",
                    context_window=200000,
                    max_output_tokens=64000,
                    capabilities=["tools", "thinking"],
                    metadata={"cost_tier": "high"},
                ),
                ModelInfo(
                    id="claude-haiku-4-5",
                    display_name="Claude Haiku 4.5",
                    context_window=200000,
                    max_output_tokens=64000,
                    capabilities=["tools"],
                    metadata={"cost_tier": "low"},
                ),
            ],
        )
        coordinator = self._make_mock_coordinator({"provider-anthropic": provider})

        results = await resolve_model_class("fast", coordinator)
        assert len(results) == 1
        assert results[0].model == "claude-haiku-4-5"

    @pytest.mark.asyncio
    async def test_max_tier_filters_expensive_models(self):
        """max_tier caps cost: models above the tier are excluded."""
        from amplifier_core import ModelInfo

        provider = self._make_mock_provider(
            "anthropic",
            [
                ModelInfo(
                    id="claude-opus-4-6",
                    display_name="Claude Opus 4.6",
                    context_window=200000,
                    max_output_tokens=64000,
                    capabilities=["tools", "vision"],
                    metadata={"cost_tier": "high"},
                ),
                ModelInfo(
                    id="claude-sonnet-4-6",
                    display_name="Claude Sonnet 4.6",
                    context_window=200000,
                    max_output_tokens=64000,
                    capabilities=["tools", "vision"],
                    metadata={"cost_tier": "medium"},
                ),
            ],
        )
        coordinator = self._make_mock_coordinator({"provider-anthropic": provider})

        routing = RoutingConfig(strategy="balanced", max_tier="medium")
        coordinator.get_capability.return_value = routing

        results = await resolve_model_class("vision", coordinator)
        assert len(results) == 1
        assert results[0].model == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_cost_strategy_sorts_cheapest_first(self):
        """With strategy=cost, cheaper models sort before expensive ones."""
        from amplifier_core import ModelInfo

        provider_a = self._make_mock_provider(
            "anthropic",
            [
                ModelInfo(
                    id="claude-opus-4-6",
                    display_name="Opus",
                    context_window=200000,
                    max_output_tokens=64000,
                    capabilities=["tools", "vision"],
                    metadata={"cost_tier": "high"},
                ),
            ],
        )
        provider_b = self._make_mock_provider(
            "openai",
            [
                ModelInfo(
                    id="gpt-5.2",
                    display_name="GPT-5.2",
                    context_window=400000,
                    max_output_tokens=128000,
                    capabilities=["tools", "vision"],
                    metadata={"cost_tier": "medium"},
                ),
            ],
        )
        coordinator = self._make_mock_coordinator(
            {
                "provider-anthropic": provider_a,
                "provider-openai": provider_b,
            }
        )
        routing = RoutingConfig(strategy="cost")
        coordinator.get_capability.return_value = routing

        results = await resolve_model_class("vision", coordinator)
        assert len(results) == 2
        # cost strategy: medium (gpt-5.2) before high (opus)
        assert results[0].model == "gpt-5.2"
        assert results[1].model == "claude-opus-4-6"

    @pytest.mark.asyncio
    async def test_unknown_class_passes_through_as_literal(self):
        """Unknown class names are treated as literal capability strings."""
        from amplifier_core import ModelInfo

        provider = self._make_mock_provider(
            "openai",
            [
                ModelInfo(
                    id="gpt-5.2",
                    display_name="GPT-5.2",
                    context_window=400000,
                    max_output_tokens=128000,
                    capabilities=["tools", "custom_capability"],
                ),
            ],
        )
        coordinator = self._make_mock_coordinator({"provider-openai": provider})

        results = await resolve_model_class("custom_capability", coordinator)
        assert len(results) == 1
        assert results[0].model == "gpt-5.2"

    @pytest.mark.asyncio
    async def test_no_matching_models_returns_empty(self):
        from amplifier_core import ModelInfo

        provider = self._make_mock_provider(
            "anthropic",
            [
                ModelInfo(
                    id="claude-sonnet-4-6",
                    display_name="Sonnet",
                    context_window=200000,
                    max_output_tokens=64000,
                    capabilities=["tools", "thinking"],
                ),
            ],
        )
        coordinator = self._make_mock_coordinator({"provider-anthropic": provider})

        results = await resolve_model_class("vision", coordinator)
        assert results == []

    @pytest.mark.asyncio
    async def test_provider_list_models_failure_is_skipped(self):
        """If a provider's list_models() raises, it's skipped (not fatal)."""
        from amplifier_core import ModelInfo

        good_provider = self._make_mock_provider(
            "openai",
            [
                ModelInfo(
                    id="gpt-5.2",
                    display_name="GPT-5.2",
                    context_window=400000,
                    max_output_tokens=128000,
                    capabilities=["tools", "reasoning"],
                    metadata={"cost_tier": "high"},
                ),
            ],
        )
        bad_provider = self._make_mock_provider("anthropic", [])
        bad_provider.list_models = AsyncMock(side_effect=Exception("API down"))

        coordinator = self._make_mock_coordinator(
            {
                "provider-anthropic": bad_provider,
                "provider-openai": good_provider,
            }
        )

        results = await resolve_model_class("reasoning", coordinator)
        assert len(results) == 1
        assert results[0].provider == "openai"

    @pytest.mark.asyncio
    async def test_standard_class_matches_medium_cost_tier(self):
        """Standard class should match models with cost_tier=medium."""
        from amplifier_core import ModelInfo

        provider = self._make_mock_provider(
            "anthropic",
            [
                ModelInfo(
                    id="claude-opus-4-6",
                    display_name="Opus",
                    context_window=200000,
                    max_output_tokens=64000,
                    capabilities=["tools", "thinking"],
                    metadata={"cost_tier": "high"},
                ),
                ModelInfo(
                    id="claude-sonnet-4-6",
                    display_name="Sonnet",
                    context_window=200000,
                    max_output_tokens=64000,
                    capabilities=["tools", "thinking"],
                    metadata={"cost_tier": "medium"},
                ),
                ModelInfo(
                    id="claude-haiku-4-5",
                    display_name="Haiku",
                    context_window=200000,
                    max_output_tokens=64000,
                    capabilities=["tools"],
                    metadata={"cost_tier": "low"},
                ),
            ],
        )
        coordinator = self._make_mock_coordinator({"provider-anthropic": provider})

        results = await resolve_model_class("standard", coordinator)
        assert len(results) == 1
        assert results[0].model == "claude-sonnet-4-6"


class TestApplyPreferencesWithClassResolution:
    """Tests for ClassPreference handling in apply_provider_preferences_with_resolution()."""

    def _make_mount_plan(
        self, *provider_modules: str
    ) -> dict[str, list[dict[str, Any]]]:
        """Build a mount plan with given provider module names."""
        return {
            "providers": [
                {"module": module, "config": {"priority": 10 + i}}
                for i, module in enumerate(provider_modules)
            ]
        }

    def _make_anthropic_coordinator(self) -> MagicMock:
        """Build a mock coordinator whose anthropic provider lists claude-haiku-4-5."""
        from amplifier_core import ModelInfo

        mock_provider = MagicMock()
        mock_provider.list_models = AsyncMock(
            return_value=[
                ModelInfo(
                    id="claude-haiku-4-5",
                    display_name="Claude Haiku 4.5",
                    context_window=200000,
                    max_output_tokens=64000,
                    capabilities=["tools"],
                    metadata={"cost_tier": "low"},
                ),
            ]
        )

        mock_coordinator = MagicMock()
        mock_coordinator.get.return_value = {"provider-anthropic": mock_provider}
        mock_coordinator.get_capability.return_value = None
        return mock_coordinator

    @pytest.mark.asyncio
    async def test_class_entry_resolves_and_applies(self) -> None:
        """ClassPreference resolves via resolve_model_class, first candidate
        in mount plan is promoted to priority 0 with default_model set."""
        mount_plan = self._make_mount_plan("provider-anthropic", "provider-openai")
        mock_coordinator = self._make_anthropic_coordinator()

        prefs: list[ProviderPreference | ClassPreference] = [
            ClassPreference(class_name="fast"),
        ]

        result = await apply_provider_preferences_with_resolution(
            mount_plan, prefs, mock_coordinator
        )

        # Anthropic should be promoted to priority 0 with resolved model
        assert result["providers"][0]["config"]["priority"] == 0
        assert result["providers"][0]["config"]["default_model"] == "claude-haiku-4-5"
        # OpenAI should be unchanged
        assert result["providers"][1]["config"]["priority"] == 11

    @pytest.mark.asyncio
    async def test_mixed_class_and_provider_entries(self) -> None:
        """ClassPreference fails (no candidate in mount plan), ProviderPreference
        fallback succeeds."""
        # Mount plan only has openai
        mount_plan = self._make_mount_plan("provider-openai")
        mock_coordinator = self._make_anthropic_coordinator()

        prefs: list[ProviderPreference | ClassPreference] = [
            ClassPreference(class_name="fast"),  # resolves to anthropic, not in plan
            ProviderPreference(provider="openai", model="gpt-4o-mini"),  # fallback
        ]

        result = await apply_provider_preferences_with_resolution(
            mount_plan, prefs, mock_coordinator
        )

        # OpenAI fallback should be applied
        assert result["providers"][0]["config"]["priority"] == 0
        assert result["providers"][0]["config"]["default_model"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_required_class_no_match_raises(self) -> None:
        """ClassPreference with required=True raises ValueError when no
        candidates match the mount plan."""
        mount_plan = self._make_mount_plan("provider-openai")
        mock_coordinator = self._make_anthropic_coordinator()

        prefs: list[ProviderPreference | ClassPreference] = [
            ClassPreference(class_name="fast", required=True),
        ]

        with pytest.raises(ValueError, match="anthropic"):
            await apply_provider_preferences_with_resolution(
                mount_plan, prefs, mock_coordinator
            )

    @pytest.mark.asyncio
    async def test_optional_class_no_match_skips(self) -> None:
        """ClassPreference with required=False skips to next preference
        when no candidates match the mount plan."""
        mount_plan = self._make_mount_plan("provider-openai")
        mock_coordinator = self._make_anthropic_coordinator()

        prefs: list[ProviderPreference | ClassPreference] = [
            ClassPreference(class_name="fast", required=False),  # no match, skip
            ProviderPreference(provider="openai", model="gpt-4o-mini"),  # fallback
        ]

        result = await apply_provider_preferences_with_resolution(
            mount_plan, prefs, mock_coordinator
        )

        # Should fall through to openai
        assert result["providers"][0]["config"]["priority"] == 0
        assert result["providers"][0]["config"]["default_model"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_backward_compat_provider_preference_only(self) -> None:
        """A list of only ProviderPreference entries works identically to before."""
        mount_plan = self._make_mount_plan("provider-anthropic", "provider-openai")

        mock_provider = AsyncMock()
        mock_provider.list_models = AsyncMock(
            return_value=[
                "claude-3-haiku-20240101",
                "claude-3-haiku-20240307",
            ]
        )
        mock_coordinator = MagicMock()
        mock_coordinator.get.return_value = {"provider-anthropic": mock_provider}

        prefs: list[ProviderPreference | ClassPreference] = [
            ProviderPreference(provider="anthropic", model="claude-3-haiku-*"),
            ProviderPreference(provider="openai", model="gpt-4o-mini"),
        ]

        result = await apply_provider_preferences_with_resolution(
            mount_plan, prefs, mock_coordinator
        )

        # Anthropic should be promoted with resolved model
        assert result["providers"][0]["config"]["priority"] == 0
        assert (
            result["providers"][0]["config"]["default_model"]
            == "claude-3-haiku-20240307"
        )
        # OpenAI unchanged
        assert result["providers"][1]["config"]["priority"] == 11
