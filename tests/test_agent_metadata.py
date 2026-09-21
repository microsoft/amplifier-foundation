"""Tests for agent file metadata extraction, including model_role support."""

from pathlib import Path

import pytest

from amplifier_foundation.bundle._dataclass import _load_agent_file_metadata
from amplifier_foundation.bundle._dataclass import _parse_agents


class TestLoadAgentFileMetadata:
    """Tests for _load_agent_file_metadata model_role extraction."""

    def test_model_role_extracted_from_frontmatter(self, tmp_path: Path) -> None:
        """model_role in frontmatter is extracted into agent metadata."""
        agent_file = tmp_path / "test-agent.md"
        agent_file.write_text(
            "---\n"
            "meta:\n"
            "  name: test-agent\n"
            "  description: A test agent\n"
            "\n"
            "model_role: fast\n"
            "\n"
            "provider_preferences:\n"
            "  - provider: anthropic\n"
            "    model: claude-haiku-*\n"
            "---\n"
            "\n"
            "# Test Agent\n"
            "\n"
            "You are a test agent.\n"
        )

        result = _load_agent_file_metadata(agent_file, "test-agent")

        assert "model_role" in result
        assert result["model_role"] == "fast"

    def test_model_role_list_extracted_from_frontmatter(self, tmp_path: Path) -> None:
        """model_role as list in frontmatter is extracted into agent metadata."""
        agent_file = tmp_path / "architect-agent.md"
        agent_file.write_text(
            "---\n"
            "meta:\n"
            "  name: architect-agent\n"
            "  description: An architect agent\n"
            "\n"
            "model_role: [reasoning, general]\n"
            "\n"
            "provider_preferences:\n"
            "  - provider: anthropic\n"
            "    model: claude-opus-*\n"
            "---\n"
            "\n"
            "# Architect Agent\n"
        )

        result = _load_agent_file_metadata(agent_file, "architect-agent")

        assert "model_role" in result
        assert result["model_role"] == ["reasoning", "general"]

    def test_no_model_role_not_in_result(self, tmp_path: Path) -> None:
        """When no model_role is set, key is absent from result."""
        agent_file = tmp_path / "plain-agent.md"
        agent_file.write_text(
            "---\n"
            "meta:\n"
            "  name: plain-agent\n"
            "  description: A plain agent\n"
            "---\n"
            "\n"
            "# Plain Agent\n"
        )

        result = _load_agent_file_metadata(agent_file, "plain-agent")

        assert "model_role" not in result


class TestFoundationAgentModelRoles:
    """Tests that foundation agent files have correct model_role assignments."""

    AGENTS_DIR = Path(__file__).parent.parent / "agents"

    def test_zen_architect_has_reasoning_role(self) -> None:
        """zen-architect should use reasoning (not planning) role."""
        result = _load_agent_file_metadata(
            self.AGENTS_DIR / "zen-architect.md", "zen-architect"
        )
        assert result["model_role"] == ["reasoning", "general"]

    def test_security_guardian_has_security_audit_role(self) -> None:
        """security-guardian should use security-audit and critique roles."""
        result = _load_agent_file_metadata(
            self.AGENTS_DIR / "security-guardian.md", "security-guardian"
        )
        assert result["model_role"] == ["security-audit", "critique", "general"]

    def test_no_agent_uses_planning_role(self) -> None:
        """No foundation agent should reference the deprecated 'planning' role."""
        for agent_file in sorted(self.AGENTS_DIR.glob("*.md")):
            result = _load_agent_file_metadata(agent_file, agent_file.stem)
            role = result.get("model_role")
            if isinstance(role, list):
                assert "planning" not in role, (
                    f"{agent_file.name} still uses deprecated 'planning' role"
                )
            else:
                assert role != "planning", (
                    f"{agent_file.name} still uses deprecated 'planning' role"
                )


