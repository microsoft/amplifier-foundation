"""Explicit child preferences must not revert to a different default account."""
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from amplifier_foundation.spawn_utils import (
    ProviderPreference,
    _build_provider_lookup,
    _find_provider_instance,
    apply_provider_preferences_with_resolution,
)


def fixture_host(broken=()):
    specs = [
        {"module": "provider-openai", "instance_id": "work", "config": {"default_model": "work-model", "priority": 1}},
        {"module": "provider-openai", "instance_id": "personal", "config": {"default_model": "personal-model", "priority": 0}},
    ]
    providers = {name: SimpleNamespace(list_models=AsyncMock(return_value=[name + "-model"]))
                 for name in ("work", "personal")}
    checked = []

    def check(name):
        checked.append(name)
        if name in broken:
            raise ValueError("Unavailable configured account: " + name)

    coordinator = SimpleNamespace(config={"providers": specs}, get=lambda _: providers,
        get_capability=lambda key: check if key == "provider.check_available" else None)
    return specs, providers, coordinator, checked


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["work-model", "work-*"])
async def test_failed_explicit_account_never_uses_healthy_default(model):
    specs, providers, coordinator, checked = fixture_host({"work"})
    plan = {"providers": specs}
    original = copy.deepcopy(plan)
    with pytest.raises(ValueError, match="account: work"):
        await apply_provider_preferences_with_resolution(plan, [ProviderPreference("work", model)], coordinator)
    assert plan == original and checked == ["work"]
    assert _build_provider_lookup(specs)["work"] == 0
    for provider in providers.values():
        provider.list_models.assert_not_awaited()


@pytest.mark.asyncio
async def test_declared_preference_fallback_keeps_exact_account_and_config():
    specs, providers, coordinator, checked = fixture_host({"work"})
    original = copy.deepcopy(specs)
    prefs = [ProviderPreference("work", "work-*"), ProviderPreference("personal", "personal-*")]
    result = await apply_provider_preferences_with_resolution({"providers": specs}, prefs, coordinator)
    assert checked == ["work", "personal"]
    assert result["providers"][1]["config"]["default_model"] == "personal-model"
    assert specs == original
    assert _find_provider_instance(providers, "openai", coordinator) is providers["personal"]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,model", [("work", "missing-*"), ("missing", "anything")])
async def test_no_matching_preference_under_host_policy_cannot_keep_default(provider, model):
    specs, _, coordinator, _ = fixture_host()
    with pytest.raises(ValueError, match="default provider was not substituted"):
        await apply_provider_preferences_with_resolution({"providers": specs}, [ProviderPreference(provider, model)], coordinator)


@pytest.mark.asyncio
async def test_no_policy_preserves_legacy_no_match_behavior():
    specs, _, coordinator, _ = fixture_host()
    coordinator.get_capability = lambda _: None
    plan = {"providers": specs}
    assert await apply_provider_preferences_with_resolution(plan, [ProviderPreference("missing", "anything")], coordinator) is plan


@pytest.mark.asyncio
async def test_cancellation_is_not_a_candidate_fallback():
    import asyncio
    specs, _, coordinator, _ = fixture_host()
    def cancel(_):
        raise asyncio.CancelledError()
    coordinator.get_capability = lambda _: cancel
    with pytest.raises(asyncio.CancelledError):
        await apply_provider_preferences_with_resolution({"providers": specs},
            [ProviderPreference("work", "work-model"), ProviderPreference("personal", "personal-model")], coordinator)


@pytest.mark.asyncio
async def test_async_policy_is_a_contract_error_not_permission_or_fallback():
    specs, providers, coordinator, _ = fixture_host()
    async def invalid(_):
        raise ValueError("must never run")
    coordinator.get_capability = lambda _: invalid
    with pytest.raises(TypeError, match="must be synchronous"):
        await apply_provider_preferences_with_resolution({"providers": specs},
            [ProviderPreference("work", "work-*"), ProviderPreference("personal", "personal-*")], coordinator)
    for provider in providers.values():
        provider.list_models.assert_not_awaited()
