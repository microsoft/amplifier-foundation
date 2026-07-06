"""Tests for the non-blocking install path in ModuleActivator.

These cover the async subprocess install helper introduced to fix a defect where
``_install_dependencies`` ran ``subprocess.run(...)`` synchronously on the event
loop thread with no timeout, freezing the entire loop (and any liveness
heartbeat) for the duration of a ``uv`` install.

Coverage:
  (a) mocked async success -> install runs, state marked installed
  (b) non-zero return      -> subprocess.CalledProcessError with stderr preserved
  (c) wait_for timeout     -> child killed + reaped, ModuleInstallTimeout raised
  (d) loop-not-blocked     -> a concurrent task makes progress while install runs
  plus: installs serialize under the per-loop lock, and a missing ``uv`` binary
  still surfaces FileNotFoundError (error surface preserved).
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from amplifier_foundation.modules.activator import (
    DEFAULT_INSTALL_TIMEOUT,
    ModuleActivator,
    ModuleInstallTimeout,
    _run_install,
)


class _FakeProc:
    """Minimal stand-in for an asyncio subprocess.Process."""

    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: bytes = b"",
        stderr: bytes = b"",
        hang: bool = False,
        communicate_delay: float = 0.0,
    ) -> None:
        self._returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._hang = hang
        self._communicate_delay = communicate_delay
        self.killed = False
        self.waited = False

    @property
    def returncode(self) -> int:
        return self._returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._hang:
            await asyncio.sleep(3600)  # never completes within a test timeout
        if self._communicate_delay:
            await asyncio.sleep(self._communicate_delay)
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.waited = True
        return self._returncode


def _patch_exec(proc: _FakeProc):
    """Patch asyncio.create_subprocess_exec to return the given fake proc."""

    async def _fake_exec(*args, **kwargs):
        _fake_exec.calls.append((args, kwargs))
        return proc

    _fake_exec.calls = []  # type: ignore[attr-defined]
    return patch("asyncio.create_subprocess_exec", new=_fake_exec)


# --------------------------------------------------------------------------- #
# (a) success path                                                            #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_run_install_success_returns_none() -> None:
    proc = _FakeProc(returncode=0, stdout=b"done", stderr=b"")
    with _patch_exec(proc) as fake_exec:
        result = await _run_install(["uv", "pip", "install", "-e", "."])
    assert result is None
    assert fake_exec.calls  # the subprocess was actually spawned


@pytest.mark.asyncio
async def test_install_dependencies_marks_installed_on_success(tmp_path: Path) -> None:
    """A successful editable install marks the module installed (post-install refresh)."""
    module_path = tmp_path / "mod"
    module_path.mkdir()
    (module_path / "pyproject.toml").write_text(
        '[project]\nname = "mod"\nversion = "0.0.0"\n'
    )

    activator = ModuleActivator(cache_dir=tmp_path / "cache", install_deps=True)
    activator._install_state = MagicMock()
    activator._install_state.is_installed.return_value = False

    with patch("amplifier_foundation.modules.activator._run_install") as run_install:

        async def _ok(cmd, *, timeout=DEFAULT_INSTALL_TIMEOUT):
            return None

        run_install.side_effect = _ok
        await activator._install_dependencies(module_path)

    run_install.assert_called_once()
    called_cmd = run_install.call_args.args[0]
    assert called_cmd[:4] == ["uv", "pip", "install", "-e"]
    activator._install_state.mark_installed.assert_called_once_with(module_path)


# --------------------------------------------------------------------------- #
# (b) non-zero return -> CalledProcessError with stderr preserved             #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_run_install_nonzero_raises_called_process_error() -> None:
    proc = _FakeProc(returncode=2, stdout=b"some out", stderr=b"boom: build failed")
    with _patch_exec(proc):
        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            await _run_install(["uv", "pip", "install", "-e", "."])
    err = exc_info.value
    assert err.returncode == 2
    assert err.stderr == "boom: build failed"
    assert err.output == "some out"


# --------------------------------------------------------------------------- #
# (c) timeout -> child killed + reaped, typed error raised                     #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_run_install_timeout_kills_child_and_raises() -> None:
    proc = _FakeProc(hang=True)
    with _patch_exec(proc):
        with pytest.raises(ModuleInstallTimeout) as exc_info:
            await _run_install(["uv", "pip", "install", "-e", "."], timeout=0.05)
    assert proc.killed is True
    assert proc.waited is True  # reaped, no zombie
    assert "0.05" in str(exc_info.value)


# --------------------------------------------------------------------------- #
# (d) loop-not-blocked regression guard                                        #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_event_loop_not_blocked_during_install() -> None:
    """A concurrent task must make progress while a slow install runs.

    With the old synchronous subprocess.run() the loop would be frozen and the
    counter could not advance until the install returned. The async install
    yields control, so the counter climbs concurrently.
    """
    proc = _FakeProc(returncode=0, communicate_delay=0.2)

    counter = 0
    stop = False

    async def ticker() -> None:
        nonlocal counter
        while not stop:
            counter += 1
            await asyncio.sleep(0.001)

    with _patch_exec(proc):
        tick_task = asyncio.create_task(ticker())
        await _run_install(["uv", "pip", "install", "-e", "."])
        stop = True
        await tick_task

    # If the loop had been blocked, counter would be ~0. It should have ticked
    # many times during the 0.2s install.
    assert counter > 5


# --------------------------------------------------------------------------- #
# locking: concurrent installs serialize (no overlap into shared env)          #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_concurrent_installs_are_serialized(tmp_path: Path) -> None:
    """gather() over two installs must not overlap the critical section."""
    active = 0
    max_active = 0

    def make_module(name: str) -> Path:
        p = tmp_path / name
        p.mkdir()
        (p / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "0.0.0"\n'
        )
        return p

    m1 = make_module("m1")
    m2 = make_module("m2")

    activator = ModuleActivator(cache_dir=tmp_path / "cache", install_deps=True)
    activator._install_state = MagicMock()
    activator._install_state.is_installed.return_value = False

    async def fake_run(cmd, *, timeout=DEFAULT_INSTALL_TIMEOUT):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.05)  # hold the critical section
        active -= 1

    with patch(
        "amplifier_foundation.modules.activator._run_install", side_effect=fake_run
    ):
        await asyncio.gather(
            activator._install_dependencies(m1),
            activator._install_dependencies(m2),
        )

    # Serialized by the per-loop lock: never more than one install in flight.
    assert max_active == 1


# --------------------------------------------------------------------------- #
# error surface: missing uv still raises FileNotFoundError                     #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_missing_uv_raises_file_not_found() -> None:
    async def _boom(*args, **kwargs):
        raise FileNotFoundError("uv not found")

    with patch("asyncio.create_subprocess_exec", new=_boom):
        with pytest.raises(FileNotFoundError):
            await _run_install(["uv", "pip", "install", "-e", "."])
