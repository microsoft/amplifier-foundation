"""Robust directory removal shared by source handlers that clean up git checkouts.

Git marks files under ``.git/objects/pack/`` (and occasionally elsewhere,
e.g. after certain checkouts) read-only. On POSIX, removing a read-only file
only requires write permission on its *parent* directory, so a plain
``shutil.rmtree`` succeeds there without any special handling. On Windows,
the read-only attribute lives on the file itself and blocks the delete
outright (``PermissionError`` / WinError 5 "Access is denied"), which means
a cache-invalidation path that calls ``shutil.rmtree`` on a git checkout can
fail *permanently* on Windows: every subsequent run re-detects the same
"invalid" cache, tries to remove it again, and is denied again, forever.

``rmtree_robust`` clears the read-only attribute and retries once before
giving up, so cache invalidation actually heals the cache instead of
re-failing identically on every run.

Design note: shutil.rmtree's own ``onexc``/``onerror`` callback is invoked
per filesystem operation (unlink one file, rmdir one directory, etc.), and
on some CPython versions a failure that survives a nested callback can
resurface at a coarser level (e.g. re-reported against ``os.scandir`` for
the whole directory rather than the one file that actually failed) -- so a
callback that decides "raise or swallow" per-callback can end up reporting
a confusing, imprecise error, or in edge cases let a real failure look like
a no-op success. To keep the failure mode simple and precise regardless of
those internals, the per-callback handler here always attempts the
read-only-clear-and-retry and never raises; ``rmtree_robust`` itself then
checks, once, whether the directory is actually gone -- and raises one
clear, unambiguous error naming the real path if it is not.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
import sys
from pathlib import Path

__all__ = ["rmtree_robust"]


def _clear_readonly(path: str) -> None:
    """Best-effort: add the write bit so a subsequent removal can succeed."""
    try:
        current_mode = os.stat(path).st_mode
    except OSError:
        return
    with contextlib.suppress(OSError):
        os.chmod(path, current_mode | stat.S_IWRITE)


def _onexc(function, path, exc) -> None:
    """onexc/onerror-shaped callback: clear read-only and retry once.

    Never raises -- ``rmtree_robust`` enforces the actual pass/fail contract
    itself afterward, by checking whether the path still exists. This keeps
    the per-file callback simple and avoids depending on which exact
    func/path pairing a given CPython version happens to escalate a nested
    failure to.
    """
    del exc  # unused; callback shape is dictated by shutil.rmtree
    _clear_readonly(path)
    with contextlib.suppress(Exception):
        function(path)


def _onerror(function, path, exc_info) -> None:
    """Adapts the deprecated ``onerror(function, path, exc_info)`` shape
    (exc_info is a ``sys.exc_info()`` triple) to ``_onexc``."""
    _onexc(function, path, exc_info[1])


def rmtree_robust(path: Path | str, *, ignore_errors: bool = False) -> None:
    """Remove a directory tree, tolerating Windows read-only files.

    Behaves like ``shutil.rmtree``, except that when removal of an entry
    fails, it clears the read-only attribute (the common cause on Windows
    for git's read-only pack files under ``.git/objects/pack/``) and retries
    once before giving up.

    Args:
        path: Directory to remove.
        ignore_errors: If True, a failure that persists after the
            read-only-clearing retry is swallowed (best-effort cleanup),
            matching plain ``shutil.rmtree``'s own ``ignore_errors``
            semantics. If False (default), a failure that persists raises
            an ``OSError`` naming the path -- a cache-invalidation removal
            that silently fails must not be treated as if it succeeded.

    Raises:
        OSError: If ``ignore_errors`` is False and the directory still
            exists after the removal attempt (including its read-only-clear
            retries).
    """
    target = Path(path)

    if sys.version_info >= (3, 12):
        # onerror is deprecated since 3.12 in favor of onexc.
        shutil.rmtree(target, onexc=_onexc)
    else:
        shutil.rmtree(target, onerror=_onerror)

    if not ignore_errors and target.exists():
        raise OSError(
            f"Failed to remove directory even after clearing read-only "
            f"attributes and retrying: {target}. This is not a transient "
            "or read-only-attribute issue (those would have been healed by "
            "the retry) -- check for files that are locked, in use, or "
            "otherwise unremovable."
        )
