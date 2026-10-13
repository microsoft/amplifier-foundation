"""Operation selection must not turn missing evidence into implicit fallback."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_foundation.operation_routing import (
    OPERATION_ROUTING_PROTOCOL, OperationRoutingError, prepare_operation,
)
from amplifier_foundation.spawn_utils import ProviderPreference


def fixture(resolver=None):
    provider = object()
    providers = {"account-blue": provider, "account-green": object()}
    caps = {"model_role_resolver": resolver}
    coordinator = SimpleNamespace(get=lambda name: providers, get_capability=caps.get)
    return coordinator, providers, caps


@pytest.mark.asyncio
async def test_unrelated_legacy_resolver_and_explicit_preferences():
    pref = ProviderPreference(provider="account-blue", model="model-one")
    resolver = SimpleNamespace(resolve=AsyncMock(return_value=[pref]))
    coordinator, providers, _ = fixture(resolver)
    prepare = AsyncMock(return_value={"version": 1})
    result = await prepare_operation(coordinator, "operation", {}, prepare)
    assert result.provider is providers["account-blue"]
    assert result.preference == pref and result.plan == {"version": 1}
    result.assert_current(coordinator)
    providers["account-blue"] = object()
    with pytest.raises(OperationRoutingError, match="changed"):
        result.assert_current(coordinator)
    resolver.resolve.reset_mock()
    await prepare_operation(coordinator, "operation", {}, prepare, preferences=[pref])
    resolver.resolve.assert_not_called()


@pytest.mark.asyncio
async def test_negotiated_resolver_owns_candidate_order():
    blue = ProviderPreference(provider="account-blue", model="first")
    green = ProviderPreference(provider="account-green", model="second")

    class Resolver:
        operation_resolution_protocol = OPERATION_ROUTING_PROTOCOL
        async def resolve_for(self, role, *, requirements, accept):
            assert role == "operation" and requirements == {"type": "test"}
            for value in [blue, green]:
                if await accept(value):
                    return [value]
            return []

    coordinator, providers, _ = fixture(Resolver())
    prepare = AsyncMock(side_effect=[None, {"bound": True}])
    result = await prepare_operation(coordinator, "operation", {"type": "test"}, prepare)
    assert result.provider is providers["account-green"] and prepare.await_count == 2


@pytest.mark.asyncio
async def test_failure_is_not_incompatibility_or_default_inheritance():
    prefs = [ProviderPreference(provider=key, model="test") for key in ["account-blue", "account-green"]]
    coordinator, _, _ = fixture(SimpleNamespace(resolve=AsyncMock(return_value=prefs)))
    prepare = AsyncMock(side_effect=PermissionError("denied"))
    with pytest.raises(PermissionError):
        await prepare_operation(coordinator, "operation", {}, prepare)
    assert prepare.await_count == 1
    coordinator, _, _ = fixture()
    with pytest.raises(OperationRoutingError) as error:
        await prepare_operation(coordinator, "operation", {}, prepare)
    assert error.value.code == "missing_resolver"


@pytest.mark.asyncio
async def test_exact_binding_required_and_scope_rechecked():
    coordinator, _, caps = fixture()
    prepare = AsyncMock(return_value={})
    with pytest.raises(OperationRoutingError) as error:
        await prepare_operation(coordinator, "operation", {}, prepare,
                                preferences=[{"provider": "family", "model": "test"}])
    assert error.value.code == "unknown_binding"
    prepare.assert_not_called()
    result = await prepare_operation(coordinator, "operation", {}, prepare,
                                     preferences=[{"provider": "account-blue", "model": "test"}])
    caps["provider.check_available"] = lambda name: False
    with pytest.raises(OperationRoutingError) as error:
        result.assert_current(coordinator)
    assert error.value.code == "provider_unavailable"


@pytest.mark.asyncio
async def test_unprepared_winner_and_async_availability_are_refused():
    pref = ProviderPreference(provider="account-blue", model="test")
    resolver = SimpleNamespace(operation_resolution_protocol=OPERATION_ROUTING_PROTOCOL,
                               resolve_for=AsyncMock(return_value=[pref]))
    coordinator, _, caps = fixture(resolver)
    with pytest.raises(OperationRoutingError) as error:
        await prepare_operation(coordinator, "operation", {}, AsyncMock())
    assert error.value.code == "unprepared_selection"
    caps["provider.check_available"] = AsyncMock()
    with pytest.raises(OperationRoutingError) as error:
        await prepare_operation(coordinator, "operation", {}, AsyncMock(), preferences=[pref])
    assert error.value.code == "invalid_availability"
