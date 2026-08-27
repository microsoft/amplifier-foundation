"""Pure-function tests for DelegateTool._parse_return_contract().

Covers the structured delegation return contract (openai_improvement-q71):
a sub-agent may append a fenced ```json block to its normal prose response,
which this method parses tolerantly into a ``contract`` dict. See the spec
(§10.2) for the exact algorithm this pins.

These tests exercise the parser directly -- no coordinator, no spawn/resume
plumbing -- since _parse_return_contract is documented as a pure function of
(response, self.return_contract_enabled, self.return_contract_strip_block).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from amplifier_module_tool_delegate import DelegateTool

# =============================================================================
# Helpers
# =============================================================================


def _make_tool(*, enabled: bool = True, strip_block: bool = True) -> DelegateTool:
    """Create a DelegateTool with only the return_contract feature configured.

    No coordinator interaction is needed for these parser-only tests, so a
    bare MagicMock is sufficient -- _parse_return_contract never touches it.
    """
    coordinator = MagicMock()
    config = {
        "features": {
            "return_contract": {
                "enabled": enabled,
                "strip_block": strip_block,
            }
        },
        "settings": {"exclude_tools": []},
    }
    return DelegateTool(coordinator, config)


# =============================================================================
# Tests: conformant parsing
# =============================================================================


class TestConformantParsing:
    @pytest.mark.asyncio
    async def test_well_formed_block_after_prose(self):
        """A well-formed block after prose parses conformant, with the block
        stripped from the cleaned response."""
        tool = _make_tool()
        response = (
            "Here is my analysis of the issue.\n\n"
            "```json\n"
            '{"summary": "Found it.", '
            '"findings": [{"claim": "X is broken", "evidence": "foo.py:12", "confidence": "high"}], '
            '"not_covered": [], "artifacts": []}\n'
            "```"
        )

        contract, cleaned = tool._parse_return_contract(response)

        assert contract["conformant"] is True
        assert contract["reason"] is None
        assert contract["summary"] == "Found it."
        assert contract["findings"] == [
            {"claim": "X is broken", "evidence": "foo.py:12", "confidence": "high"}
        ]
        assert "```json" not in cleaned
        assert "Here is my analysis of the issue." in cleaned

    def test_two_json_blocks_last_one_wins(self):
        """When two fenced json blocks are present, the LAST one is used."""
        tool = _make_tool()
        response = (
            "```json\n"
            '{"findings": [{"claim": "first block, should be ignored"}]}\n'
            "```\n\n"
            "More prose in between.\n\n"
            "```json\n"
            '{"findings": [{"claim": "second block, should win"}]}\n'
            "```"
        )

        contract, _cleaned = tool._parse_return_contract(response)

        assert contract["conformant"] is True
        assert len(contract["findings"]) == 1
        assert contract["findings"][0]["claim"] == "second block, should win"

    def test_case_insensitive_fence_info_string(self):
        """```JSON (any case) is accepted, not just ```json."""
        tool = _make_tool()
        response = '```JSON\n{"findings": []}\n```'

        contract, _cleaned = tool._parse_return_contract(response)

        assert contract["conformant"] is True


# =============================================================================
# Tests: non-conformant fallbacks (never fail the delegation)
# =============================================================================


class TestNonConformantFallbacks:
    def test_no_block_found(self):
        """No fenced json block -> conformant False, byte-identical response."""
        tool = _make_tool()
        response = "Just plain prose, no structured block at all."

        contract, cleaned = tool._parse_return_contract(response)

        assert contract["conformant"] is False
        assert "no fenced json block" in contract["reason"]
        assert cleaned == response

    def test_malformed_json(self):
        """Malformed JSON inside the fence -> conformant False, byte-identical."""
        tool = _make_tool()
        response = "Some prose.\n\n```json\n{not valid json,,,\n```"

        contract, cleaned = tool._parse_return_contract(response)

        assert contract["conformant"] is False
        assert "json parse failed" in contract["reason"]
        assert cleaned == response

    def test_block_is_array_not_object(self):
        """A fenced block that parses to a JSON array (not object) is rejected
        with a reason naming the problem."""
        tool = _make_tool()
        response = "```json\n[1, 2, 3]\n```"

        contract, cleaned = tool._parse_return_contract(response)

        assert contract["conformant"] is False
        assert "not a JSON object" in contract["reason"]
        assert cleaned == response

    def test_findings_missing(self):
        """A well-formed object lacking the required 'findings' key is
        non-conformant."""
        tool = _make_tool()
        response = '```json\n{"summary": "no findings key here"}\n```'

        contract, _cleaned = tool._parse_return_contract(response)

        assert contract["conformant"] is False
        assert "findings" in contract["reason"]

    def test_findings_not_a_list(self):
        """'findings' present but not a list is also non-conformant."""
        tool = _make_tool()
        response = '```json\n{"findings": "not-a-list"}\n```'

        contract, _cleaned = tool._parse_return_contract(response)

        assert contract["conformant"] is False


# =============================================================================
# Tests: tolerant normalization (never reject a partially-good return)
# =============================================================================


class TestTolerantNormalization:
    def test_finding_missing_claim_is_dropped_others_kept(self):
        """A finding entry lacking 'claim' is dropped; other entries survive
        and the overall contract is still conformant."""
        tool = _make_tool()
        response = (
            "```json\n"
            '{"findings": ['
            '{"evidence": "no claim here"}, '
            '{"claim": "valid claim", "evidence": "bar.py:5"}'
            "]}\n"
            "```"
        )

        contract, _cleaned = tool._parse_return_contract(response)

        assert contract["conformant"] is True
        assert len(contract["findings"]) == 1
        assert contract["findings"][0]["claim"] == "valid claim"

    def test_unknown_confidence_normalized_to_unspecified(self):
        """confidence: "certain" (not in the enum) normalizes to "unspecified"
        rather than being rejected."""
        tool = _make_tool()
        response = (
            '```json\n{"findings": [{"claim": "x", "confidence": "certain"}]}\n```'
        )

        contract, _cleaned = tool._parse_return_contract(response)

        assert contract["conformant"] is True
        assert contract["findings"][0]["confidence"] == "unspecified"

    def test_missing_evidence_defaults_to_empty_string(self):
        tool = _make_tool()
        response = '```json\n{"findings": [{"claim": "x"}]}\n```'

        contract, _cleaned = tool._parse_return_contract(response)

        assert contract["findings"][0]["evidence"] == ""

    def test_not_covered_present_surfaced_verbatim(self):
        tool = _make_tool()
        response = '```json\n{"findings": [], "not_covered": ["auth flow", "rate limits"]}\n```'

        contract, _cleaned = tool._parse_return_contract(response)

        assert contract["not_covered"] == ["auth flow", "rate limits"]

    def test_not_covered_malformed_items_dropped(self):
        """Non-string items in not_covered are dropped, not fatal."""
        tool = _make_tool()
        response = '```json\n{"findings": [], "not_covered": ["ok", 42, null]}\n```'

        contract, _cleaned = tool._parse_return_contract(response)

        assert contract["conformant"] is True
        assert contract["not_covered"] == ["ok"]

    def test_artifacts_present_surfaced(self):
        tool = _make_tool()
        response = (
            "```json\n"
            '{"findings": [], "artifacts": [{"path": "src/foo.py", "description": "added retry"}]}\n'
            "```"
        )

        contract, _cleaned = tool._parse_return_contract(response)

        assert contract["artifacts"] == [
            {"path": "src/foo.py", "description": "added retry"}
        ]

    def test_artifact_missing_path_dropped(self):
        tool = _make_tool()
        response = (
            '```json\n{"findings": [], "artifacts": [{"description": "no path"}]}\n```'
        )

        contract, _cleaned = tool._parse_return_contract(response)

        assert contract["conformant"] is True
        assert contract["artifacts"] == []


# =============================================================================
# Tests: strip_block toggle
# =============================================================================


class TestStripBlockToggle:
    def test_strip_block_false_leaves_response_byte_identical(self):
        tool = _make_tool(strip_block=False)
        response = 'Some prose.\n\n```json\n{"findings": []}\n```'

        contract, cleaned = tool._parse_return_contract(response)

        assert contract["conformant"] is True
        assert cleaned == response

    def test_strip_block_true_removes_the_block(self):
        tool = _make_tool(strip_block=True)
        response = 'Some prose.\n\n```json\n{"findings": []}\n```'

        _contract, cleaned = tool._parse_return_contract(response)

        assert "```" not in cleaned
        assert "Some prose." in cleaned


# =============================================================================
# Tests: pathological input -- must never raise
# =============================================================================


class TestPathologicalInputNeverRaises:
    def test_unterminated_fence(self):
        tool = _make_tool()
        response = '```json\n{"findings": []'  # no closing fence at all

        contract, cleaned = tool._parse_return_contract(response)

        assert contract["conformant"] is False
        assert cleaned == response

    def test_nested_fences_do_not_crash(self):
        tool = _make_tool()
        response = (
            "```json\n"
            '{"findings": [{"claim": "contains a nested fence marker below"}]}\n'
            "```\n"
            "```\n"
            "some other unrelated fenced block\n"
            "```"
        )

        # Must not raise regardless of what it parses to.
        contract, _cleaned = tool._parse_return_contract(response)
        assert contract["conformant"] in (True, False)

    def test_large_1mb_string_does_not_raise(self):
        tool = _make_tool()
        padding = "x" * (1024 * 1024)
        response = f'{padding}\n```json\n{{"findings": []}}\n```'

        contract, _cleaned = tool._parse_return_contract(response)

        assert contract["conformant"] is True

    def test_empty_string_does_not_raise(self):
        tool = _make_tool()

        contract, cleaned = tool._parse_return_contract("")

        assert contract["conformant"] is False
        assert cleaned == ""

    def test_non_dict_findings_items_do_not_raise(self):
        tool = _make_tool()
        response = (
            '```json\n{"findings": ["not-a-dict", 42, null, {"claim": "ok"}]}\n```'
        )

        contract, _cleaned = tool._parse_return_contract(response)

        assert contract["conformant"] is True
        assert len(contract["findings"]) == 1
        assert contract["findings"][0]["claim"] == "ok"


# =============================================================================
# Tests: disabled feature short-circuits parsing entirely
# =============================================================================


class TestDisabledFeature:
    def test_disabled_returns_none_conformant_and_untouched_response(self):
        tool = _make_tool(enabled=False)
        response = 'Some prose.\n\n```json\n{"findings": [{"claim": "x"}]}\n```'

        contract, cleaned = tool._parse_return_contract(response)

        assert contract["conformant"] is None
        assert contract["findings"] == []
        assert cleaned == response
