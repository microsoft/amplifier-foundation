"""Tests for amplifier_configurator.provenance module.

Tests for:
  - _extract_include_uri  (4 tests)
  - _extract_parts_from_bundle  (6 tests)
  - build_provenance_map  (8 async tests via mock_load_bundle fixture)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from amplifier_foundation.bundle import Bundle

from amplifier_configurator.models import PartKind, TrackedPart
from amplifier_configurator.provenance import (
    _extract_include_uri,
    _extract_parts_from_bundle,
    build_provenance_map,
)


# ---------------------------------------------------------------------------
# _extract_include_uri  (4 tests)
# ---------------------------------------------------------------------------


def test_extract_include_uri_from_dict_format() -> None:
    """Dict format {'bundle': 'uri'} returns the URI string."""
    result = _extract_include_uri({"bundle": "git+https://example.com/foo"})
    assert result == "git+https://example.com/foo"


def test_extract_include_uri_from_plain_string() -> None:
    """Plain string format returns the string directly."""
    result = _extract_include_uri("foundation:some-behavior")
    assert result == "foundation:some-behavior"


def test_extract_include_uri_dict_without_bundle_key_returns_none() -> None:
    """Dict without 'bundle' key returns None."""
    result = _extract_include_uri({"uri": "something", "other": "value"})
    assert result is None


def test_extract_include_uri_unknown_format_returns_none() -> None:
    """Unknown formats (int, list, None) return None."""
    assert _extract_include_uri(42) is None
    assert _extract_include_uri(["foo", "bar"]) is None
    assert _extract_include_uri(None) is None


# ---------------------------------------------------------------------------
# _extract_parts_from_bundle  (6 tests)
# ---------------------------------------------------------------------------


def test_extract_parts_tool_name_from_module_key() -> None:
    """Tool names are extracted from the 'module' key."""
    bundle = Bundle(
        name="test-bundle",
        tools=[
            {"module": "tool-bash", "source": "git+https://example.com/tool-bash@main"}
        ],
    )
    parts = _extract_parts_from_bundle(bundle, source_behavior=None)
    tool_parts = [p for p in parts if p.kind == PartKind.TOOL]
    assert len(tool_parts) == 1
    assert tool_parts[0].name == "tool-bash"
    assert tool_parts[0].tokens == 0  # tools always contribute 0 tokens


def test_extract_parts_tool_name_from_id_key() -> None:
    """Tool name falls back to 'id' key when 'module' is absent."""
    bundle = Bundle(
        name="test-bundle",
        tools=[
            {
                "id": "tool-special",
                "source": "git+https://example.com/tool-special@main",
            }
        ],
    )
    parts = _extract_parts_from_bundle(bundle, source_behavior=None)
    tool_parts = [p for p in parts if p.kind == PartKind.TOOL]
    assert len(tool_parts) == 1
    assert tool_parts[0].name == "tool-special"


def test_extract_parts_source_behavior_is_set() -> None:
    """All extracted parts carry the provided source_behavior value."""
    bundle = Bundle(
        name="my-behavior",
        tools=[{"module": "tool-bash"}],
        hooks=[{"module": "hooks-todo"}],
    )
    parts = _extract_parts_from_bundle(bundle, source_behavior="my-behavior")
    assert len(parts) == 2
    assert all(p.source_behavior == "my-behavior" for p in parts)


def test_extract_parts_root_has_none_source_behavior() -> None:
    """Root parts are extracted with source_behavior=None."""
    bundle = Bundle(
        name="root-bundle",
        tools=[{"module": "tool-bash"}, {"module": "tool-search"}],
    )
    parts = _extract_parts_from_bundle(bundle, source_behavior=None)
    assert all(p.source_behavior is None for p in parts)


def test_extract_parts_agent_tokens_from_description_and_instruction() -> None:
    """Agent token count = estimate_tokens(description) + estimate_tokens(instruction)."""
    description = "Finds bugs"  # 10 chars → 10//4 = 2 tokens
    instruction = "Hunt them relentlessly."  # 23 chars → 23//4 = 5 tokens
    bundle = Bundle(
        name="test-bundle",
        agents={
            "bug-hunter": {
                "description": description,
                "instruction": instruction,
            }
        },
    )
    parts = _extract_parts_from_bundle(bundle, source_behavior=None)
    agent_parts = [p for p in parts if p.kind == PartKind.AGENT]
    assert len(agent_parts) == 1
    expected = len(description) // 4 + len(instruction) // 4
    assert agent_parts[0].tokens == expected


def test_extract_parts_context_names_are_namespaced() -> None:
    """Context TrackedPart names are '{bundle.name}:{ctx_name}'."""
    ctx_path = Path("/tmp/nonexistent_lsp_ctx.md")
    bundle = Bundle(
        name="leaf-behavior",
        context={"lsp-config": ctx_path},
    )
    parts = _extract_parts_from_bundle(bundle, source_behavior="leaf-behavior")
    ctx_parts = [p for p in parts if p.kind == PartKind.CONTEXT]
    assert len(ctx_parts) == 1
    assert ctx_parts[0].name == "leaf-behavior:lsp-config"
    assert ctx_parts[0].source_behavior == "leaf-behavior"


def test_extract_parts_pending_context_creates_zero_token_parts() -> None:
    """_pending_context entries produce 0-token CONTEXT TrackedParts.

    This is the key bug regression test: Foundation stores namespace-referenced
    context files in bundle._pending_context (not bundle.context) when a bundle
    is loaded with auto_include=False.  _extract_parts_from_bundle() must read
    both fields so that the backfill step can assign real token counts later.
    """
    bundle = Bundle(name="ns-behavior")
    ref = "amplifier:context/ecosystem-overview.md"
    bundle._pending_context[ref] = ref

    parts = _extract_parts_from_bundle(bundle, source_behavior="ns-behavior")
    ctx_parts = [p for p in parts if p.kind == PartKind.CONTEXT]

    assert len(ctx_parts) == 1, (
        "_pending_context entry must be extracted as a CONTEXT TrackedPart; "
        f"got {len(ctx_parts)} context parts"
    )
    assert ctx_parts[0].name == ref, (
        "Part name must equal the full namespace reference string so it matches "
        "the composed.context key used by _backfill_context_tokens()"
    )
    assert ctx_parts[0].tokens == 0, "Token count must be 0 before backfill"
    assert ctx_parts[0].namespace_path == ref, (
        "namespace_path must hold the ref for diagnostics"
    )
    assert ctx_parts[0].source_behavior == "ns-behavior"


def test_extract_parts_pending_context_same_ref_from_two_behaviors_deduplicates() -> (
    None
):
    """Two behaviors referencing the same context file produce identical part keys.

    Deduplication relies on (kind, name) identity.  Both behaviors must emit a
    part whose name is the shared namespace reference so last-write-wins applies.
    """
    ref = "amplifier:context/shared.md"
    bundle_a = Bundle(name="behavior-a")
    bundle_b = Bundle(name="behavior-b")
    bundle_a._pending_context[ref] = ref
    bundle_b._pending_context[ref] = ref

    parts_a = _extract_parts_from_bundle(bundle_a, source_behavior="behavior-a")
    parts_b = _extract_parts_from_bundle(bundle_b, source_behavior="behavior-b")

    ctx_a = [p for p in parts_a if p.kind == PartKind.CONTEXT]
    ctx_b = [p for p in parts_b if p.kind == PartKind.CONTEXT]

    assert ctx_a[0].name == ctx_b[0].name == ref, (
        "Shared context ref must produce the same part name from both behaviors "
        "so deduplication by (kind, name) works correctly"
    )


# ---------------------------------------------------------------------------
# build_provenance_map  (8 async tests)
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_load_bundle(mock_behavior_tree: dict[str, Bundle]):
    """Async mock for load_bundle that serves bundles from mock_behavior_tree.

    - auto_include=False: returns the matching raw bundle for each URI
    - auto_include=True:  returns a composed bundle (all parts merged)
    """
    tree = mock_behavior_tree
    leaf = tree["leaf"]

    # Composed bundle merges all parts; context is already resolved (real file)
    composed = Bundle(
        name="root-bundle",
        tools=[
            {"module": "tool-bash", "source": "git+https://example.com/tool-bash@main"},
            {
                "module": "tool-filesystem",
                "source": "git+https://example.com/tool-fs@main",
            },
            {
                "module": "tool-search",
                "source": "git+https://example.com/tool-search@main",
            },
            {
                "module": "tool-delegate",
                "source": "git+https://example.com/tool-delegate@main",
            },
            {
                "module": "python_check",
                "source": "git+https://example.com/python-check@main",
            },
            {"module": "tool-lsp", "source": "git+https://example.com/tool-lsp@main"},
        ],
        instruction="You are a helpful assistant.",
        # Context key prefixed as "leaf-behavior:lsp-config" (post-compose namespacing)
        context={"leaf-behavior:lsp-config": list(leaf.context.values())[0]},
    )

    async def _load(source: str, *, auto_include: bool = True, registry=None) -> Bundle:
        if auto_include:
            return composed
        raw_map = {
            "root-uri": tree["root"],
            "mid-behavior-uri": tree["mid"],
            "leaf-behavior-uri": tree["leaf"],
        }
        if source not in raw_map:
            raise ValueError(f"Unknown source in mock: {source!r}")
        return raw_map[source]

    return _load


async def test_build_provenance_map_root_name_and_instruction(mock_load_bundle) -> None:
    """build_provenance_map sets root_name and root_instruction from the root bundle."""
    with patch(
        "amplifier_configurator.provenance.load_bundle", side_effect=mock_load_bundle
    ):
        pmap = await build_provenance_map("root-uri")

    assert pmap.root_name == "root-bundle"
    assert pmap.root_instruction == "You are a helpful assistant."


async def test_build_provenance_map_behaviors_count_and_names(mock_load_bundle) -> None:
    """behaviors has exactly 2 entries: leaf-behavior and mid-behavior."""
    with patch(
        "amplifier_configurator.provenance.load_bundle", side_effect=mock_load_bundle
    ):
        pmap = await build_provenance_map("root-uri")

    assert len(pmap.behaviors) == 2
    behavior_names = {beh.name for beh in pmap.behaviors.values()}
    assert behavior_names == {"leaf-behavior", "mid-behavior"}


async def test_build_provenance_map_include_order_bottom_up(mock_load_bundle) -> None:
    """include_order is bottom-up: leaf first (depth=1), then mid (depth=0)."""
    with patch(
        "amplifier_configurator.provenance.load_bundle", side_effect=mock_load_bundle
    ):
        pmap = await build_provenance_map("root-uri")

    order_uris = list(pmap.include_order)
    # Find positions of leaf and mid in include_order
    leaf_idx = next(
        i
        for i, uri in enumerate(order_uris)
        if pmap.behaviors[uri].name == "leaf-behavior"
    )
    mid_idx = next(
        i
        for i, uri in enumerate(order_uris)
        if pmap.behaviors[uri].name == "mid-behavior"
    )
    assert leaf_idx < mid_idx, "leaf must come before mid in bottom-up order"


async def test_build_provenance_map_root_parts_have_no_source_behavior(
    mock_load_bundle,
) -> None:
    """root_parts contains 4 tools, all with source_behavior=None."""
    with patch(
        "amplifier_configurator.provenance.load_bundle", side_effect=mock_load_bundle
    ):
        pmap = await build_provenance_map("root-uri")

    assert len(pmap.root_parts) == 4
    assert all(p.source_behavior is None for p in pmap.root_parts)
    root_tool_names = {p.name for p in pmap.root_parts}
    assert root_tool_names == {
        "tool-bash",
        "tool-filesystem",
        "tool-search",
        "tool-delegate",
    }


async def test_build_provenance_map_leaf_depth_and_include_chain(
    mock_load_bundle,
) -> None:
    """Leaf behavior has depth=1 and include_chain ('root-bundle', 'mid-behavior', 'leaf-behavior')."""
    with patch(
        "amplifier_configurator.provenance.load_bundle", side_effect=mock_load_bundle
    ):
        pmap = await build_provenance_map("root-uri")

    leaf_beh = next(
        beh for beh in pmap.behaviors.values() if beh.name == "leaf-behavior"
    )
    assert leaf_beh.depth == 1
    assert leaf_beh.include_chain == ("root-bundle", "mid-behavior", "leaf-behavior")


async def test_build_provenance_map_session_config_preserved(
    mock_behavior_tree: dict[str, Bundle],
) -> None:
    """session_config is read from the root bundle's session field."""
    tree = mock_behavior_tree
    leaf = tree["leaf"]

    # Build a root bundle that has session config
    root_with_session = Bundle(
        name="root-bundle",
        tools=tree["root"].tools,
        includes=tree["root"].includes,
        instruction="You are a helpful assistant.",
        session={"model": "gpt-4", "timeout": 30},
    )

    composed = Bundle(
        name="root-bundle",
        tools=[
            {"module": "tool-bash"},
            {"module": "tool-filesystem"},
            {"module": "tool-search"},
            {"module": "tool-delegate"},
            {"module": "python_check"},
            {"module": "tool-lsp"},
        ],
        instruction="You are a helpful assistant.",
        context={"leaf-behavior:lsp-config": list(leaf.context.values())[0]},
    )

    async def _load_session(
        source: str, *, auto_include: bool = True, registry=None
    ) -> Bundle:
        if auto_include:
            return composed
        raw_map = {
            "root-uri": root_with_session,
            "mid-behavior-uri": tree["mid"],
            "leaf-behavior-uri": tree["leaf"],
        }
        return raw_map[source]

    with patch(
        "amplifier_configurator.provenance.load_bundle", side_effect=_load_session
    ):
        pmap = await build_provenance_map("root-uri")

    assert pmap.session_config == {"model": "gpt-4", "timeout": 30}


