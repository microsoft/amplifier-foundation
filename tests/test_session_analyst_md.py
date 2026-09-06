"""Tests for session-analyst.md documentation rewrite.

Verifies that the session-analyst agent documentation has been updated to:
1. Use the unified amplifier-session.py script instead of bash choreography
2. Contain concise section replacements for Storage Locations, Search Workflow,
   Search Strategies, Session Repair, and Example Queries
3. Be shorter than the original (~400 lines vs ~507)
4. Have no references to the old session-repair.py in workflow sections
"""

import fnmatch
import re
from pathlib import Path

import pytest


# ── Constants ─────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.parent
AGENT_FILE = REPO_ROOT / "agents" / "session-analyst.md"
AGENTS_BEHAVIOR_FILE = REPO_ROOT / "behaviors" / "agents.yaml"

# The glob the agent uses to locate the unified script.
#
# It must match BOTH layouts the script really lives under:
#   module cache:     .../amplifier-foundation-<hash>/scripts/amplifier-session.py
#   source checkout:  .../amplifier-foundation/scripts/amplifier-session.py
#
# The bare '*/amplifier-foundation/...' form silently missed the cache -- which is
# where the script actually is at runtime -- so the agent found nothing.
SCRIPT_DISCOVERY_GLOB = "*/amplifier-foundation*/scripts/amplifier-session.py"

# The unified script path that should appear in the file
SCRIPT_DISCOVERY_LINE = (
    f"SCRIPT=\"$(find / -path '{SCRIPT_DISCOVERY_GLOB}' "
    '-type f 2>/dev/null | head -1)"'
)

# Real-world paths the discovery glob must resolve. The cache path is the one
# that matters: it is where a released Amplifier install keeps the script.
CACHE_LAYOUT_PATH = (
    "/home/user/.amplifier/cache/amplifier-foundation-c909465861f9d6ce"
    "/scripts/amplifier-session.py"
)
CHECKOUT_LAYOUT_PATH = (
    "/home/user/repos/amplifier-foundation/scripts/amplifier-session.py"
)

# Old script reference that should NOT appear in workflow sections
OLD_SCRIPT_REFERENCE = "session-repair.py"

# Maximum line count after rewrite (target ~400, must be < 507)
MAX_LINE_COUNT = 420


@pytest.fixture
def content() -> str:
    """Read the session-analyst.md file."""
    assert AGENT_FILE.exists(), f"Agent file not found: {AGENT_FILE}"
    return AGENT_FILE.read_text(encoding="utf-8")


@pytest.fixture
def lines(content: str) -> list[str]:
    """Return lines of the file."""
    return content.splitlines()


# ── Test 1: Script discovery line is present ──────────────────────────────────


class TestScriptDiscovery:
    """The unified script discovery line must be present."""

    def test_script_discovery_line_present(self, content: str) -> None:
        """The amplifier-session.py discovery line must be in the file."""
        assert "amplifier-session.py" in content, (
            "Script discovery line for amplifier-session.py not found"
        )

    def test_script_discovery_uses_find(self, content: str) -> None:
        """Discovery must use find command with correct path pattern."""
        assert f"find / -path '{SCRIPT_DISCOVERY_GLOB}'" in content, (
            "Script discovery 'find' pattern not found in file"
        )

    def test_script_variable_assigned(self, content: str) -> None:
        """SCRIPT variable must be assigned from the find command."""
        assert 'SCRIPT="$(find' in content or "SCRIPT=$(find" in content, (
            "SCRIPT variable assignment not found"
        )


# ── Test 1b: Discovery glob resolves the layouts the script really lives in ──


def _discovery_globs(content: str) -> list[str]:
    """Every `-path '<glob>'` in the file that targets amplifier-session.py."""
    globs = re.findall(r"-path\s+'([^']*)'", content)
    return [g for g in globs if g.endswith("amplifier-session.py")]


