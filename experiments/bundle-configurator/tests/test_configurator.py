"""Tests for BundleConfigurator — wraps ProvenanceMap with query API.

41 tests covering:
  - constructor / properties (2)
  - list_behaviors (3)
  - list_parts (3)
  - total_tokens (2)
  - tokens_by_behavior (2)
  - get_behavior (2)
  - get_part (2)
  - remove_behavior (7)
  - remove_part (5)
  - add_behavior (4)
  - diff (4)
  - validate (2)
  - save (3)
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pytest_mock import MockerFixture

from amplifier_foundation.bundle import Bundle

from amplifier_configurator import BundleConfigurator, DependencyError
from amplifier_configurator.models import (
    BehaviorInfo,
    BundleDiff,
    LoadError,
    PartKind,
    ProvenanceMap,
    TrackedPart,
)


# ---------------------------------------------------------------------------
# constructor / properties
# ---------------------------------------------------------------------------


def test_constructor_takes_provenance_map(simple_provenance_map: ProvenanceMap) -> None:
    """BundleConfigurator accepts a ProvenanceMap and exposes it via .provenance."""
    cfg = BundleConfigurator(simple_provenance_map)
    assert cfg.provenance is simple_provenance_map


def test_bundle_property_returns_composed_bundle(
    simple_provenance_map: ProvenanceMap,
) -> None:
    """cfg.bundle returns the composed_bundle from the ProvenanceMap."""
    cfg = BundleConfigurator(simple_provenance_map)
    assert cfg.bundle is simple_provenance_map.composed_bundle


# ---------------------------------------------------------------------------
# list_behaviors
# ---------------------------------------------------------------------------


def test_list_behaviors_returns_all(simple_provenance_map: ProvenanceMap) -> None:
    """list_behaviors() returns all behaviors (leaf + mid = 2)."""
    cfg = BundleConfigurator(simple_provenance_map)
    behaviors = cfg.list_behaviors()
    assert len(behaviors) == 2
    names = {b.name for b in behaviors}
    assert names == {"leaf-behavior", "mid-behavior"}


def test_list_behaviors_sorted_by_include_order(
    simple_provenance_map: ProvenanceMap,
) -> None:
    """list_behaviors() is sorted by include_order: leaf first (depth=1), mid second (depth=0)."""
    cfg = BundleConfigurator(simple_provenance_map)
    behaviors = cfg.list_behaviors()
    # include_order = ("leaf-behavior-uri", "mid-behavior-uri") — leaf is first
    assert behaviors[0].name == "leaf-behavior"
    assert behaviors[1].name == "mid-behavior"


def test_list_behaviors_returns_behavior_info(
    simple_provenance_map: ProvenanceMap,
) -> None:
    """Every item returned by list_behaviors() is a BehaviorInfo instance."""
    cfg = BundleConfigurator(simple_provenance_map)
    behaviors = cfg.list_behaviors()
    for b in behaviors:
        assert isinstance(b, BehaviorInfo)


# ---------------------------------------------------------------------------
# list_parts
# ---------------------------------------------------------------------------


def test_list_parts_all(simple_provenance_map: ProvenanceMap) -> None:
    """list_parts() with no filter returns all parts from all_parts."""
    cfg = BundleConfigurator(simple_provenance_map)
    parts = cfg.list_parts()
    assert len(parts) == len(simple_provenance_map.all_parts)


def test_list_parts_filter_by_kind(simple_provenance_map: ProvenanceMap) -> None:
    """list_parts(PartKind.TOOL) returns only TOOL parts."""
    cfg = BundleConfigurator(simple_provenance_map)
    tool_parts = cfg.list_parts(PartKind.TOOL)
    assert len(tool_parts) > 0
    assert all(p.kind == PartKind.TOOL for p in tool_parts)


def test_list_parts_filter_context(simple_provenance_map: ProvenanceMap) -> None:
    """list_parts(PartKind.CONTEXT) returns only the single context part."""
    cfg = BundleConfigurator(simple_provenance_map)
    ctx_parts = cfg.list_parts(PartKind.CONTEXT)
    assert len(ctx_parts) == 1
    assert ctx_parts[0].kind == PartKind.CONTEXT
    assert ctx_parts[0].name == "leaf-behavior:lsp-config"


# ---------------------------------------------------------------------------
# total_tokens
# ---------------------------------------------------------------------------


def test_total_tokens_sum_103_plus_7(simple_provenance_map: ProvenanceMap) -> None:
    """total_tokens() = sum of all part tokens (103) + root_instruction_tokens (7) = 110."""
    cfg = BundleConfigurator(simple_provenance_map)
    assert cfg.total_tokens() == 110


def test_total_tokens_includes_root_instruction(
    simple_provenance_map: ProvenanceMap,
) -> None:
    """total_tokens() decreases by exactly 7 when root_instruction_tokens is zeroed."""
    cfg = BundleConfigurator(simple_provenance_map)
    full = cfg.total_tokens()

    pmap_no_instr = dataclasses.replace(
        simple_provenance_map, root_instruction_tokens=0
    )
    cfg_no_instr = BundleConfigurator(pmap_no_instr)
    assert cfg_no_instr.total_tokens() == full - 7


# ---------------------------------------------------------------------------
# tokens_by_behavior
# ---------------------------------------------------------------------------


def test_tokens_by_behavior_values(simple_provenance_map: ProvenanceMap) -> None:
    """tokens_by_behavior() has correct per-behavior and <root> values."""
    cfg = BundleConfigurator(simple_provenance_map)
    tbb = cfg.tokens_by_behavior()
    assert tbb["leaf-behavior"] == 103
    assert tbb["<root>"] == 7


def test_tokens_by_behavior_sorted_descending(
    simple_provenance_map: ProvenanceMap,
) -> None:
    """tokens_by_behavior() is sorted in descending order of token count."""
    cfg = BundleConfigurator(simple_provenance_map)
    tbb = cfg.tokens_by_behavior()
    values = list(tbb.values())
    assert values == sorted(values, reverse=True)


# ---------------------------------------------------------------------------
# get_behavior
# ---------------------------------------------------------------------------


def test_get_behavior(simple_provenance_map: ProvenanceMap) -> None:
    """get_behavior(name) returns the matching BehaviorInfo."""
    cfg = BundleConfigurator(simple_provenance_map)
    beh = cfg.get_behavior("leaf-behavior")
    assert isinstance(beh, BehaviorInfo)
    assert beh.name == "leaf-behavior"


def test_get_behavior_not_found(simple_provenance_map: ProvenanceMap) -> None:
    """get_behavior(name) raises KeyError when behavior is not present."""
    cfg = BundleConfigurator(simple_provenance_map)
    with pytest.raises(KeyError):
        cfg.get_behavior("nonexistent-behavior")


# ---------------------------------------------------------------------------
# get_part
# ---------------------------------------------------------------------------


def test_get_part(simple_provenance_map: ProvenanceMap) -> None:
    """get_part(kind, name) returns the matching TrackedPart."""
    cfg = BundleConfigurator(simple_provenance_map)
    part = cfg.get_part(PartKind.CONTEXT, "leaf-behavior:lsp-config")
    assert isinstance(part, TrackedPart)
    assert part.kind == PartKind.CONTEXT
    assert part.name == "leaf-behavior:lsp-config"


def test_get_part_not_found(simple_provenance_map: ProvenanceMap) -> None:
    """get_part(kind, name) raises KeyError when part is not present."""
    cfg = BundleConfigurator(simple_provenance_map)
    with pytest.raises(KeyError):
        cfg.get_part(PartKind.TOOL, "nonexistent-tool")


# ---------------------------------------------------------------------------
# remove_behavior
# ---------------------------------------------------------------------------


def test_remove_behavior_returns_new_instance(
    simple_provenance_map: ProvenanceMap,
) -> None:
    """remove_behavior returns a new BundleConfigurator, not the same instance."""
    cfg = BundleConfigurator(simple_provenance_map)
    new_cfg = cfg.remove_behavior("leaf-behavior")
    assert new_cfg is not cfg


def test_remove_behavior_original_unchanged(
    simple_provenance_map: ProvenanceMap,
) -> None:
    """Original configurator is not modified after remove_behavior."""
    cfg = BundleConfigurator(simple_provenance_map)
    original_count = len(cfg.list_behaviors())
    cfg.remove_behavior("leaf-behavior")
    assert len(cfg.list_behaviors()) == original_count


def test_remove_behavior_removes_parts(simple_provenance_map: ProvenanceMap) -> None:
    """remove_behavior removes all parts contributed by the removed behavior."""
    cfg = BundleConfigurator(simple_provenance_map)
    new_cfg = cfg.remove_behavior("leaf-behavior")
    part_names = {p.name for p in new_cfg.list_parts()}
    assert "tool-lsp" not in part_names
    assert "leaf-behavior:lsp-config" not in part_names


def test_remove_behavior_raises_key_error_nonexistent(
    simple_provenance_map: ProvenanceMap,
) -> None:
    """remove_behavior raises KeyError when the behavior name is not found."""
    cfg = BundleConfigurator(simple_provenance_map)
    with pytest.raises(KeyError):
        cfg.remove_behavior("nonexistent-behavior")


def test_remove_behavior_preserves_other_behaviors(
    simple_provenance_map: ProvenanceMap,
) -> None:
    """remove_behavior preserves behaviors that were not removed."""
    cfg = BundleConfigurator(simple_provenance_map)
    new_cfg = cfg.remove_behavior("leaf-behavior")
    names = {b.name for b in new_cfg.list_behaviors()}
    assert "mid-behavior" in names
    assert "leaf-behavior" not in names


def test_remove_behavior_reduces_tokens(simple_provenance_map: ProvenanceMap) -> None:
    """remove_behavior reduces total_tokens by the removed behavior's token contribution."""
    cfg = BundleConfigurator(simple_provenance_map)
    original_tokens = cfg.total_tokens()
    new_cfg = cfg.remove_behavior("leaf-behavior")
    # leaf-behavior contributes 103 tokens (the lsp-config context part)
    assert new_cfg.total_tokens() == original_tokens - 103


