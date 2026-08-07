"""Tests for path utilities."""

from pathlib import Path

from amplifier_foundation.paths.construction import (
    construct_agent_path,
    construct_context_path,
)
from amplifier_foundation.paths.resolution import (
    describe_cross_platform_path_mismatch,
    normalize_path,
    parse_uri,
)


class TestParseUri:
    """Tests for parse_uri function."""

    def test_git_https_uri(self) -> None:
        """Parses git+https:// URIs."""
        result = parse_uri("git+https://github.com/user/repo@main")
        assert result.scheme == "git+https"
        assert result.host == "github.com"
        assert result.path == "/user/repo"
        assert result.ref == "main"

    def test_git_https_uri_with_slash_in_branch_name(self) -> None:
        """Parses git+https:// URIs with branch names containing slashes.

        Branch naming conventions like feat/, fix/, bugfix/ are standard.
        The ref pattern must allow slashes in the branch name portion.
        Regression test for: https://github.com/microsoft-amplifier/amplifier-support/issues/15
        """
        # feat/ prefix (common feature branch pattern)
        result = parse_uri(
            "git+https://github.com/robotdad/amplifier-module-provider-openai@feat/deep-research-support"
        )
        assert result.scheme == "git+https"
        assert result.host == "github.com"
        assert result.path == "/robotdad/amplifier-module-provider-openai"
        assert result.ref == "feat/deep-research-support"

        # fix/ prefix
        result = parse_uri("git+https://github.com/user/repo@fix/critical-bug")
        assert result.ref == "fix/critical-bug"

        # Multiple slashes in branch name
        result = parse_uri("git+https://github.com/org/repo@feature/2026/q1-release")
        assert result.ref == "feature/2026/q1-release"

        # bugfix/ prefix
        result = parse_uri(
            "git+https://github.com/org/repo@bugfix/issue-123/memory-leak"
        )
        assert result.ref == "bugfix/issue-123/memory-leak"

    def test_git_https_uri_with_slash_branch_and_subdirectory(self) -> None:
        """Parses git+https:// URIs with slashes in branch AND subdirectory fragment.

        Ensures both ref and subpath are correctly parsed when branch has slashes.
        """
        result = parse_uri(
            "git+https://github.com/org/repo@feat/new-feature#subdirectory=bundles/foundation"
        )
        assert result.scheme == "git+https"
        assert result.host == "github.com"
        assert result.path == "/org/repo"
        assert result.ref == "feat/new-feature"
        assert result.subpath == "bundles/foundation"

    def test_git_https_uri_without_ref_defaults_to_main(self) -> None:
        """Git URIs without explicit ref default to 'main' branch.

        When no @ref is specified, the parser assumes 'main' as the default branch.
        """
        result = parse_uri("git+https://github.com/user/repo")
        assert result.scheme == "git+https"
        assert result.host == "github.com"
        assert result.path == "/user/repo"
        assert result.ref == "main"  # Default when not specified

        # With subdirectory but no ref - should still default to main
        result = parse_uri("git+https://github.com/org/repo#subdirectory=bundles/core")
        assert result.path == "/org/repo"
        assert result.ref == "main"
        assert result.subpath == "bundles/core"

    def test_git_uri_with_subdirectory_fragment(self) -> None:
        """Parses git URI with pip/uv standard #subdirectory= fragment."""
        result = parse_uri(
            "git+https://github.com/org/repo@main#subdirectory=bundles/foundation"
        )
        assert result.scheme == "git+https"
        assert result.host == "github.com"
        assert result.path == "/org/repo"
        assert result.ref == "main"
        assert result.subpath == "bundles/foundation"

    def test_zip_https_uri(self) -> None:
        """Parses zip+https:// URIs."""
        result = parse_uri(
            "zip+https://releases.example.com/bundle.zip#subdirectory=foundation"
        )
        assert result.scheme == "zip+https"
        assert result.host == "releases.example.com"
        assert result.path == "/bundle.zip"
        assert result.subpath == "foundation"
        assert result.is_zip

    def test_zip_file_uri(self) -> None:
        """Parses zip+file:// URIs."""
        result = parse_uri("zip+file:///local/archive.zip#subdirectory=my-bundle")
        assert result.scheme == "zip+file"
        assert result.path == "/local/archive.zip"
        assert result.subpath == "my-bundle"
        assert result.is_zip

    def test_file_uri(self) -> None:
        """Parses file:// URIs."""
        result = parse_uri("file:///home/user/bundle")
        assert result.scheme == "file"
        assert result.path == "/home/user/bundle"

    def test_https_uri(self) -> None:
        """Parses https:// URIs."""
        result = parse_uri("https://example.com/bundle.yaml")
        assert result.scheme == "https"
        assert result.host == "example.com"
        assert result.path == "/bundle.yaml"

    def test_local_path(self) -> None:
        """Parses local paths as file URIs."""
        result = parse_uri("/home/user/bundle")
        assert result.scheme == "file"
        assert result.path == "/home/user/bundle"

    def test_relative_path(self) -> None:
        """Parses relative paths."""
        result = parse_uri("./bundles/my-bundle")
        assert result.scheme == "file"
        assert result.path == "./bundles/my-bundle"

    def test_windows_drive_path_backslash(self) -> None:
        """Native Windows backslash drive-letter paths resolve as file URIs.

        Regression test for GAP-015: a bare Windows path like
        C:\\Users\\x\\.amplifier\\cache\\... previously fell through every
        branch, landed with scheme="" and no "/" in its path, and
        `is_file` returned False -- causing "No handler for URI: ..." and
        module activation failures in strict mode.
        """
        result = parse_uri(r"C:\Users\brkrabac\.amplifier\cache\bundle\modules\tool-x")
        assert result.scheme == "file"
        assert (
            result.path == r"C:\Users\brkrabac\.amplifier\cache\bundle\modules\tool-x"
        )
        assert result.is_file
        assert not result.is_package

    def test_windows_drive_path_forward_slash(self) -> None:
        """Windows drive-letter paths using forward slashes also resolve as file URIs.

        Windows accepts both separators. Before the fix, this form was
        actively MIS-parsed (not just unrecognized): because it contains a
        "/", it fell into the package/subpath branch and was split into a
        bogus package name "C:" and subpath "Users/...".
        """
        result = parse_uri("C:/Users/brkrabac/.amplifier/cache/bundle")
        assert result.scheme == "file"
        assert result.path == "C:/Users/brkrabac/.amplifier/cache/bundle"
        assert result.is_file
        assert result.subpath == ""

    def test_windows_unc_path(self) -> None:
        """Backslash UNC paths (\\\\server\\share\\...) resolve as file URIs."""
        result = parse_uri(r"\\server\share\bundle")
        assert result.scheme == "file"
        assert result.path == r"\\server\share\bundle"
        assert result.is_file

    def test_single_letter_scheme_is_not_confused_with_drive_letter(self) -> None:
        """A real multi-character scheme is unaffected by the drive-letter check.

        Guards against a careless widening: every URI scheme this parser
        recognizes is more than one character, so "X:" prefixes never
        collide with "https://", "git+https://", etc.
        """
        result = parse_uri("https://example.com/bundle.yaml")
        assert result.scheme == "https"
        assert not result.is_file

        result = parse_uri("git+https://github.com/org/repo@main")
        assert result.scheme == "git+https"
        assert not result.is_file

    def test_posix_and_relative_paths_unaffected_by_windows_path_check(self) -> None:
        """Existing POSIX/relative/package parsing is unchanged by the widening."""
        assert parse_uri("/home/user/bundle").scheme == "file"
        assert parse_uri("./relative/path").scheme == "file"
        assert parse_uri("../relative/path").scheme == "file"
        assert parse_uri("foundation/providers/anthropic").scheme == ""
        assert parse_uri("package-name").is_package


