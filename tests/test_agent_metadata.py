"""Tests for agent file metadata extraction, including model_role support."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from amplifier_foundation.bundle import _load_agent_file_metadata


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
            "model_role: [planning, general]\n"
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
        assert result["model_role"] == ["planning", "general"]

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