async def test_build_provenance_map_no_duplicate_tool_names(mock_load_bundle) -> None:
    """all_parts contains no duplicate tool names (deduplication is applied)."""
    with patch(
        "amplifier_configurator.provenance.load_bundle", side_effect=mock_load_bundle
    ):
        pmap = await build_provenance_map("root-uri")

    tool_parts = [p for p in pmap.all_parts if p.kind == PartKind.TOOL]
    tool_names = [p.name for p in tool_parts]
    assert len(tool_names) == len(set(tool_names)), (
        f"Duplicate tool names found in all_parts: {tool_names}"
    )


async def test_build_provenance_map_raw_bundles_stored(mock_load_bundle) -> None:
    """_raw_bundles contains an entry for the root bundle and each behavior."""
    with patch(
        "amplifier_configurator.provenance.load_bundle", side_effect=mock_load_bundle
    ):
        pmap = await build_provenance_map("root-uri")

    # Root bundle must be present
    assert "root-bundle" in pmap._raw_bundles

    # Each behavior must have its raw bundle stored (keyed by behavior name)
    for beh_name in ["mid-behavior", "leaf-behavior"]:
        assert beh_name in pmap._raw_bundles, (
            f"Raw bundle missing for behavior '{beh_name}': "
            f"available keys = {list(pmap._raw_bundles)}"
        )


