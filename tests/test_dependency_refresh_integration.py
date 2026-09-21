"""Offline uv proof: unchanged versions must not hide changed Git dependencies."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

UV = shutil.which("uv")
pytestmark = pytest.mark.skipif(
    not UV or not shutil.which("git"), reason="requires uv and git"
)
BACKEND = Path(__file__).parent / "fixtures/dependency_refresh_backend.py"


def run(*args, cwd=None, env=None):
    result = subprocess.run(
        [str(arg) for arg in args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def commit(repo, label):
    run("git", "add", ".", cwd=repo)
    run(
        "git",
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        label,
        cwd=repo,
    )
    return run("git", "rev-parse", "HEAD", cwd=repo)


def repository(path, name, dependencies=()):
    package = path / name.replace("-", "_")
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 'old'\n")
    (path / "pyproject.toml").write_text(
        f'[project]\nname={json.dumps(name)}\nversion="1.0.0"\n'
        f"dependencies={json.dumps(list(dependencies))}\n"
        '[build-system]\nrequires=[]\nbuild-backend="fixture_backend"\nbackend-path=["."]\n'
    )
    shutil.copyfile(BACKEND, path / "fixture_backend.py")
    run("git", "init", "-b", "main", cwd=path)
    commit(path, "old")
    return path


def environment_python(venv):
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def snapshot(python, env):
    return json.loads(
        run(
            python,
            "-c",
            """
import importlib, importlib.metadata as m, json
names = ['refresh-root', 'refresh-middle', 'refresh-leaf', 'qualified-native']
print(json.dumps({name: {'version': m.version(name),
    'value': importlib.import_module(name.replace('-', '_')).VALUE,
    'direct_url': json.loads(m.distribution(name).read_text('direct_url.json'))}
    for name in names}))
""",
            env=env,
        )
    )


def activate(python, root, cache, overrides, env, refresh=False):
    # Run Foundation in the actual target interpreter. Only its library/dependencies
    # are borrowed read-only via PYTHONPATH; fixture packages live in this venv.
    run(
        python,
        "-c",
        """
import asyncio, sys
from pathlib import Path
from amplifier_foundation.modules.activator import ModuleActivator
async def main():
    a = ModuleActivator(cache_dir=Path(sys.argv[2]),
        refresh_dependencies=sys.argv[4] == 'refresh', install_overrides=Path(sys.argv[3]))
    await a.activate('fixture', sys.argv[1])
    a.finalize()
asyncio.run(main())
""",
        root,
        cache,
        overrides,
        "refresh" if refresh else "reuse",
        env=env,
    )


def test_new_generation_refreshes_same_version_direct_and_transitive_sources(tmp_path):
    # No indexes, network, external build dependencies, shared uv cache or host installs.
    # uv rejects --refresh together with --offline. Restrict resolution to our
    # file URLs instead: disable indexes/downloads and allow only Git file transport.
    env = {
        **os.environ,
        "UV_NO_INDEX": "true",
        "UV_NO_CONFIG": "true",
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_CACHE_DIR": str(tmp_path / "uv-cache"),
        "GIT_ALLOW_PROTOCOL": "file",
        "PYTHONPATH": os.pathsep.join(path for path in sys.path if path),
    }
    for key in list(env):
        if key.startswith("UV_") and key not in {
            "UV_NO_INDEX",
            "UV_NO_CONFIG",
            "UV_PYTHON_DOWNLOADS",
            "UV_CACHE_DIR",
        }:
            del env[key]
    leaf = repository(tmp_path / "leaf", "refresh-leaf")
    middle = repository(
        tmp_path / "middle",
        "refresh-middle",
        [f"refresh-leaf @ git+{leaf.as_uri()}@main"],
    )
    native = repository(tmp_path / "native-wheel-source", "qualified-native")
    wheels = tmp_path / "wheels"
    wheel = run(
        sys.executable,
        "-c",
        "import fixture_backend, sys; print(fixture_backend.build_wheel(sys.argv[1]))",
        wheels,
        cwd=native,
    )
    override = tmp_path / "qualified.txt"
    override.write_text(f"qualified-native @ {(wheels / wheel).as_uri()}\n")
    original = repository(
        tmp_path / "root",
        "refresh-root",
        [
            f"refresh-middle @ git+{middle.as_uri()}@main",
            # Without the explicit wheel policy this unavailable native source fails.
            f"qualified-native @ git+{(tmp_path / 'unavailable-native-source').as_uri()}@main",
        ],
    )
    active_root = tmp_path / "active-source"
    run("git", "clone", original, active_root)
    active_env, staged_env = tmp_path / "active-env", tmp_path / "staged-env"
    for venv in (active_env, staged_env):
        run(UV, "venv", "--python", sys.executable, venv, env=env)
        activate(environment_python(venv), active_root, venv / "cache", override, env)
    before = snapshot(environment_python(active_env), env)
    assert snapshot(environment_python(staged_env), env) == before
    active_state = (active_env / "cache/install-state.json").read_bytes()
    active_commit = run("git", "rev-parse", "HEAD", cwd=active_root)

    new_commits = {}
    for name, repo in (
        ("refresh-leaf", leaf),
        ("refresh-middle", middle),
        ("refresh-root", original),
    ):
        (repo / name.replace("-", "_") / "__init__.py").write_text("VALUE = 'new'\n")
        new_commits[name] = commit(
            repo, "new code, unchanged package version and dependencies"
        )
    staged_root = tmp_path / "staged-source"
    run("git", "clone", original, staged_root)

    # Negative control: ordinary preparation retains the installed old graph.
    activate(
        environment_python(staged_env), staged_root, staged_env / "cache", override, env
    )
    assert snapshot(environment_python(staged_env), env) == before

    activate(
        environment_python(staged_env),
        staged_root,
        staged_env / "cache",
        override,
        env,
        refresh=True,
    )
    after = snapshot(environment_python(staged_env), env)
    for name in ("refresh-root", "refresh-middle", "refresh-leaf"):
        assert after[name]["version"] == before[name]["version"] == "1.0.0"
        assert before[name]["value"] == "old" and after[name]["value"] == "new"
    for name in ("refresh-middle", "refresh-leaf"):
        assert after[name]["direct_url"]["vcs_info"]["commit_id"] == new_commits[name]
        assert (
            after[name]["direct_url"]["vcs_info"]["commit_id"]
            != before[name]["direct_url"]["vcs_info"]["commit_id"]
        )
        assert after[name]["direct_url"]["vcs_info"]["requested_revision"] == "main"
    assert after["refresh-root"]["direct_url"] == {
        "url": staged_root.resolve().as_uri(),
        "dir_info": {"editable": True},
    }
    assert after["qualified-native"] == before["qualified-native"]
    assert snapshot(environment_python(active_env), env) == before
    assert (active_env / "cache/install-state.json").read_bytes() == active_state
    assert run("git", "rev-parse", "HEAD", cwd=active_root) == active_commit
    assert override.read_text() == f"qualified-native @ {(wheels / wheel).as_uri()}\n"
