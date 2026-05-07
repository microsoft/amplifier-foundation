"""Tests for cost bridge in PreparedBundle.spawn()."""

from decimal import Decimal
import pytest
from unittest.mock import AsyncMock, MagicMock


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
