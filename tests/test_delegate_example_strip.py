"""Pins model_performance-6f80: the delegate catalog declines to render examples.

WHY A RENDERER DEFENCE AND NOT ONLY CONTENT EDITS
-------------------------------------------------
``context/shared/description-authoring-principles.md`` V3 sets the policy:
zero ``<example>`` blocks, zero ``<commentary>`` tags, in any description
surface. PR #341 set it; a sweep a year later still found example blocks in
six repos and fixed them by hand. Content hygiene fixes *today's* catalog and
holds exactly until someone composes a bundle this program does not own --
``DelegateTool.description`` renders whatever description the mounted bundles
supply, so one third-party bundle puts multi-example descriptions back into
every turn of every session with no PR of ours involved.

So the strip happens at RENDER time, not at load time. The description on
disk stays whatever its author wrote; we decline to pay for it on the wire.

WHAT THESE TESTS GUARANTEE
--------------------------
1. FAIL-BEFORE (``TestExampleBlocksAreStripped``): a fixture agent whose
   ``description`` carries ``<example>``/``<commentary>`` renders a catalog
   entry that does NOT contain them. At tool-delegate main this class fails;
   on the branch it passes.
2. FIDELITY (``TestNothingOutsideTheBlocksIsConsumed``): everything outside
   the blocks -- the trigger sentence, USE WHEN / DO NOT USE WHEN, every
   routing fact, and any text sharing a line with the block -- survives
   byte-for-byte. A regex that eats a paragraph past the closing tag is the
   failure mode this class exists to catch.
3. OBSERVABILITY (``TestStrippedAgentsAreNamedInDebugLog``): a debug line
   NAMES each stripped agent. Silent stripping is how a bundle author whose
   examples vanish is left with no way to find out why.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from amplifier_module_tool_delegate import DelegateTool

# =============================================================================
# Helpers
# =============================================================================


def _make_tool(agents: dict[str, dict]) -> DelegateTool:
    """Construct a DelegateTool whose mount plan carries ``agents``.

    Only the config surface ``_get_agent_list`` reads is populated; the spawn
    machinery is never exercised here (these tests only render the catalog).
    """
    coordinator = MagicMock()
    coordinator.session_id = "parent-session-6f80"
    coordinator.config = {"agents": agents}
    coordinator.session_state = {}
    coordinator._tool_dispatch_context = {}
    coordinator.get_capability = lambda name: None
    coordinator.get = MagicMock(return_value=None)

    config: dict = {"features": {}, "settings": {"exclude_tools": []}}
    return DelegateTool(coordinator, config)


def _catalog_entry(tool: DelegateTool, agent_name: str) -> str:
    """The rendered catalog line(s) for one agent, from the tool description.

    The catalog is ``"  - <name>: <description>"`` per agent, joined by
    newlines, under an ``Available agents:`` heading. Slicing from this
    agent's own marker to the next agent's marker (or end of string) keeps
    the assertion honest about WHICH agent's text is being inspected.
    """
    description = tool.description
    start = description.index(f"  - {agent_name}: ")
    tail = description[start:]
    return tail


# The fixture description deliberately mixes every shape the corpus actually
# ships: an example block on its own lines, a nested <commentary>, a bare
# <commentary> block outside any example, and prose on both sides of each.
FIXTURE_DESCRIPTION = """Survey a codebase and report what is there.

USE WHEN: understanding code that spans multiple files.
DO NOT USE WHEN: you need one known file -- read it directly.

<example>
user: 'How does auth work here?'
assistant: 'I'll use the explorer to map the auth flow.'
<commentary>
Multi-file question, so the explorer is the right call.
</commentary>
</example>

Authoritative on: repository surveys, module maps.

<commentary>
A stray commentary block outside any example is still a defect.
</commentary>

FINAL LINE SURVIVES."""


# =============================================================================
# 1. FAIL-BEFORE -- the blocks do not reach the rendered catalog
# =============================================================================


class TestExampleBlocksAreStripped:
    """At main these assertions fail; on the branch they pass."""

    def test_example_block_absent_from_rendered_entry(self):
        tool = _make_tool({"fixture:explorer": {"description": FIXTURE_DESCRIPTION}})

        entry = _catalog_entry(tool, "fixture:explorer")

        assert "<example>" not in entry, (
            "The rendered catalog entry still carries an <example> block; "
            "the strip did not run at render time."
        )
        assert "</example>" not in entry
        assert "<commentary>" not in entry, (
            "The rendered catalog entry still carries a <commentary> block."
        )
        assert "</commentary>" not in entry

    def test_example_body_text_absent_from_rendered_entry(self):
        """Not just the tags -- the example's PAYLOAD is what costs tokens."""
        tool = _make_tool({"fixture:explorer": {"description": FIXTURE_DESCRIPTION}})

        entry = _catalog_entry(tool, "fixture:explorer")

        assert "How does auth work here?" not in entry
        assert "Multi-file question" not in entry
        assert "A stray commentary block" not in entry

    def test_description_on_disk_is_not_mutated(self):
        """RENDER-time only: the config the bundle supplied is untouched."""
        agents = {"fixture:explorer": {"description": FIXTURE_DESCRIPTION}}
        tool = _make_tool(agents)

        _ = tool.description

        assert agents["fixture:explorer"]["description"] == FIXTURE_DESCRIPTION, (
            "The strip rewrote the mounted config. It must render a stripped "
            "copy and leave the bundle author's own text alone."
        )

    def test_rendering_twice_is_stable(self):
        tool = _make_tool({"fixture:explorer": {"description": FIXTURE_DESCRIPTION}})

        assert tool.description == tool.description


