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
from typing import Any, Literal

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
class ClassPreference:
    """A model-class preference for class-based routing.

    Used with provider_preferences to specify a model class (e.g., "fast",
    "premium") instead of a specific provider/model pair.

    Attributes:
        class_name: Model class identifier (e.g., "fast", "quality", "balanced").
        required: If True, the class is mandatory and routing must not fall back.
    """

    class_name: str
    required: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation.

        Returns dict with 'class' key, plus 'required': True if required.
        """
        result: dict[str, Any] = {"class": self.class_name}
        if self.required:
            result["required"] = True
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClassPreference:
        """Create from dictionary representation.

        Args:
            data: Dictionary with 'class' key and optional 'required' key.

        Returns:
            ClassPreference instance.

        Raises:
            ValueError: If 'class' key is missing.
        """
        if "class" not in data:
            raise ValueError("ClassPreference requires 'class' key")
        return cls(class_name=data["class"], required=data.get("required", False))


@dataclass
class RoutingConfig:
    """Configuration for model-class routing strategy.

    Attributes:
        strategy: Routing strategy — 'cost', 'quality', or 'balanced'.
        max_tier: Global cost ceiling tier (e.g., "tier-2"), or None.
        classes: Per-class override configuration, or None.
    """

    _VALID_STRATEGIES = ("cost", "quality", "balanced")

    strategy: Literal["cost", "quality", "balanced"] = "balanced"
    max_tier: str | None = None
    classes: dict[str, dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        """Validate strategy at construction time."""
        if self.strategy not in self._VALID_STRATEGIES:
            raise ValueError(
                f"Invalid strategy '{self.strategy}': must be one of {self._VALID_STRATEGIES}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation.

        Only includes optional fields (max_tier, classes) when set.

        Returns:
            Dictionary with 'strategy' and any non-None optional fields.
        """
        result: dict[str, Any] = {"strategy": self.strategy}
        if self.max_tier is not None:
            result["max_tier"] = self.max_tier
        if self.classes is not None:
            result["classes"] = self.classes
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoutingConfig:
        """Create from dictionary representation.

        Args:
            data: Dictionary with optional 'strategy', 'max_tier', 'classes' keys.

        Returns:
            RoutingConfig instance with defaults for missing keys.

        Raises:
            ValueError: If strategy value is not 'cost', 'quality', or 'balanced'.
        """
        strategy = data.get("strategy", "balanced")
        if strategy not in cls._VALID_STRATEGIES:
            raise ValueError(
                f"Invalid strategy '{strategy}': must be one of {cls._VALID_STRATEGIES}"
            )
        return cls(
            strategy=strategy,
            max_tier=data.get("max_tier"),
            classes=data.get("classes"),
        )


def preference_from_dict(data: dict[str, Any]) -> ProviderPreference | ClassPreference:
    """Discriminating deserializer for preference entries.

    Routes to the correct preference type based on which key is present:
    - 'class' key → ClassPreference
    - 'provider' key → ProviderPreference
    - Neither → ValueError

    Args:
        data: Dictionary with either 'class' or 'provider' key.

    Returns:
        ClassPreference or ProviderPreference instance.

    Raises:
        ValueError: If neither 'provider' nor 'class' key is present.
    """
    if "class" in data:
        return ClassPreference.from_dict(data)
    if "provider" in data:
        return ProviderPreference.from_dict(data)
    raise ValueError("Preference entry must contain 'provider' or 'class' key")


# Cost tier ordering for sorting
_COST_TIER_ORDER: dict[str, int] = {
    "free": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "extreme": 4,
}