def test_remove_behavior_mid_orphans_leaf(simple_provenance_map: ProvenanceMap) -> None:
    """Removing mid-behavior also removes leaf-behavior (orphan detection).

    leaf-behavior's include_chain is ('root-bundle', 'mid-behavior', 'leaf-behavior').
    mid-behavior is the only non-root includer of leaf-behavior, so removing
    mid-behavior leaves leaf-behavior without an active parent: it is orphaned
    and must also be removed.
    """
    cfg = BundleConfigurator(simple_provenance_map)
    new_cfg = cfg.remove_behavior("mid-behavior")
    names = {b.name for b in new_cfg.list_behaviors()}
    assert "mid-behavior" not in names
    assert "leaf-behavior" not in names


# ---------------------------------------------------------------------------
# remove_part
# ---------------------------------------------------------------------------


def test_remove_part_removes_specific_tool(
    simple_provenance_map: ProvenanceMap,
) -> None:
    """remove_part removes the specified tool from all parts."""
    cfg = BundleConfigurator(simple_provenance_map)
    new_cfg = cfg.remove_part(PartKind.TOOL, "tool-lsp")
    tool_names = {p.name for p in new_cfg.list_parts(PartKind.TOOL)}
    assert "tool-lsp" not in tool_names


def test_remove_part_original_unchanged(simple_provenance_map: ProvenanceMap) -> None:
    """Original configurator is not modified after remove_part."""
    cfg = BundleConfigurator(simple_provenance_map)
    original_count = len(cfg.list_parts())
    cfg.remove_part(PartKind.TOOL, "tool-lsp")
    assert len(cfg.list_parts()) == original_count


