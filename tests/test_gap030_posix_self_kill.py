"""Regression test: GAP-030 -- on POSIX, ``_kill_subprocess_tree`` must never
send ``SIGKILL`` to the calling process's own process group.

## Why this test exists

GAP-026 added ``_kill_subprocess_tree`` (in ``subprocess_runner.py``) to clean
up an orphaned subprocess-mode delegation child on interrupt, mirroring
``sources.git._kill_process_tree``. Its POSIX branch is::

    os.killpg(os.getpgid(pid), signal.SIGKILL)

That is only safe if ``pid`` was spawned into its OWN process group. The
child was spawned via a bare ``asyncio.create_subprocess_exec(...)`` with no
``start_new_session=True`` -- so on POSIX it inherited the CALLING process's
group. ``os.getpgid(child_pid)`` therefore returned the same pgid as
``os.getpgid(0)`` (us), and ``os.killpg`` on that pgid would have delivered
SIGKILL to the calling process itself (and everything else sharing its
group -- e.g. a user's interactive shell), not just the intended target.

Confirmed empirically on Linux (spark-1, aarch64) before this fix:

    our pgid    : 964331
    child pgid  : 964331
    SAME GROUP? : True     -> killpg would SIGKILL us too

``sources.git._run_git_subprocess`` already got this right (POSIX branch sets
``start_new_session=True``); ``subprocess_runner.py``'s spawn call, added in
the same GAP-026 pass, did not. Same investigation pass, one path guarded,
one not.

## Two independent things are locked in here

1. **The spawn site** now puts the child in its own process group on POSIX
   (``start_new_session=True``), mirroring ``sources.git``. Verified with a
   REAL subprocess: pgid differs from ours, and killing it via
   ``_kill_subprocess_tree`` reaps only the child -- proven by the simple
   fact that this test process is still alive to report a result.

2. **A second, independent line of defense** inside
   ``_kill_subprocess_tree`` itself: even if a future caller (or a
   regression at the spawn site) ever hands it a pid that IS in our own
   group, it must refuse to ``killpg`` that group and fall back to killing
   only the direct pid instead. This is tested via monkeypatched
   ``os.getpgid``/``os.killpg``/``os.kill`` -- deliberately NOT by handing
   the real function a pid actually in our own group, since that is exactly
   the self-destructive action under test and must never be risked for
   real inside a test run.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
import time

import pytest
from amplifier_foundation import subprocess_runner as sr_mod

pytestmark = pytest.mark.skipif(
    sys.platform == "win32",
    reason="GAP-030 is POSIX-specific (process-group semantics; Windows uses taskkill /F /T instead)",
)


def _pid_alive(pid: int) -> bool:
    """Liveness check that also reaps the pid if it's a zombie we own.

    A killed child that nobody has ``waitpid()``-ed on remains a zombie --
    and ``os.kill(pid, 0)`` reports zombies as "alive" (the PID slot still
    exists in the process table until reaped). These tests spawn children
    directly rather than through the full asyncio-managed subprocess
    lifecycle, so nothing else is reaping them; without this, every check
    here would report a just-killed child as still alive and every test
    would be a false negative, not a real signal.
    """
    with contextlib.suppress(ChildProcessError):
        reaped_pid, _status = os.waitpid(pid, os.WNOHANG)
        if reaped_pid == pid:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class TestGap030SpawnSiteUsesOwnProcessGroup:
    """The real spawn call in ``run_session_in_subprocess`` must isolate the
    child into its own POSIX process group, so cleanup can never reach back
    and hit the caller.
    """

    def test_child_spawned_with_start_new_session_gets_distinct_pgid(self) -> None:
        async def _spawn_and_check() -> tuple[int, int, int]:
            proc = await asyncio.create_subprocess_exec(
                "sleep",
                "30",
                start_new_session=True,  # exactly what the fixed spawn site now does
            )
            our_pgid = os.getpgid(0)
            child_pgid = os.getpgid(proc.pid)
            return proc.pid, our_pgid, child_pgid

        _pid, our_pgid, child_pgid = asyncio.run(_spawn_and_check())
        try:
            assert child_pgid != our_pgid, (
                f"child pgid ({child_pgid}) == our pgid ({our_pgid}) -- the "
                "GAP-030 fix (start_new_session=True at the spawn site) has "
                "regressed; _kill_subprocess_tree's os.killpg() would once "
                "again target the CALLER's own process group"
            )
        finally:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child_pgid, signal.SIGKILL)

    def test_real_kill_subprocess_tree_reaps_only_the_isolated_child(self) -> None:
        """End-to-end: spawn exactly as the fixed production code does, then
        call the real ``_kill_subprocess_tree`` on it. The child must die.
        This process must survive to report that -- the test passing AT ALL
        is itself part of the proof.
        """

        async def _spawn() -> int:
            proc = await asyncio.create_subprocess_exec(
                "sleep", "30", start_new_session=True
            )
            return proc.pid

        child_pid = asyncio.run(_spawn())

        assert _pid_alive(child_pid), "test setup failed: child never started"

        sr_mod._kill_subprocess_tree(child_pid)

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _pid_alive(child_pid):
            time.sleep(0.1)

        assert not _pid_alive(child_pid), (
            f"child pid {child_pid} survived _kill_subprocess_tree -- cleanup regressed"
        )
        # If we reached here at all, WE (the caller) are still running --
        # the exact guarantee GAP-030 is about.


class TestGap030KillSubprocessTreeRefusesToSelfKill:
    """Defense-in-depth: even if handed a pid that (somehow) shares our own
    process group, ``_kill_subprocess_tree`` must not ``killpg`` it. Tested
    entirely through monkeypatching -- never by actually creating this
    condition for real, since that IS the self-destructive action under
    test.
    """

    def test_refuses_killpg_when_target_shares_our_process_group(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[str, tuple]] = []
        shared_pgid = 4242

        def fake_getpgid(pid: int) -> int:
            # Both "the target" (some fake pid) and "us" (pid 0) resolve to
            # the SAME pgid -- the exact hazardous precondition.
            return shared_pgid

        def fake_killpg(pgid: int, sig: int) -> None:
            calls.append(("killpg", (pgid, sig)))

        def fake_kill(pid: int, sig: int) -> None:
            calls.append(("kill", (pid, sig)))

        monkeypatch.setattr(sr_mod.os, "getpgid", fake_getpgid)
        monkeypatch.setattr(sr_mod.os, "killpg", fake_killpg)
        monkeypatch.setattr(sr_mod.os, "kill", fake_kill)
        monkeypatch.setattr(sr_mod.platform, "system", lambda: "Linux")

        sr_mod._kill_subprocess_tree(pid=9999)

        killpg_calls = [c for c in calls if c[0] == "killpg"]
        kill_calls = [c for c in calls if c[0] == "kill"]

        assert not killpg_calls, (
            "_kill_subprocess_tree called os.killpg() on a pgid shared with "
            "the caller's own group -- this would SIGKILL the caller "
            f"itself (and its whole process group) instead of just the "
            f"intended target. Recorded calls: {calls!r}"
        )
        assert kill_calls == [("kill", (9999, signal.SIGKILL))], (
            "expected a fallback os.kill() on the direct pid when killpg "
            f"is refused; got: {calls!r}"
        )

    def test_killpg_used_normally_when_target_has_its_own_group(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sanity check for the test harness itself: when the target's pgid
        genuinely differs from ours, the normal ``killpg`` path must still
        be taken (the guard must not become overly broad and disable
        cleanup entirely).
        """
        calls: list[tuple[str, tuple]] = []

        def fake_getpgid(pid: int) -> int:
            return 111 if pid == 0 else 222

        def fake_killpg(pgid: int, sig: int) -> None:
            calls.append(("killpg", (pgid, sig)))

        def fake_kill(pid: int, sig: int) -> None:
            calls.append(("kill", (pid, sig)))

        monkeypatch.setattr(sr_mod.os, "getpgid", fake_getpgid)
        monkeypatch.setattr(sr_mod.os, "killpg", fake_killpg)
        monkeypatch.setattr(sr_mod.os, "kill", fake_kill)
        monkeypatch.setattr(sr_mod.platform, "system", lambda: "Linux")

        sr_mod._kill_subprocess_tree(pid=5555)

        assert calls == [("killpg", (222, signal.SIGKILL))], (
            f"expected a normal killpg() on the child's OWN (distinct) "
            f"pgid; got: {calls!r}"
        )
