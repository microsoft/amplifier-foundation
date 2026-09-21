"""Regression tests for the git clone timeout policy (R-1).

Found by the ecosystem blast-radius survey. Both defects are cross-platform --
they affect every Linux, macOS and WSL user, not just the Windows case the
GAP-014 bound was written for.

**A tight bound false-positives on legitimately slow work.**
The failure GAP-014 exists to catch is *unbounded* -- a credential helper
waiting on input that can never arrive -- so any finite bound catches it. The
only thing a tight bound buys is failures for users on the bad end of the
size/bandwidth distribution. Measured: every ecosystem clone completes in under
a second, so generous headroom costs working users nothing.

**Retrying a timeout is the wrong policy.**
Retries absorb *transient* failures -- a dropped connection mid-transfer, a
flaky DNS answer -- where a second attempt plausibly succeeds. A timeout is not
transient: the operation had a full budget and did not finish. Re-running it
with the same budget buys the same answer at three times the wait, and the
`cleanup_path` rmtree between attempts discards whatever partial progress was
made. For the blocked-credential-helper case, retrying is pure added latency in
front of an error the user will see regardless.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from amplifier_foundation.sources import git as git_mod
from amplifier_foundation.sources.git import GitCloneTimeoutError


def test_clone_timeout_default_is_generous_enough_for_slow_links() -> None:
    """The default bound must leave room for a large repo on a slow link.

    Pins the policy decision, not the exact number: a bound tight enough to
    fail a legitimate multi-minute clone is a regression for every platform.
    """
    assert git_mod._CLONE_TIMEOUT_DEFAULT_S >= 120.0, (
        f"clone bound is {git_mod._CLONE_TIMEOUT_DEFAULT_S}s -- tight enough to "
        "fail a legitimate large-repo or slow-link clone. The hang this catches "
        "is unbounded, so a generous value catches it just as well at no cost to "
        "working users."
    )


def test_timeout_override_is_read_at_call_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The documented override must work for the process that reads the advice.

    GitCloneTimeoutError's message tells the user to set
    AMPLIFIER_GIT_CLONE_TIMEOUT_S. If the value is bound at import, that
    instruction is false for any caller that sets it after the module loads --
    the advertised escape hatch silently does nothing.
    """
    monkeypatch.delenv("AMPLIFIER_GIT_CLONE_TIMEOUT_S", raising=False)
    assert git_mod._clone_timeout_s() == git_mod._CLONE_TIMEOUT_DEFAULT_S

    monkeypatch.setenv("AMPLIFIER_GIT_CLONE_TIMEOUT_S", "900")
    assert git_mod._clone_timeout_s() == 900.0, (
        "the timeout override was not honoured after the module was imported -- "
        "the error message advertises an escape hatch that does not work"
    )


@pytest.mark.parametrize("bad_value", ["not-a-number", "0", "-5", ""])
def test_malformed_timeout_override_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch, bad_value: str
) -> None:
    """A malformed override must not turn a working clone into a crash.

    The override exists to help a user on a slow link. Making a typo in it a
    hard failure at import (or at call) trades one usability problem for a
    worse one.
    """
    monkeypatch.setenv("AMPLIFIER_GIT_CLONE_TIMEOUT_S", bad_value)
    assert git_mod._clone_timeout_s() == git_mod._CLONE_TIMEOUT_DEFAULT_S


def test_timeout_is_not_retried(tmp_path: Path) -> None:
    """A timeout must fail immediately, not consume the full retry budget.

    Without this, a repo legitimately needing longer than the bound is retried
    the full `_CLONE_MAX_ATTEMPTS` times -- each attempt timing out, each
    rmtree'ing the partial result -- so the user waits attempts x bound to
    reach the same failure.
    """
    attempts: list[int] = []

    def always_times_out(args: Any, cwd: Any = None, timeout_s: Any = None) -> Any:
        attempts.append(1)
        raise GitCloneTimeoutError(["git", "clone", "x"], timeout_s or 300)

    with (
        patch.object(git_mod, "_run_git_subprocess", side_effect=always_times_out),
        pytest.raises(GitCloneTimeoutError),
    ):
        git_mod._run_git_network_op(
            ["git", "clone", "https://example.invalid/r.git", str(tmp_path / "d")],
            cleanup_path=tmp_path / "d",
        )

    assert len(attempts) == 1, (
        f"a timeout was retried {len(attempts)} times. Retries are for "
        "transient failures; a timeout had a full budget and did not finish, "
        "so retrying buys the same answer at N times the wait."
    )


def test_transient_git_errors_are_still_retried(tmp_path: Path) -> None:
    """The no-retry-on-timeout rule must not disable retries generally.

    A dropped connection surfaces as CalledProcessError and *is* worth a second
    attempt -- that behaviour predates this change and must survive it.
    """
    attempts: list[int] = []

    def always_errors(args: Any, cwd: Any = None, timeout_s: Any = None) -> Any:
        attempts.append(1)
        raise subprocess.CalledProcessError(
            returncode=128, cmd=args, stderr="fatal: the remote end hung up"
        )

    with (
        patch.object(git_mod, "_run_git_subprocess", side_effect=always_errors),
        patch.object(git_mod, "_CLONE_RETRY_BACKOFF_S", (0.0, 0.0)),
        pytest.raises(subprocess.CalledProcessError),
    ):
        git_mod._run_git_network_op(
            ["git", "clone", "https://example.invalid/r.git", str(tmp_path / "d")],
        )

    assert len(attempts) == git_mod._CLONE_MAX_ATTEMPTS, (
        f"transient git errors were attempted {len(attempts)} times, expected "
        f"{git_mod._CLONE_MAX_ATTEMPTS} -- the timeout rule must not suppress "
        "retries for genuinely transient failures"
    )
