"""Regression test: GAP-011 / GAP-025 -- interrupting a blocking git
subprocess call must not leave its child process tree running.

## Why this test exists

GAP-011 ("Ctrl+C does nothing while amplifier is stuck in the bundle-prep
hang") went through several rounds of investigation. Its literal claim
("no response to Ctrl+C at all") turned out to be false even before any
fix landed -- but the underlying user-visible guarantee behind it
("I can always get out, without leaving processes running") is real, and
depends entirely on the process-tree-kill mechanism this test locks in:

  * ``_run_git_subprocess``'s ``except BaseException:`` clause (added for
    GAP-025) -- a ``KeyboardInterrupt``/``CancelledError`` landing while
    blocked inside ``proc.communicate()`` must still trigger cleanup, not
    just the ``subprocess.TimeoutExpired`` case.
  * ``_kill_process_tree``'s Windows path (``taskkill /F /T``) -- killing
    the whole tree a blocking git invocation may have spawned (git.exe ->
    git-remote-https.exe -> a credential-helper child, in the real
    GAP-014 scenario), not just the immediate PID.

Nothing previously locked either of these in as an automated regression.
The last proof for GAP-011 was a one-shot manual ConPTY run against a
live credential-manager OAuth hang on a specific box on a specific day --
real evidence, but it protects nothing going forward. If either of the
two mechanisms above regresses (e.g. someone "simplifies" the except
clause back to ``except subprocess.TimeoutExpired:``, or drops the
``/T`` flag from the taskkill call), this test is what catches it.

## Why a deterministic stand-in instead of a live credential hang

The real GAP-014/GAP-011 scenario depends on this box's git credential
configuration and network state to actually hang -- neither fast nor
reproducible as an automated test. Instead, this test:

  1. Spawns a small two-level *stand-in* process tree (a script that
     spawns one real child of its own) that mirrors git's own
     parent -> credential-helper relationship closely enough to exercise
     ``taskkill /F /T`` for real, without needing git, a network, or an
     actual credential hang.
  2. Simulates the interrupt landing at the right moment: the tree is
     confirmed alive (both marker files, from both real OS processes,
     exist) before a ``KeyboardInterrupt`` is raised on the *first* call
     to ``Popen.communicate()`` -- exactly where GAP-025 found a real
     Ctrl+C is delivered while ``_run_git_subprocess`` is blocked.

This is Windows-only: the POSIX escape-hatch case (``setsid`` descendants
surviving ``os.killpg``) is a different code path covered by
``amplifier-module-tool-bash``'s own timeout-cleanup tests; this test is
specifically about the ``taskkill /F /T`` path GAP-011/GAP-013/GAP-025
were about.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest
from amplifier_foundation.sources import git as git_mod

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="GAP-011/013/025 orphan-prevention is Windows-specific (taskkill /F /T path)",
)


_STANDIN_SCRIPT = """
import sys
import os
import subprocess
import time
from pathlib import Path

mode = sys.argv[1]
marker_dir = Path(sys.argv[2])

if mode == "parent":
    (marker_dir / "parent.pid").write_text(str(os.getpid()))
    # Mirrors git.exe spawning a credential-helper child: a REAL OS child
    # process (not a thread), so it is only reachable through taskkill's
    # /T (process-tree) flag -- killing this PID alone would not reach it.
    subprocess.Popen([sys.executable, __file__, "child", str(marker_dir)])
    time.sleep(120)
elif mode == "child":
    (marker_dir / "child.pid").write_text(str(os.getpid()))
    time.sleep(120)
"""


def _pid_alive(pid: int) -> bool:
    """Best-effort Windows liveness check via ``tasklist`` (no admin needed)."""
    result = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return str(pid) in result.stdout


def _wait_for_marker(path: Path, timeout_s: float = 10.0) -> int:
    """Poll for a marker file to appear with a non-empty PID in it."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if path.exists():
            text = path.read_text().strip()
            if text:
                return int(text)
        time.sleep(0.1)
    raise TimeoutError(f"marker file {path} never appeared with a PID")


def _force_kill(pid: int) -> None:
    """Best-effort cleanup so a failing test never leaks a stand-in process."""
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(pid)],
        capture_output=True,
        check=False,
    )


