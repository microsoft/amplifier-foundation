"""URI parsing and path normalization utilities."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

# Precompiled regex for parsing git+https:// URI paths.
# Extracts path and optional ref (branch/tag) from patterns like /org/repo@feat/branch
# - 'path' group: repository path (everything before @)
# - 'ref' group: optional branch/tag/commit (everything after @, can contain slashes)
_GIT_PATH_PATTERN = re.compile(r"^(?P<path>[^@]+)(?:@(?P<ref>.+))?$")


def get_amplifier_home() -> Path:
    """Get the Amplifier home directory.

    Resolves in order:
    1. AMPLIFIER_HOME environment variable
    2. ~/.amplifier (default)

    This is the single source of truth for all Amplifier path resolution.
    All components should use this for determining cache and data directories.

    Returns:
        Resolved path to Amplifier home directory.
    """
    env_home = os.environ.get("AMPLIFIER_HOME")
    if env_home:
        return Path(env_home).expanduser().resolve()
    return (Path.home() / ".amplifier").resolve()


@dataclass
class ParsedURI:
    """Parsed URI components."""

    scheme: str  # git, file, http, https, zip, or empty for package names
    host: str  # github.com, etc.
    path: str  # /org/repo or local path
    ref: str  # @main, @v1.0.0, etc. (empty if not specified)
    subpath: str  # path inside container (from #subdirectory= fragment)

    @property
    def is_git(self) -> bool:
        """True if this is a git URI."""
        return self.scheme == "git" or self.scheme.startswith("git+")

    @property
    def is_file(self) -> bool:
        """True if this is a file URI or local path."""
        return self.scheme == "file" or (self.scheme == "" and "/" in self.path)

    @property
    def is_http(self) -> bool:
        """True if this is an HTTP/HTTPS URI."""
        return self.scheme in ("http", "https")

    @property
    def is_zip(self) -> bool:
        """True if this is a zip URI (zip+https://, zip+file://)."""
        return self.scheme.startswith("zip+")

    @property
    def is_package(self) -> bool:
        """True if this looks like a package/bundle name."""
        return self.scheme == "" and "/" not in self.path


@dataclass
class ResolvedSource:
    """Result of resolving a source URI to local paths.

    Tracks both the requested path (which may be a subdirectory) and the
    source root (full clone/extract root), enabling @-mention resolution
    to access files outside the immediate subdirectory when needed.

    When loading from a subdirectory (e.g., git+https://...#subdirectory=behaviors/x),
    the registry can walk back from active_path to source_root to find the nearest
    bundle.md/bundle.yaml and register it for @-mention access.

    Attributes:
        active_path: The requested path (subdirectory or root).
        source_root: The full clone/extract root (always the container root).
    """

    active_path: Path  # The requested path (subdirectory or root)
    source_root: Path  # The full clone/extract root (always the container root)

    @property
    def is_subdirectory(self) -> bool:
        """True if active_path is a subdirectory of source_root."""
        return self.active_path != self.source_root


def parse_uri(uri: str) -> ParsedURI:
    """Parse a URI into components.

    Supports pip/uv standard syntax with #subdirectory= fragment:
    - git+https://github.com/org/repo@ref#subdirectory=path/inside
    - zip+https://example.com/bundle.zip#subdirectory=path/inside
    - zip+file:///local/archive.zip#subdirectory=path/inside
    - file:///path/to/file
    - /absolute/path
    - ./relative/path
    - package-name
    - package/subpath

    Args:
        uri: URI string to parse.

    Returns:
        ParsedURI with extracted components.
    """
    # Handle git+ prefix (pip/uv standard)
    if uri.startswith("git+"):
        return _parse_vcs_uri(uri, prefix="git+")

    # Handle zip+ prefix (extended pattern for archives)
    if uri.startswith("zip+"):
        return _parse_vcs_uri(uri, prefix="zip+")

    # Handle explicit file:// scheme
    if uri.startswith("file://"):
        path, subpath = _extract_fragment_subpath(uri[7:])
        return ParsedURI(scheme="file", host="", path=path, ref="", subpath=subpath)

    # Handle absolute paths
    if uri.startswith("/"):
        return ParsedURI(scheme="file", host="", path=uri, ref="", subpath="")

    # Handle relative paths
    if uri.startswith("./") or uri.startswith("../"):
        return ParsedURI(scheme="file", host="", path=uri, ref="", subpath="")

    # Handle http/https URLs
    if uri.startswith("http://") or uri.startswith("https://"):
        parsed = urlparse(uri)
        subpath = _extract_subdirectory_from_fragment(parsed.fragment)
        return ParsedURI(
            scheme=parsed.scheme,
            host=parsed.netloc,
            path=parsed.path,
            ref="",
            subpath=subpath,
        )

    # Assume package name or package/subpath
    if "/" in uri:
        # Could be package/subpath like "foundation/providers/anthropic"
        parts = uri.split("/", 1)
        return ParsedURI(
            scheme="",
            host="",
            path=parts[0],
            ref="",
            subpath=parts[1] if len(parts) > 1 else "",
        )

    return ParsedURI(scheme="", host="", path=uri, ref="", subpath="")


def _extract_subdirectory_from_fragment(fragment: str) -> str:
    """Extract subdirectory= value from URL fragment.

    Follows pip/uv standard: #subdirectory=path/inside

    Args:
        fragment: URL fragment string (without leading #).

    Returns:
        Subdirectory path, or empty string if not specified.
    """
    if not fragment:
        return ""

    # Parse fragment as query string (handles subdirectory=value)
    # Fragment format: subdirectory=path/inside or subdirectory=path/inside&other=val
    for part in fragment.split("&"):
        if part.startswith("subdirectory="):
            return part[len("subdirectory=") :]

    return ""


def _extract_fragment_subpath(uri_with_possible_fragment: str) -> tuple[str, str]:
    """Split a URI into path and subdirectory from fragment.

    Args:
        uri_with_possible_fragment: URI that may contain #subdirectory=.

    Returns:
        Tuple of (path, subpath).
    """
    if "#" in uri_with_possible_fragment:
        path, fragment = uri_with_possible_fragment.split("#", 1)
        subpath = _extract_subdirectory_from_fragment(fragment)
        return path, subpath
    return uri_with_possible_fragment, ""


def _parse_vcs_uri(uri: str, prefix: str) -> ParsedURI:
    """Parse a VCS URI (git+ or zip+ prefix).

    Args:
        uri: Full URI including prefix.
        prefix: The prefix to strip (e.g., "git+", "zip+").

    Returns:
        ParsedURI with extracted components.
    """
    # Strip prefix for parsing
    uri_without_prefix = uri[len(prefix) :]

    # Extract any fragment (#subdirectory=)
    subpath = ""
    if "#" in uri_without_prefix:
        uri_without_prefix, fragment = uri_without_prefix.split("#", 1)
        subpath = _extract_subdirectory_from_fragment(fragment)

    parsed = urlparse(uri_without_prefix)

    # Extract path and optional ref (e.g., /org/repo@main or /org/repo@feat/branch)
    # Only git+ URIs support @ref syntax - zip archives don't have branches
    # Default ref to "main" when not specified for git+ URIs
    path = parsed.path
    ref = ""

    if prefix == "git+":
        match = _GIT_PATH_PATTERN.match(path)
        if match:
            path = match.group("path")
            ref = match.group("ref") or "main"

    return ParsedURI(
        scheme=prefix + parsed.scheme,
        host=parsed.netloc,
        path=path,
        ref=ref,
        subpath=subpath,
    )


def normalize_path(path: str | Path, relative_to: Path | None = None) -> Path:
    """Normalize a path, resolving relative paths if base provided.

    Args:
        path: Path to normalize.
        relative_to: Base path for relative paths.

    Returns:
        Normalized absolute Path.
    """
    p = Path(path)

    if p.is_absolute():
        return p.resolve()

    if relative_to:
        return (relative_to / p).resolve()

    return p.resolve()
