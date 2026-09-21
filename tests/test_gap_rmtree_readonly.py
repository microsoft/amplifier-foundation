"""Regression test: bundle cache cache-invalidation must self-heal on
Windows when a git checkout contains read-only files.

## Why this test exists

Git marks files under ``.git/objects/pack/`` read-only. On POSIX, removing
a read-only file only needs write permission on the *parent* directory, so
``shutil.rmtree`` succeeds there without any special handling. On Windows,
the read-only attribute lives on the file itself and blocks the delete
outright: ``os.unlink``/``shutil.rmtree`` raises ``PermissionError``
(WinError 5, "Access is denied").

Before this fix, ``GitSourceHandler.resolve()`` detected an invalid cached
clone, called a bare ``shutil.rmtree(cache_path, ignore_errors=True)``,
which silently left the read-only pack files behind on Windows, and then a
*second*, non-ignoring ``shutil.rmtree(cache_path)`` a few lines later hit
the same files and raised uncaught -- surfacing as a hard failure on every
single run, forever, because the cache was never actually cleaned up.
``amplifier_foundation.sources._rmtree.rmtree_robust`` fixes this by
clearing the read-only attribute and retrying once before giving up.

This test suite covers the mechanism generically (not by trying to
reproduce Windows file-attribute semantics on POSIX, which isn't possible):

  * A real read-only file removed via the real filesystem (the literal
    scenario asked for, and meaningful on POSIX too -- it proves the
    wrapper doesn't regress the common/happy path).
  * A transient failure simulated by monkeypatching ``os.unlink`` to fail
    once then succeed -- proves the retry-after-clearing mechanism is
    actually wired up and works, independent of *why* the OS raised.
  * A persistent failure (retry also fails) -- proves the fix still fails
    loudly, naming the path, rather than silently pretending the removal
    succeeded (the exact defect this fix targets: a cache invalidation
    that doesn't actually invalidate anything).
  * The ``ignore_errors=True`` best-effort path still swallows a
    persistent failure, matching plain ``shutil.rmtree``'s own semantics
    for the call sites that were already using it that way.
  * The Python-version dispatch (``onexc`` on >=3.12, ``onerror`` below)
    actually selects the right keyword.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from amplifier_foundation.sources._rmtree import rmtree_robust


def test_rmtree_robust_removes_readonly_file(tmp_path: Path) -> None:
    """The literal repro scenario: a directory containing a read-only file
    must be fully removable -- meaningful on POSIX too, since it locks in
    that the wrapper never regresses the common case.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    packfile = repo / "pack-deadbeef.idx"
    packfile.write_text("binary pack data")
    packfile.chmod(stat.S_IREAD)  # read-only, mirrors git's pack files

    rmtree_robust(repo)

    assert not repo.exists()


def test_rmtree_robust_retries_after_transient_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulates the real Windows failure mode: the first removal attempt
    is denied (as it would be for a read-only pack file on Windows); the
    fix must clear the attribute and retry, and the retry must succeed.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    stubborn = repo / "pack-stubborn.idx"
    stubborn.write_text("data")
    stubborn.chmod(stat.S_IREAD)

    real_unlink = os.unlink
    calls = {"n": 0}

    def fake_unlink(path, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError(13, "Access is denied", str(path))
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", fake_unlink)

    rmtree_robust(repo)

    assert not repo.exists()
    assert calls["n"] >= 2, (
        "expected the first (simulated) removal to fail and a retry to "
        "follow it -- rmtree_robust's read-only-clear-and-retry mechanism "
        "was never exercised"
    )


def test_rmtree_robust_raises_when_retry_still_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If clearing the read-only attribute and retrying STILL fails, the
    failure must propagate -- a cache-invalidation removal that silently
    "succeeds" while leaving the stale directory in place is the exact bug
    being fixed (GAP: cache never self-heals).
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    stubborn = repo / "pack-permanent.idx"
    stubborn.write_text("data")

    def always_fails(path, *args, **kwargs):
        raise PermissionError(13, "Access is denied", str(path))

    monkeypatch.setattr(os, "unlink", always_fails)

    # `match` is a REGEX, not a substring. On Windows `str(repo)` is a path
    # like C:\Users\...\repo -- and `\U` there is an invalid regex escape, so
    # pytest raises `re.error: Invalid regex pattern provided to 'match'`
    # before it ever evaluates the assertion. Escaping is what makes this test
    # actually run on the platform the fix exists for.
    with pytest.raises(OSError, match=re.escape(str(repo))):
        rmtree_robust(repo)

    # The directory must still exist -- nothing should look like it was
    # cleaned up when it wasn't.
    assert repo.exists()


def test_rmtree_robust_ignore_errors_swallows_persistent_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ignore_errors=True must still swallow a failure that persists after
    the retry, matching shutil.rmtree's own ignore_errors semantics -- used
    for the existing best-effort cleanup call sites (e.g. between network
    retry attempts) where a stale leftover is tolerable.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    stubborn = repo / "pack-permanent.idx"
    stubborn.write_text("data")

    def always_fails(path, *args, **kwargs):
        raise PermissionError(13, "Access is denied", str(path))

    monkeypatch.setattr(os, "unlink", always_fails)

    # Must not raise.
    rmtree_robust(repo, ignore_errors=True)


def test_rmtree_robust_dispatches_onexc_vs_onerror_by_python_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`onerror` is deprecated since Python 3.12 in favor of `onexc`. Verify
    rmtree_robust actually dispatches on sys.version_info rather than
    always using the deprecated form (or always using the newer one, which
    would break on 3.11).
    """
    fake_rmtree = MagicMock()
    monkeypatch.setattr(shutil, "rmtree", fake_rmtree)

    monkeypatch.setattr(sys, "version_info", (3, 12, 0))
    rmtree_robust("/some/path")
    _, kwargs = fake_rmtree.call_args
    assert "onexc" in kwargs, "Python >=3.12 must use the non-deprecated onexc kwarg"
    assert "onerror" not in kwargs

    fake_rmtree.reset_mock()

    monkeypatch.setattr(sys, "version_info", (3, 11, 5))
    rmtree_robust("/some/path")
    _, kwargs = fake_rmtree.call_args
    assert "onerror" in kwargs, (
        "Python <3.12 must use onerror (onexc doesn't exist yet)"
    )
    assert "onexc" not in kwargs
