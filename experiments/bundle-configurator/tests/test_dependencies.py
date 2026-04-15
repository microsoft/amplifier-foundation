"""Tests for dependencies.py — provenance-aware dependency validation.

13 tests covering:
  - REQUIRED_PARTS contents (3)
  - validate_provenance errors (3)
  - validate_provenance warnings (4)
  - validate_provenance return type (1)
  - legacy Bundle-based API smoke tests (2)
"""

from __future__ import annotations

from amplifier_foundation.bundle import Bundle

from amplifier_configurator.dependencies import (
    REQUIRED_PARTS,
    validate_provenance,
)
from amplifier_configurator.models import (
    PartKind,
    ProvenanceMap,
    TrackedPart,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_part(name: str, kind: PartKind = PartKind.TOOL) -> TrackedPart:
    """Create a minimal TrackedPart for testing."""
    return TrackedPart(
        kind=kind,
        name=name,
        source_behavior=None,
        tokens=0,
        config={},
        namespace_path=None,
    )


def _pmap(parts: list[TrackedPart]) -> ProvenanceMap:
    """Create a minimal ProvenanceMap wrapping the given parts."""
    return ProvenanceMap(
        root_name="test",
        root_uri="test",
        root_instruction=None,
        root_instruction_tokens=0,
        behaviors={},
        root_parts=tuple(parts),
        all_parts=tuple(parts),
        composed_bundle=Bundle(name="test"),
        include_order=(),
        session_config={},
        spawn_config=None,
        _raw_bundles={},
    )


# ---------------------------------------------------------------------------
# REQUIRED_PARTS contents
# ---------------------------------------------------------------------------


def test_required_parts_contains_tool_bash() -> None:
    """REQUIRED_PARTS must include tool-bash."""
    assert "tool-bash" in REQUIRED_PARTS


def test_required_parts_contains_tool_filesystem() -> None:
    """REQUIRED_PARTS must include tool-filesystem."""
    assert "tool-filesystem" in REQUIRED_PARTS


def test_required_parts_contains_tool_search() -> None:
    """REQUIRED_PARTS must include tool-search."""
    assert "tool-search" in REQUIRED_PARTS


# ---------------------------------------------------------------------------
# validate_provenance — errors (required parts)
# ---------------------------------------------------------------------------


def test_validate_all_required_present_no_errors() -> None:
    """validate_provenance returns no errors when all required parts present."""
    pmap = _pmap(
        [
            _make_part("tool-bash"),
            _make_part("tool-filesystem"),
            _make_part("tool-search"),
        ]
    )
    errors, _ = validate_provenance(pmap)
    assert errors == []


def test_validate_missing_tool_bash_produces_error() -> None:
    """validate_provenance returns an error when tool-bash is absent."""
    pmap = _pmap(
        [
            _make_part("tool-filesystem"),
            _make_part("tool-search"),
        ]
    )
    errors, _ = validate_provenance(pmap)
    assert any("tool-bash" in e for e in errors)


def test_validate_empty_bundle_errors_count_equals_required_parts() -> None:
    """validate_provenance on an empty bundle produces one error per REQUIRED_PARTS entry."""
    pmap = _pmap([])
    errors, _ = validate_provenance(pmap)
    assert len(errors) == len(REQUIRED_PARTS)


# ---------------------------------------------------------------------------
# validate_provenance — warnings (dependencies)
# ---------------------------------------------------------------------------


def test_validate_hooks_todo_reminder_without_tool_todo_produces_warning() -> None:
    """hooks-todo-reminder without tool-todo produces a warning."""
    pmap = _pmap(
        [
            _make_part("tool-bash"),
            _make_part("tool-filesystem"),
            _make_part("tool-search"),
            _make_part("hooks-todo-reminder", PartKind.HOOK),
        ]
    )
    _, warnings = validate_provenance(pmap)
    assert any("hooks-todo-reminder" in w and "tool-todo" in w for w in warnings)


def test_validate_agents_without_delegate_produces_warning() -> None:
    """An agent without tool-delegate present produces a warning."""
    pmap = _pmap(
        [
            _make_part("tool-bash"),
            _make_part("tool-filesystem"),
            _make_part("tool-search"),
            _make_part("my-agent", PartKind.AGENT),
        ]
    )
    _, warnings = validate_provenance(pmap)
    assert any("tool-delegate" in w for w in warnings)


def test_validate_agents_with_delegate_no_warning() -> None:
    """Agents with tool-delegate present produce no tool-delegate warning."""
    pmap = _pmap(
        [
            _make_part("tool-bash"),
            _make_part("tool-filesystem"),
            _make_part("tool-search"),
            _make_part("tool-delegate"),
            _make_part("my-agent", PartKind.AGENT),
        ]
    )
    _, warnings = validate_provenance(pmap)
    assert not any("Agents require tool-delegate" in w for w in warnings)


def test_validate_clean_bundle_no_warnings() -> None:
    """A bundle with only the three required tools produces no warnings."""
    pmap = _pmap(
        [
            _make_part("tool-bash"),
            _make_part("tool-filesystem"),
            _make_part("tool-search"),
        ]
    )
    _, warnings = validate_provenance(pmap)
    assert warnings == []


# ---------------------------------------------------------------------------
# validate_provenance — return type
# ---------------------------------------------------------------------------


def test_validate_returns_tuple_of_length_2() -> None:
    """validate_provenance returns a 2-tuple (errors, warnings)."""
    pmap = _pmap([])
    result = validate_provenance(pmap)
    assert isinstance(result, tuple)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# Legacy Bundle-based API (smoke tests)
# ---------------------------------------------------------------------------


def test_legacy_get_all_part_names_returns_set() -> None:
    """get_all_part_names(bundle) returns a set containing tool names."""
    from amplifier_configurator.dependencies import get_all_part_names

    bundle = Bundle(
        name="legacy-test",
        tools=[{"module": "tool-bash"}, {"module": "tool-filesystem"}],
    )
    result = get_all_part_names(bundle)
    assert isinstance(result, set)
    assert "tool-bash" in result
    assert "tool-filesystem" in result


def test_legacy_validate_returns_two_tuple() -> None:
    """validate(bundle) returns a 2-tuple of (errors, warnings)."""
    from amplifier_configurator.dependencies import validate

    bundle = Bundle(
        name="legacy-test",
        tools=[
            {"module": "tool-bash"},
            {"module": "tool-filesystem"},
            {"module": "tool-search"},
        ],
    )
    result = validate(bundle)
    assert isinstance(result, tuple)
    assert len(result) == 2
    errors, warnings = result
    # All required parts present — no errors expected
    assert errors == []