def test_remove_part_raises_key_error_nonexistent(
    simple_provenance_map: ProvenanceMap,
) -> None:
    """remove_part raises KeyError when the (kind, name) pair is not present."""
    cfg = BundleConfigurator(simple_provenance_map)
    with pytest.raises(KeyError):
        cfg.remove_part(PartKind.TOOL, "nonexistent-tool")


def test_remove_part_raises_dependency_error_for_required(
    simple_provenance_map: ProvenanceMap,
) -> None:
    """remove_part raises DependencyError when trying to remove a required part (tool-bash)."""
    cfg = BundleConfigurator(simple_provenance_map)
    with pytest.raises(DependencyError):
        cfg.remove_part(PartKind.TOOL, "tool-bash")


def test_remove_part_removes_context_part(simple_provenance_map: ProvenanceMap) -> None:
    """remove_part removes a context part correctly."""
    cfg = BundleConfigurator(simple_provenance_map)
    new_cfg = cfg.remove_part(PartKind.CONTEXT, "leaf-behavior:lsp-config")
    ctx_parts = new_cfg.list_parts(PartKind.CONTEXT)
    assert not any(p.name == "leaf-behavior:lsp-config" for p in ctx_parts)


# ---------------------------------------------------------------------------
# add_behavior
# ---------------------------------------------------------------------------