class TestGap011InterruptKillsWholeProcessTree:
    """A Ctrl+C-equivalent interrupt during a blocking git subprocess call
    must kill the ENTIRE spawned process tree, not just the immediate child.
    """

    def test_keyboard_interrupt_during_communicate_kills_grandchild(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script = tmp_path / "gap011_standin.py"
        script.write_text(_STANDIN_SCRIPT)

        parent_pid_marker = tmp_path / "parent.pid"
        child_pid_marker = tmp_path / "child.pid"

        args = [sys.executable, str(script), "parent", str(tmp_path)]

        # Simulate a real Ctrl+C landing exactly where GAP-025 found it
        # can: inside `proc.communicate()`, while _run_git_subprocess is
        # blocked waiting on the (deterministic, stand-in) "hung" tree.
        #
        # Only the FIRST communicate() call *on our own stand-in
        # subprocess* raises -- matched by identity (`self.args == args`),
        # NOT by a global ordinal ("the first call anywhere in this
        # process"). `subprocess.Popen.communicate` is a single, process-
        # wide class attribute: patching it and counting calls globally
        # means ANY other Popen().communicate() in this process (pytest's
        # own machinery, a leftover subprocess from a previous test, an
        # antivirus/CI helper, taskkill's own subprocess.run -- anything)
        # can steal "call #1" before our target subprocess's own call ever
        # happens. If that happens, our intended call becomes call #2 (or
        # later) and silently falls through to the REAL, un-mocked
        # `communicate()` with the REAL 60s timeout -- producing a bogus
        # `GitCloneTimeoutError` instead of the `KeyboardInterrupt` this
        # test means to inject, with no indication that interception (not
        # the product's interrupt-cleanup path) is what actually failed.
        # Matching by the exact argv of the subprocess this test itself
        # spawned closes that whole class of race, regardless of what else
        # is running in this process.
        #
        # Subsequent communicate() calls on our OWN stand-in proc (the
        # post-kill reap) and calls on OTHER procs (taskkill's own
        # subprocess.run) must behave normally, or this test would also
        # break the cleanup it's trying to verify -- both fall through to
        # the real implementation below.
        original_communicate = subprocess.Popen.communicate
        triggered = {"done": False}

        def fake_communicate(self, input=None, timeout=None):  # type: ignore[no-untyped-def]
            if not triggered["done"] and list(self.args) == args:
                triggered["done"] = True
                _wait_for_marker(parent_pid_marker)
                _wait_for_marker(child_pid_marker)
                raise KeyboardInterrupt()
            return original_communicate(self, input, timeout=timeout)

        monkeypatch.setattr(subprocess.Popen, "communicate", fake_communicate)

        parent_pid: int | None = None
        child_pid: int | None = None
        try:
            start = time.monotonic()
            with pytest.raises(KeyboardInterrupt):
                git_mod._run_git_subprocess(args, cwd=None, timeout_s=60)
            elapsed = time.monotonic() - start

            # Bound with real headroom above the manually-observed
            # live-scenario numbers (2.03s best case / 35.44s worst case,
            # GAP-011's end-to-end ConPTY proof against a real credential
            # hang). This test's own cleanup is purely local process
            # teardown (no network/auth variance), so it should be far
            # faster in practice -- but we deliberately don't tighten the
            # bound below the real worst-case number, in case the same
            # taskkill-based mechanism turns out to be the slow part.
            # 60s = ~1.7x the observed live worst case: enough headroom to
            # not flake on a loaded CI box, tight enough to fail if the
            # except-clause fix regresses into a real wait.
            assert elapsed < 60, (
                f"_run_git_subprocess took {elapsed:.1f}s to raise+cleanup "
                "after a simulated interrupt -- should be local process "
                "teardown, not bounded by anything resembling a real "
                "network/credential wait"
            )

            parent_pid = _wait_for_marker(parent_pid_marker)
            child_pid = _wait_for_marker(child_pid_marker)

            # Give the kill a moment to fully land -- taskkill itself is
            # synchronous but the process table doesn't always update
            # instantaneously.
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if not _pid_alive(parent_pid) and not _pid_alive(child_pid):
                    break
                time.sleep(0.2)

            assert not _pid_alive(parent_pid), (
                f"stand-in parent process (pid {parent_pid}) survived the "
                "interrupt -- GAP-025's `except BaseException:` cleanup "
                "in _run_git_subprocess regressed"
            )
            assert not _pid_alive(child_pid), (
                f"stand-in CHILD process (pid {child_pid}) survived the "
                "interrupt even though the parent was killed -- this is "
                "GAP-011/GAP-013's exact orphan signature: `taskkill /F "
                "/T` (process-TREE kill) regressed to a plain "
                "single-process kill in _kill_process_tree"
            )
        finally:
            if parent_pid is not None:
                _force_kill(parent_pid)
            if child_pid is not None:
                _force_kill(child_pid)
