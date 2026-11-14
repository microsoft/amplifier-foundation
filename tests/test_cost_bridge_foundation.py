"""Tests for cost bridge in PreparedBundle.spawn()."""

import asyncio
import json
from decimal import Decimal
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.parametrize(
    ("contributions", "expected"),
    [
        ([], None),
        ([None], None),
        ([None, {"cost_usd": "0"}], Decimal("0")),
        ([{"cost_usd": "0.03"}, {"cost_usd": Decimal("0.05")}], Decimal("0.08")),
        ([{"cost_usd": None}], None),
        ([{"cost_usd": None}, {"cost_usd": "0.05"}], None),
        ([{"cost_usd": "0.05"}, {"cost_usd": None}], None),
        ([{"cost_usd": "0.05", "complete": False}], None),
        ([{"cost_usd": None, "known_subtotal_usd": "3.5", "complete": False}], None),
    ],
)
def test_sum_cost_completeness(contributions, expected):
    from amplifier_foundation import sum_cost_usd

    assert sum_cost_usd(contributions) == expected


@pytest.mark.parametrize(
    "bad",
    ["bad", "NaN", "sNaN", "Infinity", "-Infinity", "-0.01", 0.1, True, {}, []],
)
def test_malformed_cost_cannot_become_an_exact_subtotal(bad):
    from amplifier_foundation import sum_cost_usd

    assert sum_cost_usd([{"cost_usd": "0.03"}, {"cost_usd": bad}]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("contributions", "expected"),
    [
        ([], None),
        ([None], None),
        ([None, {"cost_usd": "0"}], {"cost_usd": "0"}),
        ([{"cost_usd": None}], {
            "cost_usd": None, "known_subtotal_usd": None, "complete": False,
        }),
        ([{"cost_usd": "0.03"}, {"cost_usd": None}], {
            "cost_usd": None, "known_subtotal_usd": "0.03", "complete": False,
        }),
        ([{"cost_usd": None}, {"cost_usd": "0.03"}], {
            "cost_usd": None, "known_subtotal_usd": "0.03", "complete": False,
        }),
        ([{"cost_usd": None, "known_subtotal_usd": "1.23", "complete": False},
          {"cost_usd": "0.01"}], {
            "cost_usd": None, "known_subtotal_usd": "1.24", "complete": False,
        }),
        ([{"cost_usd": "NaN"}, {"cost_usd": "0"}], {
            "cost_usd": None, "known_subtotal_usd": "0", "complete": False,
        }),
    ],
)
async def test_bridge_preserves_absence_unknown_and_zero(contributions, expected):
    from amplifier_foundation import bridge_child_cost, sum_cost_usd

    child = MagicMock()
    child.collect_contributions = AsyncMock(return_value=contributions)
    parent = MagicMock()
    await bridge_child_cost(child, parent, "child")
    if expected is None:
        parent.register_contributor.assert_not_called()
    else:
        channel, name, callback = parent.register_contributor.call_args.args
        assert (channel, name) == ("session.cost", "delegate:child")
        assert callback() == expected
        assert json.loads(json.dumps(callback())) == expected
        # A second delegation hop cannot turn the subtotal back into an exact cost.
        if expected.get("complete") is False:
            assert sum_cost_usd([{"cost_usd": "2"}, callback()]) is None
        result = callback()
        result["cost_usd"] = "changed"
        assert callback() == expected  # Frozen snapshot, not caller-owned state.


@pytest.mark.asyncio
async def test_bridge_collection_failure_registers_unknown(caplog):
    from amplifier_foundation import bridge_child_cost

    child = MagicMock()
    child.collect_contributions = AsyncMock(side_effect=RuntimeError("unavailable"))
    parent = MagicMock()
    await bridge_child_cost(child, parent, "child")
    assert parent.register_contributor.call_args.args[2]() == {
        "cost_usd": None, "known_subtotal_usd": None, "complete": False,
    }
    assert "cost unknown" in caplog.text


@pytest.mark.asyncio
async def test_bridge_registration_failure_never_fails_spawn(caplog):
    from amplifier_foundation import bridge_child_cost

    child = MagicMock()
    child.collect_contributions = AsyncMock(return_value=[{"cost_usd": "0.1"}])
    parent = MagicMock()
    parent.register_contributor.side_effect = RuntimeError("unavailable")
    await bridge_child_cost(child, parent, "child")
    assert "Failed to register" in caplog.text


@pytest.mark.asyncio
async def test_prepared_bundle_spawn_bridges_child_cost():
    """PreparedBundle.spawn() registers child cost on parent coordinator after execute()."""
    child_coord = MagicMock()
    child_coord.collect_contributions = AsyncMock(
        return_value=[{"cost_usd": Decimal("0.06")}]
    )

    parent_coord = MagicMock()
    registered = {}

    def capture_register(channel, name, callback):
        registered[(channel, name)] = callback

    parent_coord.register_contributor = capture_register

    from amplifier_foundation import sum_cost_usd, bridge_child_cost  # noqa: F401

    await bridge_child_cost(
        child_coordinator=child_coord,
        parent_coordinator=parent_coord,
        child_session_id="foundation-child-001",
    )

    assert ("session.cost", "delegate:foundation-child-001") in registered
    result = registered[("session.cost", "delegate:foundation-child-001")]()
    assert result == {"cost_usd": "0.06"}
    assert json.loads(json.dumps(result)) == {"cost_usd": "0.06"}


@pytest.mark.asyncio
@pytest.mark.parametrize("execution_error", [None, RuntimeError, asyncio.CancelledError])
async def test_spawn_calls_bridge_child_cost_with_parent(execution_error):
    """spawn() reaches bridge_child_cost — verifies wiring, not just the helper.

    The unit test above exercises bridge_child_cost in isolation. This test
    verifies the call site inside PreparedBundle.spawn() actually invokes the
    function, catching any typo or refactor that severs the link.
    """
    # Child session mock — enough for spawn() to complete without real modules
    child_session = MagicMock()
    child_session.session_id = "child-test-001"
    child_session.execute = AsyncMock(return_value="task complete")
    if execution_error:
        child_session.execute.side_effect = execution_error("child interrupted")
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
    # Root fix (eager mention_resolver registration): spawn() now calls
    # _build_bundles_for_resolver(effective_bundle) unconditionally, before
    # child_session.initialize(). That helper reads bundle.source_base_paths
    # (dict) and bundle.name (str) -- give this minimal bundle mock real
    # values for both so it behaves like a real Bundle instead of tripping
    # dataclasses.replace() on a MagicMock.
    bundle.source_base_paths = {}
    bundle.name = "test-bundle"

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
        if execution_error:
            with pytest.raises(execution_error, match="child interrupted"):
                await prepared.spawn(
                    child_bundle=bundle,
                    instruction="test task",
                    parent_session=parent_session,
                )
        else:
            await prepared.spawn(
                child_bundle=bundle,
                instruction="test task",
                parent_session=parent_session,
            )

    child_session.cleanup.assert_awaited_once()
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
    # Root fix (eager mention_resolver registration): spawn() now calls
    # _build_bundles_for_resolver(effective_bundle) unconditionally, before
    # child_session.initialize(). That helper reads bundle.source_base_paths
    # (dict) and bundle.name (str) -- give this minimal bundle mock real
    # values for both so it behaves like a real Bundle instead of tripping
    # dataclasses.replace() on a MagicMock.
    bundle.source_base_paths = {}
    bundle.name = "test-bundle"

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
