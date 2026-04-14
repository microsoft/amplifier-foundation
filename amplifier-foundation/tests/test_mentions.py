"""Tests for mention parsing and resolution."""

from pathlib import Path

import pytest

from amplifier_foundation.mentions.parser import parse_mentions
from amplifier_foundation.mentions.resolver import BaseMentionResolver


class TestParseMentions:
    """Tests for parse_mentions function."""

    def test_no_mentions(self) -> None:
        """Text without mentions returns empty list."""
        assert parse_mentions("Hello world") == []

    def test_simple_mention(self) -> None:
        """Simple @mention is extracted."""
        mentions = parse_mentions("Check @file.md for details")
        assert mentions == ["@file.md"]

    def test_multiple_mentions(self) -> None:
        """Multiple mentions are extracted."""
        mentions = parse_mentions("See @first.md and @second.md")
        assert set(mentions) == {"@first.md", "@second.md"}

    def test_namespaced_mention(self) -> None:
        """Namespaced @bundle:resource mention is extracted."""
        mentions = parse_mentions("Follow @foundation:philosophy")
        assert mentions == ["@foundation:philosophy"]

    def test_mention_in_code_block_excluded(self) -> None:
        """Mentions inside code blocks are excluded."""
        text = """
Check @outside.md for info.

```python
# @inside.md is code
```

More @after.md content.
"""
        mentions = parse_mentions(text)
        assert "@outside.md" in mentions
        assert "@after.md" in mentions
        assert "@inside.md" not in mentions

    def test_mention_in_inline_code_excluded(self) -> None:
        """Mentions inside inline code are excluded."""
        mentions = parse_mentions("Use `@code.md` or @real.md")
        assert mentions == ["@real.md"]

    def test_mention_with_path(self) -> None:
        """Mention with path is extracted."""
        mentions = parse_mentions("Check @docs/guide.md")
        assert mentions == ["@docs/guide.md"]

    def test_deduplication(self) -> None:
        """Duplicate mentions are deduplicated."""
        mentions = parse_mentions("See @file.md and also @file.md")
        assert mentions == ["@file.md"]

    def test_tilde_home_path(self) -> None:
        """Tilde home path @~/.path is extracted."""
        mentions = parse_mentions("Check @~/.amplifier/AGENTS.md")
        assert mentions == ["@~/.amplifier/AGENTS.md"]

    def test_dot_directory_path(self) -> None:
        """Dot directory @.dir/file is extracted."""
        mentions = parse_mentions("See @.amplifier/AGENTS.md")
        assert mentions == ["@.amplifier/AGENTS.md"]

    def test_explicit_relative_path(self) -> None:
        """Explicit relative @./path is extracted."""
        mentions = parse_mentions("Check @./subdir/file.md and @./.amplifier/AGENTS.md")
        assert "@./subdir/file.md" in mentions
        assert "@./.amplifier/AGENTS.md" in mentions


