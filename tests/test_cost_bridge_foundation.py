"""Tests for cost bridge in PreparedBundle.spawn()."""

from decimal import Decimal
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_parent_coord() -> MagicMock:
    """Return a MagicMock parent_coordinator with realistic capability/contributor state.

    Emulates the real coordinator surface used by bridge_child_cost:
    - get_capability / register_capability: dict-backed, behaves like the kernel
    - register_contributor: captures (channel, name) -> callback in coord._contributors

    Exposes coord._contributors and coord._register_calls for assertions.
    """
    coord = MagicMock()
    caps: dict = {}
    coord.get_capability = lambda name: caps.get(name)
    coord.register_capability = lambda name, value: caps.__setitem__(name, value)

    contributors: dict = {}
    register_calls: list = []

    def _capture(channel, name, callback):
        register_calls.append((channel, name))
        contributors[(channel, name)] = callback

    coord.register_contributor = _capture
    coord._contributors = contributors
    coord._register_calls = register_calls
    return coord


@pytest.mark.asyncio
async def test_prepared_bundle_spawn_bridges_child_cost():
    """bridge_child_cost registers a single 'delegate-children' contributor on the parent."""
    child_coord = MagicMock()
    child_coord.collect_contributions = AsyncMock(
        return_value=[{"cost_usd": Decimal("0.06")}]
    )

    parent_coord = _make_parent_coord()

    from amplifier_foundation import bridge_child_cost

    await bridge_child_cost(
        child_coordinator=child_coord,
        parent_coordinator=parent_coord,
        child_session_id="foundation-child-001",
    )

    # One contributor named "delegate-children", not per-child
    assert ("session.cost", "delegate-children") in parent_coord._contributors
    result = parent_coord._contributors[("session.cost", "delegate-children")]()
    assert result == {"cost_usd": Decimal("0.06")}


@pytest.mark.asyncio
async def test_bridge_child_cost_no_compounding_on_multi_turn_resume():
    """Re-bridging the same child on resume replaces its slot — no N-tuple counting.

    Bug: register_contributor appended a new entry each call. After N resumes of the
    same child, collect_contributions returned N identical values summing to N× the
    real cost. This test verifies that only the latest cost is ever counted.
    """
    parent_coord = _make_parent_coord()

    from amplifier_foundation import bridge_child_cost

    # Simulate 5 turns: child's cumulative cost grows each turn
    cumulative_costs = ["0.10", "0.20", "0.30", "0.40", "0.50"]
    for cost in cumulative_costs:
        child_coord = MagicMock()
        child_coord.collect_contributions = AsyncMock(
            return_value=[{"cost_usd": Decimal(cost)}]
        )
        await bridge_child_cost(
            child_coordinator=child_coord,
            parent_coordinator=parent_coord,
            child_session_id="resume-child-001",  # same child each turn
        )

    # register_contributor must only have been called once (first bridge)
    assert len(parent_coord._register_calls) == 1, (
        f"Expected 1 register_contributor call, got {len(parent_coord._register_calls)}. "
        "Multi-turn compounding bug: a new contributor was registered each resume."
    )

    # The contributor must return the LATEST value (0.50), not the sum of all (1.50)
    contributor_fn = parent_coord._contributors[("session.cost", "delegate-children")]
    result = contributor_fn()
    assert result == {"cost_usd": Decimal("0.50")}, (
        f"Expected latest cumulative cost Decimal('0.50'), got {result}. "
        "Multi-turn compounding bug: cost was N-tuple counted."
    )


@pytest.mark.asyncio
async def test_bridge_child_cost_multiple_children_sum_correctly():
    """Multiple distinct children are summed together in the single contributor."""
    parent_coord = _make_parent_coord()

    from amplifier_foundation import bridge_child_cost

    for child_id, cost in [
        ("child-A", "0.10"),
        ("child-B", "0.20"),
        ("child-C", "0.30"),
    ]:
        child_coord = MagicMock()
        child_coord.collect_contributions = AsyncMock(
            return_value=[{"cost_usd": Decimal(cost)}]
        )
        await bridge_child_cost(
            child_coordinator=child_coord,
            parent_coordinator=parent_coord,
            child_session_id=child_id,
        )

    # Still only one contributor registered (not one per child)
    assert len(parent_coord._register_calls) == 1, (
        f"Expected 1 register_contributor call, got {len(parent_coord._register_calls)}."
    )

    # Contributor sums all three children: 0.10 + 0.20 + 0.30 = 0.60
    contributor_fn = parent_coord._contributors[("session.cost", "delegate-children")]
    result = contributor_fn()
    assert result is not None
    assert result["cost_usd"] == Decimal("0.60")


