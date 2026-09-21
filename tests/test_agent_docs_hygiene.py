"""Tests for two agent-doc defects that cost tokens on every spawn.

**ecosystem-expert's dead routing table.** It routed to three agents that exist
in no roster composing it -- and it could not have delegated to them anyway,
because foundation strips ``tool-delegate`` from spawned agents.

**bundle-design-expert's stale ``<example>`` claim.** It listed missing
``<example>`` blocks as an authoring defect while @mentioning the principles
file that bans them outright. An agent that contradicts its own cited source
teaches the contradiction.

**bundle-design-awareness.md's scaffolding.** It restated the expert's own
lifecycle, recipe table, and context keys into every root prompt that composes
``behaviors/bundle-design.yaml`` -- paid whether or not the expert is ever used.
"""

import re
from pathlib import Path

import pytest

# ── Constants ────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.parent

ECOSYSTEM_EXPERT = REPO_ROOT / "agents" / "ecosystem-expert.md"
BUNDLE_DESIGN_EXPERT = REPO_ROOT / "agents" / "bundle-design-expert.md"
BUNDLE_DESIGN_AWARENESS = REPO_ROOT / "context" / "bundle-design-awareness.md"
PRINCIPLES = (
    REPO_ROOT / "context" / "shared" / "description-authoring-principles.md"
)
AGENTS_BEHAVIOR = REPO_ROOT / "behaviors" / "agents.yaml"

#: Agent references the deleted routing table pointed at. None resolves in any
#: roster that composes ecosystem-expert (behaviors/amplifier-dev.yaml includes
#: exactly one agent: foundation:ecosystem-expert).
DEAD_ROUTING_TARGETS = (
    "amplifier:amplifier-expert",
    "core:core-expert",
    "foundation:foundation-expert",
)

#: An awareness pointer earns its place by being cheap. The expert agent is the
#: context sink; the pointer only says the domain exists. ~4 chars/token.
AWARENESS_MAX_CHARS = 600


# ── ecosystem-expert: no unreachable delegation ──────────────────────────────


class TestEcosystemExpertRouting:
    """A sub-agent that cannot delegate must not carry a routing table."""

    @pytest.fixture
    def content(self) -> str:
        return ECOSYSTEM_EXPERT.read_text(encoding="utf-8")

    def test_cannot_delegate(self, content: str) -> None:
        """Establish the premise: this agent has no delegate tool."""
        behavior = AGENTS_BEHAVIOR.read_text(encoding="utf-8")
        assert re.search(r"exclude_tools:\s*\[[^\]]*tool-delegate", behavior), (
            "foundation no longer strips tool-delegate from spawned agents; "
            "ecosystem-expert's routing table may be live again"
        )
        frontmatter = content.split("---", 2)[1]
        assert "tool-delegate" not in frontmatter, (
            "ecosystem-expert re-declares tool-delegate, overriding the exclusion"
        )

    def test_no_dead_routing_targets(self, content: str) -> None:
        """The three unresolvable delegation targets must stay deleted."""
        found = [t for t in DEAD_ROUTING_TARGETS if t in content]
        assert not found, (
            f"ecosystem-expert routes to agents that resolve in no roster "
            f"composing it: {found}"
        )

    def test_no_delegation_section(self, content: str) -> None:
        """No 'Delegation Pattern' section may return."""
        assert "## Delegation Pattern" not in content, (
            "ecosystem-expert cannot delegate -- see test_cannot_delegate"
        )


# ── bundle-design-expert: agrees with the principles file it cites ───────────


class TestBundleDesignExpertExamplePolicy:
    """The agent must not contradict the source it @mentions."""

    @pytest.fixture
    def content(self) -> str:
        return BUNDLE_DESIGN_EXPERT.read_text(encoding="utf-8")

    def test_principles_ban_example_blocks(self) -> None:
        """Establish the premise from the cited source, not from memory."""
        principles = PRINCIPLES.read_text(encoding="utf-8")
        assert "**No `<example>` blocks.**" in principles, (
            "description-authoring-principles.md no longer bans <example> blocks; "
            "bundle-design-expert's guidance may need to change with it"
        )

    def test_expert_mentions_principles(self, content: str) -> None:
        """The agent must still cite the source it now agrees with."""
        assert "description-authoring-principles.md" in content, (
            "bundle-design-expert no longer @mentions the principles file"
        )

    def test_expert_does_not_require_example_blocks(self, content: str) -> None:
        """No line may present <example> blocks as required or missing."""
        offenders = [
            line.strip()
            for line in content.splitlines()
            if "<example>" in line
            and re.search(r"\b(Missing|required|must have|with context/user)\b", line)
        ]
        assert not offenders, (
            f"bundle-design-expert still asks for <example> blocks, contradicting "
            f"description-authoring-principles.md which it @mentions: {offenders}"
        )


# ── bundle-design-awareness.md: a pointer, not a second copy ─────────────────


class TestBundleDesignAwarenessIsAPointer:
    """Root-prompt context is the most expensive place to duplicate anything."""

    @pytest.fixture
    def content(self) -> str:
        return BUNDLE_DESIGN_AWARENESS.read_text(encoding="utf-8")

    def test_awareness_is_small(self, content: str) -> None:
        """The pointer must stay cheap enough to justify always-on loading."""
        assert len(content) <= AWARENESS_MAX_CHARS, (
            f"bundle-design-awareness.md is {len(content)} chars "
            f"(~{len(content) // 4} tokens) into every root prompt that composes "
            f"behaviors/bundle-design.yaml; the cap is {AWARENESS_MAX_CHARS}. "
            f"Heavy content belongs in the agent file (context-sink pattern)."
        )

    def test_awareness_names_the_expert(self, content: str) -> None:
        """A pointer that does not name its target is not a pointer."""
        assert "bundle-design-expert" in content, (
            "bundle-design-awareness.md must name the agent it points at"
        )

    def test_awareness_does_not_duplicate_the_expert(self, content: str) -> None:
        """Recipe paths and context keys live in the expert, not the pointer."""
        duplicated = [
            token
            for token in ("output_path", "registry_path", "@foundation:recipes/")
            if token in content
        ]
        assert not duplicated, (
            f"bundle-design-awareness.md restates content the expert agent already "
            f"carries: {duplicated}"
        )
