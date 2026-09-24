"""Opt-in preparation policy preserves identity, strictness, and lifecycle order."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from amplifier_core import AmplifierSession
from amplifier_core.module_sources import ModuleNotFoundError

from amplifier_foundation.bundle import Bundle, BundleModuleResolver, PreparedBundle
from amplifier_foundation.modules.activator import ModuleActivator


@pytest.mark.asyncio
async def test_failed_provider_source_does_not_replace_healthy_same_module(
    tmp_path, monkeypatch
):
    calls = []

    async def activate(self, module, source, **kwargs):
        calls.append((module, source))
        if source == "missing":
            raise OSError("private source error")
        p = tmp_path / source
        p.mkdir(exist_ok=True)
        return p

    monkeypatch.setattr(ModuleActivator, "activate", activate)
    specs = [
        {
            "module": "provider-x",
            "instance_id": "bad",
            "source": "missing",
            "config": {"priority": 1},
        },
        {
            "module": "provider-x",
            "instance_id": "good",
            "source": "healthy",
            "config": {"priority": 2},
        },
        {"module": "provider-x", "instance_id": "another", "source": "healthy"},
    ]
    b = Bundle(
        name="test",
        providers=specs,
        agents={
            "child": {
                "providers": [
                    {
                        "module": "provider-x",
                        "instance_id": "child-account",
                        "source": "missing",
                    }
                ]
            }
        },
    )
    original = deepcopy(b.to_mount_plan())
    failures = []

    async def accept(failure):
        failures.append(failure)
        # Host callbacks own a copy; cannot rewrite the plan or stored outcome.
        failure.provider_spec["config"] = {"changed": True}

    prepared = await b.prepare(
        install_deps=False,
        cache_dir=tmp_path / "cache",
        strict=True,
        provider_failure_policy=accept,
    )
    assert prepared.mount_plan == original == b.to_mount_plan()
    assert len(failures) == 2
    assert [f.agent_name for f in failures] == [None, "child"]
    assert prepared.provider_preparation_failures[0].provider_spec == specs[0]
    assert calls.count(("provider-x", "healthy")) == 1
    assert calls.count(("provider-x", "missing")) == 1
    assert (
        await prepared.resolver.async_resolve("provider-x", "healthy")
    ).resolve() == tmp_path / "healthy"
    for source in ("missing", "different", None):
        with pytest.raises(ModuleNotFoundError):
            await prepared.resolver.async_resolve("provider-x", source)
        with pytest.raises(ModuleNotFoundError):
            prepared.resolver.resolve("provider-x", source)
    assert len(calls) == 2  # No lazy retry or other-source substitution.


@pytest.mark.asyncio
async def test_source_resolution_failure_retains_original_identity(
    tmp_path, monkeypatch
):
    async def activate(self, module, source, **kwargs):
        return tmp_path

    monkeypatch.setattr(ModuleActivator, "activate", activate)

    def resolve(module, source):
        if source == "bad":
            raise ValueError("private override failed")
        return "resolved-good"

    failures = []

    async def accept(outcome):
        failures.append(outcome)

    b = Bundle(
        name="test",
        providers=[
            {"module": "provider-x", "source": "bad", "instance_id": "broken"},
            {"module": "provider-y", "source": "good"},
        ],
    )
    p = await b.prepare(
        install_deps=False,
        cache_dir=tmp_path,
        strict=True,
        source_resolver=resolve,
        provider_failure_policy=accept,
    )
    assert failures[0].phase == "source_resolution"
    assert failures[0].provider_spec["source"] == "bad"
    assert (await p.resolver.async_resolve("provider-y", "good")).resolve() == tmp_path
    assert (
        await p.resolver.async_resolve("provider-y", "resolved-good")
    ).resolve() == tmp_path
    with pytest.raises(ModuleNotFoundError):
        await p.resolver.async_resolve("provider-x", "bad")


@pytest.mark.asyncio
@pytest.mark.parametrize("section", ["tools", "hooks", "providers"])
async def test_default_strictness_and_non_provider_errors_still_abort(
    tmp_path, monkeypatch, section
):
    async def fail(*args, **kwargs):
        raise OSError("cannot activate")

    monkeypatch.setattr(ModuleActivator, "activate", fail)
    policy = AsyncMock()
    b = Bundle(name="test", **{section: [{"module": "anything", "source": "bad"}]})
    args = {"provider_failure_policy": policy} if section != "providers" else {}
    with pytest.raises(Exception, match="strict mode"):
        await b.prepare(install_deps=False, cache_dir=tmp_path, strict=True, **args)
    policy.assert_not_awaited()


@pytest.mark.asyncio
async def test_host_can_reject_preparation_failure(tmp_path, monkeypatch):
    async def fail(*args, **kwargs):
        raise OSError("failed source")

    async def reject(outcome):
        raise RuntimeError("host rejects incomplete configuration")

    monkeypatch.setattr(ModuleActivator, "activate", fail)
    b = Bundle(name="test", providers=[{"module": "provider-x", "source": "bad"}])
    with pytest.raises(RuntimeError, match="host rejects"):
        await b.prepare(
            install_deps=False, cache_dir=tmp_path, provider_failure_policy=reject
        )


@pytest.mark.asyncio
async def test_cancellation_is_not_accepted_as_provider_failure(tmp_path, monkeypatch):
    async def cancel(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(ModuleActivator, "activate", cancel)
    policy = AsyncMock()
    b = Bundle(name="test", providers=[{"module": "provider-x", "source": "bad"}])
    with pytest.raises(asyncio.CancelledError):
        await b.prepare(
            install_deps=False, cache_dir=tmp_path, provider_failure_policy=policy
        )
    policy.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("resumed", [False, True])
async def test_host_callback_precedes_root_and_resume_initialization(
    tmp_path, monkeypatch, resumed
):
    order = []

    async def initialize(session):
        order.append("initialize")
        assert (
            session.coordinator.get_capability("provider.load_failure") == "host-marker"
        )
        assert session.coordinator.get_capability("mention_resolver") is not None

    monkeypatch.setattr(AmplifierSession, "initialize", initialize)
    b = Bundle(name="test", session={"orchestrator": "test", "context": "test"})
    p = PreparedBundle(
        mount_plan=b.to_mount_plan(), bundle=b, resolver=BundleModuleResolver({})
    )

    async def before(session):
        order.append("host")
        assert session.coordinator.get_capability("session.working_dir") == str(
            tmp_path
        )
        session.coordinator.register_capability("provider.load_failure", "host-marker")

    await p.create_session(
        is_resumed=resumed, session_cwd=tmp_path, before_initialize=before
    )
    assert order == ["host", "initialize"]


@pytest.mark.asyncio
async def test_host_callback_failure_prevents_module_initialization(
    tmp_path, monkeypatch
):
    initialize = AsyncMock()
    monkeypatch.setattr(AmplifierSession, "initialize", initialize)
    b = Bundle(name="test", session={"orchestrator": "test", "context": "test"})
    p = PreparedBundle(
        mount_plan=b.to_mount_plan(), bundle=b, resolver=BundleModuleResolver({})
    )

    async def before(session):
        raise ValueError("host validation failed")

    with pytest.raises(ValueError, match="host validation"):
        await p.create_session(before_initialize=before)
    initialize.assert_not_awaited()


@pytest.mark.asyncio
async def test_child_callback_runs_before_mount_and_preserves_bundle(
    tmp_path, monkeypatch
):
    observed = []

    async def initialize(session):
        assert (
            session.coordinator.get_capability("provider.load_failure")
            == "child-policy"
        )
        observed.append(("initialized", session.session_id))

    async def execute(session, instruction):
        assert instruction == "synthetic instruction"
        return "done"

    monkeypatch.setattr(AmplifierSession, "initialize", initialize)
    monkeypatch.setattr(AmplifierSession, "execute", execute)
    b = Bundle(name="test", session={"orchestrator": "test", "context": "test"})
    original = deepcopy(b.to_mount_plan())
    p = PreparedBundle(
        mount_plan=b.to_mount_plan(), bundle=b, resolver=BundleModuleResolver({})
    )

    async def before(session):
        assert session.coordinator.get_capability("mention_resolver") is not None
        assert session.coordinator.get_capability("session.working_dir") == str(
            tmp_path
        )
        session.coordinator.register_capability("provider.load_failure", "child-policy")
        observed.append(("host", session.session_id))

    result = await p.spawn(
        Bundle(name="child"),
        "synthetic instruction",
        session_cwd=tmp_path,
        before_initialize=before,
    )
    assert [row[0] for row in observed] == ["host", "initialized"]
    assert observed[0][1] == result["session_id"]
    assert b.to_mount_plan() == original


@pytest.mark.asyncio
async def test_package_failure_is_not_a_provider_failure(tmp_path, monkeypatch):
    from amplifier_foundation.modules.activator import BundlePackageInstallError

    async def fail(*args, **kwargs):
        raise BundlePackageInstallError(
            tmp_path, "synthetic-package", "package preparation failed"
        )

    monkeypatch.setattr(ModuleActivator, "activate_bundle_package", fail)
    policy = AsyncMock()
    b = Bundle(
        name="test",
        base_path=tmp_path,
        providers=[{"module": "provider-x", "source": "./modules/provider-x"}],
    )
    with pytest.raises(BundlePackageInstallError):
        await b.prepare(
            cache_dir=tmp_path / "cache", strict=True, provider_failure_policy=policy
        )
    policy.assert_not_awaited()
