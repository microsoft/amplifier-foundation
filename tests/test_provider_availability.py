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


def runtime_host(specs, providers, broken=()):
    """A strict host boundary keyed like Core's actual runtime registry."""
    checked = []

    def check(name):
        checked.append(name)
        if name not in providers or name in broken:
            raise ValueError("Unavailable configured account: " + name)
        # Normal return (including None) means available, not a falsey failure.

    coordinator = SimpleNamespace(
        config={"providers": specs},
        get=lambda _: providers,
        get_capability=lambda key: check if key == "provider.check_available" else None,
    )
    return coordinator, checked


@pytest.mark.asyncio
@pytest.mark.parametrize("module", ["provider-anthropic", "provider-openai"])
@pytest.mark.parametrize("literal_mount", [False, True])
@pytest.mark.parametrize("model", ["selected-model", "selected-*"])
async def test_unnamed_entry_binds_availability_catalog_and_diagnostics(
    module, literal_mount, model,
):
    key = module if literal_mount else module.removeprefix("provider-")
    specs = [{"module": module, "config": {"priority": 5}}]
    provider = SimpleNamespace(list_models=AsyncMock(return_value=["selected-model"]))
    coordinator, checked = runtime_host(specs, {key: provider})
    plan = {"providers": specs}
    original = copy.deepcopy(plan)
    diagnostics = []

    result = await apply_provider_preferences_with_resolution(
        plan, [ProviderPreference(module, model)], coordinator, diagnostics=diagnostics,
    )

    assert checked == [key]
    assert diagnostics[-1].provider == key
    assert result["providers"][0]["config"] == {
        "priority": 0, "default_model": "selected-model",
    }
    assert plan == original
    assert provider.list_models.await_count == int("*" in model)


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["selected-model", "selected-*"])
@pytest.mark.parametrize("field", ["instance_id", "id"])
async def test_explicit_connection_stays_exact_with_healthy_aliases(field, model):
    specs = [
        {"module": "provider-anthropic", field: "provider-work", "config": {"priority": 5}},
        {"module": "provider-anthropic", "instance_id": "work", "config": {"priority": 0}},
    ]
    providers = {
        key: SimpleNamespace(list_models=AsyncMock(return_value=["selected-model"]))
        for key in ("provider-work", "work", "anthropic")
    }
    coordinator, checked = runtime_host(specs, providers)
    diagnostics = []

    result = await apply_provider_preferences_with_resolution(
        {"providers": specs}, [ProviderPreference("provider-work", model)],
        coordinator, diagnostics=diagnostics,
    )

    assert checked == ["provider-work"]
    assert diagnostics[-1].provider == "provider-work"
    assert result["providers"][0]["config"]["default_model"] == "selected-model"
    providers["work"].list_models.assert_not_awaited()
    providers["anthropic"].list_models.assert_not_awaited()
    assert providers["provider-work"].list_models.await_count == int("*" in model)


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["selected-model", "selected-*"])
@pytest.mark.parametrize("state", ["missing", "failed", "claimed-short", "claimed-module", "ambiguous"])
async def test_unnamed_entry_cannot_borrow_healthy_sibling(state, model):
    module = "provider-anthropic"
    specs = [
        {"module": module, "config": {"default_model": "selected-model", "priority": 0}},
        {"module": module, "instance_id": "sibling", "config": {"priority": 1}},
    ]
    sibling = SimpleNamespace(list_models=AsyncMock(return_value=["selected-model"]))
    providers = {"sibling": sibling}
    request = module
    if state == "failed":
        providers["anthropic"] = SimpleNamespace(list_models=AsyncMock())
    elif state in ("claimed-short", "claimed-module"):
        claimed = "anthropic" if state == "claimed-short" else module
        specs[1]["instance_id"] = claimed
        providers = {claimed: sibling}
        # Use the other alias so the explicit ID does not select the sibling.
        request = module if state == "claimed-short" else "anthropic"
    elif state == "ambiguous":
        providers.update({
            module: SimpleNamespace(list_models=AsyncMock()),
            "anthropic": SimpleNamespace(list_models=AsyncMock()),
        })
    coordinator, checked = runtime_host(specs, providers, broken={"anthropic"})
    plan = {"providers": specs}
    original = copy.deepcopy(plan)

    with pytest.raises(ValueError):
        await apply_provider_preferences_with_resolution(
            plan, [ProviderPreference(request, model)], coordinator,
        )

    assert plan == original
    assert checked == (
        [module] if state == "missing" else ["anthropic"] if state == "failed" else []
    )
    for provider in providers.values():
        provider.list_models.assert_not_awaited()


@pytest.mark.asyncio
async def test_duplicate_alias_of_same_object_is_not_ambiguous():
    specs = [{"module": "provider-anthropic", "config": {}}]
    provider = SimpleNamespace(list_models=AsyncMock(return_value=["selected-model"]))
    coordinator, checked = runtime_host(
        specs, {"provider-anthropic": provider, "anthropic": provider},
    )
    await apply_provider_preferences_with_resolution(
        {"providers": specs}, [ProviderPreference("anthropic", "selected-*")], coordinator,
    )
    assert checked == ["provider-anthropic"]
    provider.list_models.assert_awaited_once()


