"""Batch resolution must be one transaction with no partial success receipt."""

import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from amplifier_foundation.modules.batch import DependencyBatch, DependencyConflict


def owner(python=sys.executable):
    return SimpleNamespace(
        refresh_dependencies=True,
        install_python=python,
        install_constraints=None,
        install_overrides=None,
        _needs_python_install=lambda p: True,
        _install_state=SimpleNamespace(mark_installed=Mock()),
        finalize=Mock(),
    )


def package(tmp_path, name):
    path = tmp_path / name
    path.mkdir()
    (path / "pyproject.toml").write_text(f'[project]\nname="{name}"\nversion="0.0.1"\n')
    return path


@pytest.mark.asyncio
async def test_shared_packages_are_one_transaction(tmp_path, monkeypatch):
    batch = DependencyBatch()
    install = Mock()
    monkeypatch.setattr(subprocess, "run", install)
    a, b = package(tmp_path, "package-a"), package(tmp_path, "package-b")
    first, second = owner(), owner()
    batch.add(first, a)
    batch.add(second, a)
    batch.add(second, b)
    result = await batch.install()
    assert result == {"sources": 2, "packages": 2, "resolverTransactions": 1}
    await batch.install()
    assert install.call_count == 1
    arguments = install.call_args.args[0]
    assert arguments.count(str(a)) == arguments.count(str(b)) == 1
    assert batch.installed


@pytest.mark.asyncio
async def test_failed_resolution_is_not_qualified(tmp_path, monkeypatch):
    batch = DependencyBatch()
    module = owner()
    batch.add(module, package(tmp_path, "package-a"))

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "uv")

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        await batch.install()
    assert not batch.installed
    module._install_state.mark_installed.assert_not_called()


def test_conflicting_selection_requires_a_separate_graph(tmp_path):
    batch = DependencyBatch()
    first = package(tmp_path, "first")
    second = package(tmp_path, "second")
    (second / "pyproject.toml").write_text((first / "pyproject.toml").read_text())
    batch.add(owner(), first)
    with pytest.raises(DependencyConflict):
        batch.add(owner(), second)
    with pytest.raises(ValueError):
        batch.add(owner("/other/python"), package(tmp_path, "third"))


@pytest.mark.asyncio
async def test_two_real_wheels_resolve_together_offline(tmp_path, monkeypatch):
    """One package depends on the other; independent installs cannot resolve it."""
    import asyncio
    import shutil

    from amplifier_foundation.sources import shared

    uv = shutil.which("uv")
    if not uv:
        pytest.skip("uv required for resolver integration")
    env = tmp_path / "env"
    await asyncio.to_thread(
        subprocess.run,
        [uv, "venv", str(env), "--python", sys.executable],
        check=True,
        capture_output=True,
    )
    python = str(
        env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    )
    batch = DependencyBatch()
    for name, depends in [
        ("fixture-batch-a", ["fixture-batch-b==0.0.1"]),
        ("fixture-batch-b", []),
    ]:
        path = package(tmp_path, name)
        (path / "pyproject.toml").write_text(
            f'[project]\nname="{name}"\nversion="0.0.1"\ndependencies={json.dumps(depends)}\n[build-system]\nrequires=[]\nbuild-backend="backend"\nbackend-path=["."]\n'
        )
        (path / "backend.py").write_text("""import pathlib,zipfile,tomllib

def build_wheel(wheel_directory,config_settings=None,metadata_directory=None):
    data=tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']
    name=data['name'].replace('-','_');info=name+'-0.0.1.dist-info/'
    wheel=name+'-0.0.1-py3-none-any.whl'
    metadata='Metadata-Version: 2.1\\nName: '+data['name']+'\\nVersion: 0.0.1\\n'+''.join('Requires-Dist: '+d+'\\n' for d in data['dependencies'])
    with zipfile.ZipFile(pathlib.Path(wheel_directory)/wheel,'w') as z:
        z.writestr(info+'METADATA',metadata)
        z.writestr(info+'WHEEL','Wheel-Version: 1.0\\nGenerator: fixture\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n')
        z.writestr(info+'RECORD','')
    return wheel
""")
        batch.add(owner(python), path)
    monkeypatch.setattr(shared, "build_view", lambda path: (path, True))
    monkeypatch.setenv("UV_OFFLINE", "1")
    try:
        report = await batch.install()
    except subprocess.CalledProcessError as error:
        pytest.fail(error.stderr)
    assert report["resolverTransactions"] == 1
    names = json.loads(
        await asyncio.to_thread(
            subprocess.check_output,
            [
                python,
                "-c",
                "import importlib.metadata as m,json;print(json.dumps(sorted(d.metadata['Name'] for d in m.distributions())))",
            ],
            text=True,
        )
    )
    assert names == ["fixture-batch-a", "fixture-batch-b"]


@pytest.mark.asyncio
async def test_concurrent_callers_share_install_and_cancellation_joins(
    tmp_path, monkeypatch
):
    import asyncio
    import threading

    batch = DependencyBatch()
    batch.add(owner(), package(tmp_path, "package-a"))
    started = threading.Event()
    finish = threading.Event()
    calls = []

    def install():
        calls.append("install")
        started.set()
        finish.wait(5)

    monkeypatch.setattr(batch, "_install", install)
    one = asyncio.create_task(batch.install())
    two = asyncio.create_task(batch.install())
    await asyncio.to_thread(started.wait, 5)
    one.cancel()
    await asyncio.sleep(0)
    assert not one.done()  # Candidate cleanup cannot race an unfinished resolver.
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await one
    assert (await two)["resolverTransactions"] == 1
    assert calls == ["install"] and batch.installed
    with pytest.raises(RuntimeError):
        batch.add(owner(), tmp_path)


@pytest.mark.asyncio
async def test_sibling_build_views_materialize_before_shared_lock(
    tmp_path, monkeypatch
):
    from filelock import FileLock

    from amplifier_foundation.sources import shared

    lockfile = str(tmp_path / "repository.lock")
    seen = []

    def view(source):
        # This reproduces build_view's own lock acquisition for each sibling.
        with FileLock(lockfile, timeout=0):
            seen.append(source)
        return source, True

    monkeypatch.setattr(shared, "build_view", view)
    monkeypatch.setattr(
        shared, "build_lock", lambda source: FileLock(lockfile, timeout=0)
    )
    monkeypatch.setattr(subprocess, "run", Mock())
    batch = DependencyBatch()
    for name in ("sibling-a", "sibling-b"):
        batch.add(owner(), package(tmp_path, name))
    assert (await batch.install())["resolverTransactions"] == 1
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_selected_source_replaces_bare_file_policy_without_reinheriting_it(
    tmp_path, monkeypatch
):
    from pathlib import Path

    old = package(tmp_path, "old")
    selected = package(tmp_path, "new")
    (selected / "pyproject.toml").write_text((old / "pyproject.toml").read_text())
    policy = tmp_path / "overrides.txt"
    policy.write_text(old.as_uri() + "\nother==1\n")
    module = owner()
    module.install_overrides = policy
    monkeypatch.setenv("UV_OVERRIDE", str(policy))
    batch = DependencyBatch()
    batch.add(module, selected)

    def install(command, **kwargs):
        actual = Path(command[command.index("--overrides") + 1]).read_text()
        assert old.as_uri() not in actual
        assert selected.as_uri() in actual
        assert "other==1" in actual
        assert "UV_OVERRIDE" not in kwargs["env"]

    monkeypatch.setattr(subprocess, "run", install)
    await batch.install()