# ---------------------------------------------------------------------------
# _backfill_context_tokens  (2 tests)
# ---------------------------------------------------------------------------


def test_backfill_context_tokens_updates_zero_token_parts(tmp_path: Path) -> None:
    """Context parts with tokens=0 are patched from the composed bundle's real files."""
    from amplifier_configurator.models import BehaviorInfo
    from amplifier_configurator.provenance import _backfill_context_tokens

    # Real file on disk: 400 chars → 100 tokens (400 // 4)
    ctx_file = tmp_path / "context.md"
    ctx_file.write_text("A" * 400)

    # Behavior has a 0-token context part whose name matches the composed.context key.
    ctx_part = TrackedPart(
        kind=PartKind.CONTEXT,
        name="my-ctx-key",
        source_behavior="my-behavior",
        tokens=0,
        config={"path": str(ctx_file)},
        namespace_path=None,
    )
    behavior_info = BehaviorInfo(
        name="my-behavior",
        uri="my-behavior-uri",
        parts=(ctx_part,),
        raw_parts=(ctx_part,),
        total_tokens=0,
        depth=0,
        include_chain=("root", "my-behavior"),
    )
    behaviors = {"my-behavior-uri": behavior_info}
    include_order = ("my-behavior-uri",)

    # Composed bundle has a context entry pointing at the real file.
    composed = Bundle(name="root", context={"my-ctx-key": ctx_file})

    updated_behaviors, _root_parts, _all_parts = _backfill_context_tokens(
        behaviors, (), include_order, composed
    )

    updated_part = next(
        p
        for p in updated_behaviors["my-behavior-uri"].parts
        if p.kind == PartKind.CONTEXT
    )
    assert updated_part.tokens == 100, (
        f"Expected 100 tokens (400 chars // 4), got {updated_part.tokens}"
    )
    # total_tokens on the BehaviorInfo should also be refreshed.
    assert updated_behaviors["my-behavior-uri"].total_tokens == 100


