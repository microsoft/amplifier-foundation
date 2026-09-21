"""Fresh generation preparation must not substitute installed versions for sources."""

import importlib.metadata
from pathlib import Path
from unittest.mock import patch

import pytest

from amplifier_foundation.bundle import Bundle
from amplifier_foundation.modules.activator import ModuleActivator


def project(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "pyproject.toml").write_text(
        '[project]\nname="refresh-fixture"\nversion="1.0.0"\n'
        'dependencies=["amplifier-module-context-simple @ '
        'git+https://example.invalid/context-simple@main"]\n'
    )
    return path


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["module", "bundle", "requirements"])
async def test_refresh_bypasses_legacy_shortcuts_and_retains_explicit_policy(
    tmp_path, entry
):
    module = project(tmp_path / "module")
    if entry == "requirements":
        (module / "pyproject.toml").unlink()
        (module / "requirements.txt").write_text("example==1.0\n")
    overrides = tmp_path / "qualified-overrides.txt"
    overrides.write_text("amplifier-core==1.6.1\n")
    constraints = tmp_path / "constraints.txt"
    constraints.write_text("qualified-dependency<2\n")
    activator = ModuleActivator(
        cache_dir=tmp_path / "stage/cache",
        install_python="/isolated-stage/bin/python",
        install_constraints=constraints,
        refresh_dependencies=True,
        install_overrides=overrides,
    )
    with (
        patch("subprocess.run") as run,
        patch(
            "amplifier_foundation.modules.activator._distribution_installed"
        ) as present,
        patch.object(activator._install_state, "is_installed") as installed,
        patch.object(activator, "_build_git_dep_overrides") as automatic_overrides,
        patch("site.addsitedir"),
    ):
        if entry == "bundle":
            await activator.activate_bundle_package(
                module, module_sources=[str(module)]
            )
        else:
            await activator._install_dependencies(module)
        present.assert_not_called()
        installed.assert_not_called()
        automatic_overrides.assert_not_called()
        command = run.call_args.args[0]
        assert command[command.index("--python") + 1] == "/isolated-stage/bin/python"
        assert "--upgrade" in command and "--refresh" in command
        assert command[command.index("--overrides") + 1] == str(overrides)
        assert command[command.index("--constraints") + 1] == str(constraints)
        if entry != "requirements":
            assert (
                command[command.index("--reinstall-package") + 1] == "refresh-fixture"
            )
    assert overrides.read_text() == "amplifier-core==1.6.1\n"
    assert constraints.read_text() == "qualified-dependency<2\n"


def test_legacy_automatic_override_remains_available(tmp_path):
    module = project(tmp_path / "module")
    with patch.object(importlib.metadata, "version", return_value="1.0.0"):
        assert ModuleActivator._build_git_dep_overrides(module / "pyproject.toml") == [
            "amplifier-module-context-simple==1.0.0"
        ]


@pytest.mark.asyncio
@pytest.mark.parametrize("refresh", [False, True])
async def test_prepare_policy_reaches_initial_agent_and_lazy_activation(
    tmp_path, monkeypatch, refresh
):
    import sys

    monkeypatch.setattr(sys, "path", list(sys.path))
    root = project(tmp_path / "root")
    agent = project(tmp_path / "agent")
    lazy = project(tmp_path / "lazy")
    overrides = tmp_path / "overrides.txt"
    overrides.write_text("qualified-dependency==1.0\n")
    bundle = Bundle(
        name="fixture",
        base_path=root,
        tools=[{"module": "tool-fixture", "source": str(root)}],
        agents={"worker": {"tools": [{"module": "tool-agent", "source": str(agent)}]}},
    )
    observed = []

    async def install(activator, module_path, *args, **kwargs):
        observed.append(
            (module_path, activator.refresh_dependencies, activator.install_overrides)
        )

    with (
        patch.object(ModuleActivator, "_install_dependencies", install),
        patch(
            "amplifier_foundation.modules.activator._distribution_installed",
            return_value=False,
        ),
    ):
        prepared = await bundle.prepare(
            strict=True,
            cache_dir=tmp_path / "stage/cache",
            refresh_dependencies=refresh,
            install_overrides=overrides,
        )
        await prepared.resolver.async_resolve("tool-lazy", source_hint=str(lazy))
    assert {entry[0] for entry in observed} == {root, agent, lazy}
    assert all(entry[1:] == (refresh, overrides) for entry in observed)


@pytest.mark.asyncio
async def test_refresh_does_not_enable_disabled_installs(tmp_path, monkeypatch):
    import sys

    monkeypatch.setattr(sys, "path", list(sys.path))
    root = project(tmp_path / "root")
    bundle = Bundle(
        name="fixture",
        base_path=root,
        tools=[{"module": "tool-fixture", "source": str(root)}],
    )
    with patch.object(ModuleActivator, "_install_dependencies") as install:
        await bundle.prepare(
            install_deps=False, refresh_dependencies=True, cache_dir=tmp_path / "cache"
        )
    install.assert_not_called()