class TestBaseMentionResolver:
    """Tests for BaseMentionResolver class."""

    def test_resolve_simple_file(self, tmp_path: Path) -> None:
        """Simple @file.md resolves in CWD."""
        import os

        test_file = tmp_path / "AGENTS.md"
        test_file.write_text("# Test")

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            resolver = BaseMentionResolver()
            result = resolver.resolve("@AGENTS.md")
            assert result == test_file
        finally:
            os.chdir(old_cwd)

    def test_resolve_dot_directory(self, tmp_path: Path) -> None:
        """@.amplifier/AGENTS.md resolves relative to CWD."""
        import os

        subdir = tmp_path / ".amplifier"
        subdir.mkdir()
        test_file = subdir / "AGENTS.md"
        test_file.write_text("# Test")

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            resolver = BaseMentionResolver()
            result = resolver.resolve("@.amplifier/AGENTS.md")
            assert result == test_file
        finally:
            os.chdir(old_cwd)

    def test_resolve_explicit_relative(self, tmp_path: Path) -> None:
        """@./path resolves relative to CWD."""
        import os

        test_file = tmp_path / "AGENTS.md"
        test_file.write_text("# Test")

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            resolver = BaseMentionResolver()
            result = resolver.resolve("@./AGENTS.md")
            assert result is not None
            assert result.resolve() == test_file.resolve()
        finally:
            os.chdir(old_cwd)

    def test_resolve_explicit_relative_subdir(self, tmp_path: Path) -> None:
        """@./.amplifier/AGENTS.md resolves relative to CWD."""
        import os

        subdir = tmp_path / ".amplifier"
        subdir.mkdir()
        test_file = subdir / "AGENTS.md"
        test_file.write_text("# Test")

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            resolver = BaseMentionResolver()
            result = resolver.resolve("@./.amplifier/AGENTS.md")
            assert result is not None
            assert result.resolve() == test_file.resolve()
        finally:
            os.chdir(old_cwd)

    def test_resolve_home_tilde_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """@~/.amplifier/AGENTS.md expands tilde to home directory."""
        # Create test file in a fake home directory
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        subdir = fake_home / ".amplifier"
        subdir.mkdir()
        test_file = subdir / "AGENTS.md"
        test_file.write_text("# Test")

        # Monkeypatch HOME environment variable
        monkeypatch.setenv("HOME", str(fake_home))

        resolver = BaseMentionResolver()
        result = resolver.resolve("@~/.amplifier/AGENTS.md")
        assert result == test_file

    def test_resolve_home_tilde_md_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """@~/.amplifier/AGENTS (no .md) falls back to .md extension."""
        # Create test file in a fake home directory
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        subdir = fake_home / ".amplifier"
        subdir.mkdir()
        test_file = subdir / "AGENTS.md"
        test_file.write_text("# Test")

        # Monkeypatch HOME environment variable
        monkeypatch.setenv("HOME", str(fake_home))

        resolver = BaseMentionResolver()
        result = resolver.resolve("@~/.amplifier/AGENTS")  # No .md extension
        assert result == test_file

    def test_resolve_md_extension_fallback(self, tmp_path: Path) -> None:
        """@file without extension falls back to file.md."""
        import os

        test_file = tmp_path / "AGENTS.md"
        test_file.write_text("# Test")

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            resolver = BaseMentionResolver()
            result = resolver.resolve("@AGENTS")  # No .md extension
            assert result == test_file
        finally:
            os.chdir(old_cwd)

    def test_resolve_not_found_returns_none(self, tmp_path: Path) -> None:
        """Non-existent file returns None."""
        import os

        old_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            resolver = BaseMentionResolver()
            result = resolver.resolve("@nonexistent.md")
            assert result is None
        finally:
            os.chdir(old_cwd)

    def test_resolve_bundle_pattern_unchanged(self) -> None:
        """@bundle:path pattern still routes to bundle lookup."""
        resolver = BaseMentionResolver(bundles={})
        # Without a registered bundle, should return None (not try CWD)
        result = resolver.resolve("@foundation:context/file.md")
        assert result is None  # Bundle not registered, but didn't try local path

    def test_resolve_uses_base_path_not_cwd(self, tmp_path: Path) -> None:
        """Local @path resolves relative to base_path, not CWD.

        This is the critical test for production behavior: when the CLI runs
        from a different directory than the user's project, @AGENTS.md should
        resolve relative to the project's base_path, not the CLI's CWD.
        """
        import os

        # Create a project directory with a file
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        test_file = project_dir / "AGENTS.md"
        test_file.write_text("# Project agents")

        # Create a different directory to be CWD (simulating CLI running elsewhere)
        other_dir = tmp_path / "other"
        other_dir.mkdir()

        old_cwd = os.getcwd()
        try:
            # Change CWD to the "other" directory (NOT where the file is)
            os.chdir(other_dir)

            # Create resolver with base_path pointing to the project
            resolver = BaseMentionResolver(base_path=project_dir)

            # @AGENTS.md should resolve relative to base_path (project_dir),
            # NOT relative to CWD (other_dir)
            result = resolver.resolve("@AGENTS.md")
            assert result == test_file, (
                f"Expected {test_file}, got {result}. "
                "Resolver should use base_path, not CWD."
            )
        finally:
            os.chdir(old_cwd)
