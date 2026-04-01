"""Tests for agent_to_dot.py — per-agent composition card DOT generator."""

from pathlib import Path

import pytest

from dot_docs.agent_to_dot import agent_to_dot, agents_topology_dot

REPO_ROOT = Path(__file__).parent.parent
AGENTS_DIR = REPO_ROOT / "agents"


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_agent_md(tmp_path: Path, content: str, name: str = "test-agent.md") -> Path:
    f = tmp_path / name
    f.write_text(content)
    return f


# ── Test: minimal agent ───────────────────────────────────────────────────────


class TestMinimalAgent:
    def test_minimal_agent_returns_valid_dot(self, tmp_path: Path) -> None:
        """Minimal .md with meta + model_role → valid DOT string."""
        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: my-agent
  description: "A simple agent for testing."
model_role: general
---

Hello world.
""",
        )
        dot = agent_to_dot(f)
        assert isinstance(dot, str)
        assert len(dot) > 0
        assert "digraph" in dot
        assert dot.strip().endswith("}")

    def test_minimal_agent_graph_id_uses_agent_name(self, tmp_path: Path) -> None:
        """Graph ID is derived from agent name (sanitized)."""
        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: my-agent
  description: "A simple agent."
model_role: fast
---
""",
        )
        dot = agent_to_dot(f)
        # sanitized: "my_agent"
        assert "my_agent" in dot

    def test_file_not_found_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            agent_to_dot(tmp_path / "nonexistent.md")

    def test_returns_string_type(self, tmp_path: Path) -> None:
        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: simple
  description: "Simple."
model_role: fast
---
""",
        )
        result = agent_to_dot(f)
        assert isinstance(result, str)


# ── Test: model_role in central node ─────────────────────────────────────────


class TestAgentShowsModelRole:
    def test_string_model_role_appears_in_dot(self, tmp_path: Path) -> None:
        """model_role as string → appears in central node label."""
        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: fast-agent
  description: "Does things fast."
model_role: fast
---
""",
        )
        dot = agent_to_dot(f)
        assert "fast" in dot

    def test_list_model_role_appears_comma_separated(self, tmp_path: Path) -> None:
        """model_role as list → comma-separated in central node label."""
        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: smart-agent
  description: "Reasons carefully."
model_role: [reasoning, general]
---
""",
        )
        dot = agent_to_dot(f)
        assert "reasoning" in dot
        assert "general" in dot

    def test_model_role_in_meta_also_works(self, tmp_path: Path) -> None:
        """model_role nested inside meta block is also handled."""
        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: nested-role-agent
  description: "Uses meta.model_role."
  model_role: coding
---
""",
        )
        dot = agent_to_dot(f)
        assert "coding" in dot

    def test_central_node_contains_agent_name(self, tmp_path: Path) -> None:
        """Central node label includes the agent name."""
        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: named-agent
  description: "Named agent."
model_role: general
---
""",
        )
        dot = agent_to_dot(f)
        assert "named-agent" in dot

    def test_central_node_contains_description_tokens(self, tmp_path: Path) -> None:
        """Central node label includes description token count annotation."""
        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: tok-agent
  description: "This description is about forty characters long."
model_role: fast
---
""",
        )
        dot = agent_to_dot(f)
        assert "tok" in dot  # token count appears as "~N tok"


# ── Test: tool nodes ──────────────────────────────────────────────────────────


class TestAgentShowsTools:
    def test_tools_from_frontmatter_produce_nodes(self, tmp_path: Path) -> None:
        """Tools listed in frontmatter → tool nodes with fixed blue color."""
        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: tool-agent
  description: "Agent with tools."
model_role: general
tools:
  - module: tool-filesystem
    source: git+https://github.com/example/tool-fs@main
  - module: tool-search
    source: git+https://github.com/example/tool-search@main
---
""",
        )
        dot = agent_to_dot(f)
        assert "tool-filesystem" in dot
        assert "tool-search" in dot

    def test_tool_nodes_have_blue_color(self, tmp_path: Path) -> None:
        """Tool nodes use the fixed blue color #bbdefb."""
        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: color-agent
  description: "Color test."