def test_backfill_context_tokens_preserves_nonzero_tokens(tmp_path: Path) -> None:
    """Context parts that already have tokens > 0 are left unchanged."""
    from amplifier_configurator.models import BehaviorInfo
    from amplifier_configurator.provenance import _backfill_context_tokens

    # File has 400 chars = 100 tokens, but the part already reports 99.
    ctx_file = tmp_path / "context.md"
    ctx_file.write_text("A" * 400)

    ctx_part = TrackedPart(
        kind=PartKind.CONTEXT,
        name="existing-ctx-key",
        source_behavior="my-behavior",
        tokens=99,  # pre-existing non-zero — must NOT be overwritten
        config={},
        namespace_path=None,
    )
    behavior_info = BehaviorInfo(
        name="my-behavior",
        uri="my-behavior-uri",
        parts=(ctx_part,),
        raw_parts=(ctx_part,),
        total_tokens=99,
        depth=0,
        include_chain=("root", "my-behavior"),
    )
    behaviors = {"my-behavior-uri": behavior_info}
    include_order = ("my-behavior-uri",)

    composed = Bundle(name="root", context={"existing-ctx-key": ctx_file})

    updated_behaviors, _, __ = _backfill_context_tokens(
        behaviors, (), include_order, composed
    )

    updated_part = next(
        p
        for p in updated_behaviors["my-behavior-uri"].parts
        if p.kind == PartKind.CONTEXT
    )
    assert updated_part.tokens == 99, (
        f"Non-zero tokens must be preserved; got {updated_part.tokens}"
    )


