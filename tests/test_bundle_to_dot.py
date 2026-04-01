"""Tests for bundle_to_dot.py — behavior/bundle to DOT generation."""

from pathlib import Path

import pytest

from dot_docs.bundle_to_dot import bundle_to_dot

REPO_ROOT = Path(__file__).parent.parent


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_yaml(tmp_path: Path, content: str, name: str = "test.yaml") -> Path:
    f = tmp_path / name
    f.write_text(content)
    return f


# ── Test: minimal behavior ────────────────────────────────────────────────────


class TestMinimalBehavior:
    def test_minimal_behavior_returns_valid_dot(self, tmp_path: Path) -> None:
        """Minimal YAML with one tool → valid DOT string."""
        f = _make_yaml(
            tmp_path,
            """
bundle:
  name: test-behavior
  version: 1.0.0
  description: A test behavior
tools:
  - module: tool-test
    source: git+https://github.com/example/test@main
""",
        )
        dot = bundle_to_dot(f)
        assert "digraph" in dot
        assert "test_behavior" in dot  # sanitized graph ID
        assert "tool-test" in dot  # tool node label
        assert dot.strip().endswith("}")  # well-formed DOT

    def test_file_not_found_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            bundle_to_dot(tmp_path / "nonexistent.yaml")

    def test_returns_string(self, tmp_path: Path) -> None:
        f = _make_yaml(
            tmp_path,
            "bundle:\n  name: simple\n  version: 1.0.0\n",
        )
        result = bundle_to_dot(f)
        assert isinstance(result, str)
        assert len(result) > 0


# ── Test: source_hash ─────────────────────────────────────────────────────────


class TestSourceHash:
    def test_contains_source_hash(self, tmp_path: Path) -> None:
        """DOT output embeds a source_hash graph attribute."""
        f = _make_yaml(
            tmp_path,
            """
bundle:
  name: hash-test
tools:
  - module: tool-x
    source: git+https://github.com/example/tool-x@main
""",
        )
        dot = bundle_to_dot(f)
        assert 'source_hash="' in dot

    def test_source_hash_is_hex(self, tmp_path: Path) -> None:
        """The source_hash value looks like a SHA-256 hex digest."""
        import re

        f = _make_yaml(
            tmp_path,
            "bundle:\n  name: hash-check\n  version: 1.0.0\n",
        )
        dot = bundle_to_dot(f)
        # Extract hex string after source_hash="
        match = re.search(r'source_hash="([0-9a-f]+)"', dot)
        assert match is not None, f"source_hash not found in DOT:\n{dot[:500]}"
        assert len(match.group(1)) == 64  # SHA-256 = 64 hex chars

    def test_source_hash_changes_with_content(self, tmp_path: Path) -> None:
        """Different YAML content → different source_hash."""
        import re

        f1 = _make_yaml(
            tmp_path,
            "bundle:\n  name: behavior-a\n  version: 1.0.0\n",
            "a.yaml",
        )
        f2 = _make_yaml(
            tmp_path,
            "bundle:\n  name: behavior-b\n  version: 1.0.0\n",
            "b.yaml",
        )
        dot1 = bundle_to_dot(f1)
        dot2 = bundle_to_dot(f2)

        hash1 = re.search(r'source_hash="([0-9a-f]+)"', dot1).group(1)  # type: ignore[union-attr]
        hash2 = re.search(r'source_hash="([0-9a-f]+)"', dot2).group(1)  # type: ignore[union-attr]
        assert hash1 != hash2


# ── Test: context file nodes ──────────────────────────────────────────────────


