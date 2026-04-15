"""Tests for amplifier_configurator.models — error hierarchy and PartKind enum."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

from amplifier_foundation.bundle import Bundle

from amplifier_configurator.models import (
    BehaviorInfo,
    BundleDiff,
    ConfiguratorError,
    ConfiguratorWarning,
    DependencyError,
    LoadError,
    PartKind,
    ProvenanceMap,
    TrackedPart,
)


# ---------------------------------------------------------------------------
# PartKind tests
# ---------------------------------------------------------------------------


def test_partkind_values() -> None:
    """PartKind members have the expected string values."""
    assert PartKind.TOOL == "tool"
    assert PartKind.HOOK == "hook"
    assert PartKind.PROVIDER == "provider"
    assert PartKind.AGENT == "agent"
    assert PartKind.CONTEXT == "context"


def test_partkind_is_str() -> None:
    """PartKind members are plain str instances (isinstance check)."""
    for member in PartKind:
        assert isinstance(member, str), f"{member!r} is not a str"


def test_partkind_serializes_to_json() -> None:
    """PartKind values serialize cleanly to JSON without a custom encoder."""
    serialized = json.dumps({"kind": PartKind.TOOL})
    assert serialized == '{"kind": "tool"}'


def test_partkind_has_five_members() -> None:
    """PartKind has exactly 5 members."""
    assert len(PartKind) == 5


# ---------------------------------------------------------------------------
# ConfiguratorError tests
# ---------------------------------------------------------------------------


def test_configurator_error_is_exception() -> None:
    """ConfiguratorError is a subclass of Exception."""
    assert issubclass(ConfiguratorError, Exception)
    err = ConfiguratorError("boom")
    assert isinstance(err, Exception)


# ---------------------------------------------------------------------------
# DependencyError tests
# ---------------------------------------------------------------------------


def test_dependency_error_is_configurator_error() -> None:
    """DependencyError is a ConfiguratorError with a parts attribute."""
    err = DependencyError(parts=["tool:foo", "hook:bar"])
    assert isinstance(err, ConfiguratorError)
    assert err.parts == ["tool:foo", "hook:bar"]


# ---------------------------------------------------------------------------
# LoadError tests
# ---------------------------------------------------------------------------


def test_load_error_is_configurator_error() -> None:
    """LoadError is a ConfiguratorError with source and cause attributes."""
    cause = ValueError("underlying")
    err = LoadError(source="bundle.yaml", cause=cause)
    assert isinstance(err, ConfiguratorError)
    assert err.source == "bundle.yaml"
    assert err.cause is cause


def test_load_error_cause_defaults_to_none() -> None:
    """LoadError.cause defaults to None when not supplied."""
    err = LoadError(source="bundle.yaml")
    assert err.source == "bundle.yaml"
    assert err.cause is None


# ---------------------------------------------------------------------------
# ConfiguratorWarning tests
# ---------------------------------------------------------------------------


def test_configurator_warning_is_user_warning() -> None:
    """ConfiguratorWarning is a subclass of UserWarning."""
    assert issubclass(ConfiguratorWarning, UserWarning)
    w = ConfiguratorWarning("heads up")
    assert isinstance(w, UserWarning)


# ---------------------------------------------------------------------------
# TrackedPart tests
# ---------------------------------------------------------------------------


def test_tracked_part_construction() -> None:
    """TrackedPart can be constructed with all required fields."""
    part = TrackedPart(
        kind=PartKind.TOOL,
        name="tool-bash",
        source_behavior="my-behavior",
        tokens=42,
        config={"module": "tool-bash"},
        namespace_path=None,
    )
    assert part.kind == PartKind.TOOL
    assert part.name == "tool-bash"
    assert part.source_behavior == "my-behavior"
    assert part.tokens == 42
    assert part.config == {"module": "tool-bash"}
    assert part.namespace_path is None


def test_tracked_part_is_frozen() -> None:
    """TrackedPart is a frozen dataclass — mutation raises FrozenInstanceError."""
    part = TrackedPart(
        kind=PartKind.HOOK,
        name="hook-example",
        source_behavior=None,
        tokens=10,
        config={},
        namespace_path=None,
    )
    try:
        part.name = "changed"  # type: ignore[misc]
        raise AssertionError("Expected FrozenInstanceError was not raised")
    except FrozenInstanceError:
        pass


def test_tracked_part_source_behavior_none_means_root() -> None:
    """source_behavior=None indicates a root-level (non-included) part."""
    part = TrackedPart(
        kind=PartKind.PROVIDER,
        name="provider-openai",
        source_behavior=None,
        tokens=5,
        config={"module": "provider-openai"},
        namespace_path=None,
    )
    assert part.source_behavior is None


def test_tracked_part_context_has_namespace_path() -> None:
    """A context TrackedPart can carry a namespace_path."""
    part = TrackedPart(
        kind=PartKind.CONTEXT,
        name="instructions",
        source_behavior="foundation",
        tokens=200,
        config={"file": "instructions.md"},
        namespace_path="foundation:instructions",
    )
    assert part.namespace_path == "foundation:instructions"


def test_tracked_part_hashable() -> None:
    """TrackedPart instances are hashable and can be stored in sets."""
    part_a = TrackedPart(
        kind=PartKind.TOOL,
        name="tool-bash",
        source_behavior=None,
        tokens=42,
        config={"module": "tool-bash"},
        namespace_path=None,
    )
    part_b = TrackedPart(
        kind=PartKind.TOOL,
        name="tool-bash",
        source_behavior=None,
        tokens=42,
        config={"module": "tool-bash"},
        namespace_path=None,
    )
    # Both should hash identically and be usable in set operations
    s = {part_a, part_b}
    assert len(s) == 1


# ---------------------------------------------------------------------------
# BehaviorInfo tests
# ---------------------------------------------------------------------------


def test_behavior_info_construction() -> None:
    """BehaviorInfo can be constructed with all required fields."""
    part = TrackedPart(
        kind=PartKind.TOOL,
        name="tool-bash",
        source_behavior=None,
        tokens=42,
        config={},
        namespace_path=None,
    )
    info = BehaviorInfo(
        name="my-agent",
        uri="foundation:my-agent",
        parts=(part,),
        raw_parts=(part,),
        total_tokens=42,
        depth=0,
        include_chain=("root",),
    )
    assert info.name == "my-agent"
    assert info.uri == "foundation:my-agent"
    assert info.parts == (part,)
    assert info.raw_parts == (part,)
    assert info.total_tokens == 42
    assert info.depth == 0
    assert info.include_chain == ("root",)
    assert info.instruction is None


def test_behavior_info_is_frozen() -> None:
    """BehaviorInfo is a frozen dataclass — mutation raises FrozenInstanceError."""
    info = BehaviorInfo(
        name="my-agent",
        uri="foundation:my-agent",
        parts=(),
        raw_parts=(),
        total_tokens=0,
        depth=0,
        include_chain=(),
    )
    try:
        info.name = "changed"  # type: ignore[misc]
        raise AssertionError("Expected FrozenInstanceError was not raised")
    except FrozenInstanceError:
        pass


def test_behavior_info_with_instruction() -> None:
    """BehaviorInfo instruction field stores the agent instruction string when provided."""
    info = BehaviorInfo(
        name="bug-hunter",
        uri="foundation:bug-hunter",
        parts=(),
        raw_parts=(),
        total_tokens=0,
        depth=1,
        include_chain=("root", "foundation"),
        instruction="Hunt down bugs relentlessly.",
    )
    assert info.instruction == "Hunt down bugs relentlessly."


# ---------------------------------------------------------------------------
# ProvenanceMap tests
# ---------------------------------------------------------------------------


def _make_part(name: str, source_behavior: str | None = None) -> TrackedPart:
    """Helper to create a TrackedPart for testing."""
    return TrackedPart(
        kind=PartKind.TOOL,
        name=name,
        source_behavior=source_behavior,
        tokens=10,
        config={},
        namespace_path=None,
    )


def _make_behavior(uri: str, parts: tuple[TrackedPart, ...] = ()) -> BehaviorInfo:
    """Helper to create a BehaviorInfo for testing."""
    return BehaviorInfo(
        name=uri.split(":")[-1],
        uri=uri,
        parts=parts,
        raw_parts=parts,
        total_tokens=sum(p.tokens for p in parts),
        depth=0,
        include_chain=(uri,),
    )


def test_provenance_map_construction(simple_bundle: Bundle) -> None:
    """ProvenanceMap can be constructed with all required fields."""
    root_part = _make_part("tool-bash")
    behavior_part = _make_part("tool-search", source_behavior="foundation:bug-hunter")
    behavior = _make_behavior("foundation:bug-hunter", parts=(behavior_part,))

    pmap = ProvenanceMap(
        root_name="test-bundle",
        root_uri="foundation:test-bundle",
        root_instruction="You are a test agent.",
        root_instruction_tokens=10,
        behaviors={"foundation:bug-hunter": behavior},
        root_parts=(root_part,),
        all_parts=(root_part, behavior_part),
        composed_bundle=simple_bundle,
        include_order=("foundation:bug-hunter",),
        session_config={"model": "gpt-4"},
        spawn_config=None,
        _raw_bundles={"test-bundle": simple_bundle},
    )

    assert pmap.root_name == "test-bundle"
    assert pmap.root_uri == "foundation:test-bundle"
    assert pmap.root_instruction == "You are a test agent."
    assert pmap.root_instruction_tokens == 10
    assert "foundation:bug-hunter" in pmap.behaviors
    assert pmap.root_parts == (root_part,)
    assert pmap.all_parts == (root_part, behavior_part)
    assert pmap.composed_bundle is simple_bundle
    assert pmap.include_order == ("foundation:bug-hunter",)
    assert pmap.session_config == {"model": "gpt-4"}
    assert pmap.spawn_config is None
    assert "test-bundle" in pmap._raw_bundles


def test_provenance_map_is_mutable(simple_bundle: Bundle) -> None:
    """ProvenanceMap is NOT frozen — field mutation must succeed."""
    root_part = _make_part("tool-bash")
    pmap = ProvenanceMap(
        root_name="original",
        root_uri="foundation:original",
        root_instruction=None,
        root_instruction_tokens=0,
        behaviors={},
        root_parts=(root_part,),
        all_parts=(root_part,),
        composed_bundle=simple_bundle,
        include_order=(),
        session_config={},
        spawn_config=None,
        _raw_bundles={},
    )
    # Mutating a field should not raise
    pmap.root_name = "mutated"
    assert pmap.root_name == "mutated"


def test_provenance_map_all_parts_is_union(simple_bundle: Bundle) -> None:
    """all_parts contains both root_parts and parts from all behaviors."""
    root_part = _make_part("tool-bash")
    b_part_1 = _make_part("tool-search", source_behavior="foundation:agent-a")
    b_part_2 = _make_part("tool-filesystem", source_behavior="foundation:agent-b")
    behavior_a = _make_behavior("foundation:agent-a", parts=(b_part_1,))
    behavior_b = _make_behavior("foundation:agent-b", parts=(b_part_2,))

    all_p = (root_part, b_part_1, b_part_2)
    pmap = ProvenanceMap(
        root_name="union-test",
        root_uri="foundation:union-test",
        root_instruction=None,
        root_instruction_tokens=0,
        behaviors={
            "foundation:agent-a": behavior_a,
            "foundation:agent-b": behavior_b,
        },
        root_parts=(root_part,),
        all_parts=all_p,
        composed_bundle=simple_bundle,
        include_order=("foundation:agent-a", "foundation:agent-b"),
        session_config={},
        spawn_config=None,
        _raw_bundles={},
    )

    # all_parts should contain root_parts entries
    assert root_part in pmap.all_parts
    # all_parts should contain behavior parts
    assert b_part_1 in pmap.all_parts
    assert b_part_2 in pmap.all_parts
    # all_parts length equals root + all behavior parts
    assert len(pmap.all_parts) == 3


# ---------------------------------------------------------------------------
# BundleDiff tests
# ---------------------------------------------------------------------------


def test_bundle_diff_construction() -> None:
    """BundleDiff can be constructed with all required fields."""
    added = _make_part("tool-new")
    removed = _make_part("tool-old")
    diff = BundleDiff(
        added_parts=(added,),
        removed_parts=(removed,),
        added_behaviors=("foundation:new-agent",),
        removed_behaviors=("foundation:old-agent",),
        token_delta=50,
        before_tokens=100,
        after_tokens=150,
    )

    assert diff.added_parts == (added,)
    assert diff.removed_parts == (removed,)
    assert diff.added_behaviors == ("foundation:new-agent",)
    assert diff.removed_behaviors == ("foundation:old-agent",)
    assert diff.token_delta == 50
    assert diff.before_tokens == 100
    assert diff.after_tokens == 150


def test_bundle_diff_is_frozen() -> None:
    """BundleDiff is a frozen dataclass — mutation raises FrozenInstanceError."""
    diff = BundleDiff(
        added_parts=(),
        removed_parts=(),
        added_behaviors=(),
        removed_behaviors=(),
        token_delta=0,
        before_tokens=0,
        after_tokens=0,
    )
    try:
        diff.token_delta = 99  # type: ignore[misc]
        raise AssertionError("Expected FrozenInstanceError was not raised")
    except FrozenInstanceError:
        pass
