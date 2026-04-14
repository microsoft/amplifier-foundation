"""Tests for session-storage-knowledge.md documentation update.

Verifies that the session-storage-knowledge.md file has been updated to:
1. Remove manual repair recipe (grep/sed/head/tail instructions)
2. Replace with script-based recovery using amplifier-session.py diagnose/repair
3. No manual transcript.jsonl editing instructions remain in Recovery section
"""

from pathlib import Path

import pytest

# ── Constants ──────────────────────────────────────────────────────────────

KNOWLEDGE_FILE = (
    Path(__file__).parent.parent / "context" / "agents" / "session-storage-knowledge.md"
)


@pytest.fixture
def content() -> str:
    """Read the session-storage-knowledge.md file."""
    assert KNOWLEDGE_FILE.exists(), f"Knowledge file not found: {KNOWLEDGE_FILE}"
    return KNOWLEDGE_FILE.read_text(encoding="utf-8")


@pytest.fixture
def recovery_section(content: str) -> str:
    """Extract the Recovery subsection from the content."""
    start = content.find("### Recovery")
    assert start != -1, "Recovery section not found in session-storage-knowledge.md"
    # Find the next heading after Recovery
    next_heading = content.find("\n## ", start + 1)
    if next_heading == -1:
        next_heading = content.find("\n### ", start + len("### Recovery") + 1)
    if next_heading == -1:
        return content[start:]
    return content[start:next_heading]


# ── Test 1: File exists ─────────────────────────────────────────────────────


class TestFileExists:
    """File must exist."""

    def test_file_exists(self) -> None:
        """session-storage-knowledge.md must exist."""
        assert KNOWLEDGE_FILE.exists(), "session-storage-knowledge.md not found"


# ── Test 2: No manual repair instructions in Recovery section ───────────────


class TestNoManualRepairInstructions:
    """Recovery section must not contain manual repair instructions."""

    def test_no_grep_for_orphaned_tool(self, recovery_section: str) -> None:
        """Recovery section must not instruct using grep to find orphaned tool_use lines."""
        assert "THE_TOOL_ID" not in recovery_section, (
            "Recovery section still contains manual grep instructions for THE_TOOL_ID. "
            "Replace with script-based recovery."
        )

    def test_no_manual_insert_instructions(self, recovery_section: str) -> None:
        """Recovery section must not instruct manually inserting tool_result JSON."""
        assert "Insert a synthetic" not in recovery_section, (
            "Recovery section still contains manual insert instructions. "
            "Replace with script-based recovery."
        )

    def test_no_head_tail_sed_instructions(self, recovery_section: str) -> None:
        """Recovery section must not instruct using head/tail or sed to edit transcript.jsonl."""
        assert (
            "head`/`tail`" not in recovery_section
            and "head/tail" not in recovery_section
        ), (
            "Recovery section still contains head/tail instructions for manual editing. "
            "Replace with script-based recovery."
        )

    def test_no_backup_cp_instructions(self, recovery_section: str) -> None:
        """Recovery section must not instruct using cp to back up transcript.jsonl."""
        assert "cp transcript.jsonl transcript.jsonl.backup" not in recovery_section, (
            "Recovery section still contains manual cp backup instructions. "
            "Replace with script-based recovery."
        )

    def test_no_manual_transcript_editing(self, recovery_section: str) -> None:
        """Recovery section must not contain manual transcript.jsonl editing instructions."""
        assert "transcript.jsonl.backup" not in recovery_section, (
            "Recovery section still references manual backup of transcript.jsonl. "
            "Replace with script-based recovery."
        )


# ── Test 3: Script-based recovery is present ────────────────────────────────


class TestScriptBasedRecovery:
    """Recovery section must contain script-based recovery using amplifier-session.py."""

    def test_recovery_uses_diagnose_command(self, recovery_section: str) -> None:
        """Recovery section must show 'amplifier-session.py diagnose' command."""
        assert (
            "amplifier-session.py diagnose" in recovery_section
            or ('amplifier-session.py" diagnose' in recovery_section)
            or ('"$SCRIPT" diagnose' in recovery_section)
        ), 'Recovery section must show: python "$SCRIPT" diagnose <session>'

    def test_recovery_uses_repair_command(self, recovery_section: str) -> None:
        """Recovery section must show 'amplifier-session.py repair' command."""
        assert "amplifier-session.py repair" in recovery_section or (
            '"$SCRIPT" repair' in recovery_section
        ), 'Recovery section must show: python "$SCRIPT" repair <session>'

    def test_recovery_warns_against_manual_editing(self, recovery_section: str) -> None:
        """Recovery section must warn against manually editing transcript.jsonl."""
        assert "do not manually edit" in recovery_section or (
            "not manually edit" in recovery_section
        ), "Recovery section must warn: do not manually edit transcript.jsonl"

    def test_recovery_mentions_script_find(self, recovery_section: str) -> None:
        """Recovery section must show how to find the script via find command."""
        assert (
            "amplifier-foundation/scripts/amplifier-session.py" in recovery_section
        ), (
            "Recovery section must show how to locate amplifier-session.py via find command"
        )
