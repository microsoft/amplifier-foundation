"""Utilities for session spawning with provider/model selection.

This module provides mechanisms for specifying provider/model preferences
when spawning sub-sessions. It supports:
- Ordered list of provider/model pairs (fallback chain)
- Model glob pattern resolution (e.g., "claude-haiku-*")
- Flexible provider matching (e.g., "anthropic" matches "provider-anthropic")
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ProviderPreference:
    """A provider/model preference for ordered selection.

    Used with provider_preferences to specify fallback order when spawning
    sub-sessions. The system tries each preference in order until finding
    an available provider.

    Model supports glob patterns (e.g., "claude-haiku-*") which are resolved
    against the provider's available models.

    Attributes:
        provider: Provider identifier (e.g., "anthropic", "openai", "azure").
            Supports flexible matching - "anthropic" matches "provider-anthropic".
        model: Model name or glob pattern (e.g., "claude-haiku-*", "gpt-4o-mini").
            Patterns are resolved to concrete model names at runtime.

    Example:
        >>> prefs = [
        ...     ProviderPreference(provider="anthropic", model="claude-haiku-*"),
        ...     ProviderPreference(provider="openai", model="gpt-4o-mini"),
        ... ]
    """

    provider: str
    model: str

    def to_dict(self) -> dict[str, str]:
        """Convert to dictionary representation."""
        return {"provider": self.provider, "model": self.model}

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> ProviderPreference:
        """Create from dictionary representation.

        Args:
            data: Dictionary with 'provider' and 'model' keys.

        Returns:
            ProviderPreference instance.

        Raises:
            ValueError: If required keys are missing.
        """
        if "provider" not in data:
            raise ValueError("ProviderPreference requires 'provider' key")
        if "model" not in data:
            raise ValueError("ProviderPreference requires 'model' key")
        return cls(provider=data["provider"], model=data["model"])


@dataclass
class ModelResolutionResult:
    """Result of model pattern resolution.

    Attributes:
        resolved_model: The final model name to use.
        pattern: Original pattern (None if input wasn't a pattern).
        available_models: All models available from the provider.
        matched_models: Models that matched the pattern.
    """

    resolved_model: str
    pattern: str | None = None
    available_models: list[str] | None = None
    matched_models: list[str] | None = None


def is_glob_pattern(model_hint: str) -> bool:
    """Check if model_hint contains glob pattern characters.

    Args:
        model_hint: Model name or pattern to check.

    Returns:
        True if the string contains glob wildcards (*, ?, [).
    """
    return any(c in model_hint for c in "*?[")


async def resolve_model_pattern(
    model_hint: str,
    provider_name: str | None,
    coordinator: Any,
) -> ModelResolutionResult:
    """Resolve a model pattern to a concrete model name.

    Args:
        model_hint: Exact model name or glob pattern (e.g., "claude-haiku-*").
        provider_name: Provider to query for available models (e.g., "anthropic").
        coordinator: Amplifier coordinator for accessing providers.

    Returns:
        ModelResolutionResult with resolved model and resolution metadata.

    Resolution strategy:
        1. If not a glob pattern, return as-is
        2. Query provider for available models
        3. Filter with fnmatch
        4. Sort descending (latest date/version wins)
        5. Return first match, or original if no matches
    """
    # Not a pattern - return as-is
    if not is_glob_pattern(model_hint):
        logger.debug("Model '%s' is not a pattern, using as-is", model_hint)
        return ModelResolutionResult(
            resolved_model=model_hint,
            pattern=None,
            available_models=None,
            matched_models=None,
        )

    # Need provider to resolve pattern
    if not provider_name:
        logger.warning(
            "Model pattern '%s' specified but no provider - cannot resolve, using as-is",
            model_hint,
        )
        return ModelResolutionResult(
            resolved_model=model_hint,
            pattern=model_hint,
            available_models=None,
            matched_models=None,
        )

    # Try to get available models from provider
    available_models: list[str] = []
    try:
        providers = coordinator.get("providers")
        if providers:
            provider = _find_provider_instance(providers, provider_name)
            if provider and hasattr(provider, "list_models"):
                models = await provider.list_models()
                # Handle both list of strings and list of model objects
                available_models = [
                    m if isinstance(m, str) else getattr(m, "id", str(m))
                    for m in models
                ]
                logger.debug(
                    "Provider '%s' has %d available models",
                    provider_name,
                    len(available_models),
                )
            else:
                logger.debug(
                    "Provider '%s' not found or does not support list_models()",
                    provider_name,
                )
    except Exception as e:
        logger.warning(
            "Failed to query models from provider '%s': %s",
            provider_name,
            e,
        )

    if not available_models:
        logger.warning(
            "No available models from provider '%s' for pattern '%s' - using pattern as-is",
            provider_name,
            model_hint,
        )
        return ModelResolutionResult(
            resolved_model=model_hint,
            pattern=model_hint,
            available_models=[],
            matched_models=[],
        )

    # Match pattern against available models
    matched = fnmatch.filter(available_models, model_hint)

    if not matched:
        logger.warning(
            "Pattern '%s' matched no models from provider '%s'. "
            "Available: %s. Using pattern as-is.",
            model_hint,
            provider_name,
            ", ".join(available_models[:10])
            + ("..." if len(available_models) > 10 else ""),
        )
        return ModelResolutionResult(
            resolved_model=model_hint,
            pattern=model_hint,
            available_models=available_models,
            matched_models=[],
        )

    # Sort descending (latest date/version typically sorts last alphabetically,
    # so reverse sort puts newest first)
    matched.sort(reverse=True)
    resolved = matched[0]

    logger.info(
        "Resolved model pattern '%s' -> '%s' (matched %d of %d available: %s)",
        model_hint,
        resolved,
        len(matched),
        len(available_models),
        ", ".join(matched[:5]) + ("..." if len(matched) > 5 else ""),
    )

    return ModelResolutionResult(
        resolved_model=resolved,
        pattern=model_hint,
        available_models=available_models,
        matched_models=matched,
    )


def _find_provider_instance(
    providers: dict[str, Any],
    provider_name: str,
) -> Any | None:
    """Find a provider instance by name with flexible matching.

    Args:
        providers: Dict of mounted providers by name.
        provider_name: Provider to find (e.g., "anthropic").

    Returns:
        Provider instance or None if not found.
    """
    for name, provider in providers.items():
        if provider_name in (
            name,
            name.replace("provider-", ""),
            f"provider-{provider_name}",
        ):
            return provider
    return None


def _find_provider_index(
    providers: list[dict[str, Any]],
    provider_id: str,
) -> int | None:
    """Find the index of a provider in the providers list.

    Supports flexible matching: "anthropic", "provider-anthropic",
    or full module ID.

    Args:
        providers: List of provider configs from mount plan.
        provider_id: Provider to find.

    Returns:
        Index of the provider, or None if not found.
    """
    for i, p in enumerate(providers):
        module_id = p.get("module", "")
        if provider_id in (
            module_id,
            module_id.replace("provider-", ""),
            f"provider-{provider_id}",
        ):
            return i
    return None


def _build_provider_lookup(
    providers: list[dict[str, Any]],
) -> dict[str, int]:
    """Build a lookup dict mapping provider names to indices.

    Args:
        providers: List of provider configs from mount plan.

    Returns:
        Dict mapping various name formats to provider index.
    """
    lookup: dict[str, int] = {}
    for i, p in enumerate(providers):
        module_id = p.get("module", "")
        lookup[module_id] = i
        # Also index by short name
        short_name = module_id.replace("provider-", "")
        if short_name != module_id:
            lookup[short_name] = i
        # And with provider- prefix
        lookup[f"provider-{short_name}"] = i
    return lookup


def apply_provider_preferences(
    mount_plan: dict[str, Any],
    preferences: list[ProviderPreference],
) -> dict[str, Any]:
    """Apply provider preferences to a mount plan.

    Finds the first preferred provider that exists in the mount plan,
    promotes it to priority 0 (highest), and sets its model.

    Args:
        mount_plan: The mount plan to modify (will be shallow-copied).
        preferences: Ordered list of ProviderPreference objects.
            The system tries each in order until finding an available provider.

    Returns:
        New mount plan with the first matching provider promoted.
        Returns original mount plan if no preferences match.

    Example:
        >>> prefs = [
        ...     ProviderPreference(provider="anthropic", model="claude-haiku-3"),
        ...     ProviderPreference(provider="openai", model="gpt-4o-mini"),
        ... ]
        >>> new_plan = apply_provider_preferences(plan, prefs)
    """
    if not preferences:
        return mount_plan

    providers = mount_plan.get("providers", [])
    if not providers:
        logger.warning("Provider preferences specified but no providers in mount plan")
        return mount_plan

    # Build lookup for efficient matching
    lookup = _build_provider_lookup(providers)

    # Find first matching preference
    for pref in preferences:
        if pref.provider in lookup:
            target_idx = lookup[pref.provider]
            return _apply_single_override(mount_plan, providers, target_idx, pref.model)

    # No preferences matched
    logger.warning(
        "No preferred providers found in mount plan. Preferences: %s, Available: %s",
        [p.provider for p in preferences],
        list({p.get("module", "?") for p in providers}),
    )
    return mount_plan


def _apply_single_override(
    mount_plan: dict[str, Any],
    providers: list[dict[str, Any]],
    target_idx: int,
    model: str,
) -> dict[str, Any]:
    """Apply a single provider/model override to the mount plan.

    Args:
        mount_plan: Original mount plan.
        providers: Original providers list.
        target_idx: Index of provider to promote.
        model: Model to set for the provider.

    Returns:
        New mount plan with override applied.
    """
    # Clone mount plan and providers list
    new_plan = dict(mount_plan)
    new_providers = []

    for i, p in enumerate(providers):
        p_copy = dict(p)
        p_copy["config"] = dict(p.get("config", {}))

        if i == target_idx:
            # Promote to priority 0 (highest)
            p_copy["config"]["priority"] = 0
            p_copy["config"]["model"] = model
            logger.info(
                "Provider preference applied: %s (priority=0, model=%s)",
                p_copy.get("module"),
                model,
            )

        new_providers.append(p_copy)

    new_plan["providers"] = new_providers
    return new_plan


async def apply_provider_preferences_with_resolution(
    mount_plan: dict[str, Any],
    preferences: list[ProviderPreference],
    coordinator: Any,
) -> dict[str, Any]:
    """Apply provider preferences with model pattern resolution.

    Like apply_provider_preferences(), but also resolves glob patterns
    in model names (e.g., "claude-haiku-*" -> "claude-3-haiku-20240307").

    Args:
        mount_plan: The mount plan to modify.
        preferences: Ordered list of ProviderPreference objects.
        coordinator: Amplifier coordinator for querying provider models.

    Returns:
        New mount plan with the first matching provider promoted and
        model pattern resolved.

    Example:
        >>> prefs = [
        ...     ProviderPreference(provider="anthropic", model="claude-haiku-*"),
        ...     ProviderPreference(provider="openai", model="gpt-4o-mini"),
        ... ]
        >>> new_plan = await apply_provider_preferences_with_resolution(
        ...     plan, prefs, coordinator
        ... )
    """
    if not preferences:
        return mount_plan

    providers = mount_plan.get("providers", [])
    if not providers:
        logger.warning("Provider preferences specified but no providers in mount plan")
        return mount_plan

    # Build lookup for efficient matching
    lookup = _build_provider_lookup(providers)

    # Find first matching preference and resolve its model pattern
    for pref in preferences:
        if pref.provider in lookup:
            target_idx = lookup[pref.provider]

            # Resolve model pattern if it's a glob
            resolved_model = pref.model
            if is_glob_pattern(pref.model):
                result = await resolve_model_pattern(
                    pref.model, pref.provider, coordinator
                )
                resolved_model = result.resolved_model

            return _apply_single_override(
                mount_plan, providers, target_idx, resolved_model
            )

    # No preferences matched
    logger.warning(
        "No preferred providers found in mount plan. Preferences: %s, Available: %s",
        [p.provider for p in preferences],
        list({p.get("module", "?") for p in providers}),
    )
    return mount_plan
