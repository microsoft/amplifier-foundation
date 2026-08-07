"""URI parsing and path normalization utilities."""

from __future__ import annotations

import os
import platform
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

# Precompiled regex for parsing git+https:// URI paths.
# Extracts path and optional ref (branch/tag) from patterns like /org/repo@feat/branch
# - 'path' group: repository path (everything before @)
# - 'ref' group: optional branch/tag/commit (everything after @, can contain slashes)
_GIT_PATH_PATTERN = re.compile(r"^(?P<path>[^@]+)(?:@(?P<ref>.+))?$")

# Native Windows drive-letter absolute path: "C:\..." or "C:/...".
# A single ASCII letter followed by ":" is unambiguous here -- every real URI
# scheme this parser recognizes is multi-character (http, https, git+https,
# file, zip+https, ...), so this can never collide with a scheme prefix.
_WINDOWS_DRIVE_PATH_PATTERN = re.compile(r"^[A-Za-z]:[\\/]")


def _is_windows_absolute_path(uri: str) -> bool:
    """True for a native Windows absolute path: drive-letter or UNC form.

    Covers both separator styles Windows accepts for a drive-letter path
    (``C:\\Users\\x`` and ``C:/Users/x``) plus backslash UNC paths
    (``\\\\server\\share``).

    Deliberately NOT matched: forward-slash UNC (``//server/share``). That
    spelling is syntactically identical to a protocol-relative URL, which
    this resolver has no other support for, so it is left unhandled rather
    than guessed at.
    """
    return bool(_WINDOWS_DRIVE_PATH_PATTERN.match(uri)) or uri.startswith("\\\\")


def _is_posix_style_absolute_path(path: str) -> bool:
    """True for a path spelled as a POSIX absolute path (``/home/x``, ``/etc``).

    Deliberately NOT matched: forward-slash UNC (``//server/share``) -- same
    reasoning as the exclusion in ``_is_windows_absolute_path``: that
    spelling is ambiguous with a protocol-relative URL and is left unhandled
    here rather than guessed at.
    """
    return path.startswith("/") and not path.startswith("//")


def describe_cross_platform_path_mismatch(path_str: str) -> str | None:
    """Return an explanation if `path_str` is an absolute path shaped for a
    *different* OS than the one currently running, else return None.

    GAP-007: naive resolution (``pathlib.Path(path_str).resolve()``) does not
    fail on a foreign-OS absolute path -- it silently coerces it into a
    nonsense local one. On Windows, ``Path("/home/bkrabach/dev/x").resolve()``
    prepends the current drive letter, producing ``C:\\home\\bkrabach\\dev\\x``,
    which then fails with a generic "File not found" that sends the user
    hunting for a file that was never expected to exist on this machine. The
    mirror case exists on POSIX: a Windows drive-letter path like
    ``C:\\Users\\x`` is not absolute by POSIX rules, so it silently resolves
    relative to the current working directory instead of failing clearly.

    This function lets callers (source handlers) catch either case *before*
    attempting resolution and raise a message that names the actual problem
    -- a config written for another OS's filesystem -- instead of a
    misleading "file not found".

    Args:
        path_str: The raw path string as parsed from the source URI (i.e.
            ``ParsedURI.path``), before any OS-specific resolution.

    Returns:
        A human-readable explanation of the mismatch, or None if `path_str`
        is not a foreign-OS absolute path (including: it's a relative path,
        or it's already native to the current OS).
    """
    running_on_windows = os.name == "nt"

    if running_on_windows and _is_posix_style_absolute_path(path_str):
        return (
            f"'{path_str}' is a POSIX-style absolute path (Linux/macOS-shaped), "
            "which cannot exist on Windows. This bundle source refers to "
            "another OS's filesystem, not a missing file on this machine -- "
            "update the source in settings.yaml to a Windows path, or copy/"
            "re-clone the referenced content locally on this machine."
        )

    if not running_on_windows and _is_windows_absolute_path(path_str):
        return (
            f"'{path_str}' is a Windows-style absolute path (drive-letter or "
            f"UNC), which cannot exist on {platform.system()}. This bundle "
            "source refers to another OS's filesystem, not a missing file "
            "on this machine -- update the source in settings.yaml to a "
            "POSIX path, or copy/re-clone the referenced content locally on "
            "this machine."
        )

    return None


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
        if self.scheme == "file":
            return True
        if self.scheme != "":
            return False
        # Empty scheme: treat as a local path if it looks like one under
        # either separator convention -- POSIX ("/" present) or native
        # Windows (backslash, or a bare drive-letter/UNC path). parse_uri()
        # already assigns scheme="file" for the Windows forms it recognizes,
        # so this branch is defense-in-depth for ParsedURI values built by
        # other means, not the primary path for real Windows URIs.
        return (
            "/" in self.path
            or "\\" in self.path
            or _is_windows_absolute_path(self.path)
        )

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

    # Handle absolute paths (POSIX)
    if uri.startswith("/"):
        return ParsedURI(scheme="file", host="", path=uri, ref="", subpath="")

    # Handle native Windows absolute paths (drive-letter or backslash UNC).
    # Must run before the "package/subpath" fallback below: a forward-slash
    # Windows path like "C:/Users/x" contains a "/" and would otherwise be
    # misparsed there as package="C:", subpath="Users/x".
    if _is_windows_absolute_path(uri):
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
    p = Path(path).expanduser()

    if p.is_absolute():
        return p.resolve()

    if relative_to:
        return (relative_to / p).resolve()

    return p.resolve()
