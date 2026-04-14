"""Zip source handler for zip+https:// and zip+file:// URIs."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.request import urlopen

from amplifier_foundation.exceptions import BundleNotFoundError
from amplifier_foundation.paths.resolution import ParsedURI
from amplifier_foundation.paths.resolution import ResolvedSource


class ZipSourceHandler:
    """Handler for zip+https:// and zip+file:// URIs.

    Downloads/copies zip archives, extracts to cache, returns local path.
    Uses content-addressable storage (hash of URI).
    """

    def can_handle(self, parsed: ParsedURI) -> bool:
        """Check if this handler can handle the given URI."""
        return parsed.is_zip

    async def resolve(self, parsed: ParsedURI, cache_dir: Path) -> ResolvedSource:
        """Resolve zip URI to local extracted path.

        Args:
            parsed: Parsed URI components.
            cache_dir: Directory for caching extracted content.

        Returns:
            ResolvedSource with active_path and source_root.

        Raises:
            BundleNotFoundError: If download/extraction fails.
        """
        # Build the source URL (without zip+ prefix)
        inner_scheme = parsed.scheme.replace("zip+", "")

        if inner_scheme == "file":
            # Local zip file
            zip_path = Path(parsed.path)
            source_uri = str(zip_path)
        else:
            # Remote zip (https, http)
            zip_path = None
            source_uri = f"{inner_scheme}://{parsed.host}{parsed.path}"

        # Create cache key from URI
        cache_key = hashlib.sha256(source_uri.encode()).hexdigest()[:16]
        zip_name = Path(parsed.path).stem or "archive"
        extract_path = cache_dir / f"{zip_name}-{cache_key}"

        # Check if already cached (before checking if source exists)
        if extract_path.exists():
            result_path = extract_path
            if parsed.subpath:
                result_path = extract_path / parsed.subpath
            if result_path.exists():
                return ResolvedSource(active_path=result_path, source_root=extract_path)

        # Now check if source exists (for local files)
        if inner_scheme == "file" and zip_path and not zip_path.exists():
            raise BundleNotFoundError(f"Zip file not found: {zip_path}")

        # Ensure cache directory exists
        cache_dir.mkdir(parents=True, exist_ok=True)

        # Remove partial extraction if exists
        if extract_path.exists():
            shutil.rmtree(extract_path)

        try:
            if inner_scheme == "file" and zip_path:
                # Extract directly from local zip
                self._extract_zip(zip_path, extract_path)
            else:
                # Download then extract
                self._download_and_extract(source_uri, extract_path)
        except Exception as e:
            # Clean up on failure
            if extract_path.exists():
                shutil.rmtree(extract_path)
            raise BundleNotFoundError(f"Failed to extract {source_uri}: {e}") from e

        # Return path with subpath if specified
        result_path = extract_path
        if parsed.subpath:
            result_path = extract_path / parsed.subpath

        if not result_path.exists():
            raise BundleNotFoundError(f"Subpath not found after extraction: {parsed.subpath}")

        return ResolvedSource(active_path=result_path, source_root=extract_path)

    def _extract_zip(self, zip_path: Path, extract_path: Path) -> None:
        """Extract a zip file to the target path.

        Args:
            zip_path: Path to zip file.
            extract_path: Directory to extract to.
        """
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_path)

    def _download_and_extract(self, url: str, extract_path: Path) -> None:
        """Download a zip from URL and extract it.

        Args:
            url: URL to download from.
            extract_path: Directory to extract to.
        """
        # Download to temp file
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            try:
                with urlopen(url, timeout=60) as response:  # noqa: S310
                    tmp.write(response.read())

                # Extract
                self._extract_zip(tmp_path, extract_path)
            finally:
                # Clean up temp file
                tmp_path.unlink(missing_ok=True)
