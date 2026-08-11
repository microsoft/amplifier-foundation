"""Git source handler for git+https:// URIs."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from amplifier_foundation.exceptions import BundleNotFoundError
from amplifier_foundation.paths.resolution import ParsedURI, ResolvedSource
from amplifier_foundation.sources.protocol import SourceStatus

logger = logging.getLogger(__name__)

# Metadata file name for tracking cache info
CACHE_METADATA_FILE = ".amplifier_cache_meta.json"

# Full 40-character hex commit SHA (case-insensitive, matching SourceStatus.is_pinned).
# Short/abbreviated SHAs are intentionally NOT matched: they are ambiguous as refs
# and fall through to the existing --branch clone path (and its clear error).
_FULL_COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")


def _is_full_commit_sha(ref: str) -> bool:
    """Check whether a ref is a full 40-hex-character commit SHA."""
    return bool(_FULL_COMMIT_SHA_PATTERN.match(ref))


# Retry policy for network-touching git operations (clone/fetch).
#
# Deliberately NOT gated on an error-message allowlist. Classifying git stderr
# into "transient" vs "permanent" is a policy table that drifts and misfires;
# the entire cost of retrying a genuinely permanent error here is
# _CLONE_RETRY_BACKOFF_S summed over the retries (3s), paid only on a path that
# is already failing. Retrying everything is simpler and fails safe.
_CLONE_MAX_ATTEMPTS = 3
_CLONE_RETRY_BACKOFF_S = (1.0, 2.0)

# Hard wall-clock bound per git network-operation attempt. GAP-014: without
# this, a git invocation that never returns (observed cause: a credential
# helper blocked on interactive auth it cannot complete headlessly, e.g. Git
# Credential Manager attempting an OAuth/browser flow with no desktop session)
# hangs the calling process forever, with no diagnostic and no way for the
# user to know why. Overridable because "how long is too long" legitimately
# varies with repo size and network conditions.
_CLONE_TIMEOUT_S = float(os.environ.get("AMPLIFIER_GIT_CLONE_TIMEOUT_S", "45"))


class GitCloneTimeoutError(Exception):
    """A git network operation exceeded its wall-clock bound without completing.

    This is a *bounded* failure, not a hang: the git process (and its process
    tree) was killed after ``timeout_s`` seconds because it made no progress
    a caller could observe. This is deliberately distinct from
    ``subprocess.CalledProcessError`` (git exited with a real error) because
    the diagnosis and remedy are different: a timeout usually means something
    downstream of git itself (most commonly a credential helper) is stuck
    waiting on interactive input it will never receive, not that git failed
    outright. See GAP-014 in WINDOWS-GAP-LEDGER.md for the investigation that
    root-caused this on native Windows.
    """

    def __init__(self, args: list[str], timeout_s: float) -> None:
        # Named git_args (not `args`) to avoid shadowing Exception.args, which
        # is a plain tuple used by the base class's own machinery.
        self.git_args = args
        self.timeout_s = timeout_s
        cmd = " ".join(args)
        super().__init__(
            f"git command exceeded {timeout_s:.0f}s and was killed (it was "
            f"still running, not erroring out): {cmd}\n"
            "This is usually NOT a slow network. The most common cause is a "
            "git credential helper blocked waiting on interactive "
            "authentication it cannot complete in this context (e.g. Git "
            "Credential Manager attempting a browser/OAuth flow with no "
            "desktop session available, or no cached credential for this "
            "host). To check: confirm `gh auth status` shows a valid login, "
            "and make sure that helper is tried before any interactive one "
            "for this host (`gh auth setup-git`), or run `git credential-"
            "manager github logout` to clear a stuck credential-manager "
            "state. Set AMPLIFIER_GIT_CLONE_TIMEOUT_S to change this timeout "
            f"(currently {timeout_s:.0f}s per attempt)."
        )


def _kill_process_tree(pid: int) -> None:
    """Kill a process and its descendants.

    Plain ``Popen.kill()`` only terminates the immediate child. Git spawns its
    own helper children for network operations (``git-remote-https``,
    credential helpers) that are NOT reparented/reaped when git.exe itself is
    killed, and were observed to accumulate as orphaned processes across an
    entire investigation session (GAP-013's failure mode) when only the
    top-level process was targeted.
    """
    if platform.system() == "Windows":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            check=False,
        )
    else:
        import signal

        # GAP-030 (mirrored from subprocess_runner._kill_subprocess_tree):
        # os.killpg only isolates the target if that target is in its OWN
        # process group. Every caller here spawns via _run_git_subprocess,
        # which sets start_new_session=True, so it always is -- but that is an
        # invisible coupling between two functions, and if it ever breaks,
        # killpg would SIGKILL this process and everything sharing its group,
        # which on an interactive POSIX session includes the user's shell.
        # Assert the isolation rather than trusting it.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            target_pgid = os.getpgid(pid)
            if target_pgid == os.getpgid(0):
                logger.warning(
                    "Refusing to killpg(%s): target shares this process's own "
                    "process group. Killing only the direct child instead -- "
                    "descendants may leak. This means the subprocess was not "
                    "started with start_new_session=True.",
                    target_pgid,
                )
                os.kill(pid, signal.SIGKILL)
            else:
                os.killpg(target_pgid, signal.SIGKILL)


def _run_git_subprocess(
    args: list[str], cwd: Path | None, timeout_s: float
) -> subprocess.CompletedProcess[str]:
    """Run a single git command with a hard timeout, killing the whole tree on expiry.

    A bare ``subprocess.run(..., timeout=...)`` only kills the direct child on
    expiry, which is not sufficient here: see ``_kill_process_tree``.
    """
    creationflags = 0
    start_new_session = False
    if platform.system() == "Windows":
        # Only defined on Windows builds of the subprocess module.
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        start_new_session = True

    proc = subprocess.Popen(
        args,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=creationflags,
        start_new_session=start_new_session,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc.pid)
        # Reap the now-killed process so it doesn't linger as a zombie;
        # this is expected to return almost immediately post-kill.
        with contextlib.suppress(Exception):
            proc.communicate(timeout=5)
        raise GitCloneTimeoutError(args, timeout_s) from None
    except BaseException:
        # GAP-025: proc.communicate() can also be interrupted by something
        # OTHER than its own timeout -- most importantly a user Ctrl+C
        # (KeyboardInterrupt) while this call is blocking the calling
        # thread, but also asyncio task cancellation (CancelledError) or
        # any other unwind. Both of those derive from BaseException, not
        # Exception, so the TimeoutExpired-only handler above never sees
        # them. Confirmed on native Windows: a real Ctrl+C during this
        # window IS delivered and raises KeyboardInterrupt here promptly
        # (~1s), but git.exe (or its credential-helper/remote-https
        # children) is left running as an orphan for the rest of its
        # natural lifetime -- exactly GAP-013/GAP-024's failure mode, in a
        # third, independent code path neither of those fixes covers.
        # Cleaning up here, then re-raising, closes it for every cause of
        # interruption, not just the ones we anticipated.
        _kill_process_tree(proc.pid)
        with contextlib.suppress(Exception):
            proc.communicate(timeout=5)
        raise

    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode, args, output=stdout, stderr=stderr
        )
    return subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)


def _run_git_network_op(
    args: list[str], cwd: Path | None = None, cleanup_path: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a network-touching git command, retrying transient failures.

    A single dropped connection while cloning used to fail the module
    permanently. Combined with strict activation (where a failed module aborts
    the session rather than degrading it silently), one network blip would take
    down a whole session. This absorbs that class of failure.

    Each attempt is bounded by _CLONE_TIMEOUT_S (GAP-014): a git invocation
    that never returns is treated as a failed attempt like any other, rather
    than hanging the caller indefinitely.

    Args:
        args: Full git command line.
        cwd: Working directory for the command.
        cleanup_path: If given, removed before each retry (not before the
            first attempt). ``git clone`` creates its destination directory
            immediately, before any content arrives, so a failed attempt
            (timeout OR a real git error - e.g. a dropped connection
            mid-transfer) leaves a non-empty directory behind. Without this,
            retrying `git clone` into the same path fails immediately with
            "destination path ... already exists and is not an empty
            directory", masking the real failure behind an unrelated one.
            Only meaningful for callers doing a fresh clone into a new
            directory - not for e.g. `git fetch` into an existing repo.

    Returns:
        The CompletedProcess from the first successful attempt.

    Raises:
        subprocess.CalledProcessError: From the final attempt, if all fail
            with a real git error.
        GitCloneTimeoutError: From the final attempt, if all fail by timing
            out without git ever returning.
    """
    last_error: subprocess.CalledProcessError | GitCloneTimeoutError | None = None

    for attempt in range(_CLONE_MAX_ATTEMPTS):
        try:
            return _run_git_subprocess(args, cwd, _CLONE_TIMEOUT_S)
        except (subprocess.CalledProcessError, GitCloneTimeoutError) as e:
            last_error = e
            if attempt < _CLONE_MAX_ATTEMPTS - 1:
                delay = _CLONE_RETRY_BACKOFF_S[attempt]
                if isinstance(e, GitCloneTimeoutError):
                    reason = f"timed out after {_CLONE_TIMEOUT_S:.0f}s"
                else:
                    reason = (e.stderr or "").strip()
                logger.warning(
                    f"git {args[1] if len(args) > 1 else ''} failed "
                    f"(attempt {attempt + 1}/{_CLONE_MAX_ATTEMPTS}), "
                    f"retrying in {delay}s: {reason}"
                )
                if cleanup_path is not None:
                    shutil.rmtree(cleanup_path, ignore_errors=True)
                time.sleep(delay)

    assert last_error is not None  # loop always sets it before exhausting
    raise last_error


class GitSourceHandler:
    """Handler for git+https:// URIs.

    Clones repositories to a cache directory and returns the local path.
    Uses shallow clones for efficiency.

    Implements SourceHandlerWithStatusProtocol for update detection.
    """

    def can_handle(self, parsed: ParsedURI) -> bool:
        """Check if this handler can handle the given URI."""
        return parsed.is_git

    def _build_git_url(self, parsed: ParsedURI) -> str:
        """Build git URL from parsed URI (without git+ prefix)."""
        scheme = parsed.scheme.replace("git+", "")
        return f"{scheme}://{parsed.host}{parsed.path}"

    def _get_cache_path(self, parsed: ParsedURI, cache_dir: Path) -> Path:
        """Get the cache path for a parsed URI."""
        git_url = self._build_git_url(parsed)
        ref = parsed.ref or "HEAD"
        cache_key = hashlib.sha256(f"{git_url}@{ref}".encode()).hexdigest()[:16]
        repo_name = parsed.path.rstrip("/").split("/")[-1]
        return cache_dir / f"{repo_name}-{cache_key}"

    def _get_local_commit(self, cache_path: Path) -> str | None:
        """Get the commit SHA of the cached repository."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=cache_path,
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            return None

    async def _get_remote_commit(self, git_url: str, ref: str) -> str | None:
        """Get the current commit SHA from remote without cloning.

        Uses git ls-remote which is fast and doesn't download content.
        """
        try:
            # Run in thread pool to not block
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    ["git", "ls-remote", git_url, ref],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=30,
                ),
            )
            # Parse output: "SHA\trefs/heads/main" or "SHA\tHEAD"
            if result.stdout.strip():
                return result.stdout.split()[0]
            return None
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            FileNotFoundError,
        ):
            return None

    def _get_cache_metadata(self, cache_path: Path) -> dict:
        """Load cache metadata if it exists."""
        meta_path = cache_path / CACHE_METADATA_FILE
        if meta_path.exists():
            try:
                return json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_cache_metadata(self, cache_path: Path, metadata: dict) -> None:
        """Save cache metadata."""
        meta_path = cache_path / CACHE_METADATA_FILE
        with contextlib.suppress(OSError):
            meta_path.write_text(json.dumps(metadata, indent=2, default=str))

    def _verify_clone_integrity(self, cache_path: Path) -> bool:
        """Verify that a cloned repository has expected structure.

        Checks for indicators that the clone completed successfully and contains
        a recognized module marker (Python, bundle, or amplifier.toml as a non-Python
        module). This catches cases where git clone partially
        succeeds but leaves an incomplete directory (e.g., due to network issues,
        cloud sync interference, or disk I/O errors).

        Args:
            cache_path: Path to the cloned repository.

        Returns:
            True if the clone appears complete and valid, False otherwise.
        """
        if not cache_path.exists():
            return False

        # Must have .git directory (indicates git clone completed)
        if not (cache_path / ".git").exists():
            logger.warning(f"Clone missing .git directory: {cache_path}")
            return False

        # For Python modules, check for pyproject.toml, setup.py, or setup.cfg
        # Also check for bundle.md/bundle.yaml for amplifier bundles
        # Non-Python modules (Rust, WASM, gRPC) use amplifier.toml as their marker
        has_python_module = (
            (cache_path / "pyproject.toml").exists()
            or (cache_path / "setup.py").exists()
            or (cache_path / "setup.cfg").exists()
        )
        has_bundle = (cache_path / "bundle.md").exists() or (
            cache_path / "bundle.yaml"
        ).exists()
        has_amplifier_module = (cache_path / "amplifier.toml").exists()

        if not has_python_module and not has_bundle and not has_amplifier_module:
            logger.warning(
                f"Clone missing expected files (pyproject.toml/setup.py/bundle.md/amplifier.toml): {cache_path}"
            )
            return False

        return True

    def _clone_at_commit(self, git_url: str, sha: str, cache_path: Path) -> None:
        """Clone a repository pinned to a specific commit SHA.

        ``git clone --branch`` only accepts branch/tag names, so commit SHAs
        need a fetch + checkout sequence instead. Tries the cheapest form
        first: shallow-fetch exactly the requested commit (supported by
        GitHub and any server with ``uploadpack.allowReachableSHA1InWant``).
        Falls back to a full clone + checkout when the server refuses to
        serve the SHA directly.

        Args:
            git_url: Repository URL (without git+ prefix).
            sha: Full 40-character commit SHA to pin.
            cache_path: Destination directory for the clone.

        Raises:
            subprocess.CalledProcessError: If both strategies fail (converted
                to BundleNotFoundError by the caller).
            GitCloneTimeoutError: If the network-touching fetch step hangs
                past _CLONE_TIMEOUT_S without git returning (GAP-014).
        """

        def run_git(args: list[str], cwd: Path | None = None) -> None:
            # Local-only git plumbing, so a plain bounded run is fine here. The
            # network-touching step below goes through _run_git_network_op
            # instead, which has the GAP-014 timeout.
            #
            # The 30s bound is NOT purely defensive: two of this helper's four
            # call sites are `git checkout`, which on a large working tree is
            # genuinely disk-bound and can exceed 30s. So the timeout path is
            # reachable in normal use, not just under pathology.
            #
            # `subprocess.TimeoutExpired` is a SubprocessError, NOT a
            # CalledProcessError -- every `except (CalledProcessError,
            # GitCloneTimeoutError)` handler in this module would let it sail
            # straight past, so a slow checkout would escape the designed
            # full-clone fallback as a raw traceback instead of degrading
            # gracefully. Convert it to GitCloneTimeoutError, which every one
            # of those handlers already catches.
            try:
                subprocess.run(
                    args,
                    cwd=cwd,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except subprocess.TimeoutExpired as exc:
                raise GitCloneTimeoutError(args, 30) from exc

        try:
            # Cheap path: init + shallow fetch of the exact commit.
            cache_path.mkdir(parents=True, exist_ok=True)
            run_git(["git", "init", "--quiet", str(cache_path)])
            run_git(["git", "remote", "add", "origin", git_url], cwd=cache_path)
            # GAP-030 cross-platform validation: this used to call
            # _run_git_network_op (which RETRIES up to _CLONE_MAX_ATTEMPTS
            # times), directly contradicting the "Retry only this clone, not
            # the shallow fetch above" comment a few lines below -- a
            # regression introduced by GAP-014 switching this call over
            # without noticing it also inherited retry behavior it was never
            # meant to have. A server without allowReachableSHA1InWant now
            # refused the fetch 3 times (with 1s+2s backoff, ~3s wasted) on
            # EVERY clone before falling back, instead of once. Still routed
            # through _run_git_subprocess directly (not a bare subprocess.run)
            # so the GAP-014 wall-clock timeout still applies to a fetch that
            # hangs -- just without the retry-on-failure this specific call
            # was never supposed to have.
            _run_git_subprocess(
                ["git", "fetch", "--depth", "1", "origin", sha],
                cwd=cache_path,
                timeout_s=_CLONE_TIMEOUT_S,
            )
            run_git(
                [
                    "git",
                    "-c",
                    "advice.detachedHead=false",
                    "checkout",
                    "--quiet",
                    "FETCH_HEAD",
                ],
                cwd=cache_path,
            )
        except (subprocess.CalledProcessError, GitCloneTimeoutError) as e:
            # Server refused direct SHA fetch (e.g., unadvertised object on a
            # server without allowReachableSHA1InWant) - or the fetch hung
            # (GAP-014). Fall back to a full clone + checkout. Errors here
            # propagate to the caller.
            detail = (
                e.stderr if isinstance(e, subprocess.CalledProcessError) else str(e)
            )
            logger.debug(
                f"Shallow fetch of commit {sha} from {git_url} failed "
                f"({detail}); falling back to full clone + checkout"
            )
            shutil.rmtree(cache_path, ignore_errors=True)
            # Retry only this clone, not the shallow fetch above: that fetch
            # failing is an *expected* outcome on servers without
            # allowReachableSHA1InWant, and retrying it would add seconds to a
            # designed fallback path rather than to an error path.
            _run_git_network_op(
                ["git", "clone", git_url, str(cache_path)], cleanup_path=cache_path
            )
            run_git(
                ["git", "-c", "advice.detachedHead=false", "checkout", "--quiet", sha],
                cwd=cache_path,
            )

    async def resolve(self, parsed: ParsedURI, cache_dir: Path) -> ResolvedSource:
        """Resolve git URI to local cached path.

        Args:
            parsed: Parsed URI components.
            cache_dir: Directory for caching cloned repos.

        Returns:
            ResolvedSource with active_path and source_root.

        Raises:
            BundleNotFoundError: If clone fails or ref not found.
        """
        git_url = self._build_git_url(parsed)
        ref = parsed.ref or "HEAD"
        cache_path = self._get_cache_path(parsed, cache_dir)

        # Check if already cached and valid
        if cache_path.exists():
            # Verify cache integrity before using
            if not self._verify_clone_integrity(cache_path):
                logger.warning(f"Cached clone is invalid, removing: {cache_path}")
                shutil.rmtree(cache_path, ignore_errors=True)
            else:
                result_path = cache_path
                if parsed.subpath:
                    result_path = cache_path / parsed.subpath
                if result_path.exists():
                    return ResolvedSource(
                        active_path=result_path, source_root=cache_path
                    )

        # Clone repository
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        # Remove partial clone if exists
        if cache_path.exists():
            shutil.rmtree(cache_path)

        try:
            if parsed.ref and _is_full_commit_sha(parsed.ref):
                # Commit SHAs are not valid --branch arguments; use fetch + checkout.
                self._clone_at_commit(git_url, parsed.ref, cache_path)
            else:
                # Shallow clone with specific ref
                # Note: "HEAD" is not a valid --branch argument; it's a symbolic reference.
                # When ref is HEAD (or not specified), let git clone use the repo's default branch.
                clone_args = ["git", "clone", "--depth", "1"]
                if parsed.ref and parsed.ref != "HEAD":
                    clone_args.extend(["--branch", parsed.ref])
                clone_args.extend([git_url, str(cache_path)])

                _run_git_network_op(clone_args, cleanup_path=cache_path)

            # Verify clone completed with expected structure
            if not self._verify_clone_integrity(cache_path):
                # Clone succeeded but result is invalid - remove and raise error
                shutil.rmtree(cache_path, ignore_errors=True)
                raise BundleNotFoundError(
                    f"Clone of {git_url}@{ref} completed but result is invalid "
                    "(missing pyproject.toml/setup.py/bundle.md/amplifier.toml). "
                    "This may indicate a network issue or cloud sync interference."
                )

            # Save metadata after successful clone
            commit = self._get_local_commit(cache_path)
            self._save_cache_metadata(
                cache_path,
                {
                    "cached_at": datetime.now().isoformat(),
                    "ref": ref,
                    "commit": commit,
                    "git_url": git_url,
                },
            )
        except (subprocess.CalledProcessError, GitCloneTimeoutError) as e:
            detail = (
                e.stderr if isinstance(e, subprocess.CalledProcessError) else str(e)
            )
            raise BundleNotFoundError(
                f"Failed to clone {git_url}@{ref}: {detail}"
            ) from e

        # Return path with subpath if specified
        result_path = cache_path
        if parsed.subpath:
            result_path = cache_path / parsed.subpath

        if not result_path.exists():
            raise BundleNotFoundError(
                f"Subpath not found after clone: {parsed.subpath}"
            )

        return ResolvedSource(active_path=result_path, source_root=cache_path)

    async def get_status(self, parsed: ParsedURI, cache_dir: Path) -> SourceStatus:
        """Check status of git source without downloading.

        Compares cached commit (if any) against remote HEAD to detect updates.
        Uses git ls-remote which is fast and bandwidth-efficient.

        Args:
            parsed: Parsed URI components.
            cache_dir: Directory where cached content would be stored.

        Returns:
            SourceStatus with update detection information.
        """
        git_url = self._build_git_url(parsed)
        ref = parsed.ref or "HEAD"
        cache_path = self._get_cache_path(parsed, cache_dir)

        # Build source URI for display
        source_uri = f"git+{git_url}"
        if parsed.ref:
            source_uri += f"@{parsed.ref}"

        # Initialize status
        status = SourceStatus(
            source_uri=source_uri,
            is_cached=cache_path.exists(),
            cached_ref=ref,
            remote_ref=ref,
        )

        # Get cached info if exists
        if cache_path.exists():
            metadata = self._get_cache_metadata(cache_path)
            if metadata.get("cached_at"):
                with contextlib.suppress(ValueError):
                    status.cached_at = datetime.fromisoformat(metadata["cached_at"])
            status.cached_commit = metadata.get("commit") or self._get_local_commit(
                cache_path
            )
        else:
            status.cached_commit = None

        # Check for pinned refs (can't have updates)
        if status.is_pinned:
            status.has_update = False
            status.summary = f"Pinned to {ref} (no updates possible)"
            return status

        # Get remote commit
        try:
            status.remote_commit = await self._get_remote_commit(git_url, ref)

            if status.remote_commit is None:
                status.has_update = None
                status.error = f"Could not find ref '{ref}' on remote"
                status.summary = f"Error: ref '{ref}' not found"
            elif not status.is_cached:
                status.has_update = True
                status.summary = f"Not cached (remote: {status.remote_commit[:8]})"
            elif status.cached_commit == status.remote_commit:
                status.has_update = False
                cached_short = (
                    status.cached_commit[:8] if status.cached_commit else "unknown"
                )
                status.summary = f"Up to date ({cached_short})"
            else:
                status.has_update = True
                cached_short = (
                    status.cached_commit[:8] if status.cached_commit else "unknown"
                )
                remote_short = status.remote_commit[:8]
                status.summary = f"Update available ({cached_short} → {remote_short})"
        except Exception as e:
            status.has_update = None
            status.error = str(e)
            status.summary = f"Error checking remote: {e}"

        return status

    async def update(self, parsed: ParsedURI, cache_dir: Path) -> ResolvedSource:
        """Force re-clone of repository, ignoring cache.

        Removes any cached version and downloads fresh content.

        Args:
            parsed: Parsed URI components.
            cache_dir: Directory for caching downloaded content.

        Returns:
            ResolvedSource with the updated content.

        Raises:
            BundleNotFoundError: If clone fails.
        """
        cache_path = self._get_cache_path(parsed, cache_dir)

        # Remove existing cache
        if cache_path.exists():
            shutil.rmtree(cache_path)

        # Re-resolve (will clone fresh)
        return await self.resolve(parsed, cache_dir)