async def resolve_model_class(
    class_name: str,
    coordinator: Any,
    routing_config: RoutingConfig | None = None,
) -> list[ProviderPreference]:
    """Resolve a model class to concrete provider/model preferences.

    Queries all installed providers' list_models(), matches by capability,
    filters by cost tier, and sorts by the user's routing strategy.

    Args:
        class_name: The model class (e.g., "reasoning", "fast", "vision")
        coordinator: ModuleCoordinator with providers registered
        routing_config: Optional user routing config. If None, reads from
            coordinator.get_capability("session.routing").

    Returns:
        Ordered list of ProviderPreference entries (first = best match).
        Empty list if no models match.
    """
    from amplifier_core.capabilities import MODEL_CLASS_CAPABILITIES

    # 1. Determine which capability tags satisfy this class
    matching_caps = MODEL_CLASS_CAPABILITIES.get(class_name, [class_name])

    # 2. Get routing config
    if routing_config is None:
        routing_config = coordinator.get_capability("session.routing")
    effective_strategy = routing_config.strategy if routing_config else "balanced"

    # Determine effective max_tier (per-class override > global)
    effective_max_tier = None
    if routing_config:
        effective_max_tier = routing_config.max_tier
        if routing_config.classes and class_name in routing_config.classes:
            class_cfg = routing_config.classes[class_name]
            if "max_tier" in class_cfg:
                effective_max_tier = class_cfg["max_tier"]

    # Determine per-class provider filter
    class_providers = None
    if (
        routing_config
        and routing_config.classes
        and class_name in routing_config.classes
    ):
        class_providers = routing_config.classes[class_name].get("providers")

    # 3. Query all installed providers
    providers = coordinator.get("providers") or {}
    candidates: list[tuple[str, str, int]] = []  # (provider_name, model_id, tier_rank)

    for module_id, provider in providers.items():
        # Extract short provider name (strip 'provider-' prefix)
        provider_name = module_id
        if provider_name.startswith("provider-"):
            provider_name = provider_name[len("provider-") :]

        # Apply per-class provider filter
        if class_providers and provider_name not in class_providers:
            continue

        try:
            models = await provider.list_models()
        except Exception:
            logger.debug(
                "resolve_model_class: %s list_models() failed, skipping", module_id
            )
            continue

        for model in models:
            model_caps = set(getattr(model, "capabilities", []))
            if not model_caps.intersection(matching_caps):
                continue

            # Check cost tier filter
            model_metadata = getattr(model, "metadata", {}) or {}
            cost_tier: str | None = model_metadata.get("cost_tier")
            tier_rank = _COST_TIER_ORDER.get(cost_tier, 2) if cost_tier else 2

            if effective_max_tier:
                max_rank = _COST_TIER_ORDER.get(effective_max_tier, 4)
                if tier_rank > max_rank:
                    continue

            candidates.append((provider_name, model.id, tier_rank))

    # 4. Sort by strategy
    if effective_strategy == "cost":
        candidates.sort(key=lambda c: c[2])  # ascending tier
    elif effective_strategy == "quality":
        candidates.sort(key=lambda c: c[2], reverse=True)  # descending tier
    else:  # "balanced" — prefer medium, then spread outward
        candidates.sort(key=lambda c: abs(c[2] - 2))  # distance from medium

    # 5. Convert to ProviderPreference list
    return [
        ProviderPreference(provider=name, model=model_id)
        for name, model_id, _ in candidates
    ]


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
            p_copy["config"]["default_model"] = model
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
    preferences: list[ProviderPreference | ClassPreference],
    coordinator: Any,
) -> dict[str, Any]:
    """Apply provider preferences with model pattern resolution.

    Like apply_provider_preferences(), but also resolves glob patterns
    in model names (e.g., "claude-haiku-*" -> "claude-3-haiku-20240307")
    and ClassPreference entries via resolve_model_class().

    Args:
        mount_plan: The mount plan to modify.
        preferences: Ordered list of ProviderPreference or ClassPreference objects.
        coordinator: Amplifier coordinator for querying provider models.

    Returns:
        New mount plan with the first matching provider promoted and
        model pattern resolved.

    Raises:
        ValueError: If a required ClassPreference cannot be satisfied.

    Example:
        >>> prefs = [
        ...     ClassPreference(class_name="fast"),
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
        # Check for required class entries before returning
        for pref in preferences:
            if isinstance(pref, ClassPreference) and pref.required:
                raise ValueError(
                    f"Required class '{pref.class_name}' cannot be satisfied: "
                    "no providers in mount plan"
                )
        logger.warning("Provider preferences specified but no providers in mount plan")
        return mount_plan

    # Find first matching preference and resolve its model pattern
    for pref in preferences:
        if isinstance(pref, ClassPreference):
            # Resolve class to candidate provider/model pairs
            candidates = await resolve_model_class(pref.class_name, coordinator)

            if not candidates:
                if pref.required:
                    raise ValueError(
                        f"Required class '{pref.class_name}' resolved to no candidates"
                    )
                logger.debug(
                    "Class '%s' resolved to no candidates, skipping",
                    pref.class_name,
                )
                continue

            # Try each candidate against the mount plan
            for candidate in candidates:
                target_idx = _find_provider_index(providers, candidate.provider)
                if target_idx is not None:
                    return _apply_single_override(
                        mount_plan, providers, target_idx, candidate.model
                    )

            # Candidates exist but none in mount plan
            if pref.required:
                candidate_providers = [c.provider for c in candidates]
                raise ValueError(
                    f"Required class '{pref.class_name}' resolved to providers "
                    f"{candidate_providers} but none are in the mount plan"
                )
            logger.debug(
                "Class '%s' candidates not in mount plan, skipping",
                pref.class_name,
            )

        # ProviderPreference handling (unchanged logic)
        elif isinstance(pref, ProviderPreference):
            target_idx = _find_provider_index(providers, pref.provider)
            if target_idx is not None:
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
        "No preferred providers found in mount plan. Available: %s",
        list({p.get("module", "?") for p in providers}),
    )
    return mount_plan