class TestContextInclude:
    def test_behavior_with_context_include(self, tmp_path: Path) -> None:
        """Context include refs produce note-shaped nodes in the DOT."""
        f = _make_yaml(
            tmp_path,
            """
bundle:
  name: ctx-test
context:
  include:
    - foundation:context/myfile.md
""",
        )
        dot = bundle_to_dot(f, repo_root=tmp_path)
        assert "shape=note" in dot

    def test_context_node_label_contains_filename(self, tmp_path: Path) -> None:
        """Context node label shows the file name."""
        f = _make_yaml(
            tmp_path,
            """
bundle:
  name: ctx-label-test
context:
  include:
    - foundation:context/delegation-instructions.md
""",
        )
        dot = bundle_to_dot(f, repo_root=tmp_path)
        # The short path (filename) should appear in the label
        assert "delegation-instructions.md" in dot

    def test_multiple_context_files(self, tmp_path: Path) -> None:
        """Multiple context includes → multiple note-shaped nodes."""
        f = _make_yaml(
            tmp_path,
            """
bundle:
  name: multi-ctx
context:
  include:
    - foundation:context/file-a.md
    - foundation:context/file-b.md
""",
        )
        dot = bundle_to_dot(f, repo_root=tmp_path)
        # Both filenames should appear
        assert "file-a.md" in dot
        assert "file-b.md" in dot


# ── Test: agent reference nodes ───────────────────────────────────────────────


class TestAgentsInclude:
    def test_behavior_with_agents_include(self, tmp_path: Path) -> None:
        """Agent include refs produce agent nodes in the DOT."""
        f = _make_yaml(
            tmp_path,
            """
bundle:
  name: agent-test
agents:
  include:
    - foundation:my-agent
""",
        )
        dot = bundle_to_dot(f, repo_root=tmp_path)
        assert "foundation:my-agent" in dot

    def test_agent_node_color(self, tmp_path: Path) -> None:
        """Agent nodes use token-tier color (overrides base green)."""
        f = _make_yaml(
            tmp_path,
            """
bundle:
  name: agent-color-test
agents:
  include:
    - foundation:small-agent
""",
        )
        dot = bundle_to_dot(f, repo_root=tmp_path)
        # With no agent file, tokens = 0 → green tier
        assert "#c8e6c9" in dot  # COLOR_GREEN

    def test_multiple_agent_includes(self, tmp_path: Path) -> None:
        """Multiple agent includes → multiple agent nodes."""
        f = _make_yaml(
            tmp_path,
            """
bundle:
  name: multi-agent
agents:
  include:
    - foundation:agent-a
    - foundation:agent-b
""",
        )
        dot = bundle_to_dot(f, repo_root=tmp_path)
        assert "foundation:agent-a" in dot
        assert "foundation:agent-b" in dot


# ── Test: hook nodes ──────────────────────────────────────────────────────────


class TestHooks:
    def test_behavior_with_hooks(self, tmp_path: Path) -> None:
        """Hook entries produce hook nodes in the DOT."""
        f = _make_yaml(
            tmp_path,
            """
bundle:
  name: hook-test
hooks:
  - module: hooks-logging
    source: git+https://github.com/example/hooks-logging@main
""",
        )
        dot = bundle_to_dot(f, repo_root=tmp_path)
        assert "hooks-logging" in dot

    def test_hook_color(self, tmp_path: Path) -> None:
        """Hook nodes use the hook base color #ffe0b2."""
        f = _make_yaml(
            tmp_path,
            """
bundle:
  name: hook-color
hooks:
  - module: hooks-status
    source: git+https://github.com/example/hooks-status@main
""",
        )
        dot = bundle_to_dot(f, repo_root=tmp_path)
        assert "#ffe0b2" in dot  # hook color

    def test_multiple_hooks(self, tmp_path: Path) -> None:
        f = _make_yaml(
            tmp_path,
            """
bundle:
  name: multi-hook
hooks:
  - module: hooks-a
    source: git+https://github.com/example/hooks-a@main
  - module: hooks-b
    source: git+https://github.com/example/hooks-b@main
""",
        )
        dot = bundle_to_dot(f, repo_root=tmp_path)
        assert "hooks-a" in dot
        assert "hooks-b" in dot


