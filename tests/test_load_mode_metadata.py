"""Tests for _load_mode_file_metadata — happy path extraction."""

from pathlib import Path
from typing import Any

import pytest

from amplifier_foundation.bundle._dataclass import _load_mode_file_metadata


DEMO_MODE_FRONTMATTER = """\
---
mode:
  name: demo-mode
  description: A demo mode for tests
  shortcut: demo
  advertised: false
  default_action: block
  tools:
    safe:
      - read_file
      - grep
  contributes:
    agents:
      mode-author:
        source: "@modes:agents/mode-author"
    context:
      - "@modes:context/schema.md"
    skills:
      - "@modes:skills/mode-design-discipline"
---
Demo Mode body

system reminder text
"""


@pytest.fixture
def mode_file(tmp_path: Path) -> Path:
    """Write a demo-mode.md with full frontmatter to tmp_path."""
    f = tmp_path / "demo-mode.md"
    f.write_text(DEMO_MODE_FRONTMATTER, encoding="utf-8")
    return f


class TestLoadModeFileMetadata:
    """Tests for _load_mode_file_metadata happy-path extraction."""

    def test_extracts_name_and_description(self, mode_file: Path) -> None:
        """name and description are extracted from the mode: section."""
        result: dict[str, Any] = _load_mode_file_metadata(mode_file, "fallback-name")

        assert result["name"] == "demo-mode"
        assert result["description"] == "A demo mode for tests"

    def test_extracts_advertised_flag(self, mode_file: Path) -> None:
        """advertised=false in frontmatter is extracted as Python False."""
        result: dict[str, Any] = _load_mode_file_metadata(mode_file, "fallback-name")

        assert result["advertised"] is False

    def test_extracts_contributes_block(self, mode_file: Path) -> None:
        """contributes block is extracted with agents, context, and skills."""
        result: dict[str, Any] = _load_mode_file_metadata(mode_file, "fallback-name")

        contrib = result.get("contributes")
        assert isinstance(contrib, dict)
        assert "mode-author" in contrib["agents"]
        assert contrib["agents"]["mode-author"]["source"] == "@modes:agents/mode-author"
        assert contrib["context"] == ["@modes:context/schema.md"]
        assert contrib["skills"] == ["@modes:skills/mode-design-discipline"]

    def test_includes_body_as_instruction(self, mode_file: Path) -> None:
        """Markdown body is included as the 'instruction' key."""
        result: dict[str, Any] = _load_mode_file_metadata(mode_file, "fallback-name")

        assert "instruction" in result
        assert "Demo Mode body" in result["instruction"]
        assert "system reminder text" in result["instruction"]

    def test_minimal_mode_defaults(self, tmp_path: Path) -> None:
        """Backward compat: modes with only name+description get advertised=True, contributes={}."""
        f = tmp_path / "minimal.md"
        f.write_text(
            "---\nmode:\n  name: minimal\n  description: Minimal mode\n---\n",
            encoding="utf-8",
        )

        result: dict[str, Any] = _load_mode_file_metadata(f, "minimal")

        assert result["advertised"] is True
        assert result["contributes"] == {}

    def test_fallback_name_when_no_mode_section(self, tmp_path: Path) -> None:
        """Modes with no frontmatter fall back to the caller-provided name, with safe defaults."""
        f = tmp_path / "noframe.md"
        f.write_text("just markdown body, no frontmatter\n", encoding="utf-8")

        result: dict[str, Any] = _load_mode_file_metadata(f, "noframe")

        assert result["name"] == "noframe"
        assert result["advertised"] is True
        assert result["contributes"] == {}

    def test_contributes_explicit_null_yields_empty_dict(self, tmp_path: Path) -> None:
        """Explicit null contributes: (no value) is coerced to empty dict via `or {}`."""
        f = tmp_path / "nullcontrib.md"
        f.write_text(
            "---\nmode:\n  name: nullcontrib\n  contributes:\n---\n",
            encoding="utf-8",
        )

        result: dict[str, Any] = _load_mode_file_metadata(f, "nullcontrib")

        assert result["contributes"] == {}
