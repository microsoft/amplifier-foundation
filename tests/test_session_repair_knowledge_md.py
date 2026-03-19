"""Tests for session-repair-knowledge.md documentation update.

Verifies that the session-repair-knowledge.md file has been updated to:
1. Use the new script path (amplifier-session.py) with deprecation note
2. Use subcommand-style CLI examples (not old flag-style)
3. Have no stale --diagnose / --repair / --rewind flag references anywhere
4. Show new commands: info and find
5. Include the <session> argument note
6. Update the Verification section to use the new script
"""

from pathlib import Path

import pytest

# ── Constants ────────────────────────────────────────────────────────────────

KNOWLEDGE_FILE = (
    Path(__file__).parent.parent / "context" / "agents" / "session-repair-knowledge.md"
)


@pytest.fixture
def content() -> str:
    """Read the session-repair-knowledge.md file."""
    assert KNOWLEDGE_FILE.exists(), f"Knowledge file not found: {KNOWLEDGE_FILE}"
    return KNOWLEDGE_FILE.read_text(encoding="utf-8")


# ── Test 1: File exists and has reasonable length ────────────────────────────


class TestFileExists:
    """File must exist and have reasonable content."""

    def test_file_exists(self) -> None:
        """session-repair-knowledge.md must exist."""
        assert KNOWLEDGE_FILE.exists(), "session-repair-knowledge.md not found"

    def test_file_has_reasonable_length(self, content: str) -> None:
        """File must have roughly similar length to the original."""
        lines = content.splitlines()
        assert 80 <= len(lines) <= 200, (
            f"File has {len(lines)} lines — expected between 80 and 200"
        )


# ── Test 2: Location line updated ───────────────────────────────────────────


class TestLocationLine:
    """Location line must reference amplifier-session.py and deprecate old script."""

    def test_location_references_new_script(self, content: str) -> None:
        """Location line must reference scripts/amplifier-session.py."""
        assert "scripts/amplifier-session.py" in content, (
            "Location line must reference scripts/amplifier-session.py"
        )

    def test_location_mentions_deprecation(self, content: str) -> None:
        """Location line must mention deprecation of session-repair.py."""
        assert "deprecated" in content, (
            "Location line must mention that scripts/session-repair.py is deprecated"
        )


# ── Test 3: Usage section uses subcommand style ──────────────────────────────


class TestUsageSection:
    """Usage section must use subcommand-style examples."""

    def test_usage_has_diagnose_subcommand(self, content: str) -> None:
        """Usage section must show 'diagnose <session>' subcommand."""
        assert "amplifier-session.py diagnose" in content, (
            "Usage section must show subcommand: amplifier-session.py diagnose <session>"
        )

    def test_usage_has_repair_subcommand(self, content: str) -> None:
        """Usage section must show 'repair <session>' subcommand."""
        assert "amplifier-session.py repair" in content, (
            "Usage section must show subcommand: amplifier-session.py repair <session>"
        )

    def test_usage_has_rewind_subcommand(self, content: str) -> None:
        """Usage section must show 'rewind <session>' subcommand."""
        assert "amplifier-session.py rewind" in content, (
            "Usage section must show subcommand: amplifier-session.py rewind <session>"
        )

    def test_usage_has_info_command(self, content: str) -> None:
        """Usage section must show new 'info <session>' command."""
        assert "amplifier-session.py info" in content, (
            "Usage section must show new command: amplifier-session.py info <session>"
        )

    def test_usage_has_find_command(self, content: str) -> None:
        """Usage section must show new 'find' command."""
        assert "amplifier-session.py find" in content, (
            "Usage section must show new command: amplifier-session.py find --project ..."
        )

    def test_usage_has_session_arg_note(self, content: str) -> None:
        """Usage section must note that <session> accepts full paths, IDs, or partial IDs."""
        assert "partial IDs" in content or "partial ID" in content, (
            "Usage section must note that <session> accepts full paths, session IDs, or partial IDs"
        )

    def test_usage_section_no_old_flag_style(self, content: str) -> None:
        """Usage section must not use old 'session-repair.py --diagnose' style."""
        assert "session-repair.py --diagnose" not in content, (
            "Usage section still uses old flag style: session-repair.py --diagnose"
        )
        assert "session-repair.py --repair" not in content, (
            "Usage section still uses old flag style: session-repair.py --repair"
        )
        assert "session-repair.py --rewind" not in content, (
            "Usage section still uses old flag style: session-repair.py --rewind"
        )


# ── Test 4: No stale flag references anywhere in the document ────────────────


class TestNoStaleFlags:
    """The document must not contain old --diagnose / --repair / --rewind flag references."""

    def test_no_stale_diagnose_flag(self, content: str) -> None:
        """No standalone --diagnose flag reference anywhere in the document."""
        assert "`--diagnose`" not in content, (
            "Stale --diagnose flag reference remains (found in Exit Codes table or section heading). "
            "Replace with `diagnose` (subcommand style without --)."
        )

    def test_no_stale_repair_flag(self, content: str) -> None:
        """No standalone --repair flag reference anywhere in the document."""
        assert "`--repair`" not in content, (
            "Stale --repair flag reference remains (found in Exit Codes table). "
            "Replace with `repair` (subcommand style without --)."
        )

    def test_no_stale_rewind_flag(self, content: str) -> None:
        """No standalone --rewind flag reference anywhere in the document."""
        assert "`--rewind`" not in content, (
            "Stale --rewind flag reference remains (found in Exit Codes table). "
            "Replace with `rewind` (subcommand style without --)."
        )

    def test_diagnose_section_heading_no_flag(self, content: str) -> None:
        """The diagnose JSON Output Format section heading must not use --diagnose."""
        assert "### `--diagnose`" not in content, (
            "Section heading still uses old flag style: ### `--diagnose` JSON Output Format. "
            "Replace with ### `diagnose` JSON Output Format"
        )


# ── Test 5: Verification section uses new script ─────────────────────────────


class TestVerificationSection:
    """Verification section must use amplifier-session.py with subcommand style."""

    def test_verification_uses_new_script(self, content: str) -> None:
        """Verification section must use amplifier-session.py diagnose."""
        # Check section exists and uses the new script
        verification_start = content.find("### Verification")
        assert verification_start != -1, "Verification section not found"
        verification_content = content[verification_start:]
        assert "amplifier-session.py diagnose" in verification_content, (
            "Verification section must use: python scripts/amplifier-session.py diagnose <session>"
        )