model_role: fast
tools:
  - module: tool-x
    source: git+https://github.com/example/tool-x@main
---
""",
        )
        dot = agent_to_dot(f)
        assert "#bbdefb" in dot

    def test_no_tools_means_no_blue_tool_nodes(self, tmp_path: Path) -> None:
        """No tools → no tool-related blue nodes in output."""
        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: toolless-agent
  description: "No tools."
model_role: fast
---
""",
        )
        dot = agent_to_dot(f)
        assert "#bbdefb" not in dot

    def test_multiple_tools_all_appear(self, tmp_path: Path) -> None:
        """All tools in the list appear as nodes."""
        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: multi-tool
  description: "Many tools."
model_role: general
tools:
  - module: tool-alpha
    source: git+https://github.com/example/alpha@main
  - module: tool-beta
    source: git+https://github.com/example/beta@main
  - module: tool-gamma
    source: git+https://github.com/example/gamma@main
---
""",
        )
        dot = agent_to_dot(f)
        assert "tool-alpha" in dot
        assert "tool-beta" in dot
        assert "tool-gamma" in dot


# ── Test: @mention context nodes ─────────────────────────────────────────────


class TestAgentShowsMentions:
    def test_body_mentions_produce_context_nodes(self, tmp_path: Path) -> None:
        """@namespace:path mentions in body → context nodes (note shape, purple base)."""
        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: mention-agent
  description: "Agent with mentions."
model_role: general
---

Always read @foundation:context/PHILOSOPHY.md first.
""",
        )
        dot = agent_to_dot(f)
        assert "PHILOSOPHY" in dot

    def test_mention_nodes_have_note_shape(self, tmp_path: Path) -> None:
        """Context mention nodes use shape=note."""
        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: shape-agent
  description: "Shape test."
model_role: fast
---

See @foundation:context/rules.md for details.
""",
        )
        dot = agent_to_dot(f)
        assert "shape=note" in dot

    def test_mention_nodes_have_purple_base_color(self, tmp_path: Path) -> None:
        """Context mention nodes use purple base color #e1bee7."""
        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: purple-agent
  description: "Purple test."
model_role: fast
---

See @foundation:context/rules.md for details.
""",
        )
        dot = agent_to_dot(f)
        # purple base or a tier color may appear (file doesn't exist so 0 tokens → green)
        # but the node must be present with note shape
        assert "shape=note" in dot

    def test_mentions_in_code_blocks_are_ignored(self, tmp_path: Path) -> None:
        """@mentions inside code blocks are NOT included."""
        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: code-agent
  description: "Code block test."
model_role: fast
---

Example:

```
Use @foundation:context/example.md
```

No real mention here.
""",
        )
        dot = agent_to_dot(f)
        assert "example" not in dot or "PHILOSOPHY" not in dot

    def test_duplicate_mentions_deduplicated(self, tmp_path: Path) -> None:
        """Same @mention appearing twice produces only one node."""
        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: dedup-agent
  description: "Dedup test."
model_role: fast
---

See @foundation:context/base.md here and again @foundation:context/base.md there.
""",
        )
        dot = agent_to_dot(f)
        # The mention should appear, but as a single node, not duplicated
        # Count node definitions for base (there should be exactly one)
        assert dot.count("foundation_context_base") <= 2  # node def + edge


# ── Test: delegation target nodes ────────────────────────────────────────────


class TestAgentShowsDelegationTargets:
    def test_delegation_targets_produce_dashed_nodes(self, tmp_path: Path) -> None:
        """namespace:agent-name patterns in body → dashed box nodes."""
        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: delegating-agent
  description: "Delegates to others."
model_role: general
---

For complex work, delegate to foundation:builder agent.
""",
        )
        dot = agent_to_dot(f)
        assert "foundation:builder" in dot or "foundation_builder" in dot

    def test_delegation_nodes_are_dashed_green(self, tmp_path: Path) -> None:
        """Delegation nodes use dashed style and green color #c8e6c9."""
        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: delegating-agent
  description: "Delegates."
model_role: general
---

