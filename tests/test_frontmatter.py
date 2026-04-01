"""Tests for YAML frontmatter parsing and mention extraction."""

from pathlib import Path

from dot_docs.frontmatter import parse_frontmatter


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
