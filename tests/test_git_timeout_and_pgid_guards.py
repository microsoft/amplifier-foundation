"""Regression tests for two defects found by the ecosystem blast-radius survey.

Both live in `amplifier_foundation.sources.git` and both were introduced by the
GAP-014/GAP-025 changes on this branch.

**R-2 — `TimeoutExpired` escapes every handler.**
`_clone_at_commit`'s local `run_git()` helper runs git with `timeout=30`. Two of
its four call sites are `git checkout`, which on a large working tree is
genuinely disk-bound and can exceed 30s, so the timeout path is reachable in
normal use. `subprocess.TimeoutExpired` is a `SubprocessError`, **not** a
`CalledProcessError` — so every `except (CalledProcessError,
GitCloneTimeoutError)` handler in the module lets it sail straight past, and a
slow checkout escapes the designed full-clone fallback as a raw traceback.

**R-4 — `_kill_process_tree` had no self-pgid guard.**
`os.killpg(os.getpgid(pid), SIGKILL)` only isolates the target if that target
is in its *own* process group. Every caller spawns via `_run_git_subprocess`,
which sets `start_new_session=True`, so it always is — but that is an invisible
coupling between two functions. If it ever breaks, `killpg` SIGKILLs this
process and everything sharing its group, which on an interactive POSIX session
includes the user's shell. This is the same defect fixed as GAP-030 in
`subprocess_runner._kill_subprocess_tree`; `git.py` was missed at the time.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from amplifier_foundation.sources.git import GitCloneTimeoutError
from amplifier_foundation.sources.git import GitSourceHandler
from amplifier_foundation.sources.git import _kill_process_tree

POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX process-group semantics"
)


# --------------------------------------------------------------------------
# R-2
# --------------------------------------------------------------------------


def test_timeout_expired_is_not_a_called_process_error() -> None:
    """Pin the class relationship the bug depends on.

    If this ever becomes True upstream, the R-2 conversion below is redundant
    rather than load-bearing — and this test says so out loud instead of
    leaving the next reader to rediscover it.
    """
    assert not issubclass(subprocess.TimeoutExpired, subprocess.CalledProcessError)
    assert issubclass(subprocess.TimeoutExpired, subprocess.SubprocessError)


def test_slow_local_git_raises_a_handled_error_not_timeoutexpired(
    tmp_path: Path,
) -> None:
    """A local git step that exceeds its bound must surface as a *handled* type.

    Drives the real `_clone_at_commit`. `subprocess.run` is stubbed to raise
    `TimeoutExpired` the way a slow `git checkout` would. The assertion is not
    "some exception was raised" — it is that the raised type is one the
    module's own handlers actually catch.
    """
    handler = GitSourceHandler()

    def fake_run(*args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd=["git", "checkout"], timeout=30)

    with patch("subprocess.run", side_effect=fake_run):
        with pytest.raises(BaseException) as exc_info:
            handler._clone_at_commit(  # type: ignore[attr-defined]
                "https://example.invalid/repo.git",
                "0" * 40,
                tmp_path / "cache",
            )

    raised = exc_info.value
    assert not isinstance(raised, subprocess.TimeoutExpired), (
        "raw TimeoutExpired escaped: every `except (CalledProcessError, "
        "GitCloneTimeoutError)` handler in git.py will let it through, so the "
        "full-clone fallback never runs"
    )
    assert isinstance(raised, GitCloneTimeoutError | subprocess.CalledProcessError), (
        f"raised {type(raised).__name__}, which no handler in git.py catches"
    )


# --------------------------------------------------------------------------
# R-4
# --------------------------------------------------------------------------


@POSIX_ONLY
def test_kill_process_tree_refuses_to_killpg_its_own_group() -> None:
    """A target sharing our process group must NOT be killed via killpg.

    Without the guard this call would SIGKILL the test runner itself — and in
    real use, the user's shell. The test therefore cannot simply "call it and
    assert"; it asserts on which syscall was chosen.
    """
    our_pgid = os.getpgid(0)
    calls: dict[str, Any] = {}

    def fake_killpg(pgid: int, sig: int) -> None:
        calls["killpg"] = pgid

    def fake_kill(pid: int, sig: int) -> None:
        calls["kill"] = pid

    # getpgid(target) == our own pgid: the dangerous case.
    with (
        patch("os.getpgid", return_value=our_pgid),
        patch("os.killpg", side_effect=fake_killpg),
        patch("os.kill", side_effect=fake_kill),
    ):
        _kill_process_tree(999999)

    assert "killpg" not in calls, (
        f"killpg({calls.get('killpg')}) called on this process's own group — "
        "this would SIGKILL the caller and everything sharing its group"
    )
    assert calls.get("kill") == 999999, (
        "expected a fallback to a direct kill of the child when group "
        "isolation is absent"
    )


@POSIX_ONLY
def test_kill_process_tree_uses_killpg_when_target_is_isolated() -> None:
    """The guard must not defeat the mechanism it protects.

    When the target *is* in its own group — the normal case, because
    `_run_git_subprocess` sets `start_new_session=True` — killpg is still the
    right call, so descendants get reaped.
    """
    our_pgid = os.getpgid(0)
    foreign_pgid = our_pgid + 4242
    calls: dict[str, Any] = {}

    # Resolve our own pgid BEFORE patching -- a lambda that calls os.getpgid(0)
    # inside the patch would recurse into the mock forever.
    def fake_getpgid(pid: int) -> int:
        return our_pgid if pid == 0 else foreign_pgid

    with (
        patch("os.getpgid", side_effect=fake_getpgid),
        patch("os.killpg", side_effect=lambda pgid, sig: calls.__setitem__("killpg", pgid)),
        patch("os.kill", side_effect=lambda pid, sig: calls.__setitem__("kill", pid)),
    ):
        _kill_process_tree(999999)

    assert calls.get("killpg") == foreign_pgid, (
        "an isolated target must still be killed by process group, or git's "
        "helper children leak"
    )
    assert "kill" not in calls