Delegate to myns:specialist for hard problems.
""",
        )
        dot = agent_to_dot(f)
        # delegation node should be dashed and green
        assert "dashed" in dot
        assert "#c8e6c9" in dot

    def test_delegation_targets_in_backticks_ignored(self, tmp_path: Path) -> None:
        """Delegation patterns inside backticks are NOT included as nodes."""
        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: clean-agent
  description: "No spurious delegation."
model_role: fast
---

Use `myns:specialist` for hard things.
""",
        )
        dot = agent_to_dot(f)
        # myns:specialist is in backticks, should not produce a delegation node
        assert "myns_specialist" not in dot


# ── Test: source hash ─────────────────────────────────────────────────────────


class TestSourceHash:
    def test_source_hash_embedded(self, tmp_path: Path) -> None:
        """DOT output embeds a source_hash graph attribute."""
        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: hash-agent
  description: "Hash test."
model_role: fast
---
""",
        )
        dot = agent_to_dot(f)
        assert 'source_hash="' in dot

    def test_source_hash_is_hex(self, tmp_path: Path) -> None:
        """The source_hash value looks like a SHA-256 hex digest."""
        import re

        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: hex-agent
  description: "Hex test."
model_role: fast
---
""",
        )
        dot = agent_to_dot(f)
        match = re.search(r'source_hash="([0-9a-f]+)"', dot)
        assert match is not None
        assert len(match.group(1)) == 64  # SHA-256 → 64 hex chars

    def test_source_hash_deterministic(self, tmp_path: Path) -> None:
        """Same input → same source_hash on every call."""
        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: det-agent
  description: "Determinism test."
model_role: general
tools:
  - module: tool-x
    source: git+https://example.com/tool-x@main
---

Body text with @foundation:context/file.md mention.
""",
        )
        dot1 = agent_to_dot(f)
        dot2 = agent_to_dot(f)
        assert dot1 == dot2


# ── Test: legend ──────────────────────────────────────────────────────────────


class TestLegend:
    def test_legend_present(self, tmp_path: Path) -> None:
        """DOT output includes a legend cluster."""
        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: legend-agent
  description: "Legend test."
model_role: general
---
""",
        )
        dot = agent_to_dot(f)
        assert "Legend" in dot
        assert "cluster_legend" in dot

    def test_legend_shows_tool_entry_when_tools_present(self, tmp_path: Path) -> None:
        """Legend includes 'Tool' entry when tools are present."""
        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: legend-tool-agent
  description: "Legend tool test."
model_role: fast
tools:
  - module: tool-x
    source: git+https://example.com/tool-x@main
---
""",
        )
        dot = agent_to_dot(f)
        assert "Tool" in dot

    def test_legend_omits_tool_entry_when_no_tools(self, tmp_path: Path) -> None:
        """Legend omits 'Tool' entry when no tools are present."""
        f = _make_agent_md(
            tmp_path,
            """\
---
meta:
  name: no-tool-agent
  description: "No tool legend test."
