"""Shared fixtures for all tests.

All fixtures use Foundation's Bundle directly — no YAML parsing, no file loading.
"""

from __future__ import annotations

from pathlib import Path

import pytest


from amplifier_foundation.bundle import Bundle

from amplifier_configurator.models import (
    BehaviorInfo,
    PartKind,
    ProvenanceMap,
    TrackedPart,
)


# ---------------------------------------------------------------------------
# Integration test gating
# ---------------------------------------------------------------------------


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip integration-marked tests unless ``-m integration`` is specified."""
    markexpr: str = config.option.markexpr or ""
    if "integration" not in markexpr:
        skip_marker = pytest.mark.skip(
            reason="integration test — run with: pytest -m integration"
        )
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip_marker)


# ---------------------------------------------------------------------------
# Helper function (not a fixture)
# ---------------------------------------------------------------------------


def _make_tracked_part(
    name: str,
    kind: PartKind = PartKind.TOOL,
    source_behavior: str | None = None,
    tokens: int = 0,
    config: dict | None = None,
    namespace_path: str | None = None,
) -> TrackedPart:
    """Create a TrackedPart with sensible defaults for testing."""
    return TrackedPart(
        kind=kind,
        name=name,
        source_behavior=source_behavior,
        tokens=tokens,
        config=config or {},
        namespace_path=namespace_path,
    )


# ---------------------------------------------------------------------------
# Bundle fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def simple_bundle() -> Bundle:
    """A minimal bundle with tools, a hook, an agent, and context."""
    return Bundle(
        name="test-bundle",
        version="1.0.0",
        description="Test bundle",
        tools=[
            {"module": "tool-bash", "source": "git+https://example.com/tool-bash"},
            {"module": "tool-filesystem", "source": "git+https://example.com/tool-fs"},
            {"module": "tool-search", "source": "git+https://example.com/tool-search"},
            {
                "module": "tool-delegate",
                "source": "git+https://example.com/tool-delegate",
            },
        ],
        hooks=[
            {"module": "hooks-todo-reminder", "source": "git+https://example.com/hook"},
        ],
        providers=[
            {"module": "provider-openai", "source": "git+https://example.com/provider"},
        ],
        agents={
            "bug-hunter": {
                "description": "Finds bugs",
                "instruction": "Hunt down bugs relentlessly.",
            }
        },
        context={},
    )


@pytest.fixture()
def bundle_with_context(tmp_path: Path) -> Bundle:
    """A bundle that has real context files on disk."""
    ctx_file = tmp_path / "instructions.md"
    ctx_file.write_text("# Instructions\n" + "A" * 400)  # ~100 tokens
    return Bundle(
        name="ctx-bundle",
        tools=[
            {"module": "tool-bash", "source": "git+https://example.com/tool-bash"},
            {"module": "tool-filesystem", "source": "git+https://example.com/tool-fs"},
            {"module": "tool-search", "source": "git+https://example.com/tool-search"},
        ],
        context={"instructions": ctx_file},
    )


# ---------------------------------------------------------------------------
# Provenance / behavior-tree fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_behavior_tree(tmp_path: Path) -> dict[str, Bundle]:
    """Three-level include tree for provenance testing.

    Structure: root → mid (depth 0) → leaf (depth 1)

    - root: 4 tools (bash, filesystem, search, delegate), instruction set
    - mid:  1 tool (python_check), includes leaf
    - leaf: 1 tool (tool-lsp), 1 context (lsp-config, 103 tokens)
    """
    # Create a real context file so token estimation works (103 tokens = 412 chars)
    lsp_file = tmp_path / "lsp.md"
    lsp_file.write_text("A" * 412)

    leaf = Bundle(
        name="leaf-behavior",
        tools=[
            {"module": "tool-lsp", "source": "git+https://example.com/tool-lsp@main"}
        ],
        context={"lsp-config": lsp_file},
    )

    mid = Bundle(
        name="mid-behavior",
        tools=[
            {
                "module": "python_check",
                "source": "git+https://example.com/python-check@main",
            }
        ],
        includes=[{"bundle": "leaf-behavior-uri"}],  # type: ignore[arg-type]
    )

    root = Bundle(
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
        ],
        includes=[{"bundle": "mid-behavior-uri"}],  # type: ignore[arg-type]
        instruction="You are a helpful assistant.",
    )

    return {"root": root, "mid": mid, "leaf": leaf}


@pytest.fixture()
def simple_provenance_map(mock_behavior_tree: dict[str, Bundle]) -> ProvenanceMap:
    """Pre-built ProvenanceMap for configurator tests.

    Mirrors the mock_behavior_tree structure:
    - root: tool-bash, tool-filesystem, tool-search, tool-delegate (source_behavior=None)
    - mid:  python_check (source_behavior="mid-behavior")
    - leaf: tool-lsp, context:lsp-config at 103 tokens (source_behavior="leaf-behavior")
    - root instruction: 'You are a helpful assistant.' → 7 tokens
    """
    # Root parts — source_behavior=None
    root_parts = (
        _make_tracked_part("tool-bash", PartKind.TOOL, source_behavior=None),
        _make_tracked_part("tool-filesystem", PartKind.TOOL, source_behavior=None),
        _make_tracked_part("tool-search", PartKind.TOOL, source_behavior=None),
        _make_tracked_part("tool-delegate", PartKind.TOOL, source_behavior=None),
    )

    # Leaf parts
    leaf_lsp_tool = _make_tracked_part(
        "tool-lsp", PartKind.TOOL, source_behavior="leaf-behavior"
    )
    leaf_lsp_ctx = _make_tracked_part(
        "leaf-behavior:lsp-config",
        PartKind.CONTEXT,
        source_behavior="leaf-behavior",
        tokens=103,
    )
    leaf_parts = (leaf_lsp_tool, leaf_lsp_ctx)

    # Mid parts
    mid_python_check = _make_tracked_part(
        "python_check", PartKind.TOOL, source_behavior="mid-behavior"
    )
    mid_parts = (mid_python_check,)

    # BehaviorInfo for leaf (depth=1, include_chain includes mid)
    leaf_info = BehaviorInfo(
        name="leaf-behavior",
        uri="leaf-behavior-uri",
        parts=leaf_parts,
        raw_parts=leaf_parts,
        total_tokens=103,  # context dominates; tool contributes 0
        depth=1,
        include_chain=("root-bundle", "mid-behavior", "leaf-behavior"),
    )

    # BehaviorInfo for mid (depth=0, direct include of root)
    mid_info = BehaviorInfo(
        name="mid-behavior",
        uri="mid-behavior-uri",
        parts=mid_parts,
        raw_parts=mid_parts,
        total_tokens=0,  # tool-only, 0 tokens
        depth=0,
        include_chain=("root-bundle", "mid-behavior"),
    )

    behaviors: dict[str, BehaviorInfo] = {
        "leaf-behavior-uri": leaf_info,
        "mid-behavior-uri": mid_info,
    }
    include_order = ("leaf-behavior-uri", "mid-behavior-uri")

    # all_parts = root + leaf + mid (no deduplication needed — all unique names)
    all_parts = root_parts + leaf_parts + mid_parts

    # root instruction "You are a helpful assistant." → len=28 // 4 = 7 tokens
    root_instruction = "You are a helpful assistant."
    root_instruction_tokens = len(root_instruction) // 4  # = 7

    return ProvenanceMap(
        root_name="root-bundle",
        root_uri="root-uri",
        root_instruction=root_instruction,
        root_instruction_tokens=root_instruction_tokens,
        behaviors=behaviors,
        root_parts=root_parts,
        all_parts=all_parts,
        composed_bundle=mock_behavior_tree["root"],
        include_order=include_order,
        session_config={},
        spawn_config=None,
        _raw_bundles={
            "root-bundle": mock_behavior_tree["root"],
            "mid-behavior": mock_behavior_tree["mid"],
            "leaf-behavior": mock_behavior_tree["leaf"],
        },
    )


@pytest.fixture(scope="session")
def lean_bundle_path() -> Path:
    """Path to the lean bundle. Skips the test if not found."""
    possible_paths = [
        Path(__file__).parent.parent.parent
        / "stripped-bundles"
        / "lean-bundle"
        / "bundle.md",
        Path.home() / ".amplifier" / "bundles" / "lean-bundle" / "bundle.md",
    ]
    for p in possible_paths:
        if p.exists():
            return p
    pytest.skip("Lean bundle not found; skipping integration test")