class TestDescribeCrossPlatformPathMismatch:
    """Tests for describe_cross_platform_path_mismatch (GAP-007).

    Regression test for GAP-007: a Linux-authored settings.yaml with a
    `file:///home/x/...` bundle source, run on Windows, previously produced
    "File not found: C:\\home\\x\\..." -- pathlib silently prepending the
    current drive letter to a rooted-but-driveless path instead of failing
    with a message that names the real problem (a config written for
    another OS's filesystem). The mirror case exists on POSIX for a
    Windows-shaped absolute path.

    `os.name` is monkeypatched per-test so both branches are exercised
    deterministically regardless of the OS actually running the suite.
    """

    def test_posix_path_on_windows_is_flagged(self, monkeypatch) -> None:
        """A POSIX-absolute path is flagged as impossible when os.name == 'nt'."""
        monkeypatch.setattr("amplifier_foundation.paths.resolution.os.name", "nt")
        message = describe_cross_platform_path_mismatch(
            "/home/bkrabach/dev/computer-use-improvements/amplifier-bundle-computer-use"
        )
        assert message is not None
        assert "POSIX-style absolute path" in message
        assert "Windows" in message
        # Echo the original (unmangled) path, not a mangled local translation.
        assert "/home/bkrabach/dev/computer-use-improvements" in message

    def test_windows_path_on_posix_is_flagged(self, monkeypatch) -> None:
        """A Windows drive-letter/UNC path is flagged as impossible when os.name != 'nt'."""
        monkeypatch.setattr("amplifier_foundation.paths.resolution.os.name", "posix")
        message = describe_cross_platform_path_mismatch(r"C:\Users\brkrabac\dev\bundle")
        assert message is not None
        assert "Windows-style absolute path" in message
        assert r"C:\Users\brkrabac\dev\bundle" in message

        message_unc = describe_cross_platform_path_mismatch(r"\\server\share\bundle")
        assert message_unc is not None
        assert "Windows-style absolute path" in message_unc

    def test_native_absolute_paths_are_not_flagged(self, monkeypatch) -> None:
        """A path native to the current OS is never a mismatch."""
        monkeypatch.setattr("amplifier_foundation.paths.resolution.os.name", "nt")
        assert (
            describe_cross_platform_path_mismatch(r"C:\Users\brkrabac\dev\bundle")
            is None
        )
        assert describe_cross_platform_path_mismatch(r"\\server\share\bundle") is None

        monkeypatch.setattr("amplifier_foundation.paths.resolution.os.name", "posix")
        assert describe_cross_platform_path_mismatch("/home/user/bundle") is None

    def test_relative_paths_are_never_flagged(self, monkeypatch) -> None:
        """Relative paths aren't absolute on any OS, so never a cross-OS mismatch."""
        monkeypatch.setattr("amplifier_foundation.paths.resolution.os.name", "nt")
        assert describe_cross_platform_path_mismatch("./relative/path") is None
        assert describe_cross_platform_path_mismatch("../relative/path") is None
        assert describe_cross_platform_path_mismatch("package-name") is None

        monkeypatch.setattr("amplifier_foundation.paths.resolution.os.name", "posix")
        assert describe_cross_platform_path_mismatch("./relative/path") is None

    def test_forward_slash_unc_is_left_unhandled(self, monkeypatch) -> None:
        """Forward-slash UNC (//server/share) is ambiguous with a protocol-
        relative URL and is deliberately left unhandled here, consistent
        with `_is_windows_absolute_path`'s existing exclusion of that form.
        """
        monkeypatch.setattr("amplifier_foundation.paths.resolution.os.name", "nt")
        assert describe_cross_platform_path_mismatch("//server/share/bundle") is None