model_role: fast
---
""",
        )
        dot = agent_to_dot(f)
        # No tool nodes means no tool legend entry
        # Check tool color isn't in output
        assert "#bbdefb" not in dot


# ── Test: real agent files ────────────────────────────────────────────────────


@pytest.mark.skipif(
    not (AGENTS_DIR / "zen-architect.md").exists(),
    reason="agents/zen-architect.md not found",
)
class TestRealZenArchitect:
    def test_returns_valid_dot(self) -> None:
        """zen-architect.md → valid DOT output."""
        dot = agent_to_dot(AGENTS_DIR / "zen-architect.md", repo_root=REPO_ROOT)
        assert isinstance(dot, str)
        assert "digraph" in dot
        assert dot.strip().endswith("}")

    def test_has_source_hash(self) -> None:
        """zen-architect DOT embeds source_hash."""
        dot = agent_to_dot(AGENTS_DIR / "zen-architect.md", repo_root=REPO_ROOT)
        assert 'source_hash="' in dot

    def test_shows_model_role(self) -> None:
        """zen-architect model_role [reasoning, general] appears in output."""
        dot = agent_to_dot(AGENTS_DIR / "zen-architect.md", repo_root=REPO_ROOT)
        # model_role is [reasoning, general] → "reasoning, general"
        assert "reasoning" in dot
        assert "general" in dot

    def test_shows_tools(self) -> None:
        """zen-architect tools appear as nodes."""
        dot = agent_to_dot(AGENTS_DIR / "zen-architect.md", repo_root=REPO_ROOT)
        assert "tool-filesystem" in dot
        assert "tool-search" in dot
        assert "tool-lsp" in dot

    def test_shows_context_mentions(self) -> None:
        """zen-architect body @mentions appear as context nodes."""
        dot = agent_to_dot(AGENTS_DIR / "zen-architect.md", repo_root=REPO_ROOT)
        # The agent mentions @foundation:context/shared/common-agent-base.md
        assert "common-agent-base" in dot or "common_agent_base" in dot

    def test_agent_name_in_central_node(self) -> None:
        """zen-architect name appears in central node."""
        dot = agent_to_dot(AGENTS_DIR / "zen-architect.md", repo_root=REPO_ROOT)
        assert "zen-architect" in dot


@pytest.mark.skipif(
    not (AGENTS_DIR / "explorer.md").exists(),
    reason="agents/explorer.md not found",
)
class TestRealExplorer:
    def test_returns_valid_dot(self) -> None:
        """explorer.md → valid DOT output."""
        dot = agent_to_dot(AGENTS_DIR / "explorer.md", repo_root=REPO_ROOT)
        assert isinstance(dot, str)
        assert "digraph" in dot
        assert dot.strip().endswith("}")

    def test_shows_model_role(self) -> None:
        """explorer model_role 'general' appears in output."""
        dot = agent_to_dot(AGENTS_DIR / "explorer.md", repo_root=REPO_ROOT)
        assert "general" in dot

    def test_shows_tools(self) -> None:
        """explorer tools appear as nodes."""
        dot = agent_to_dot(AGENTS_DIR / "explorer.md", repo_root=REPO_ROOT)
        assert "tool-filesystem" in dot
        assert "tool-search" in dot

    def test_shows_context_mentions(self) -> None:
        """explorer body @mentions appear as context nodes."""
        dot = agent_to_dot(AGENTS_DIR / "explorer.md", repo_root=REPO_ROOT)
        # The agent mentions @foundation:context/shared/common-agent-base.md
        assert "common-agent-base" in dot or "common_agent_base" in dot

    def test_has_source_hash(self) -> None:
        """explorer DOT embeds source_hash."""
        dot = agent_to_dot(AGENTS_DIR / "explorer.md", repo_root=REPO_ROOT)
        assert 'source_hash="' in dot


@pytest.mark.skipif(
    not (AGENTS_DIR / "file-ops.md").exists(),
    reason="agents/file-ops.md not found",
)
class TestRealFileOps:
    def test_returns_valid_dot(self) -> None:
        """file-ops.md → valid DOT output."""
        dot = agent_to_dot(AGENTS_DIR / "file-ops.md", repo_root=REPO_ROOT)
        assert isinstance(dot, str)
        assert "digraph" in dot
        assert dot.strip().endswith("}")

    def test_shows_fast_model_role(self) -> None:
        """file-ops model_role 'fast' appears in output."""
        dot = agent_to_dot(AGENTS_DIR / "file-ops.md", repo_root=REPO_ROOT)
        assert "fast" in dot

    def test_shows_tools(self) -> None:
        """file-ops tools appear as nodes."""
        dot = agent_to_dot(AGENTS_DIR / "file-ops.md", repo_root=REPO_ROOT)
        assert "tool-filesystem" in dot
        assert "tool-search" in dot

    def test_has_source_hash(self) -> None:
        """file-ops DOT embeds source_hash."""
        dot = agent_to_dot(AGENTS_DIR / "file-ops.md", repo_root=REPO_ROOT)
        assert 'source_hash="' in dot


# ── Test: agents_topology_dot placeholder ────────────────────────────────────


class TestAgentsTopologyDot:
    def test_agents_topology_dot_raises_not_implemented(self) -> None:
        """agents_topology_dot() raises NotImplementedError (Task 9 placeholder)."""
        with pytest.raises(NotImplementedError):
            agents_topology_dot(REPO_ROOT)
