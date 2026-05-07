"""Tests for cost bridge in PreparedBundle.spawn()."""

from decimal import Decimal
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


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
    assert result == {"cost_usd": Decimal("0.06")}


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