class TestScriptDiscoveryResolvesRealLayouts:
    """The discovery glob must actually match where the script lives.

    `find -path` matches the whole path with shell-glob semantics (`*` spans
    `/`), which is what `fnmatch.fnmatchcase` models. A glob that compiles
    fine but matches nothing is the exact defect this guards.
    """

    def test_at_least_one_discovery_glob(self, content: str) -> None:
        """There must be a script-discovery glob to check."""
        assert _discovery_globs(content), (
            "No `-path '<glob>'` targeting amplifier-session.py found in the file"
        )

    def test_every_discovery_glob_matches_cache_layout(self, content: str) -> None:
        """Every discovery glob must match the hashed module-cache layout."""
        for glob in _discovery_globs(content):
            assert fnmatch.fnmatchcase(CACHE_LAYOUT_PATH, glob), (
                f"Discovery glob {glob!r} does not match the module-cache layout "
                f"{CACHE_LAYOUT_PATH!r} -- this is where the script actually is at "
                f"runtime, so the agent would find nothing"
            )

    def test_every_discovery_glob_matches_checkout_layout(self, content: str) -> None:
        """Every discovery glob must still match a plain source checkout."""
        for glob in _discovery_globs(content):
            assert fnmatch.fnmatchcase(CHECKOUT_LAYOUT_PATH, glob), (
                f"Discovery glob {glob!r} does not match the source-checkout layout "
                f"{CHECKOUT_LAYOUT_PATH!r}"
            )


# ── Test 2: Old session-repair.py removed from workflow sections ──────────────


class TestNoOldScriptInWorkflow:
    """session-repair.py must not appear in the workflow/repair sections."""

    def test_session_repair_py_not_in_repair_section(self, content: str) -> None:
        """session-repair.py should not appear in the Session Repair workflow."""
        # Find the repair section
        repair_section_start = content.find("## Session Repair")
        if repair_section_start == -1:
            pytest.skip("Session Repair section not found")

        # Get content after repair section
        repair_content = content[repair_section_start:]
        assert OLD_SCRIPT_REFERENCE not in repair_content, (
            f"Old script reference '{OLD_SCRIPT_REFERENCE}' found in Session Repair section"
        )

    def test_script_in_repair_section_is_new(self, content: str) -> None:
        """The repair section should reference amplifier-session.py, not session-repair.py."""
        repair_section_start = content.find("## Session Repair")
        if repair_section_start == -1:
            pytest.skip("Session Repair section not found")

        repair_content = content[repair_section_start:]
        assert "amplifier-session.py" in repair_content, (
            "amplifier-session.py not found in Session Repair section"
        )


# ── Test 3: python "$SCRIPT" invocations present ──────────────────────────────


class TestScriptInvocations:
    """Workflow sections must use python \"$SCRIPT\" invocations."""

    def test_python_script_invocation_present(self, content: str) -> None:
        """The file must contain python \"$SCRIPT\" invocations."""
        assert 'python "$SCRIPT"' in content, (
            'python "$SCRIPT" invocations not found in file'
        )

    def test_repair_command_uses_script(self, content: str) -> None:
        """Repair step must use python \"$SCRIPT\" subcommand syntax."""
        assert 'python "$SCRIPT" repair' in content, (
            "Repair command via $SCRIPT not found (must use subcommand syntax: repair, not --repair)"
        )

    def test_diagnose_command_uses_script(self, content: str) -> None:
        """Diagnose step must use python \"$SCRIPT\" subcommand syntax."""
        assert 'python "$SCRIPT" diagnose' in content, (
            "Diagnose command via $SCRIPT not found (must use subcommand syntax: diagnose, not --diagnose)"
        )


# ── Test 4: File is shorter than original ────────────────────────────────────


class TestFileLength:
    """File must be shorter after the rewrite."""

    def test_file_shorter_than_original(self, lines: list[str]) -> None:
        """File must be shorter than the 507-line original."""
        line_count = len(lines)
        assert line_count < MAX_LINE_COUNT, (
            f"File has {line_count} lines, expected < {MAX_LINE_COUNT} "
            f"(original was 507 lines)"
        )

    def test_file_not_empty(self, lines: list[str]) -> None:
        """File must have content."""
        assert len(lines) > 100, "File appears to be too short (less than 100 lines)"


# ── Test 5: Storage Locations section is concise ─────────────────────────────


class TestStorageLocationsSection:
    """Storage Locations section must be condensed."""

    def test_storage_locations_section_exists(self, content: str) -> None:
        """Storage Locations section must still exist."""
        assert "## Storage Locations" in content, "Storage Locations section not found"

    def test_storage_locations_mentions_metadata_json(self, content: str) -> None:
        """metadata.json must be mentioned in Storage Locations."""
        assert "metadata.json" in content, (
            "metadata.json not found in Storage Locations"
        )

    def test_storage_locations_mentions_events_jsonl(self, content: str) -> None:
        """events.jsonl must be mentioned with DANGER warning."""
        assert "events.jsonl" in content, "events.jsonl not found"
        # Should have danger/warning about events.jsonl
        assert "DANGER" in content or "100k" in content, (
            "DANGER warning for events.jsonl not found"
        )

    def test_storage_locations_mentions_transcript_jsonl(self, content: str) -> None:
        """transcript.jsonl must be mentioned."""
        assert "transcript.jsonl" in content, "transcript.jsonl not found"