# ---------------------------------------------------------------------------
# _recompute_all_parts  (2 tests)
# ---------------------------------------------------------------------------


def test_recompute_all_parts_deduplication() -> None:
    """Two behaviors sharing a tool name: the one later in include_order wins."""
    from amplifier_configurator.models import BehaviorInfo
    from amplifier_configurator.provenance import _recompute_all_parts

    # "First" behavior contributes tool-shared v1; "second" contributes v2.
    tool_v1 = TrackedPart(
        kind=PartKind.TOOL,
        name="tool-shared",
        source_behavior="behavior-first",
        tokens=0,
        config={"module": "tool-shared", "version": "1"},
        namespace_path=None,
    )
    tool_v2 = TrackedPart(
        kind=PartKind.TOOL,
        name="tool-shared",
        source_behavior="behavior-second",
        tokens=0,
        config={"module": "tool-shared", "version": "2"},
        namespace_path=None,
    )

    behavior_first = BehaviorInfo(
        name="behavior-first",
        uri="uri-first",
        parts=(tool_v1,),
        raw_parts=(tool_v1,),
        total_tokens=0,
        depth=1,
        include_chain=("root", "behavior-first"),
    )
    behavior_second = BehaviorInfo(
        name="behavior-second",
        uri="uri-second",
        parts=(tool_v2,),
        raw_parts=(tool_v2,),
        total_tokens=0,
        depth=0,
        include_chain=("root", "behavior-second"),
    )

    behaviors = {"uri-first": behavior_first, "uri-second": behavior_second}
    # uri-second is later in include_order → last-write-wins → tool_v2 wins.
    include_order = ("uri-first", "uri-second")

    all_parts = _recompute_all_parts(behaviors, (), include_order)

    shared_tools = [p for p in all_parts if p.name == "tool-shared"]
    assert len(shared_tools) == 1, (
        f"Expected exactly 1 deduplicated tool, got {len(shared_tools)}"
    )
    assert shared_tools[0].source_behavior == "behavior-second", (
        "Last behavior in include_order must win (last-write-wins)"
    )


def test_recompute_all_parts_includes_root_parts() -> None:
    """Root parts are always present in the result, even when behaviors is empty."""
    from amplifier_configurator.provenance import _recompute_all_parts

    root_tool = TrackedPart(
        kind=PartKind.TOOL,
        name="tool-root-only",
        source_behavior=None,
        tokens=0,
        config={},
        namespace_path=None,
    )

    all_parts = _recompute_all_parts({}, (root_tool,), ())

    assert any(p.name == "tool-root-only" for p in all_parts), (
        "Root parts must appear in recomputed all_parts"
    )
