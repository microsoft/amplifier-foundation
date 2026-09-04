"""Utilities for session spawning with provider/model selection.

This module provides mechanisms for specifying provider/model preferences
when spawning sub-sessions. It supports:
- Ordered list of provider/model pairs (fallback chain)
- Model glob pattern resolution (e.g., "claude-haiku-*")
- Flexible provider matching (e.g., "anthropic" matches "provider-anthropic")
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import time
import weakref
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# TTL for memoizing provider.list_models() during pattern resolution.
# Every child/delegate spawn with a glob-pattern model hint resolves the
# pattern by querying the provider live. Parallel delegate waves (e.g. recipe
# fan-out) otherwise fire one identical GET /v1/models per spawned session.
# Model catalogs change rarely, so a short TTL is functionally exact while
# collapsing a whole wave into a single upstream call.
#
# Overridable via AMPLIFIER_LIST_MODELS_CACHE_TTL_SECONDS (see
# _get_ttl_seconds()). This module-level name is kept as the fallback/default
# -- and remains directly monkey-patchable (`su.LIST_MODELS_CACHE_TTL_SECONDS
# = ...`) -- so existing tests and callers are unaffected.
LIST_MODELS_CACHE_TTL_SECONDS = 60.0

# Maximum time a caller will wait on another caller's in-flight
# list_models() fetch before falling through to a direct, uncached call of
# its own (bounds head-of-line blocking: a single hung/slow fetch must not
# stall an entire delegate wave indefinitely).
#
# Overridable via AMPLIFIER_LIST_MODELS_WAIT_TIMEOUT_SECONDS (see
# _get_wait_timeout_seconds()); same fallback/monkey-patch contract as
# LIST_MODELS_CACHE_TTL_SECONDS above.
LIST_MODELS_WAIT_TIMEOUT_SECONDS = 20.0


def _get_ttl_seconds() -> float:
    """Resolve the cache TTL: env override first, module global fallback.

    Reads the module global by name on every call (not a captured default),
    so direct monkey-patching (`su.LIST_MODELS_CACHE_TTL_SECONDS = ...`, as
    the existing test suite does) keeps working unchanged.
    """
    raw = os.environ.get("AMPLIFIER_LIST_MODELS_CACHE_TTL_SECONDS")
    if raw:
        try:
            return float(raw)
        except ValueError:
            logger.warning(
                "Invalid AMPLIFIER_LIST_MODELS_CACHE_TTL_SECONDS=%r; "
                "falling back to default %.1fs",
                raw,
                LIST_MODELS_CACHE_TTL_SECONDS,
            )
    return LIST_MODELS_CACHE_TTL_SECONDS


def _get_wait_timeout_seconds() -> float:
    """Resolve the single-flight wait timeout: env override first, fallback.

    Same env-first/module-global-fallback contract as _get_ttl_seconds().
    """
    raw = os.environ.get("AMPLIFIER_LIST_MODELS_WAIT_TIMEOUT_SECONDS")
    if raw:
        try:
            return float(raw)
        except ValueError:
            logger.warning(
                "Invalid AMPLIFIER_LIST_MODELS_WAIT_TIMEOUT_SECONDS=%r; "
                "falling back to default %.1fs",
                raw,
                LIST_MODELS_WAIT_TIMEOUT_SECONDS,
            )
    return LIST_MODELS_WAIT_TIMEOUT_SECONDS


@dataclass
class _ModelListCacheEntry:
    """Per-provider memoization state for list_models().

    `models`/`expires_at` are populated ONLY by a fetch that produced a
    genuinely non-empty result (see `_fetch_and_store`). Several shipped
    providers convert a failed fetch into a *successful-looking* return
    value instead of raising -- ollama returns `[]` on connection errors,
    chat-completions returns a degraded 1-model fallback list on any
    exception, azure-openai returns `[]` unconditionally on error. Caching
    any of those would silently poison model resolution for the full TTL
    window even after the provider recovers. Treating an empty result as a
    cache miss (never populating `models`/`expires_at`) means the very next
    resolution call refetches live instead of being stuck on stale, wrong
    "success" data.

    `task` holds the shared in-flight fetch (see `_list_models_cached`):
    concurrent callers await the same asyncio.Task rather than each
    re-running their own fetch (and, with provider-level retries, their own
    full retry campaign). A task is only ever left installed here while it
    is still running or has succeeded; a task that failed clears itself
    (see `_fetch_and_store`) so the next new caller starts a fresh fetch
    instead of adopting an already-raised task.
    """

    expires_at: float = 0.0
    models: Any = None
    task: asyncio.Task[Any] | None = None
    lock: asyncio.Lock | None = None
    loop_id: int = 0


_MODEL_LIST_CACHE: weakref.WeakKeyDictionary[Any, _ModelListCacheEntry] = (
    weakref.WeakKeyDictionary()
)


async def _fetch_and_store(
    provider: Any, provider_name: str, entry: _ModelListCacheEntry
) -> Any:
    """Run the actual provider.list_models() call as the body of a shared Task.

    This is the one place that talks to the provider; `_list_models_cached`
    only ever schedules this as a Task and awaits it, so the lock in
    `_list_models_cached` never has to be held across the network call.
    """
    try:
        models = await provider.list_models()
    except BaseException:
        # A failed fetch must not persist as "the" shared in-flight attempt.
        # Clear it now (synchronously, before this coroutine actually
        # completes) so the next caller that acquires the entry lock sees
        # entry.task is None and starts a fresh fetch, rather than adopting
        # a task that has already raised.
        entry.task = None
        raise

    if models:
        entry.models = models
        entry.expires_at = time.monotonic() + _get_ttl_seconds()
    else:
        # Soft failure: the provider answered without raising, but the
        # answer is not a real one (see class docstring). Never cache it --
        # leave entry.models as None (still "no answer yet") and clear the
        # task so the next resolution retries live instead of being stuck
        # on this non-answer for the full TTL.
        logger.debug(
            "Provider '%s' returned an empty model list -- treating as a "
            "cache miss, not caching",
            provider_name,
        )
        entry.task = None

    return models


async def _list_models_cached(provider: Any, provider_name: str) -> Any:
    """Return provider.list_models(), memoized per provider with single-flight.

    Concurrent resolutions against the same provider coalesce onto a single
    shared asyncio.Task: the first caller schedules the fetch, every other
    caller awaits that same Task instead of running its own fetch (or, with
    provider-level retries, its own full retry campaign). This holds for
    both success AND failure -- a failure is delivered once to every waiter
    that piled up during the window.

    A waiter is bounded: if the shared fetch doesn't finish within
    `_get_wait_timeout_seconds()`, this falls through to a direct, uncached
    `provider.list_models()` call of its own rather than blocking forever
    (the shared fetch is shielded and keeps running for whoever is still
    waiting on it).

    Cache entries are keyed by the provider instance (weakly referenced), so
    they expire naturally with the provider. Only a non-empty result is
    ever cached (see `_fetch_and_store`); empty results and raised
    exceptions both propagate to callers without poisoning the cache.
    """
    try:
        entry = _MODEL_LIST_CACHE.get(provider)
    except TypeError:
        # Provider not weak-referenceable/unhashable -- pass through uncached.
        return await provider.list_models()

    now = time.monotonic()
    if entry is not None and entry.models is not None and entry.expires_at > now:
        return entry.models

    loop_id = id(asyncio.get_running_loop())
    if entry is None:
        entry = _ModelListCacheEntry()
        try:
            _MODEL_LIST_CACHE[provider] = entry
        except TypeError:
            return await provider.list_models()
    # asyncio primitives are bound to their event loop; rotate the lock (and
    # drop any task from that other loop, which could never be safely
    # awaited here) if this provider is being driven from a different loop
    # than before.
    if entry.lock is None or entry.loop_id != loop_id:
        entry.lock = asyncio.Lock()
        entry.loop_id = loop_id
        entry.task = None

    # The lock only guards entry/task bookkeeping -- deciding whether a
    # fetch is already in flight -- never the network call itself. Holding
    # it across the await would serialize everyone who arrives after the
    # lock is released but before the fetch finishes into their own
    # sequential retry campaigns, exactly the head-of-line problem this is
    # meant to fix.
    async with entry.lock:
        now = time.monotonic()
        if entry.models is not None and entry.expires_at > now:
            return entry.models
        if entry.task is None or entry.task.done():
            entry.task = asyncio.ensure_future(
                _fetch_and_store(provider, provider_name, entry)
            )
        task = entry.task

    wait_timeout = _get_wait_timeout_seconds()
    try:
        async with asyncio.timeout(wait_timeout):
            # Shielded: a timeout here cancels only this caller's wait, not
            # the shared fetch itself -- other waiters (and a subsequent
            # cache read) still benefit from it completing in the background.
            return await asyncio.shield(task)
    except TimeoutError:
        logger.warning(
            "Timed out after %.1fs waiting for an in-flight list_models() "
            "fetch from provider '%s'; falling through to a direct call",
            wait_timeout,
            provider_name,
        )
        return await provider.list_models()


PROTECTED_CONFIG_KEYS = frozenset(
    {
        # Credentials
        "api_key",
        "secret",
        "password",
        "token",
        "access_token",
        "bearer_token",
        "client_id",
        "client_secret",
        "tenant_id",
        # Endpoints / infrastructure
        "base_url",
        "host",
        "azure_endpoint",
        "api_version",
        "deployment_name",
        "organization",
        "project",
        # Azure auth control
        "managed_identity_client_id",
        "use_managed_identity",
        "use_default_credential",
        # Network control
        "proxy",
        "http_proxy",
        "https_proxy",
        "verify_ssl",
        "ssl_verify",
        "verify",
        "ca_bundle",
    }
)


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
        model: Model name or glob pattern (e.g., "claude-haiku-*", "gpt-5-mini").
            Patterns are resolved to concrete model names at runtime.
        config: Optional routing/preference config to merge into the provider's
            mount config (e.g., {"reasoning_effort": "high", "temperature": 0.3}).
            Keys in PROTECTED_CONFIG_KEYS (credentials, infrastructure) are never
            overridden. Omitted from to_dict() when empty for backward compatibility.

    Example:
        >>> prefs = [
        ...     ProviderPreference(provider="anthropic", model="claude-haiku-*"),
        ...     ProviderPreference(provider="openai", model="gpt-5-mini"),
        ... ]
    """

    provider: str
    model: str
    config: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        result: dict[str, Any] = {"provider": self.provider, "model": self.model}
        if self.config:
            result["config"] = self.config
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProviderPreference:
        """Create from dictionary representation.

        Args:
            data: Dictionary with 'provider' and 'model' keys, and optional 'config'.

        Returns:
            ProviderPreference instance.

        Raises:
            ValueError: If required keys are missing.
        """
        if "provider" not in data:
            raise ValueError("ProviderPreference requires 'provider' key")
        if "model" not in data:
            raise ValueError("ProviderPreference requires 'model' key")
        return cls(
            provider=data["provider"],
            model=data["model"],
            config=data.get("config", {}),
        )


