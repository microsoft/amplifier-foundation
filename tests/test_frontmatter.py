"""Tests for YAML frontmatter parsing and mention extraction."""

from pathlib import Path

from dot_docs.frontmatter import (
    extract_delegation_targets,
    extract_mentions,
    parse_frontmatter,
)


class TestParseFrontmatter:
    """Tests for parse_frontmatter()."""

    def test_markdown_with_frontmatter(self, tmp_path: Path) -> None:
        """Markdown file with --- delimited frontmatter."""
        f = tmp_path / "test.md"
        f.write_text("---\nmeta:\n  name: test\n---\n\n# Body\n")
        data, body = parse_frontmatter(f)
        assert data["meta"]["name"] == "test"
        assert "# Body" in body

    def test_yaml_file(self, tmp_path: Path) -> None:
        """YAML file returns entire content as data, empty body."""
        f = tmp_path / "test.yaml"
        f.write_text("bundle:\n  name: test\n  version: 1.0.0\n")
        data, body = parse_frontmatter(f)
        assert data["bundle"]["name"] == "test"
        assert body == ""

    def test_markdown_no_frontmatter(self, tmp_path: Path) -> None:
        """Markdown without frontmatter returns empty dict and full body."""
        f = tmp_path / "test.md"
        f.write_text("# Just a heading\n\nSome text.\n")
        data, body = parse_frontmatter(f)
        assert data == {}
        assert "Just a heading" in body

    def test_real_agent_file(self) -> None:
        """Parse a real agent file from the repo."""
        agent = Path(__file__).parent.parent / "agents" / "explorer.md"
        data, body = parse_frontmatter(agent)
        # explorer.md has meta.name in frontmatter
        assert data["meta"]["name"] == "explorer"
        assert "model_role" in data
        assert len(body) > 0  # has a markdown body

    def test_real_behavior_file(self) -> None:
        """Parse a real behavior YAML from the repo."""
        behavior = Path(__file__).parent.parent / "behaviors" / "agents.yaml"
        data, body = parse_frontmatter(behavior)
        assert data["bundle"]["name"] == "behavior-agents"
        assert body == ""

    def test_real_root_bundle(self) -> None:
        """Parse the real root bundle.md from the repo."""
        bundle = Path(__file__).parent.parent / "bundle.md"
        data, body = parse_frontmatter(bundle)
        assert data["bundle"]["name"] == "foundation"
        assert "@foundation:" in body


class TestExtractMentions:
    def test_no_mentions(self) -> None:
        assert extract_mentions("Hello world") == []

    def test_namespaced_mention(self) -> None:
        result = extract_mentions("See @foundation:context/file.md for details")
        assert result == ["@foundation:context/file.md"]

    def test_multiple_mentions(self) -> None:
        text = "Use @foundation:context/a.md and @foundation:context/b.md"
        result = extract_mentions(text)
        assert result == ["@foundation:context/a.md", "@foundation:context/b.md"]

    def test_deduplication(self) -> None:
        text = "See @foundation:context/a.md and also @foundation:context/a.md"
        result = extract_mentions(text)
        assert result == ["@foundation:context/a.md"]

    def test_code_block_excluded(self) -> None:
        text = "Use @foundation:real.md\n\n```\n@foundation:fake.md\n```\n"
        result = extract_mentions(text)
        assert "@foundation:real.md" in result
        assert "@foundation:fake.md" not in result

    def test_inline_code_excluded(self) -> None:
        text = "Use `@foundation:fake.md` or @foundation:real.md"
        result = extract_mentions(text)
        assert result == ["@foundation:real.md"]

    def test_real_agent_body(self) -> None:
        agent = Path(__file__).parent.parent / "agents" / "zen-architect.md"
        _, body = parse_frontmatter(agent)
        mentions = extract_mentions(body)
        # zen-architect should reference specific foundation context files
        assert "@foundation:context/IMPLEMENTATION_PHILOSOPHY.md" in mentions
        assert "@foundation:context/shared/common-agent-base.md" in mentions


class TestExtractDelegationTargets:
    def test_no_targets(self) -> None:
        assert extract_delegation_targets("Hello world") == []

    def test_foundation_agent(self) -> None:
        text = "delegate to foundation:modular-builder for implementation"
        result = extract_delegation_targets(text)
        assert "foundation:modular-builder" in result

    def test_external_namespace(self) -> None:
        text = "use lsp:code-navigator or python-dev:code-intel"
        result = extract_delegation_targets(text)
        assert "lsp:code-navigator" in result
        assert "python-dev:code-intel" in result

    def test_file_paths_excluded(self) -> None:
        text = "see foundation:context/file.md"
        result = extract_delegation_targets(text)
        assert len(result) == 0

    def test_code_block_excluded(self) -> None:
        text = "use foundation:explorer\n```\nfoundation:fake-agent\n```\n"
        result = extract_delegation_targets(text)
        assert "foundation:explorer" in result
        assert "foundation:fake-agent" not in result

    def test_deduplication(self) -> None:
        text = "foundation:explorer and foundation:explorer"
        result = extract_delegation_targets(text)
        assert result.count("foundation:explorer") == 1

    def test_real_agent_body(self) -> None:
        # zen-architect.md uses inline code for lsp:code-navigator and
        # python-dev:code-intel — both should be excluded after stripping.
        # Bare agent names (modular-builder, bug-hunter, etc.) lack a
        # namespace prefix so they also produce no delegation targets.
        agent = Path(__file__).parent.parent / "agents" / "zen-architect.md"
        _, body = parse_frontmatter(agent)
        targets = extract_delegation_targets(body)
        assert isinstance(targets, list)
        # Inline-code targets must be excluded
        assert "lsp:code-navigator" not in targets
        assert "python-dev:code-intel" not in targets
