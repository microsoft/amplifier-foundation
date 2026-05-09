"""Tests for Bundle.validate_modes() — Phase 1 schema-only modes walk.

validate_modes() scans self.base_path/'modes' for .md files, parses each,
and logs WARN on malformed contributes blocks.  It does NOT call
ModuleActivator (reserved for v1.1 when contributes.tools joins).
"""

from pathlib import Path

import pytest

from amplifier_foundation.bundle import Bundle


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _write(p: Path, body: str) -> None:
    """Write *body* to *p* as UTF-8."""
    p.write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_MODE_CONTENT = """\
---
mode:
  name: demo-mode
  description: A demo mode for tests
  contributes:
    agents:
      mode-author:
        source: "@modes:agents/mode-author"
---
Demo mode body.
"""

MALFORMED_CONTRIBUTES_CONTENT = """\
---
mode:
  name: bad
  contributes:
    - item1
    - item2
---
Mode with list-type contributes (should be a dict).
"""


@pytest.fixture
def bundle_with_valid_mode(tmp_path: Path) -> Bundle:
    """Bundle with modes/demo.md containing a well-formed contributes block."""
    modes_dir = tmp_path / "modes"
    modes_dir.mkdir()
    _write(modes_dir / "demo.md", VALID_MODE_CONTENT)
    return Bundle(name="modes", base_path=tmp_path)


@pytest.fixture
def bundle_with_malformed_contributes(tmp_path: Path) -> Bundle:
    """Bundle with modes/bad.md where contributes is a list, not a dict."""
    modes_dir = tmp_path / "modes"
    modes_dir.mkdir()
    _write(modes_dir / "bad.md", MALFORMED_CONTRIBUTES_CONTENT)
    return Bundle(name="modes", base_path=tmp_path)