# ── Test 6: Search Workflow section uses script ───────────────────────────────


class TestSearchWorkflowSection:
    """Search Workflow section must use script-based examples."""

    def test_search_section_exists(self, content: str) -> None:
        """Search Workflow or Search section must exist."""
        assert "## Search Workflow" in content or "## Searching Sessions" in content, (
            "Search Workflow section not found"
        )

    def test_synthesis_guidance_preserved(self, content: str) -> None:
        """Synthesis guidance must be preserved (overview, per-session summary)."""
        assert "Overview" in content or "overview" in content, (
            "Overview synthesis guidance not found"
        )

    def test_cross_session_insights_preserved(self, content: str) -> None:
        """Cross-session insights guidance must be preserved."""
        assert "cross-session" in content.lower() or "Cross-Session" in content, (
            "Cross-session insights guidance not found"
        )


# ── Test 7: Search Strategies section uses one-liners ────────────────────────


class TestSearchStrategiesSection:
    """Search Strategies section must have one-liner examples with $SCRIPT."""

    def test_search_strategies_section_exists(self, content: str) -> None:
        """Search Strategies section must exist."""
        assert "## Search Strategies" in content, "Search Strategies section not found"

    def test_deep_event_analysis_preserved(self, content: str) -> None:
        """Deep Event Analysis section must be preserved with NEVER warning."""
        assert "Deep Event Analysis" in content, "Deep Event Analysis section not found"
        assert "NEVER" in content, "NEVER warning not found in Deep Event Analysis"


# ── Test 8: Session Repair section uses script ───────────────────────────────


class TestSessionRepairSection:
    """Session Repair section must use the unified script workflow."""

    def test_session_repair_section_exists(self, content: str) -> None:
        """Session Repair section must exist."""
        assert "## Session Repair" in content, "Session Repair section not found"

    def test_failure_modes_table_present(self, content: str) -> None:
        """Failure modes table (FM1, FM2, FM3) must be present."""
        assert "FM1" in content, "FM1 failure mode not found"
        assert "FM2" in content, "FM2 failure mode not found"
        assert "FM3" in content, "FM3 failure mode not found"

    def test_mandatory_script_mandate(self, content: str) -> None:
        """Mandatory script-only mandate must be present."""
        assert "MANDATORY" in content or "mandatory" in content.lower(), (
            "Mandatory script mandate not found"
        )

    def test_three_step_workflow_present(self, content: str) -> None:
        """Three-step workflow (diagnose -> repair/rewind -> verify) must be present."""
        assert "diagnose" in content.lower(), "Diagnose step not found"
        assert "verify" in content.lower(), "Verify step not found"

    def test_script_failure_guidance_present(self, content: str) -> None:
        """Script failure guidance (STOP and report) must be present."""
        assert (
            "script fails" in content.lower()
            or "If the script fails" in content
            or "Script Fails" in content
        ), "Script failure guidance not found"

    def test_parent_session_note_present(self, content: str) -> None:
        """Parent session modification note must be present."""
        assert "parent" in content.lower() and "session" in content.lower(), (
            "Parent session note not found"
        )


# ── Test 9: Example Queries section uses script ──────────────────────────────


class TestExampleQueriesSection:
    """Example Queries section must use script-based examples."""

    def test_example_queries_section_exists(self, content: str) -> None:
        """Example Queries section must exist."""
        assert "## Example Queries" in content, "Example Queries section not found"

    def test_session_resume_query_present(self, content: str) -> None:
        """\"Why won't session X resume\" example must be present."""
        assert "resume" in content.lower(), "Session resume query not found"

    def test_find_session_query_present(self, content: str) -> None:
        """Find session example must be present."""
        assert "c3843177" in content or "Find session" in content, (
            "Find session example not found"
        )

    def test_rewind_example_present(self, content: str) -> None:
        """Rewind example must be present."""
        assert "Rewind" in content or "rewind" in content, "Rewind example not found"

    def test_example_queries_use_script(self, content: str) -> None:
        """Example Queries should reference the script approach."""
        # Find the example queries section
        eq_start = content.find("## Example Queries")
        if eq_start == -1:
            pytest.skip("Example Queries section not found")
        eq_content = content[eq_start : eq_start + 2000]
        # Should have script invocations in examples
        assert "$SCRIPT" in eq_content or "amplifier-session.py" in eq_content, (
            "Script invocations not found in Example Queries section"
        )