class TestLoadAgentFileMetadataAgentsAccessControl:
    """Tests for _load_agent_file_metadata forwarding of the `agents:` access-
    control declaration ("all" | "none" | [names]), as distinct from the
    `agents:` roster form (dict), which is bundle-level and unrelated.
    """

    def test_top_level_agents_none_is_forwarded(self, tmp_path: Path) -> None:
        """Top-level `agents: none` is forwarded as the string "none"."""
        agent_file = tmp_path / "restricted-agent.md"
        agent_file.write_text(
            "---\n"
            "meta:\n"
            "  name: restricted-agent\n"
            "  description: A restricted agent\n"
            "\n"
            "agents: none\n"
            "---\n"
            "\n"
            "# Restricted Agent\n"
        )

        result = _load_agent_file_metadata(agent_file, "restricted-agent")

        assert result["agents"] == "none"

    def test_top_level_agents_all_is_forwarded(self, tmp_path: Path) -> None:
        """Top-level `agents: all` is forwarded as the string "all"."""
        agent_file = tmp_path / "unrestricted-agent.md"
        agent_file.write_text(
            "---\n"
            "meta:\n"
            "  name: unrestricted-agent\n"
            "  description: An unrestricted agent\n"
            "\n"
            "agents: all\n"
            "---\n"
            "\n"
            "# Unrestricted Agent\n"
        )

        result = _load_agent_file_metadata(agent_file, "unrestricted-agent")

        assert result["agents"] == "all"

    def test_top_level_agents_list_is_forwarded(self, tmp_path: Path) -> None:
        """Top-level `agents: [a, b]` is forwarded as the list ["a", "b"]."""
        agent_file = tmp_path / "scoped-agent.md"
        agent_file.write_text(
            "---\n"
            "meta:\n"
            "  name: scoped-agent\n"
            "  description: A scoped agent\n"
            "\n"
            "agents: [a, b]\n"
            "---\n"
            "\n"
            "# Scoped Agent\n"
        )

        result = _load_agent_file_metadata(agent_file, "scoped-agent")

        assert result["agents"] == ["a", "b"]

    def test_top_level_agents_dict_roster_is_not_forwarded(
        self, tmp_path: Path
    ) -> None:
        """Top-level dict `agents: {include: [...]}` is the roster form and is
        NOT forwarded from the agent-file layer -- unchanged behavior.
        """
        agent_file = tmp_path / "roster-agent.md"
        agent_file.write_text(
            "---\n"
            "meta:\n"
            "  name: roster-agent\n"
            "  description: An agent with a roster-shaped agents key\n"
            "\n"
            "agents:\n"
            "  include:\n"
            "    - foundation:explorer\n"
            "---\n"
            "\n"
            "# Roster Agent\n"
        )

        result = _load_agent_file_metadata(agent_file, "roster-agent")

        assert "agents" not in result

    def test_meta_nested_agents_none_still_works(self, tmp_path: Path) -> None:
        """`meta:`-nested `agents: none` still passes through via the meta
        splat (the shape that has always worked) -- regression guard.
        """
        agent_file = tmp_path / "meta-restricted-agent.md"
        agent_file.write_text(
            "---\n"
            "meta:\n"
            "  name: meta-restricted-agent\n"
            "  description: A restricted agent (meta-nested form)\n"
            "  agents: none\n"
            "---\n"
            "\n"
            "# Meta Restricted Agent\n"
        )

        result = _load_agent_file_metadata(agent_file, "meta-restricted-agent")

        assert result["agents"] == "none"

    def test_invalid_agents_string_raises_with_file_and_value(
        self, tmp_path: Path
    ) -> None:
        """An invalid string value (typo of "none") raises ValueError naming
        the offending file and value.
        """
        agent_file = tmp_path / "typo-agent.md"
        agent_file.write_text(
            "---\n"
            "meta:\n"
            "  name: typo-agent\n"
            "  description: An agent with a typo'd agents value\n"
            "\n"
            "agents: nonr\n"
            "---\n"
            "\n"
            "# Typo Agent\n"
        )

        with pytest.raises(ValueError) as exc_info:
            _load_agent_file_metadata(agent_file, "typo-agent")

        message = str(exc_info.value)
        assert str(agent_file) in message
        assert "nonr" in message

    def test_invalid_agents_type_raises(self, tmp_path: Path) -> None:
        """A non-dict/str/list value (e.g. an int) raises ValueError."""
        agent_file = tmp_path / "bad-type-agent.md"
        agent_file.write_text(
            "---\n"
            "meta:\n"
            "  name: bad-type-agent\n"
            "  description: An agent with an invalid agents type\n"
            "\n"
            "agents: 42\n"
            "---\n"
            "\n"
            "# Bad Type Agent\n"
        )

        with pytest.raises(ValueError) as exc_info:
            _load_agent_file_metadata(agent_file, "bad-type-agent")

        assert "42" in str(exc_info.value)


class TestParseAgents:
    """Tests for _parse_agents handling of the roster (dict) vs access-control
    (str/list) forms of the `agents:` key.
    """

    def test_string_none_returns_empty_roster(self) -> None:
        """A str value is the access-control form, not a roster -- no crash."""
        assert _parse_agents("none", None) == {}

    def test_string_all_returns_empty_roster(self) -> None:
        """A str value is the access-control form, not a roster -- no crash."""
        assert _parse_agents("all", None) == {}

    def test_list_returns_empty_roster(self) -> None:
        """A list value is the access-control form, not a roster -- no crash."""
        assert _parse_agents(["a", "b"], None) == {}

    def test_dict_include_list_roster_unchanged(self) -> None:
        """dict `{include: [...]}` still returns the expected roster."""
        result = _parse_agents({"include": ["x"]}, None)
        assert result == {"x": {"name": "x"}}

    def test_dict_inline_definition_roster_unchanged(self) -> None:
        """dict with an inline agent definition still returns it unchanged."""
        result = _parse_agents({"helper": {"description": "d"}}, None)
        assert result == {"helper": {"description": "d"}}