async def test_add_behavior_new_parts_appear_in_all_parts(
    simple_provenance_map: ProvenanceMap,
    mocker: MockerFixture,
) -> None:
    """add_behavior: new behavior's parts appear in all_parts after merge."""
    new_part = TrackedPart(
        kind=PartKind.TOOL,
        name="tool-new",
        source_behavior="new-behavior",
        tokens=0,
        config={},
        namespace_path=None,
    )
    new_beh = BehaviorInfo(
        name="new-behavior",
        uri="new-behavior-uri",
        parts=(new_part,),
        raw_parts=(new_part,),
        total_tokens=0,
        depth=0,
        include_chain=("root-bundle", "new-behavior"),
    )
    mocker.patch(
        "amplifier_configurator.provenance._load_behavior_tree",
        new=AsyncMock(
            return_value=([new_beh], {"new-behavior": Bundle(name="new-behavior")})
        ),
    )

    cfg = BundleConfigurator(simple_provenance_map)
    new_cfg = await cfg.add_behavior("new-behavior-uri")

    part_names = {p.name for p in new_cfg.list_parts()}
    assert "tool-new" in part_names


async def test_add_behavior_duplicate_uri_new_wins(
    simple_provenance_map: ProvenanceMap,
    mocker: MockerFixture,
) -> None:
    """add_behavior: when a known URI is re-added, the new version replaces the old."""
    updated_part = TrackedPart(
        kind=PartKind.TOOL,
        name="tool-lsp-v2",
        source_behavior="leaf-behavior",
        tokens=0,
        config={},
        namespace_path=None,
    )
    updated_leaf = BehaviorInfo(
        name="leaf-behavior",
        uri="leaf-behavior-uri",
        parts=(updated_part,),
        raw_parts=(updated_part,),
        total_tokens=0,
        depth=1,
        include_chain=("root-bundle", "mid-behavior", "leaf-behavior"),
    )
    mocker.patch(
        "amplifier_configurator.provenance._load_behavior_tree",
        new=AsyncMock(return_value=([updated_leaf], {})),
    )

    cfg = BundleConfigurator(simple_provenance_map)
    new_cfg = await cfg.add_behavior("leaf-behavior-uri")

    part_names = {p.name for p in new_cfg.list_parts()}
    # New part from updated behavior appears; old exclusive part is gone
    assert "tool-lsp-v2" in part_names
    assert "tool-lsp" not in part_names


async def test_add_behavior_extends_include_order(
    simple_provenance_map: ProvenanceMap,
    mocker: MockerFixture,
) -> None:
    """add_behavior: new behavior URI is appended to include_order."""
    new_beh = BehaviorInfo(
        name="extra-behavior",
        uri="extra-behavior-uri",
        parts=(),
        raw_parts=(),
        total_tokens=0,
        depth=0,
        include_chain=("root-bundle", "extra-behavior"),
    )
    mocker.patch(
        "amplifier_configurator.provenance._load_behavior_tree",
        new=AsyncMock(return_value=([new_beh], {})),
    )

    cfg = BundleConfigurator(simple_provenance_map)
    new_cfg = await cfg.add_behavior("extra-behavior-uri")

    assert "extra-behavior-uri" in new_cfg.provenance.include_order


async def test_add_behavior_propagates_load_error(
    simple_provenance_map: ProvenanceMap,
    mocker: MockerFixture,
) -> None:
    """add_behavior: LoadError raised by _load_behavior_tree propagates to caller."""
    mocker.patch(
        "amplifier_configurator.provenance._load_behavior_tree",
        new=AsyncMock(side_effect=LoadError("not found")),
    )

    cfg = BundleConfigurator(simple_provenance_map)
    with pytest.raises(LoadError):
        await cfg.add_behavior("bad-uri")


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