@pytest.fixture
def bundle_with_no_modes_dir(tmp_path: Path) -> Bundle:
    """Bundle whose base_path has no modes/ directory at all."""
    return Bundle(name="no-modes", base_path=tmp_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestValidateModes:
    """Tests for Bundle.validate_modes() schema-only walk."""

    def test_validate_modes_returns_zero_warnings_for_valid_mode(
        self, bundle_with_valid_mode: Bundle
    ) -> None:
        """A well-formed mode file produces no warnings."""
        warnings = bundle_with_valid_mode.validate_modes()
        assert warnings == []

    def test_validate_modes_warns_on_non_dict_contributes(
        self, bundle_with_malformed_contributes: Bundle
    ) -> None:
        """A mode with a list-typed contributes block produces one warning
        that mentions both the filename and the word 'contributes'."""
        warnings = bundle_with_malformed_contributes.validate_modes()
        assert len(warnings) == 1
        assert "bad.md" in warnings[0]
        assert "contributes" in warnings[0]

    def test_validate_modes_no_modes_dir_is_noop(
        self, bundle_with_no_modes_dir: Bundle
    ) -> None:
        """When there is no modes/ directory the method returns an empty list."""
        warnings = bundle_with_no_modes_dir.validate_modes()
        assert warnings == []

    def test_validate_modes_handles_yaml_error_as_warning(self, tmp_path: Path) -> None:
        """A mode file with malformed YAML frontmatter is logged as a warning,
        not a hard failure.  The warning must mention the filename."""
        modes_dir = tmp_path / "modes"
        modes_dir.mkdir()
        _write(
            modes_dir / "broken.md",
            "---\nmode: name: broken\ncontributes: { not closed\n---\n",
        )
        bundle = Bundle(name="modes", base_path=tmp_path)
        warnings = bundle.validate_modes()
        assert len(warnings) == 1
        assert "broken.md" in warnings[0]

    def test_validate_modes_logs_warnings_via_logger(
        self,
        bundle_with_malformed_contributes: Bundle,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """validate_modes() emits the warning through the module logger so it
        shows up in caplog when the WARNING level is captured."""
        import logging

        with caplog.at_level(
            logging.WARNING, logger="amplifier_foundation.bundle._dataclass"
        ):
            bundle_with_malformed_contributes.validate_modes()

        # At least one WARNING record must mention 'bad.md'
        assert any("bad.md" in record.message for record in caplog.records)

    def test_validate_modes_clean_on_existing_modes_bundle(
        self, tmp_path: Path
    ) -> None:
        """Backward-compat smoke test: legacy mode files (no contributes block,
        no advertised key) must parse cleanly and produce zero warnings.

        Mirrors the shipped shape of modes in microsoft/amplifier-bundle-modes.
        """
        modes_dir = tmp_path / "modes"
        modes_dir.mkdir()

        # (a) plan.md — warn on bash, safe for read tools
        _write(
            modes_dir / "plan.md",
            """\
---
mode:
  name: plan
  description: "Plan only — no writes."
  shortcut: plan
  default_action: warn
  tools:
    safe:
      - read_file
      - glob
      - grep
    warn:
      - bash
---
You are in plan mode. Read-only.
""",
        )

        # (b) careful.md — confirm before writes
        _write(
            modes_dir / "careful.md",
            """\
---
mode:
  name: careful
  description: "Confirm before writes."
  shortcut: careful
  default_action: allow
  tools:
    confirm:
      - write_file
      - edit_file
      - bash
---
Careful mode body.
""",
        )

        # (c) explore.md — read-only exploration
        _write(
            modes_dir / "explore.md",
            """\
---
mode:
  name: explore
  description: "Read-only exploration."
  shortcut: explore
  default_action: block
  tools:
    safe:
      - read_file
      - glob
      - grep
---
Explore mode body.
""",
        )

        bundle = Bundle(name="modes", base_path=tmp_path)
        warnings = bundle.validate_modes()
        assert warnings == []


# ---------------------------------------------------------------------------
# Item 3: mode_warnings surfaced on PreparedBundle
# ---------------------------------------------------------------------------


class TestPreparedBundleModeWarnings:
    """Tests: PreparedBundle.mode_warnings exposes validate_modes() output."""

    @pytest.mark.asyncio
    async def test_prepare_surfaces_mode_warnings_on_prepared_bundle(
        self, tmp_path: Path
    ) -> None:
        """Bundle.prepare() populates PreparedBundle.mode_warnings with warnings
        from validate_modes() so callers can inspect them post-prepare.
        """
        modes_dir = tmp_path / "modes"
        modes_dir.mkdir()
        _write(
            modes_dir / "bad.md",
            """\
---
mode:
  name: bad
  contributes:
    - item1
    - item2
---
""",
        )
        bundle = Bundle(name="modes", base_path=tmp_path)
        prepared = await bundle.prepare(install_deps=False)

        assert hasattr(prepared, "mode_warnings"), (
            "PreparedBundle must have a mode_warnings attribute"
        )
        assert len(prepared.mode_warnings) >= 1, (
            f"Expected at least one warning, got: {prepared.mode_warnings}"
        )
        assert any("bad.md" in w for w in prepared.mode_warnings), (
            f"Expected 'bad.md' in warnings; got: {prepared.mode_warnings}"
        )


# ---------------------------------------------------------------------------
# Item 2: inner shape validation for contributes block
# ---------------------------------------------------------------------------


class TestValidateModesInnerShapes:
    """Tests for validate_modes() inner-shape validation of contributes keys."""

    def _bundle_with_mode(self, tmp_path: Path, content: str) -> Bundle:
        modes_dir = tmp_path / "modes"
        modes_dir.mkdir(exist_ok=True)
        _write(modes_dir / "subject.md", content)
        return Bundle(name="modes", base_path=tmp_path)

    def test_agents_as_list_produces_warning(self, tmp_path: Path) -> None:
        """contributes.agents being a list (not a dict) must produce a warning."""
        bundle = self._bundle_with_mode(
            tmp_path,
            """\
---
mode:
  name: bad-agents
  contributes:
    agents:
      - agent-one
      - agent-two
---
""",
        )
        warnings = bundle.validate_modes()
        assert len(warnings) == 1
        assert "agents" in warnings[0]
        assert "subject.md" in warnings[0]

    def test_agents_with_non_dict_value_produces_warning(self, tmp_path: Path) -> None:
        """contributes.agents mapping a string value (not dict) must produce a warning."""
        bundle = self._bundle_with_mode(
            tmp_path,
            """\
---
mode:
  name: bad-agent-value
  contributes:
    agents:
      my-agent: "just a string"
---
""",
        )
        warnings = bundle.validate_modes()
        assert len(warnings) == 1
        assert "agents" in warnings[0]
        assert "subject.md" in warnings[0]

    def test_context_as_string_produces_warning(self, tmp_path: Path) -> None:
        """contributes.context being a string (not a list) must produce a warning."""
        bundle = self._bundle_with_mode(
            tmp_path,
            """\
---
mode:
  name: bad-context
  contributes:
    context: "@modes:context/schema.md"
---
""",
        )
        warnings = bundle.validate_modes()
        assert len(warnings) == 1
        assert "context" in warnings[0]
        assert "subject.md" in warnings[0]

    def test_skills_with_non_string_entry_produces_warning(self, tmp_path: Path) -> None:
        """contributes.skills list containing a non-string entry must produce a warning."""
        bundle = self._bundle_with_mode(
            tmp_path,
            """\
---
mode:
  name: bad-skills
  contributes:
    skills:
      - "@modes:skills/good-skill"
      - 42
---
""",
        )
        warnings = bundle.validate_modes()
        assert len(warnings) == 1
        assert "skills" in warnings[0]
        assert "subject.md" in warnings[0]

    def test_unknown_contributes_key_produces_warning(self, tmp_path: Path) -> None:
        """An unknown top-level key in contributes must produce a warning (not failure)."""
        bundle = self._bundle_with_mode(
            tmp_path,
            """\
---
mode:
  name: unknown-key
  contributes:
    agents:
      my-agent:
        source: "@modes:agents/my-agent"
    future_feature: some_value
---
""",
        )
        warnings = bundle.validate_modes()
        assert len(warnings) == 1
        assert "future_feature" in warnings[0]
        assert "subject.md" in warnings[0]

    def test_valid_full_contributes_block_produces_no_warnings(
        self, tmp_path: Path
    ) -> None:
        """A fully populated valid contributes block must produce no warnings."""
        bundle = self._bundle_with_mode(
            tmp_path,
            """\
---
mode:
  name: valid-full
  contributes:
    agents:
      mode-author:
        source: "@modes:agents/mode-author"
    context:
      - "@modes:context/schema.md"
      - "@modes:context/anti-patterns.md"
    skills:
      - "@modes:skills/mode-design-discipline"
---
""",
        )
        warnings = bundle.validate_modes()
        assert warnings == []