@dataclass
class ModelResolutionResult:
    """Result of model pattern resolution.

    Attributes:
        resolved_model: The final model name to use, or None if the pattern
            could not be resolved against the provider's available models.
            None is an explicit failure signal -- callers must NOT treat it
            as (or substitute in) the raw, unresolved pattern string, since
            that string is not a real model name and would be sent literally
            to the provider's API.
        pattern: Original pattern (None if input wasn't a pattern).
        available_models: All models available from the provider.
        matched_models: Models that matched the pattern.
    """

    resolved_model: str | None
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
        5. Return the best match, or a failure signal (resolved_model=None)
           if the provider has no models, or none of them match the pattern.
           The raw, unresolved pattern string is never returned disguised as
           a successful resolution -- callers must check for None and treat
           it as "could not resolve", not substitute in the pattern itself.
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
            provider = _find_provider_instance(providers, provider_name, coordinator)
            if provider and hasattr(provider, "list_models"):
                models = await _list_models_cached(provider, provider_name)
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
            "No available models from provider '%s' for pattern '%s' - "
            "cannot resolve (provider has no models)",
            provider_name,
            model_hint,
        )
        return ModelResolutionResult(
            resolved_model=None,
            pattern=model_hint,
            available_models=[],
            matched_models=[],
        )

    # Match pattern against available models: case-insensitive, OS-independent.
    # Raw fnmatch.filter() uses os.path.normcase, which is case-sensitive on
    # Linux/Mac and case-insensitive on Windows -- an OS-dependent
    # inconsistency. Lowercasing both sides before comparing matches the
    # canonical model-glob semantics used by the routing-matrix resolver
    # (amplifier_module_hooks_routing.resolver) and the unified-llm-client
    # reference implementation, so a pattern like "qwen3.6-*" deterministically
    # matches "Qwen3.6-35B-A3B-..." on every platform. Original casing of the
    # matched model name is preserved in the result.
    lowered_hint = model_hint.lower()
    matched = [m for m in available_models if fnmatch.fnmatch(m.lower(), lowered_hint)]

    if not matched:
        logger.warning(
            "Pattern '%s' matched no models from provider '%s'. "
            "Available: %s. Cannot resolve.",
            model_hint,
            provider_name,
            ", ".join(available_models[:10])
            + ("..." if len(available_models) > 10 else ""),
        )
        return ModelResolutionResult(
            resolved_model=None,
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


def _get_provider_specs(coordinator: Any) -> list[dict[str, Any]]:
    """Best-effort fetch of the mount plan's provider config list.

    ``coordinator.config`` is a stable, documented property of
    ``amplifier_core``'s Rust-backed coordinator (``RustCoordinator.config
    -> dict[str, Any]``), already relied on elsewhere in this codebase
    (e.g. ``configurator/_inspector.py``) to read back ``module``/``id``
    metadata for mounted instances. This helper degrades gracefully to an
    empty list for any coordinator-like object that doesn't expose it
    (e.g. bare test doubles), rather than raising.
    """
    config = getattr(coordinator, "config", None)
    if not isinstance(config, dict):
        return []
    specs = config.get("providers", [])
    return specs if isinstance(specs, list) else []


def _spec_for_instance(
    provider_specs: list[dict[str, Any]], instance_id: str
) -> dict[str, Any] | None:
    """Find the mount plan config spec matching a runtime provider instance name."""
    for spec in provider_specs:
        if not isinstance(spec, dict):
            continue
        spec_id = spec.get("id") or spec.get("module", "")
        if spec_id == instance_id:
            return spec
    return None


def _module_type_of(spec: dict[str, Any] | None) -> str | None:
    """Extract the bare module type (e.g. "anthropic") from a provider spec."""
    if spec is None:
        return None
    module = spec.get("module", "")
    if not module:
        return None
    return module.replace("provider-", "")


def _find_provider_instance(
    providers: dict[str, Any],
    provider_name: str,
    coordinator: Any = None,
) -> Any | None:
    """Find a provider instance by name with flexible matching.

    Args:
        providers: Dict of mounted providers by name.
        provider_name: Provider to find (e.g., "anthropic").
        coordinator: Optional coordinator, used as a fallback source of
            mount plan config (module/id/priority) when ``provider_name``
            is a bare module type that doesn't match any dict key directly
            (see fallback below).

    Returns:
        Provider instance or None if not found.

    Matching strategy:
        1. Exact key, "provider-" prefix stripped, or "provider-" prefix
           added -- covers the single-instance case and any instance
           explicitly keyed by the bare type.
        2. Fallback: if ``provider_name`` is a bare module type (e.g.
           "anthropic") and no provider is keyed by it directly, this
           happens when 2+ instances of that module each have a distinct
           explicit ``id:`` (needed for routing-matrix disambiguation) and
           none of them is the bare type itself. Search the mount plan's
           provider config list for every instance whose underlying
           module type matches, and return the one configured with the
           highest priority (lowest priority number) -- mirroring how the
           default provider is selected elsewhere in this module (see
           ``_apply_single_override``'s ``priority = 0`` promotion).
    """
    for name, provider in providers.items():
        if provider_name in (
            name,
            name.replace("provider-", ""),
            f"provider-{provider_name}",
        ):
            return provider

    provider_specs = _get_provider_specs(coordinator)
    if not provider_specs:
        return None

    candidates: list[tuple[int, str]] = []
    for name in providers:
        spec = _spec_for_instance(provider_specs, name)
        if _module_type_of(spec) == provider_name:
            assert spec is not None  # narrowed by _module_type_of returning non-None
            priority = spec.get("config", {}).get("priority", 0)
            candidates.append((priority, name))

    if not candidates:
        return None

    candidates.sort(key=lambda c: c[0])
    return providers[candidates[0][1]]


# ---------------------------------------------------------------------------
# "Which instance does a preference mean?" -- one answer, every caller
# ---------------------------------------------------------------------------
#
# WHY THIS EXISTS (model_performance-67u, then recipes-0ac)
#
# A routing matrix addresses providers by bare module type (`provider:
# anthropic`), but a mount plan may carry SEVERAL instances of that module,
# each with its own `id:` and `priority:` -- which is precisely the shape the
# routing-matrix bundle asks for (see _find_provider_instance's docstring:
# distinct `id:`s exist "for routing-matrix disambiguation").
#
# ROUND 1 (model_performance-67u): three helpers in this file answered
# "which instance is `anthropic`?" three DIFFERENT ways:
#
#   _find_provider_instance  -> highest priority (lowest number)
#   _find_provider_index     -> first declared
#   _build_provider_lookup   -> LAST declared (plain dict, last write wins)
#
# apply_provider_preferences_with_resolution calls TWO of them in one pass:
# it resolves the candidate's model glob against the instance
# _find_provider_instance picks, then promotes the index
# _build_provider_lookup returns. On a 10-mount plan with 2 module types
# (the eval harness roster: sol/terra/openai/luna/luna-max +
# opus-4.8/opus/sonnet/haiku/fable) those are different mounts, so the model
# resolved from instance A's model list was written onto instance B's config
# and B was promoted to priority 0 -- right model, wrong instance, and with
# it B's base_url / long-context / cache-retention settings. Silently.
#
# ROUND 2 (recipes-0ac), fixed here: even with ONE agreed rule, the rule was
# MODEL-BLIND. A preference is a (provider, model) PAIR, but only the
# `provider` half ever reached the resolution -- so on a measured 14-provider
# host (module `provider-anthropic` mounted as opus/priority 1, sonnet/5,
# fable/6) a preference {provider: anthropic, model: claude-sonnet-4-5}
# promoted `opus` (the highest-priority anthropic mount) and stamped
# `claude-sonnet-4-5` onto opus's config. Same substitution class as round 1
# -- right model name, wrong instance, and with it that instance's base_url /
# context-window / cache-retention settings -- reached by a different route.
#
# THE RULE, in one place (:func:`_resolve_provider_index`):
#
#   1. An explicit instance `id:` is the most specific address there is and
#      always wins outright -- unchanged behaviour.
#   2. Otherwise the name is a MODULE (module id or short name). Among that
#      module's mounted instances, prefer the ones whose locally-known models
#      satisfy the preference's model hint. This is the half that was
#      missing: it is what makes `{anthropic, claude-sonnet-4-5}` mean
#      `fable` rather than "whichever anthropic mount ranks first".
#   3. Among whatever survives step 2, HIGHEST PRIORITY WINS, ties broken by
#      declaration order -- never "last declared".
#
# Step 2 only ever NARROWS an already-correct candidate set: if no instance
# advertises a matching model (the common case -- most mount configs carry no
# model metadata at all), every candidate survives and step 3 decides exactly
# as it did before. Single-instance plans are therefore untouched by
# construction, whatever the model hint says.
#
# Step 2 is deliberately SYNCHRONOUS and local: it reads only what the mount
# plan already states. It never queries a provider's live catalog -- that is
# resolve_model_pattern()'s job, it is async, and _build_provider_lookup /
# _find_provider_index are sync helpers with sync callers.


def _provider_priority(provider: dict[str, Any]) -> int:
    """Priority of a mount-plan provider entry; lower ranks higher.

    Missing/unparseable priority sorts as 0 (highest), matching
    :func:`_find_provider_instance`, so plans that never set ``priority``
    keep resolving by declaration order.
    """
    raw = (provider.get("config") or {}).get("priority", 0)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _declared_models(provider: dict[str, Any]) -> list[str]:
    """Model names a mount-plan entry states it serves, WITHOUT any I/O.

    Reads only the mount plan itself: the instance's ``default_model`` plus,
    when a plan happens to declare one, a ``models`` list. Returns ``[]`` when
    the entry says nothing about models -- which is the common case, and which
    callers must treat as "no information", never as "matches nothing".
    """
    config = provider.get("config") or {}
    models: list[str] = []

    declared = config.get("models")
    if isinstance(declared, (list, tuple)):
        models.extend(m for m in declared if isinstance(m, str) and m)

    default_model = config.get("default_model")
    if isinstance(default_model, str) and default_model:
        models.append(default_model)

    return models


def _model_hint_matches(model_name: str, model_hint: str) -> bool:
    """Does a concrete model name satisfy a preference's model hint?

    Uses the same case-insensitive glob convention
    :func:`resolve_model_pattern` already applies to a provider's live model
    list, so a hint that would resolve against the live catalog is the same
    hint that selects the instance here. Exact (non-glob) hints work too --
    fnmatch treats a pattern with no wildcard as an equality test.
    """
    return fnmatch.fnmatch(model_name.lower(), model_hint.lower())


def _resolve_provider_index(
    providers: list[dict[str, Any]],
    provider_id: str,
    model_hint: str | None = None,
) -> int | None:
    """THE answer to "which mounted instance does this preference name?".

    Every name-to-instance resolution in this module funnels through here so
    the helpers cannot drift apart again (see the module comment above).

    Args:
        providers: List of provider configs from mount plan.
        provider_id: Provider to find -- an instance ``id``, a module id, or
            a module short name ("anthropic" for "provider-anthropic").
        model_hint: Optional model name or glob from the same preference.
            Used ONLY to choose between several instances of one module, and
            only when at least one of them declares a matching model. Never
            causes a miss: a hint nothing matches is simply not consulted.

    Returns:
        Index of the resolved provider, or None if no entry matches the name.
    """
    # 1. An explicit instance id is the most specific address there is.
    for i, p in enumerate(providers):
        if p.get("id", "") == provider_id:
            return i

    # 2. Otherwise the name addresses a MODULE -- gather every instance of it.
    candidates = [
        i
        for i, p in enumerate(providers)
        if provider_id
        in (p.get("module", ""), p.get("module", "").replace("provider-", ""))
    ]
    if not candidates:
        return None

    # 3. Narrow by the model half of the preference, when it discriminates.
    #    An empty result means the plan simply carries no model metadata to
    #    judge by, so every candidate stays in the running.
    if model_hint:
        matching = [
            i
            for i in candidates
            if any(
                _model_hint_matches(model, model_hint)
                for model in _declared_models(providers[i])
            )
        ]
        if matching:
            if len(matching) < len(candidates):
                logger.debug(
                    "Provider %r narrowed to %d/%d instance(s) by model hint %r",
                    provider_id,
                    len(matching),
                    len(candidates),
                    model_hint,
                )
            candidates = matching

    # 4. Highest priority wins; ties broken by declaration order.
    return min(candidates, key=lambda i: (_provider_priority(providers[i]), i))


def _find_provider_index(
    providers: list[dict[str, Any]],
    provider_id: str,
    model_hint: str | None = None,
) -> int | None:
    """Find the index of a provider in the providers list.

    Supports flexible matching: "anthropic", "provider-anthropic",
    or full module ID.

    Thin wrapper over :func:`_resolve_provider_index` -- kept as the named
    entry point its existing callers and tests use. ``model_hint`` is
    optional; omitting it asks the module-type question on its own, exactly
    as this helper always did.

    Args:
        providers: List of provider configs from mount plan.
        provider_id: Provider to find.
        model_hint: Optional model name/glob to disambiguate between several
            instances of the same module.

    Returns:
        Index of the provider, or None if not found.
    """
    return _resolve_provider_index(providers, provider_id, model_hint)


def _build_provider_lookup(
    providers: list[dict[str, Any]],
) -> dict[str, int]:
    """Build a lookup dict mapping provider names to indices.

    Every value is produced by :func:`_resolve_provider_index`, so this
    lookup and :func:`_find_provider_index` cannot disagree -- they are the
    same function, and agreement is structural rather than two
    implementations that happen to coincide.

    Module-type keys ("anthropic", "provider-anthropic", the full module id)
    resolve to the HIGHEST-PRIORITY instance of that module, not the
    last-declared one. Instance ``id`` keys are the most specific address and
    always win, even when an id collides with a module-type name.

    This lookup is model-BLIND by construction: a dict keyed by provider name
    alone cannot express "which instance for THIS model". Callers holding a
    (provider, model) preference should call :func:`_resolve_provider_index`
    with the model hint instead -- see the module comment above.

    Args:
        providers: List of provider configs from mount plan.

    Returns:
        Dict mapping various name formats to provider index.
    """
    lookup: dict[str, int] = {}

    for p in providers:
        module_id = p.get("module", "")
        short_name = module_id.replace("provider-", "")
        for key in (module_id, f"provider-{short_name}", short_name):
            if not key or key in lookup:
                continue
            resolved = _resolve_provider_index(providers, key)
            if resolved is not None:
                lookup[key] = resolved

    # An explicit instance id is the most specific address there is, so it
    # overwrites any module-type key it collides with. (_resolve_provider_index
    # already applies this precedence; re-asserting it here keeps every
    # instance addressable by its own id even if its id never appears as a
    # module-type key above.)
    for i, p in enumerate(providers):
        instance_id = p.get("id")
        if instance_id:
            lookup[instance_id] = i

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
        ...     ProviderPreference(provider="openai", model="gpt-5-mini"),
        ... ]
        >>> new_plan = apply_provider_preferences(plan, prefs)
    """
    if not preferences:
        return mount_plan

    providers = mount_plan.get("providers", [])
    if not providers:
        logger.warning("Provider preferences specified but no providers in mount plan")
        return mount_plan

    # Find first matching preference. The preference is resolved as a PAIR:
    # its model participates in choosing WHICH instance of a module-named
    # provider is meant, not just what gets stamped onto the winner.
    for pref in preferences:
        target_idx = _resolve_provider_index(providers, pref.provider, pref.model)
        if target_idx is not None:
            return _apply_single_override(
                mount_plan, providers, target_idx, pref.model, pref.config
            )

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
    pref_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply a single provider/model override to the mount plan.

    Args:
        mount_plan: Original mount plan.
        providers: Original providers list.
        target_idx: Index of provider to promote.
        model: Model to set for the provider.
        pref_config: Optional routing/preference config to merge into the
            provider config. Preference wins over base config for non-protected
            keys. Keys in PROTECTED_CONFIG_KEYS (credentials, infrastructure)
            are never overridden.

    Returns:
        New mount plan with override applied. The overridden (target)
        provider is guaranteed to STRICTLY outrank every other provider in
        this child mount plan's priority ordering (see the tie-break note
        below) -- it is never merely tied with another instance.
    """
    # Clone mount plan and providers list
    new_plan = dict(mount_plan)
    new_providers = []

    target_priority = 0

    for i, p in enumerate(providers):
        p_copy = dict(p)
        p_copy["config"] = dict(p.get("config", {}))

        if i == target_idx:
            # Merge routing config first (lower precedence)
            if pref_config:
                for key, value in pref_config.items():
                    if key not in PROTECTED_CONFIG_KEYS:
                        p_copy["config"][key] = value
            # Then enforce invariants — these always win
            p_copy["config"]["priority"] = target_priority
            p_copy["config"]["default_model"] = model
            logger.info(
                "Provider preference applied: %s (priority=0, model=%s)",
                p_copy.get("module"),
                model,
            )

        new_providers.append(p_copy)

    # The promotion above only ever RAISES the target's priority -- it never
    # touches anyone else's. If another instance in this child's provider
    # list already sits at (or below) the target's new priority, provider
    # resolution ties at that priority and the tie is broken by declaration
    # order (first-declared wins), silently handing the child's session back
    # to whichever instance happens to be declared first -- even though an
    # override was explicitly applied to select a *different* instance. This
    # is the substitution-class failure fixed here (see
    # openai_improvement-ejq): a sub-agent's chosen provider must STRICTLY
    # win its own child's resolution, never merely tie for it.
    #
    # Fix: demote every other instance in THIS CHILD MOUNT PLAN ONLY whose
    # priority would tie or beat the target's, to strictly below it. This
    # never touches the root/parent session's provider configuration --
    # `providers`/`new_providers` here are always the child mount plan's own
    # provider list (see `apply_provider_preferences` /
    # `apply_provider_preferences_with_resolution`, the only callers).
    for i, p_copy in enumerate(new_providers):
        if i == target_idx:
            continue
        other_priority = p_copy["config"].get("priority", 0)
        if other_priority <= target_priority:
            demoted_priority = target_priority + 1
            p_copy["config"]["priority"] = demoted_priority
            logger.debug(
                "Provider override tie-break: demoting '%s' (id=%s) from "
                "priority=%s to priority=%s in this child's mount plan so "
                "the overridden provider '%s' strictly outranks it instead "
                "of losing a stable-sort tie by declaration order",
                p_copy.get("module"),
                p_copy.get("id"),
                other_priority,
                demoted_priority,
                new_providers[target_idx].get("module"),
            )

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
        ...     ProviderPreference(provider="openai", model="gpt-5-mini"),
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

    # Find first matching preference whose model actually resolves, and
    # apply it. A preference whose provider is present but whose glob
    # pattern fails to resolve (no matching models) is NOT applied with
    # the raw, unresolved pattern -- that would send a literal glob string
    # to the provider's API. Instead we advance to the next preference in
    # the ordered list, mirroring resolve_model_role()'s `continue`
    # behavior in the sibling routing-matrix resolver.
    for pref in preferences:
        # Resolved as a PAIR: pref.model participates in choosing which
        # instance of a module-named provider is meant (see the module
        # comment above _provider_priority), so the model glob below is
        # resolved against the very instance that will be promoted.
        target_idx = _resolve_provider_index(providers, pref.provider, pref.model)
        if target_idx is not None:
            # Resolve model pattern if it's a glob
            resolved_model = pref.model
            if is_glob_pattern(pref.model):
                result = await resolve_model_pattern(
                    pref.model, pref.provider, coordinator
                )
                if result.resolved_model is None:
                    logger.warning(
                        "Preference for provider '%s' failed to resolve model "
                        "pattern '%s' - trying next preference",
                        pref.provider,
                        pref.model,
                    )
                    continue
                resolved_model = result.resolved_model

            return _apply_single_override(
                mount_plan, providers, target_idx, resolved_model, pref.config
            )

    # No preferences matched -- either no preference's provider was present
    # in the mount plan, or every candidate's model pattern failed to
    # resolve. Either way, leave the mount plan unmodified rather than
    # writing an unresolved pattern string into it.
    logger.warning(
        "No preferred providers found in mount plan. Preferences: %s, Available: %s",
        [p.provider for p in preferences],
        list({p.get("module", "?") for p in providers}),
    )
    return mount_plan