class TestNormalizePath:
    """Tests for normalize_path function."""

    def test_absolute_path(self) -> None:
        """Absolute paths remain absolute."""
        result = normalize_path("/home/user/file.txt")
        assert result == Path("/home/user/file.txt")

    def test_relative_path_with_base(self) -> None:
        """Relative paths are resolved against base."""
        result = normalize_path("file.txt", relative_to=Path("/home/user"))
        assert result == Path("/home/user/file.txt")

    def test_relative_path_without_base(self) -> None:
        """Relative paths without base use cwd."""
        result = normalize_path("file.txt")
        assert result.is_absolute()

    def test_path_object_input(self) -> None:
        """Accepts Path objects."""
        result = normalize_path(Path("/home/user/file.txt"))
        assert result == Path("/home/user/file.txt")

    def test_tilde_path_expands_home(self) -> None:
        """Tilde paths expand to home directory."""
        result = normalize_path("~/some/file.txt")
        assert "~" not in str(result)
        assert result.is_absolute()
        assert str(result).endswith("/some/file.txt")

    def test_tilde_path_with_base_expands_home(self) -> None:
        """Tilde paths expand even when relative_to is provided."""
        result = normalize_path("~/some/file.txt", relative_to=Path("/ignored"))
        assert "~" not in str(result)
        assert result.is_absolute()
        assert str(result).endswith("/some/file.txt")


class TestConstructPaths:
    """Tests for path construction utilities."""

    def test_construct_agent_path(self) -> None:
        """Constructs agent path."""
        base = Path("/bundle")
        result = construct_agent_path(base, "code-reviewer")
        assert result == Path("/bundle/agents/code-reviewer.md")

    def test_construct_context_path(self) -> None:
        """Constructs context path relative to bundle root (explicit paths)."""
        base = Path("/bundle")
        # Paths are relative to bundle root - explicit, no implicit prefix
        result = construct_context_path(base, "context/philosophy.md")
        assert result == Path("/bundle/context/philosophy.md")
        # Works with any extension and directory
        result = construct_context_path(base, "context/config.yaml")
        assert result == Path("/bundle/context/config.yaml")
        # Works with nested paths
        result = construct_context_path(base, "context/examples/snippet.py")
        assert result == Path("/bundle/context/examples/snippet.py")
        # Works with non-context directories too
        result = construct_context_path(base, "providers/anthropic.yaml")
        assert result == Path("/bundle/providers/anthropic.yaml")
        result = construct_context_path(base, "agents/explorer.md")
        assert result == Path("/bundle/agents/explorer.md")

    def test_paths_are_standardized(self) -> None:
        """Paths use standard locations."""
        base = Path("/test")
        agent = construct_agent_path(base, "agent")
        # Context path is now explicit - must include context/ prefix
        context = construct_context_path(base, "context/ctx")
        assert "agents" in str(agent)
        assert "context" in str(context)