# =============================================================================
# 2. FIDELITY -- nothing outside the blocks is consumed
# =============================================================================


class TestNothingOutsideTheBlocksIsConsumed:
    """The boundary is the closing tag. Not one byte past it."""

    def test_all_surrounding_prose_survives(self):
        tool = _make_tool({"fixture:explorer": {"description": FIXTURE_DESCRIPTION}})

        entry = _catalog_entry(tool, "fixture:explorer")

        for surviving in (
            "Survey a codebase and report what is there.",
            "USE WHEN: understanding code that spans multiple files.",
            "DO NOT USE WHEN: you need one known file -- read it directly.",
            "Authoritative on: repository surveys, module maps.",
            "FINAL LINE SURVIVES.",
        ):
            assert surviving in entry, (
                f"Text outside the example blocks was consumed: {surviving!r}"
            )

    def test_text_sharing_a_line_with_the_block_survives(self):
        """A block inline in a sentence loses the block, keeps the sentence."""
        inline = "BEFORE <example>eaten</example> AFTER"
        tool = _make_tool({"fixture:inline": {"description": inline}})

        entry = _catalog_entry(tool, "fixture:inline")

        assert "BEFORE " in entry
        assert " AFTER" in entry
        assert "eaten" not in entry

    def test_a_description_with_no_blocks_is_byte_identical(self):
        """The common case must be a pure pass-through."""
        clean = (
            "Design, architecture, planning, and code review.\n"
            "USE WHEN: requirements need analysis.\n"
            "DO NOT USE WHEN: a clear spec already exists.\n"
        )
        tool = _make_tool({"fixture:architect": {"description": clean}})

        entry = _catalog_entry(tool, "fixture:architect")

        assert entry == f"  - fixture:architect: {clean}", (
            "A description carrying no example blocks was altered anyway -- "
            "the strip is reflowing or normalising, which it must not do."
        )

    def test_unpaired_opening_tag_does_not_eat_the_rest(self):
        """An unclosed <example> is a defect in the bundle, not a licence to
        truncate. The renderer leaves it alone rather than swallowing every
        routing fact that follows it."""
        broken = "TRIGGER.\n<example>\nnever closed\nUSE WHEN: this still matters."
        tool = _make_tool({"fixture:broken": {"description": broken}})

        entry = _catalog_entry(tool, "fixture:broken")

        assert "USE WHEN: this still matters." in entry
        assert "TRIGGER." in entry

    def test_other_agents_are_untouched(self):
        clean = "Plain description, no blocks."
        tool = _make_tool(
            {
                "fixture:aaa": {"description": clean},
                "fixture:zzz": {"description": FIXTURE_DESCRIPTION},
            }
        )

        description = tool.description

        assert f"  - fixture:aaa: {clean}" in description


# =============================================================================
# 3. OBSERVABILITY -- the strip names who it stripped
# =============================================================================


class TestStrippedAgentsAreNamedInDebugLog:
    """A count is not enough: a bundle author needs to see their own name."""

    def test_debug_line_names_the_stripped_agent(self, caplog):
        tool = _make_tool(
            {
                "fixture:clean": {"description": "No blocks here."},
                "fixture:explorer": {"description": FIXTURE_DESCRIPTION},
            }
        )

        with caplog.at_level(logging.DEBUG, logger="amplifier_module_tool_delegate"):
            _ = tool.description

        stripped_records = [
            record.getMessage()
            for record in caplog.records
            if "fixture:explorer" in record.getMessage()
        ]
        assert stripped_records, (
            "No debug record named the stripped agent. Silent stripping leaves "
            "a bundle author with no way to discover why their examples vanished."
        )
        message = stripped_records[0]
        assert "example" in message.lower()
        assert "fixture:clean" not in message, (
            "The log named an agent that was not stripped."
        )

    def test_no_log_line_when_nothing_was_stripped(self, caplog):
        tool = _make_tool({"fixture:clean": {"description": "No blocks here."}})

        with caplog.at_level(logging.DEBUG, logger="amplifier_module_tool_delegate"):
            _ = tool.description

        assert not [
            record
            for record in caplog.records
            if "stripped" in record.getMessage().lower()
        ], "A no-op strip must not log."
