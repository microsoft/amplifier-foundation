"""Regression test: git invocations that write working-tree paths must carry
``-c core.longpaths=true``.

**The defect.** Reproduced on native Windows 11 (build 26200), every run:
``git clone`` reports success ("Clone succeeded") but its *checkout* step
silently fails with ``fatal: cannot create directory at '...': Filename too
long``, because Windows' legacy MAX_PATH limit (260 characters) is exceeded
by ``<cache-dir-prefix>\\<repo-name>-<hash>\\<repo's own nested paths>``. The
partial working tree left behind is then detected as invalid by
``_verify_clone_integrity`` and removed -- only for the identical failure to
repeat on every subsequent attempt, forever. The behavior/bundle is silently
dropped from every composed session on that machine with no visible error to
the user.

``core.longpaths=true`` makes git use the Unicode ``\\\\?\\``-prefixed Win32
APIs, which are not subject to MAX_PATH. It is passed per-invocation (``git
-c core.longpaths=true ...``), never written to the user's global/system git
config -- this module should never reach outside its own operations to
change machine state the user did not ask it to change.

Applied unconditionally on every platform (not gated to
``platform.system() == "Windows"``): git treats the setting as a no-op on
POSIX, where no equivalent path-length ceiling exists, so the unconditional
form is exactly as safe as a Windows-only branch while keeping exactly one
code path -- the same one exercised by this test on every platform, rather
than a branch only a native Windows CI run would ever execute.

Scoped to the invocations that actually materialize working-tree paths --
``clone`` and ``checkout`` -- not ``init``/``remote add``/``fetch``, whose
writes (``.git/config``, the hash-addressed object store) are not subject to
MAX_PATH regardless of the repository's own directory structure.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from amplifier_foundation.paths.resolution import ParsedURI
from amplifier_foundation.sources import git as git_mod


def _has_longpaths(args: list[str]) -> bool:
    """True if a git argv carries ``-c core.longpaths=true`` before the subcommand."""
    return "-c" in args and "core.longpaths=true" in args


@pytest.mark.asyncio
async def test_shallow_clone_argv_carries_longpaths(tmp_path: Path) -> None:
    """The primary `resolve()` path: `git clone --depth 1 [--branch ref] <url> <dest>`.

    This is exactly the invocation from the reproduction: a non-bare clone
    performs an implicit checkout of the working tree, which is where
    "Filename too long" is raised on Windows. Goes through the real
    `GitSourceHandler.resolve()` call site (not a synthetic argv) so this
    proves the wrapping is actually applied where the defect lives, not just
    that the helper function exists.
    """
    captured: list[list[str]] = []

    def fake_run_git_network_op(
        args: list[str], cwd: Any = None, cleanup_path: Any = None
    ) -> Any:
        captured.append(args)
        # Short-circuit with a real git error so resolve() fails fast rather
        # than needing a fully working fake clone on disk.
        raise subprocess.CalledProcessError(1, args, stderr="boom")

    parsed = ParsedURI(
        scheme="git+https",
        host="example.invalid",
        path="/org/r",
        ref="main",
        subpath="",
    )

    with patch.object(
        git_mod, "_run_git_network_op", side_effect=fake_run_git_network_op
    ):
        handler = git_mod.GitSourceHandler()
        with pytest.raises(git_mod.BundleNotFoundError):
            await handler.resolve(parsed, tmp_path)

    assert captured, "expected _run_git_network_op to be invoked"
    assert _has_longpaths(captured[0]), (
        f"clone argv does not carry -c core.longpaths=true: {captured[0]!r}. "
        "Without it, a clone whose destination path exceeds Windows' "
        "260-character MAX_PATH silently fails its checkout step while "
        "reporting the clone itself as successful."
    )


def test_clone_at_commit_checkout_and_fallback_clone_carry_longpaths(
    tmp_path: Path,
) -> None:
    """`_clone_at_commit`'s checkout calls and its full-clone fallback.

    `_clone_at_commit` is used whenever a full 40-char commit SHA is pinned.
    Its cheap path does `init` + `fetch` + `checkout FETCH_HEAD`; if the
    server refuses to serve the SHA directly it falls back to a full `clone`
    + `checkout <sha>`. Both `checkout` calls and the fallback `clone`
    materialize working-tree paths and must carry the flag.
    """
    cache_path = tmp_path / "repo"
    network_op_calls: list[list[str]] = []

    def fake_run_git_subprocess(args: list[str], cwd: Any, timeout_s: Any) -> Any:
        # Simulate the shallow-fetch-of-exact-commit failing (e.g. a server
        # without allowReachableSHA1InWant), forcing the full-clone fallback.
        raise subprocess.CalledProcessError(1, args, stderr="unadvertised object")

    def fake_run_git_network_op(
        args: list[str], cwd: Any = None, cleanup_path: Any = None
    ) -> Any:
        network_op_calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    with (
        patch.object(
            git_mod, "_run_git_subprocess", side_effect=fake_run_git_subprocess
        ),
        patch.object(
            git_mod, "_run_git_network_op", side_effect=fake_run_git_network_op
        ),
        patch.object(git_mod, "rmtree_robust"),
        patch.object(git_mod.subprocess, "run") as mock_subprocess_run,
    ):
        mock_subprocess_run.return_value = subprocess.CompletedProcess([], 0, "", "")
        handler = git_mod.GitSourceHandler()
        handler._clone_at_commit("https://example.invalid/r.git", "a" * 40, cache_path)

    checkout_calls: list[list[str]] = [
        call.args[0]
        for call in mock_subprocess_run.call_args_list
        if "checkout" in call.args[0]
    ]

    assert network_op_calls, "expected the fallback full clone to be invoked"
    assert _has_longpaths(network_op_calls[0]), (
        f"fallback clone argv does not carry -c core.longpaths=true: "
        f"{network_op_calls[0]!r}"
    )
    assert checkout_calls, "expected at least one checkout invocation to be captured"
    for call_args in checkout_calls:
        assert _has_longpaths(call_args), (
            f"checkout argv does not carry -c core.longpaths=true: {call_args!r}. "
            "checkout is exactly the step that fails with 'Filename too "
            "long' in the reproduced Windows failure."
        )


def test_long_path_failure_message_names_max_path() -> None:
    """A clone failure whose stderr matches the known Windows long-path
    signature must be detectable, so the caller can name MAX_PATH as the
    likely cause instead of degrading into a generic 'clone failed'.

    `core.longpaths=true` is not a complete cure (it doesn't help every
    Win32 API, and some systems additionally need the OS-level
    'LongPathsEnabled' policy). If the fix above is applied and the clone
    still fails this way, the next person must not be left guessing.
    """
    assert git_mod._is_long_path_error(
        "fatal: cannot create directory at 'a/b/c': Filename too long\n"
        "warning: Clone succeeded, but checkout failed.\n"
    )
    assert git_mod._is_long_path_error("Filename too long")
    assert not git_mod._is_long_path_error("fatal: repository not found")