def test_diff_with_self_is_empty(simple_provenance_map: ProvenanceMap) -> None:
    """diff(self) returns a BundleDiff with no added/removed parts or behaviors and zero token_delta."""
    cfg = BundleConfigurator(simple_provenance_map)
    d = cfg.diff(cfg)
    assert d.added_parts == ()
    assert d.removed_parts == ()
    assert d.added_behaviors == ()
    assert d.removed_behaviors == ()
    assert d.token_delta == 0


def test_diff_after_remove_behavior_shows_removed(
    simple_provenance_map: ProvenanceMap,
) -> None:
    """diff after remove_behavior shows removed_parts, removed behavior URI, and negative token_delta."""
    cfg = BundleConfigurator(simple_provenance_map)
    new_cfg = cfg.remove_behavior("leaf-behavior")
    d = cfg.diff(new_cfg)

    # leaf-behavior contributed tool-lsp and the lsp-config context part
    removed_names = {p.name for p in d.removed_parts}
    assert "tool-lsp" in removed_names
    assert "leaf-behavior:lsp-config" in removed_names

    # behavior URI should appear in removed_behaviors
    assert "leaf-behavior-uri" in d.removed_behaviors

    # removing a behavior with tokens must decrease the total
    assert d.token_delta < 0


def test_diff_after_remove_part_shows_removed_name(
    simple_provenance_map: ProvenanceMap,
) -> None:
    """diff after remove_part includes the removed part name in removed_parts."""
    cfg = BundleConfigurator(simple_provenance_map)
    new_cfg = cfg.remove_part(PartKind.CONTEXT, "leaf-behavior:lsp-config")
    d = cfg.diff(new_cfg)

    removed_names = {p.name for p in d.removed_parts}
    assert "leaf-behavior:lsp-config" in removed_names


def test_diff_returns_bundle_diff_type(simple_provenance_map: ProvenanceMap) -> None:
    """diff() returns a BundleDiff instance."""
    cfg = BundleConfigurator(simple_provenance_map)
    d = cfg.diff(cfg)
    assert isinstance(d, BundleDiff)


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_clean_bundle_returns_empty_errors(
    simple_provenance_map: ProvenanceMap,
) -> None:
    """validate() on a clean bundle (all required parts present) returns empty errors."""
    cfg = BundleConfigurator(simple_provenance_map)
    errors, _ = cfg.validate()
    assert errors == []


def test_validate_returns_tuple_of_length_2(
    simple_provenance_map: ProvenanceMap,
) -> None:
    """validate() returns a 2-tuple of (errors, warnings)."""
    cfg = BundleConfigurator(simple_provenance_map)
    result = cfg.validate()
    assert isinstance(result, tuple)
    assert len(result) == 2


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------


def test_save_writes_md_file(
    simple_provenance_map: ProvenanceMap, tmp_path: Path
) -> None:
    """save() writes a .md file and returns the output path.

    Verifies that result path equals output path, file exists, and content
    contains '---' (YAML frontmatter delimiter) and 'root-bundle' (bundle name).
    """
    cfg = BundleConfigurator(simple_provenance_map)
    output_path = tmp_path / "bundle.md"
    result = cfg.save(output_path)
    assert result == output_path
    assert output_path.exists()
    content = output_path.read_text()
    assert "---" in content
    assert "root-bundle" in content


def test_save_creates_parent_directories(
    simple_provenance_map: ProvenanceMap, tmp_path: Path
) -> None:
    """save() creates parent directories automatically when they don't exist."""
    cfg = BundleConfigurator(simple_provenance_map)
    nested_path = tmp_path / "deep" / "nested" / "bundle.md"
    cfg.save(nested_path)
    assert nested_path.exists()


def test_save_instruction_in_body(
    simple_provenance_map: ProvenanceMap, tmp_path: Path
) -> None:
    """save() writes the root instruction into the file body."""
    cfg = BundleConfigurator(simple_provenance_map)
    output_path = tmp_path / "bundle.md"
    cfg.save(output_path)
    content = output_path.read_text()
    assert "You are a helpful assistant." in content