# ── Test: real behavior files ─────────────────────────────────────────────────


class TestRealBehaviorAgentsYaml:
    """Tests against the real behaviors/agents.yaml file."""

    BEHAVIOR = REPO_ROOT / "behaviors" / "agents.yaml"

    def test_real_behavior_agents_yaml(self) -> None:
        """behaviors/agents.yaml → valid DOT with tools and context nodes."""
        dot = bundle_to_dot(self.BEHAVIOR, repo_root=REPO_ROOT)
        # Basic structure
        assert "digraph" in dot
        assert "source_hash=" in dot
        assert dot.strip().endswith("}")
        # agents.yaml has tools: tool-delegate and tool-skills
        assert "tool-delegate" in dot
        assert "tool-skills" in dot
        # agents.yaml has context.include with 2 files → note-shaped nodes
        assert "shape=note" in dot
        assert "delegation-instructions.md" in dot

    def test_agents_yaml_has_legend(self) -> None:
        """Legend cluster is present in the DOT output."""
        dot = bundle_to_dot(self.BEHAVIOR, repo_root=REPO_ROOT)
        assert "cluster_legend" in dot
        assert "Legend" in dot

    def test_agents_yaml_has_summary(self) -> None:
        """Summary node is present showing total token estimate."""
        dot = bundle_to_dot(self.BEHAVIOR, repo_root=REPO_ROOT)
        assert "Total" in dot
        assert "tok" in dot


class TestRealBehaviorRedactionYaml:
    """Tests against the real behaviors/redaction.yaml file."""

    BEHAVIOR = REPO_ROOT / "behaviors" / "redaction.yaml"

    def test_real_behavior_redaction_yaml(self) -> None:
        """behaviors/redaction.yaml → valid DOT with hook nodes."""
        dot = bundle_to_dot(self.BEHAVIOR, repo_root=REPO_ROOT)
        # Basic structure
        assert "digraph" in dot
        assert "source_hash=" in dot
        assert dot.strip().endswith("}")
        # redaction.yaml has hooks: hooks-redaction
        assert "hooks-redaction" in dot

    def test_redaction_yaml_hook_color(self) -> None:
        """Hook nodes in redaction behavior use orange color #ffe0b2."""
        dot = bundle_to_dot(self.BEHAVIOR, repo_root=REPO_ROOT)
        assert "#ffe0b2" in dot

    def test_redaction_yaml_graph_id(self) -> None:
        """Digraph ID is derived from bundle name: behavior-redaction."""
        dot = bundle_to_dot(self.BEHAVIOR, repo_root=REPO_ROOT)
        assert "digraph behavior_redaction" in dot


# ── Test: bundle_overview_dot placeholder ────────────────────────────────────


class TestBundleOverviewDotPlaceholder:
    def test_bundle_overview_dot_raises_not_implemented(self, tmp_path: Path) -> None:
        from dot_docs.bundle_to_dot import bundle_overview_dot

        with pytest.raises(NotImplementedError):
            bundle_overview_dot(tmp_path)


# ── Test: tool node color is fixed blue ───────────────────────────────────────────


class TestToolNodeColor:
    def test_tool_node_uses_fixed_blue_color(self, tmp_path: Path) -> None:
        """Tool nodes always use _COLOR_TOOL (#bbdefb), never a tier color.

        The legend also uses #bbdefb for the tool legend entry, so we must see
        at least 2 occurrences: one for the actual tool node, one for the legend.
        """
        f = _make_yaml(
            tmp_path,
            """
bundle:
  name: tool-blue-test
tools:
  - module: tool-small
    source: git+https://github.com/example/tool-small@main
""",
        )
        dot = bundle_to_dot(f, repo_root=tmp_path)
        # The legend entry alone contributes 1 occurrence of #bbdefb.
        # The actual tool node must also contribute, so we need >= 2.
        assert dot.count("#bbdefb") >= 2, (
            "Expected tool node to use #bbdefb (fixed blue), but it only appears "
            "in the legend entry — tool nodes are still being tier-colored."
        )


