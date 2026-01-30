"""Tests for path utilities."""

from pathlib import Path

from amplifier_foundation.paths.construction import construct_agent_path
from amplifier_foundation.paths.construction import construct_context_path
from amplifier_foundation.paths.resolution import normalize_path
from amplifier_foundation.paths.resolution import parse_uri


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
