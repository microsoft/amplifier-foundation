"""Frontmatter parsing must tolerate a leading UTF-8 BOM.

Editors and shells on Windows (Notepad, PowerShell ``Set-Content`` and
``Out-File``) write UTF-8 *with* a byte-order mark by default. Decoding such a
file as plain ``utf-8`` leaves a U+FEFF at position 0, which pushes the opening
``---`` off the start of the text. Frontmatter detection then fails silently:
the parser returns an empty mapping instead of raising, so an agent, mode, or
bundle authored on Windows loads with none of its declared configuration and no
error to explain why.

These tests pin the BOM-tolerant behaviour at every markdown entry point, and
pin that BOM-free files are unaffected.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from amplifier_foundation.bundle._dataclass import (
    _load_agent_file_metadata,
    _load_mode_file_metadata,
)
from amplifier_foundation.bundle_docs.frontmatter import parse_frontmatter as parse_path
from amplifier_foundation.io.frontmatter import parse_frontmatter
from amplifier_foundation.registry import BundleRegistry

BOM = "\ufeff"

AGENT_MD = """\
---
meta:
  name: windows-agent
  description: An agent authored on Windows
model_role: fast
---

You are a helpful agent.
"""

MODE_MD = """\
---
mode:
  name: windows-mode
  description: A mode authored on Windows
  default_action: block
---

Mode guidance body.
"""

BUNDLE_MD = """\
---
bundle:
  name: windows-bundle
  version: 1.0.0
---

Bundle instruction body.
"""


def _write_with_bom(path: Path, text: str) -> Path:
    """Write ``text`` as UTF-8 with a BOM, the way Windows editors do."""
    path.write_bytes((BOM + text).encode("utf-8"))
    return path


class TestParseFrontmatterBOM:
    """The shared string parser used by agents, modes, and markdown bundles."""

    def test_bom_prefixed_text_parses(self) -> None:
        data, _body = parse_frontmatter(BOM + AGENT_MD)
        assert data["meta"]["name"] == "windows-agent"
        assert data["model_role"] == "fast"

    def test_bom_is_stripped_from_body(self) -> None:
        _data, body = parse_frontmatter(BOM + AGENT_MD)
        assert not body.startswith(BOM)
        assert body.strip() == "You are a helpful agent."

    def test_repeated_bom_tolerated(self) -> None:
        """A file round-tripped through BOM-adding tools can accumulate marks."""
        data, body = parse_frontmatter(BOM + BOM + AGENT_MD)
        assert data["meta"]["name"] == "windows-agent"
        assert not body.startswith(BOM)

    def test_without_bom_unchanged(self) -> None:
        data, body = parse_frontmatter(AGENT_MD)
        assert data["meta"]["name"] == "windows-agent"
        assert body.strip() == "You are a helpful agent."

    def test_no_frontmatter_body_preserved(self) -> None:
        """Plain markdown still round-trips, with the BOM removed."""
        data, body = parse_frontmatter(BOM + "# Heading\n")
        assert data == {}
        assert body == "# Heading\n"


class TestAgentMetadataBOM:
    """``_load_agent_file_metadata`` — agent .md files."""

    def test_bom_prefixed_agent_file(self, tmp_path: Path) -> None:
        path = _write_with_bom(tmp_path / "windows-agent.md", AGENT_MD)
        meta = _load_agent_file_metadata(path, "fallback-name")
        assert meta["name"] == "windows-agent"
        assert meta["description"] == "An agent authored on Windows"
        assert meta["model_role"] == "fast"
        assert not str(meta["instruction"]).startswith(BOM)

    def test_without_bom_unchanged(self, tmp_path: Path) -> None:
        path = tmp_path / "plain-agent.md"
        path.write_text(AGENT_MD, encoding="utf-8")
        meta = _load_agent_file_metadata(path, "fallback-name")
        assert meta["name"] == "windows-agent"
        assert meta["model_role"] == "fast"


class TestModeMetadataBOM:
    """``_load_mode_file_metadata`` — mode .md files."""

    def test_bom_prefixed_mode_file(self, tmp_path: Path) -> None:
        path = _write_with_bom(tmp_path / "windows-mode.md", MODE_MD)
        meta = _load_mode_file_metadata(path, "fallback-name")
        assert meta["name"] == "windows-mode"
        assert meta["description"] == "A mode authored on Windows"
        assert meta["default_action"] == "block"

    def test_without_bom_unchanged(self, tmp_path: Path) -> None:
        path = tmp_path / "plain-mode.md"
        path.write_text(MODE_MD, encoding="utf-8")
        meta = _load_mode_file_metadata(path, "fallback-name")
        assert meta["name"] == "windows-mode"
        assert meta["default_action"] == "block"


class TestMarkdownBundleBOM:
    """``BundleRegistry._load_markdown_bundle`` — bundle.md files."""

    def test_bom_prefixed_bundle_file(self, tmp_path: Path) -> None:
        path = _write_with_bom(tmp_path / "bundle.md", BUNDLE_MD)
        bundle = asyncio.run(BundleRegistry()._load_markdown_bundle(path))
        assert bundle.name == "windows-bundle"
        assert bundle.version == "1.0.0"
        assert bundle.instruction == "Bundle instruction body."

    def test_without_bom_unchanged(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.md"
        path.write_text(BUNDLE_MD, encoding="utf-8")
        bundle = asyncio.run(BundleRegistry()._load_markdown_bundle(path))
        assert bundle.name == "windows-bundle"


class TestBundleDocsParseFrontmatterBOM:
    """``bundle_docs.frontmatter.parse_frontmatter`` — reads the file itself."""

    def test_bom_prefixed_markdown(self, tmp_path: Path) -> None:
        path = _write_with_bom(tmp_path / "agent.md", AGENT_MD)
        data, body = parse_path(path)
        assert data["meta"]["name"] == "windows-agent"
        assert "You are a helpful agent." in body

    def test_bom_prefixed_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "behavior.yaml"
        path.write_bytes((BOM + "bundle:\n  name: windows-behavior\n").encode("utf-8"))
        data, body = parse_path(path)
        assert data["bundle"]["name"] == "windows-behavior"
        assert body == ""

    def test_without_bom_unchanged(self, tmp_path: Path) -> None:
        path = tmp_path / "agent.md"
        path.write_text(AGENT_MD, encoding="utf-8")
        data, _body = parse_path(path)
        assert data["meta"]["name"] == "windows-agent"