# ── Test: _estimate_agent_tokens uses meta.description only ───────────────────────


class TestEstimateAgentTokens:
    def test_uses_meta_description_not_full_content(self, tmp_path: Path) -> None:
        """_estimate_agent_tokens returns tokens for meta.description only."""
        from dot_docs.bundle_to_dot import _estimate_agent_tokens

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        short_desc = "Short description only"
        long_body = "x" * 400  # 100 extra tokens if read in full

        (agents_dir / "my-agent.md").write_text(
            f"---\nmeta:\n  name: my-agent\n  description: {short_desc}\n---\n{long_body}"
        )

        tokens = _estimate_agent_tokens("foundation:my-agent", tmp_path)

        expected = len(short_desc) // 4
        assert tokens == expected, (
            f"Expected {expected} tokens (description only), got {tokens}"
        )

    def test_missing_description_returns_zero(self, tmp_path: Path) -> None:
        """_estimate_agent_tokens returns 0 when meta.description is absent."""
        from dot_docs.bundle_to_dot import _estimate_agent_tokens

        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        (agents_dir / "no-desc-agent.md").write_text(
            "---\nmeta:\n  name: no-desc-agent\n---\nSome body content here.\n"
        )

        tokens = _estimate_agent_tokens("foundation:no-desc-agent", tmp_path)
        assert tokens == 0

    def test_missing_agent_file_returns_zero(self, tmp_path: Path) -> None:
        """_estimate_agent_tokens returns 0 when the agent file does not exist."""
        from dot_docs.bundle_to_dot import _estimate_agent_tokens

        tokens = _estimate_agent_tokens("foundation:ghost-agent", tmp_path)
        assert tokens == 0


class TestBundleToDotRootBundle:
    """Tests for bundle_to_dot() with root bundle (has includes)."""

    def test_root_bundle_generates_dot(self) -> None:
        repo = Path(__file__).parent.parent
        dot = bundle_to_dot(repo / "bundle.md", repo_root=repo)
        assert dot.startswith("digraph ")
        assert "foundation" in dot
        assert 'source_hash="' in dot

    def test_root_bundle_has_local_behavior_clusters(self) -> None:
        repo = Path(__file__).parent.parent
        dot = bundle_to_dot(repo / "bundle.md", repo_root=repo)
        assert "cluster_" in dot

    def test_root_bundle_has_external_nodes(self) -> None:
        repo = Path(__file__).parent.parent
        dot = bundle_to_dot(repo / "bundle.md", repo_root=repo)
        assert "ext_" in dot
        assert "(external)" in dot

    def test_root_bundle_has_direct_tools(self) -> None:
        repo = Path(__file__).parent.parent
        dot = bundle_to_dot(repo / "bundle.md", repo_root=repo)
        assert "tool-filesystem" in dot or "tool_filesystem" in dot

    def test_root_bundle_has_session_config(self) -> None:
        repo = Path(__file__).parent.parent
        dot = bundle_to_dot(repo / "bundle.md", repo_root=repo)
        assert "loop-streaming" in dot or "loop_streaming" in dot

    def test_root_bundle_has_summary_node(self) -> None:
        repo = Path(__file__).parent.parent
        dot = bundle_to_dot(repo / "bundle.md", repo_root=repo)
        assert "summary" in dot

    def test_source_hash_deterministic(self) -> None:
        import re

        repo = Path(__file__).parent.parent
        dot1 = bundle_to_dot(repo / "bundle.md", repo_root=repo)
        dot2 = bundle_to_dot(repo / "bundle.md", repo_root=repo)
        hashes1 = re.findall(r'source_hash="([a-f0-9]+)"', dot1)
        hashes2 = re.findall(r'source_hash="([a-f0-9]+)"', dot2)
        assert len(hashes1) == 1
        assert hashes1 == hashes2