# ── Test 10: Valid markdown structure ────────────────────────────────────────


class TestMarkdownValidity:
    """File must be valid markdown with proper frontmatter."""

    def test_frontmatter_present(self, content: str) -> None:
        """File must start with YAML frontmatter."""
        assert content.startswith("---"), "File must start with YAML frontmatter"

    def test_frontmatter_closes(self, content: str) -> None:
        """Frontmatter must be closed with ---."""
        # Find second --- after start
        second_dashes = content.find("---", 3)
        assert second_dashes > 0, "Frontmatter closing --- not found"

    def test_agent_name_in_frontmatter(self, content: str) -> None:
        """Agent name must be in frontmatter."""
        assert "session-analyst" in content[:500], (
            "session-analyst name not in frontmatter"
        )

    def test_attribution_rule_preserved(self, content: str) -> None:
        """Attribution rule (parent_id / sub-session) must be preserved."""
        assert (
            "parent_id" in content
            or "parent chain" in content
            or "Attribution" in content
        ), "Attribution rule not preserved"


# ── Test 11: the self-delegation IDENTITY NOTICE is dead and stays deleted ──


class TestNoSelfDelegationNotice:
    """session-analyst cannot delegate, so it needs no "don't delegate" notice.

    The notice warned the agent not to delegate to itself. That is unreachable:
    foundation's own composition strips `tool-delegate` from every spawned agent
    (behaviors/agents.yaml, `settings.exclude_tools`), and session-analyst does
    not re-declare `tool-delegate` in its own `tools:` block -- which is the only
    carve-out that would restore it (see amplifier_app_cli/session_spawner.py's
    `_filter_tools`, "explicit declarations always honored").

    These two tests are the measurement that justified deleting the notice. If
    either invariant ever changes, the notice becomes live again and this fails.
    """

    def test_foundation_composition_excludes_delegate(self) -> None:
        """behaviors/agents.yaml must exclude tool-delegate from spawned agents."""
        assert AGENTS_BEHAVIOR_FILE.exists(), (
            f"agents behavior not found: {AGENTS_BEHAVIOR_FILE}"
        )
        behavior = AGENTS_BEHAVIOR_FILE.read_text(encoding="utf-8")
        assert re.search(r"exclude_tools:\s*\[[^\]]*tool-delegate", behavior), (
            "behaviors/agents.yaml no longer excludes tool-delegate -- spawned "
            "agents can delegate again, so the self-delegation notice removed "
            "from session-analyst.md may need to come back"
        )

    def test_agent_does_not_redeclare_delegate(self, content: str) -> None:
        """session-analyst must not re-declare tool-delegate in its own tools."""
        frontmatter = content.split("---", 2)[1]
        assert "tool-delegate" not in frontmatter, (
            "session-analyst re-declares tool-delegate, which overrides the "
            "exclusion -- it can delegate again, so the self-delegation notice "
            "may need to come back"
        )

    def test_identity_notice_absent(self, content: str) -> None:
        """The dead IDENTITY NOTICE must not be reintroduced."""
        assert "IDENTITY NOTICE" not in content, (
            "The IDENTITY NOTICE is dead text -- see this class's docstring"
        )


# ── Test 12: the events.jsonl rule is stated once, not five times ──


class TestEventsJsonlRuleStatedOnce:
    """One authoritative warning; everything else points at it.

    The rule was previously restated five times (top warning, Storage Locations,
    Operating Principles, Deep Event Analysis, Important Constraints). Repetition
    is paid on every spawn and gives the model five near-identical rules to
    reconcile instead of one.
    """

    def test_single_never_do_this_block(self, content: str) -> None:
        """Only one 'NEVER DO THIS' command block may exist."""
        occurrences = content.count("NEVER DO THIS")
        assert occurrences == 1, (
            f"'NEVER DO THIS' appears {occurrences} times; the events.jsonl rule "
            f"must be stated exactly once"
        )

    def test_single_safe_patterns_block(self, content: str) -> None:
        """Only one 'ALWAYS DO THIS' safe-pattern block may exist."""
        occurrences = content.count("ALWAYS DO THIS")
        assert occurrences == 1, (
            f"'ALWAYS DO THIS' appears {occurrences} times; the safe-pattern list "
            f"must be stated exactly once"
        )

    def test_restatements_point_back(self, content: str) -> None:
        """Sections that mention the rule must point at the one statement."""
        assert content.count("CRITICAL warning at the top of this file") >= 3, (
            "Sections that used to restate the events.jsonl rule must instead "
            "point back at the single CRITICAL warning"
        )
