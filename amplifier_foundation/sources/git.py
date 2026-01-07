"""Git source handler for git+https:// URIs."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from amplifier_foundation.exceptions import BundleNotFoundError
from amplifier_foundation.paths.resolution import ParsedURI
from amplifier_foundation.paths.resolution import ResolvedSource
from amplifier_foundation.sources.protocol import SourceStatus

logger = logging.getLogger(__name__)

# Metadata file name for tracking cache info
CACHE_METADATA_FILE = ".amplifier_cache_meta.json"


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
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
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
        a valid Python module. This catches cases where git clone partially
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
        has_python_module = (
            (cache_path / "pyproject.toml").exists()
            or (cache_path / "setup.py").exists()
            or (cache_path / "setup.cfg").exists()
        )
        has_bundle = (cache_path / "bundle.md").exists() or (cache_path / "bundle.yaml").exists()

        if not has_python_module and not has_bundle:
            logger.warning(f"Clone missing expected files (pyproject.toml/setup.py/bundle.md): {cache_path}")
            return False

        return True

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
                    return ResolvedSource(active_path=result_path, source_root=cache_path)

        # Clone repository
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        # Remove partial clone if exists
        if cache_path.exists():
            shutil.rmtree(cache_path)

        try:
            # Shallow clone with specific ref
            # Note: "HEAD" is not a valid --branch argument; it's a symbolic reference.
            # When ref is HEAD (or not specified), let git clone use the repo's default branch.
            clone_args = ["git", "clone", "--depth", "1"]
            if parsed.ref and parsed.ref != "HEAD":
                clone_args.extend(["--branch", parsed.ref])
            clone_args.extend([git_url, str(cache_path)])

            subprocess.run(
                clone_args,
                check=True,
                capture_output=True,
                text=True,
            )

            # Verify clone completed with expected structure
            if not self._verify_clone_integrity(cache_path):
                # Clone succeeded but result is invalid - remove and raise error
                shutil.rmtree(cache_path, ignore_errors=True)
                raise BundleNotFoundError(
                    f"Clone of {git_url}@{ref} completed but result is invalid "
                    "(missing pyproject.toml/setup.py/bundle.md). "
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
        except subprocess.CalledProcessError as e:
            raise BundleNotFoundError(f"Failed to clone {git_url}@{ref}: {e.stderr}") from e

        # Return path with subpath if specified
        result_path = cache_path
        if parsed.subpath:
            result_path = cache_path / parsed.subpath

        if not result_path.exists():
            raise BundleNotFoundError(f"Subpath not found after clone: {parsed.subpath}")

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
            status.cached_commit = metadata.get("commit") or self._get_local_commit(cache_path)
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
                cached_short = status.cached_commit[:8] if status.cached_commit else "unknown"
                status.summary = f"Up to date ({cached_short})"
            else:
                status.has_update = True
                cached_short = status.cached_commit[:8] if status.cached_commit else "unknown"
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
