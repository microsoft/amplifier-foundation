"""Optional, strategy-neutral selection for bounded provider operations.

No operation schema, vendor policy, credential handling or execution lives here.
A resolver owns order; the supplied preparer returns a plan or None for explicit
incompatibility. Exceptions are failures, never permission to try another account.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from .spawn_utils import ProviderPreference

OPERATION_ROUTING_PROTOCOL = "amplifier.operation-routing.v1"


class OperationRoutingError(ValueError):
    """A route could not be selected without guessing or weakening requirements."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PreparedOperation:
    """An exact mounted instance plus an operation-owned plan, not an authority."""

    provider_id: str
    provider: Any
    preference: ProviderPreference
    plan: dict[str, Any]

    def assert_current(self, coordinator: Any) -> None:
        providers = coordinator.get("providers") or {}
        if providers.get(self.provider_id) is not self.provider:
            raise OperationRoutingError("binding_changed", "The prepared provider connection changed.")
        _check_available(coordinator, self.provider_id)


def _check_available(coordinator: Any, provider_id: str) -> None:
    import inspect

    available = coordinator.get_capability("provider.check_available")
    if not callable(available):
        return
    result = available(provider_id)
    if inspect.isawaitable(result):
        if inspect.iscoroutine(result):
            result.close()
        raise OperationRoutingError("invalid_availability", "Provider availability must be synchronous.")
    if result is False:
        raise OperationRoutingError("provider_unavailable", "The selected provider connection is unavailable.")


def _preference(value: Any) -> ProviderPreference:
    if isinstance(value, dict):
        if set(value) - {"provider", "model", "config"}:
            raise OperationRoutingError("invalid_preference", "Unexpected preference fields.")
        value = ProviderPreference(**value)
    if not isinstance(value, ProviderPreference):
        raise OperationRoutingError("invalid_preference", "A provider preference is required.")
    if not isinstance(value.provider, str) or not value.provider or not isinstance(value.model, str) or not value.model:
        raise OperationRoutingError("invalid_preference", "A provider and model must be explicit.")
    if value.config is not None and not isinstance(value.config, dict):
        raise OperationRoutingError("invalid_preference", "Preference config must be a mapping.")
    return ProviderPreference(provider=value.provider, model=value.model, config=deepcopy(value.config or {}))


async def prepare_operation(
    coordinator: Any,
    role: str,
    requirements: dict[str, Any],
    prepare: Callable[[Any, ProviderPreference], Awaitable[dict[str, Any] | None]],
    *,
    preferences: list[ProviderPreference | dict[str, Any]] | None = None,
) -> PreparedOperation:
    """Select and prepare once without invoking an operation.

    Exact mounted keys are deliberate: a generic consumer cannot infer an
    account from a provider-family name. Legacy resolvers can return ordered
    preferences; a singleton cannot license an invented fallback chain.

    An optional resolver advertises ``operation_resolution_protocol`` and
    implements ``resolve_for(role, *, requirements, accept)``. It must call
    accept on its final concrete choice before returning that choice. The
    callback is non-generating and returns a strict bool; a failure propagates.
    """
    if not isinstance(role, str) or not role or not isinstance(requirements, dict):
        raise OperationRoutingError("invalid_requirements", "An operation role and requirements are required.")
    prepared: list[PreparedOperation] = []

    async def accept(value: Any) -> bool:
        preference = _preference(value)
        providers = coordinator.get("providers") or {}
        provider = providers.get(preference.provider)
        if provider is None:
            raise OperationRoutingError("unknown_binding", "The preference does not identify an exact mounted connection.")
        _check_available(coordinator, preference.provider)
        plan = await prepare(provider, preference)
        if plan is None:
            return False
        if not isinstance(plan, dict):
            raise OperationRoutingError("invalid_plan", "Operation preparation must return a plain plan.")
        result = PreparedOperation(preference.provider, provider, preference, deepcopy(plan))
        result.assert_current(coordinator)
        prepared.append(result)
        return True

    if preferences is not None:
        candidates = preferences
    else:
        resolver = coordinator.get_capability("model_role_resolver")
        if resolver is None:
            raise OperationRoutingError("missing_resolver", "No model-role resolver is composed for this operation.")
        protocol = getattr(resolver, "operation_resolution_protocol", None)
        if protocol is not None:
            method = getattr(resolver, "resolve_for", None)
            if protocol != OPERATION_ROUTING_PROTOCOL or not callable(method):
                raise OperationRoutingError("unsupported_resolver", "Unsupported operation-routing protocol.")
            candidates = await method(role, requirements=deepcopy(requirements), accept=accept)
            if not isinstance(candidates, list) or len(candidates) != 1:
                raise OperationRoutingError("no_compatible_route", "No single compatible operation route was selected.")
            selected = _preference(candidates[0])
            matches = [item for item in prepared if item.preference == selected]
            if len(matches) != 1:
                raise OperationRoutingError("unprepared_selection", "The resolver did not prepare its exact final selection once.")
            matches[0].assert_current(coordinator)
            return matches[0]
        method = getattr(resolver, "resolve", None)
        if not callable(method):
            raise OperationRoutingError("invalid_resolver", "The model-role resolver cannot resolve roles.")
        candidates = await method(role)
    if not isinstance(candidates, list):
        raise OperationRoutingError("invalid_resolver", "The resolver must return ordered preferences.")
    for candidate in candidates:
        if await accept(candidate):
            return prepared[-1]
    raise OperationRoutingError("no_compatible_route", "No compatible operation route exists within the selected policy.")