@pytest.mark.asyncio
async def test_selected_explicit_catalog_key_never_flexibly_matches_another_id():
    specs = [
        {"module": "provider-anthropic", "instance_id": "provider-work", "config": {}},
        {"module": "provider-anthropic", "instance_id": "work", "config": {}},
    ]
    sibling = SimpleNamespace(list_models=AsyncMock(return_value=["selected-model"]))
    coordinator, _ = runtime_host(specs, {"work": sibling})
    # Even without host isolation, a selected entry cannot query another account.
    coordinator.get_capability = lambda _: None
    plan = {"providers": specs}
    diagnostics = []
    result = await apply_provider_preferences_with_resolution(
        plan, [ProviderPreference("provider-work", "selected-*")], coordinator,
        diagnostics=diagnostics,
    )
    assert result is plan
    assert diagnostics[-1].status == "provider_not_mounted"
    assert diagnostics[-1].provider == "provider-work"
    sibling.list_models.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", ["anthropic", "provider-anthropic"])
@pytest.mark.parametrize("model", ["selected-model", "selected-*"])
async def test_alias_of_named_sibling_cannot_stand_in_for_unnamed_entry(alias, model):
    specs = [
        {"module": "provider-anthropic", "config": {"priority": 0}},
        {"module": "provider-anthropic", "instance_id": "work", "config": {"priority": 1}},
    ]
    sibling = SimpleNamespace(list_models=AsyncMock(return_value=["selected-model"]))
    coordinator, checked = runtime_host(specs, {"work": sibling, alias: sibling})
    with pytest.raises(ValueError):
        await apply_provider_preferences_with_resolution(
            {"providers": specs}, [ProviderPreference("anthropic", model)],
            coordinator,
        )
    assert checked == []
    sibling.list_models.assert_not_awaited()


@pytest.mark.asyncio
async def test_instance_id_precedes_legacy_id_for_check_and_catalog():
    specs = [{
        "module": "provider-anthropic", "instance_id": "work", "id": "legacy",
        "config": {},
    }]
    provider = SimpleNamespace(list_models=AsyncMock(return_value=["selected-model"]))
    coordinator, checked = runtime_host(specs, {"work": provider})
    diagnostics = []
    await apply_provider_preferences_with_resolution(
        {"providers": specs}, [ProviderPreference("work", "selected-*")], coordinator,
        diagnostics=diagnostics,
    )
    assert checked == ["work"]
    assert diagnostics[-1].provider == "work"
    provider.list_models.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["selected-model", "selected-*"])
async def test_parent_connection_ownership_excludes_child_alias(model):
    child_specs = [{"module": "provider-anthropic", "config": {}}]
    parent_specs = [{"module": "provider-openai", "instance_id": "anthropic", "config": {}}]
    other_account = SimpleNamespace(list_models=AsyncMock(return_value=["selected-model"]))
    coordinator, checked = runtime_host(parent_specs, {"anthropic": other_account})
    plan = {"providers": child_specs}
    with pytest.raises(ValueError, match="default provider was not substituted"):
        await apply_provider_preferences_with_resolution(
            plan, [ProviderPreference("anthropic", model)], coordinator,
        )
    assert checked == []
    other_account.list_models.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("host_policy", [False, True])
async def test_unnamed_registry_error_is_controlled_not_cold_resume(host_policy):
    specs = [{"module": "provider-anthropic", "config": {"default_model": "selected-model"}}]
    coordinator, checked = runtime_host(specs, {})
    def failed_registry(_):
        raise RuntimeError("registry unavailable")
    coordinator.get = failed_registry
    if not host_policy:
        coordinator.get_capability = lambda _: None
    plan = {"providers": specs}
    diagnostics = []
    if host_policy:
        with pytest.raises(ValueError, match="default provider was not substituted"):
            await apply_provider_preferences_with_resolution(
                plan, [ProviderPreference("anthropic", "selected-*")], coordinator,
                diagnostics=diagnostics,
            )
    else:
        assert await apply_provider_preferences_with_resolution(
            plan, [ProviderPreference("anthropic", "selected-*")], coordinator,
            diagnostics=diagnostics,
        ) is plan
    assert diagnostics[-1].status == "catalog_query_failed"
    assert checked == []


@pytest.mark.asyncio
async def test_exact_model_without_host_policy_does_not_require_readable_registry():
    specs = [{"module": "provider-anthropic", "config": {}}]
    def failed_registry(_):
        raise RuntimeError("registry unavailable")
    coordinator = SimpleNamespace(get=failed_registry, get_capability=lambda _: None)
    result = await apply_provider_preferences_with_resolution(
        {"providers": specs}, [ProviderPreference("anthropic", "selected-model")],
        coordinator,
    )
    assert result["providers"][0]["config"]["default_model"] == "selected-model"
