"""Qualified workers prohibit installers without changing standalone defaults."""
import asyncio
import subprocess
from unittest.mock import Mock

import pytest

from amplifier_foundation.modules import (
    DependencyInstallationDenied,
    ModuleActivator,
    dependency_installation_policy,
)
from amplifier_foundation.modules.batch import DependencyBatch
from amplifier_foundation.modules.install_policy import installation_allowed


def source(tmp_path, requirements=False):
    root = tmp_path / 'module'
    root.mkdir()
    if requirements:
        (root / 'requirements.txt').write_text('example==1.0\n')
    else:
        (root / 'pyproject.toml').write_text('[project]\nname="policy-test-missing"\nversion="0.1.0"\n')
    return root


@pytest.mark.asyncio
@pytest.mark.parametrize('requirements', [False, True])
@pytest.mark.parametrize('capture', [False, True])
async def test_protected_install_never_builds_or_launches(tmp_path, monkeypatch, requirements, capture):
    root = source(tmp_path, requirements)
    launch = Mock()
    build = Mock(side_effect=AssertionError('protected sources must not be built'))
    monkeypatch.setattr(subprocess, 'run', launch)
    monkeypatch.setattr('amplifier_foundation.sources.shared.build_view', build)
    if capture:
        with dependency_installation_policy(False):
            activator = ModuleActivator(cache_dir=tmp_path/'cache', refresh_dependencies=True)
        with pytest.raises(DependencyInstallationDenied):
            await activator._install_dependencies_uncached(root)
    else:
        activator = ModuleActivator(cache_dir=tmp_path/'cache', refresh_dependencies=True)
        with dependency_installation_policy(False), pytest.raises(DependencyInstallationDenied):
            await activator._install_dependencies_uncached(root)
    launch.assert_not_called()
    build.assert_not_called()


@pytest.mark.asyncio
async def test_mutable_requirements_install_keeps_selected_target(tmp_path, monkeypatch):
    launch = Mock()
    monkeypatch.setattr(subprocess, 'run', launch)
    activator = ModuleActivator(cache_dir=tmp_path/'cache', install_python='/owned/staging/bin/python')
    await activator._install_dependencies_uncached(source(tmp_path, True))
    args = launch.call_args.args[0]
    assert args[args.index('--python')+1] == '/owned/staging/bin/python'


@pytest.mark.asyncio
async def test_protected_already_installed_distribution_needs_no_install(tmp_path, monkeypatch):
    root = source(tmp_path)
    launch = Mock()
    monkeypatch.setattr(subprocess, 'run', launch)
    monkeypatch.setattr('amplifier_foundation.modules.activator._distribution_installed', lambda name: True)
    with dependency_installation_policy(False):
        activator = ModuleActivator(cache_dir=tmp_path/'cache')
        await activator._install_dependencies_uncached(root)
    launch.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('capture', [False, True])
async def test_batch_denial_survives_task_and_thread_boundaries(tmp_path, monkeypatch, capture):
    root = source(tmp_path)
    launch = Mock()
    monkeypatch.setattr(subprocess, 'run', launch)
    batch = DependencyBatch()
    if capture:
        with dependency_installation_policy(False):
            activator = ModuleActivator(cache_dir=tmp_path/'cache', refresh_dependencies=True)
        batch.add(activator, root)
        with pytest.raises(DependencyInstallationDenied):
            await batch.install()
    else:
        activator = ModuleActivator(cache_dir=tmp_path/'cache', refresh_dependencies=True)
        batch.add(activator, root)
        with dependency_installation_policy(False), pytest.raises(DependencyInstallationDenied):
            await batch.install()
    assert not batch.installed
    assert batch.install_count == 0
    launch.assert_not_called()


@pytest.mark.asyncio
async def test_nested_scope_cannot_relax_and_new_task_inherits():
    assert installation_allowed()
    with dependency_installation_policy(False):
        with dependency_installation_policy(True):
            assert not installation_allowed()
            assert not await asyncio.create_task(asyncio.to_thread(installation_allowed))
    assert installation_allowed()


def test_policy_restores_after_exception_and_rejects_non_bool():
    with pytest.raises(ValueError):
        with dependency_installation_policy(False):
            raise ValueError('test')
    assert installation_allowed()
    with pytest.raises(TypeError):
        with dependency_installation_policy('false'):
            pass


@pytest.mark.asyncio
async def test_batch_collection_cannot_escape_protected_scope(tmp_path, monkeypatch):
    root = source(tmp_path)
    launch = Mock()
    monkeypatch.setattr(subprocess, 'run', launch)
    activator = ModuleActivator(cache_dir=tmp_path/'cache', refresh_dependencies=True)
    batch = DependencyBatch()
    with dependency_installation_policy(False):
        batch.add(activator, root)
    with pytest.raises(DependencyInstallationDenied):
        await batch.install()
    launch.assert_not_called()
    assert not batch.installed


@pytest.mark.asyncio
async def test_lazy_prepared_resolver_retains_protection(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from amplifier_foundation.bundle import BundleModuleResolver
    root = source(tmp_path)
    launch = Mock()
    monkeypatch.setattr(subprocess, 'run', launch)
    with dependency_installation_policy(False):
        activator = ModuleActivator(cache_dir=tmp_path/'cache', refresh_dependencies=True)
        resolver = BundleModuleResolver({}, activator)
    activator._resolver.resolve = AsyncMock(return_value=SimpleNamespace(active_path=root))
    from amplifier_core.module_sources import ModuleNotFoundError
    with pytest.raises(ModuleNotFoundError, match='Dependency installation is prohibited'):
        await resolver.async_resolve('tool-lazy', source_hint='declared-source')
    launch.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('denied_first', [False, True])
async def test_duplicate_source_retains_every_activator_prohibition(tmp_path, monkeypatch, denied_first):
    root = source(tmp_path)
    allowed = ModuleActivator(cache_dir=tmp_path/'allowed', refresh_dependencies=True)
    with dependency_installation_policy(False):
        denied = ModuleActivator(cache_dir=tmp_path/'denied', refresh_dependencies=True)
    batch = DependencyBatch()
    for activator in ((denied, allowed) if denied_first else (allowed, denied)):
        batch.add(activator, root)
    launch = Mock()
    build = Mock(side_effect=AssertionError('Duplicate-source deny must precede build'))
    monkeypatch.setattr(subprocess, 'run', launch)
    monkeypatch.setattr('amplifier_foundation.sources.shared.build_view', build)
    with pytest.raises(DependencyInstallationDenied):
        await batch.install()
    launch.assert_not_called()
    build.assert_not_called()
    assert not batch.installed
    assert batch.install_count == 0