@pytest.mark.asyncio
async def test_bridge_child_cost_isolates_state_per_parent():
    """Two distinct parents bridging children with the SAME id see ONLY their own.

    Regression guard: under the previous module-level dict + id(coord) keying scheme,
    a new coordinator allocated at a recycled memory address (after the prior one was
    GC'd) would inherit stale entries — silently under-counting the new parent's costs
    because register_contributor was skipped. State now lives on the coordinator itself
    via register_capability, so this failure mode is impossible by construction: each
    parent has its own capability dict, independent of memory address reuse.
    """
    parent_A = _make_parent_coord()
    parent_B = _make_parent_coord()

    from amplifier_foundation import bridge_child_cost

    # Both parents bridge a child with the SAME session_id but different cumulative costs
    child_for_A = MagicMock()
    child_for_A.collect_contributions = AsyncMock(
        return_value=[{"cost_usd": Decimal("0.10")}]
    )
    await bridge_child_cost(
        child_coordinator=child_for_A,
        parent_coordinator=parent_A,
        child_session_id="child-X",
    )

    child_for_B = MagicMock()
    child_for_B.collect_contributions = AsyncMock(
        return_value=[{"cost_usd": Decimal("0.99")}]
    )
    await bridge_child_cost(
        child_coordinator=child_for_B,
        parent_coordinator=parent_B,
        child_session_id="child-X",  # identical name, different parent
    )

    # Each parent must have its OWN contributor (one register call each)
    assert len(parent_A._register_calls) == 1
    assert len(parent_B._register_calls) == 1

    # Each parent's contributor reflects only its own child's cost
    contrib_A = parent_A._contributors[("session.cost", "delegate-children")]()
    contrib_B = parent_B._contributors[("session.cost", "delegate-children")]()
    assert contrib_A == {"cost_usd": Decimal("0.10")}
    assert contrib_B == {"cost_usd": Decimal("0.99")}


@pytest.mark.asyncio
async def test_spawn_calls_bridge_child_cost_with_parent():
    """spawn() reaches bridge_child_cost — verifies wiring, not just the helper.

    The unit test above exercises bridge_child_cost in isolation. This test
    verifies the call site inside PreparedBundle.spawn() actually invokes the
    function, catching any typo or refactor that severs the link.
    """
    # Child session mock — enough for spawn() to complete without real modules
    child_session = MagicMock()
    child_session.session_id = "child-test-001"
    child_session.execute = AsyncMock(return_value="task complete")
    child_session.cleanup = AsyncMock()
    child_session.initialize = AsyncMock()

    child_coord = MagicMock()
    child_coord.mount = AsyncMock()
    child_coord.register_capability = MagicMock()
    child_coord.get = MagicMock(return_value=None)
    child_coord.get_capability = MagicMock(return_value=None)
    # hooks.register must return a callable (the unregister function)
    child_coord.hooks.register = MagicMock(return_value=lambda: None)
    child_session.coordinator = child_coord

    # Parent session mock
    parent_coord = MagicMock()
    parent_session = MagicMock()
    parent_session.session_id = "parent-test-001"
    parent_session.coordinator = parent_coord

    # Bundle mock — minimal surface used by spawn()
    bundle = MagicMock()
    bundle.compose.return_value = bundle
    bundle.to_mount_plan.return_value = {}
    bundle.base_path = None
    bundle.instruction = None
    bundle.context = None

    from amplifier_foundation.bundle._prepared import PreparedBundle

    prepared = PreparedBundle(
        mount_plan={},
        resolver=MagicMock(),
        bundle=bundle,
    )

    bridge_calls = []

    async def capture_bridge(child_coordinator, parent_coordinator, child_session_id):
        bridge_calls.append(
            {
                "child_coordinator": child_coordinator,
                "parent_coordinator": parent_coordinator,
                "child_session_id": child_session_id,
            }
        )

    with (
        patch("amplifier_core.AmplifierSession", return_value=child_session),
        patch(
            "amplifier_foundation.bundle._prepared.bridge_child_cost",
            capture_bridge,
        ),
    ):
        await prepared.spawn(
            child_bundle=bundle,
            instruction="test task",
            parent_session=parent_session,
        )

    assert len(bridge_calls) == 1, (
        "bridge_child_cost must be called exactly once from spawn()"
    )
    assert bridge_calls[0]["child_coordinator"] is child_coord
    assert bridge_calls[0]["parent_coordinator"] is parent_coord
    assert bridge_calls[0]["child_session_id"] == "child-test-001"


@pytest.mark.asyncio
async def test_spawn_does_not_call_bridge_without_parent():
    """spawn() skips bridge_child_cost when no parent_session is provided."""
    child_session = MagicMock()
    child_session.session_id = "child-no-parent-001"
    child_session.execute = AsyncMock(return_value="done")
    child_session.cleanup = AsyncMock()
    child_session.initialize = AsyncMock()

    child_coord = MagicMock()
    child_coord.mount = AsyncMock()
    child_coord.register_capability = MagicMock()
    child_coord.get = MagicMock(return_value=None)
    child_coord.get_capability = MagicMock(return_value=None)
    child_coord.hooks.register = MagicMock(return_value=lambda: None)
    child_session.coordinator = child_coord

    bundle = MagicMock()
    bundle.compose.return_value = bundle
    bundle.to_mount_plan.return_value = {}
    bundle.base_path = None
    bundle.instruction = None
    bundle.context = None

    from amplifier_foundation.bundle._prepared import PreparedBundle

    prepared = PreparedBundle(mount_plan={}, resolver=MagicMock(), bundle=bundle)

    bridge_calls = []

    async def capture_bridge(**kwargs):
        bridge_calls.append(kwargs)

    with (
        patch("amplifier_core.AmplifierSession", return_value=child_session),
        patch(
            "amplifier_foundation.bundle._prepared.bridge_child_cost",
            capture_bridge,
        ),
    ):
        await prepared.spawn(child_bundle=bundle, instruction="rootless task")

    assert len(bridge_calls) == 0, (
        "bridge_child_cost must NOT be called without a parent"
    )
