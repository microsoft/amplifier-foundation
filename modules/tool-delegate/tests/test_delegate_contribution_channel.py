"""Tests for register_contributor migration: delegate events via collect_contributions.

Verifies that tool-delegate registers its observable events via the contribution
channel (register_contributor / collect_contributions) rather than the legacy
singleton-ownership pattern (register_capability).
"""

from __future__ import annotations

import pytest
from amplifier_core.testing import MockCoordinator
from amplifier_module_tool_delegate import mount

EXPECTED_DELEGATE_EVENTS = [
    "delegate:agent_spawned",
    "delegate:agent_resumed",
    "delegate:agent_completed",
    "delegate:agent_cancelled",
    "delegate:error",
    "delegate:model_role_unresolved",
]


@pytest.mark.asyncio
async def test_delegate_events_discoverable_via_collect_contributions():
    """Events registered by tool-delegate are discoverable via collect_contributions.

    After mount(), all 6 delegate events must appear when collect_contributions
    is called for the 'observability.events' channel.
    """
    coordinator = MockCoordinator()
    await mount(coordinator, {})

    contributions = await coordinator.collect_contributions("observability.events")
    all_events = [event for contribution in contributions for event in contribution]

    for event in EXPECTED_DELEGATE_EVENTS:
        assert event in all_events, (
            f"{event} not found in observability.events contributions."
        )


@pytest.mark.asyncio
async def test_delegate_contributes_exactly_five_events():
    """tool-delegate contributes exactly the five known delegate events, no more, no fewer."""
    coordinator = MockCoordinator()
    await mount(coordinator, {})

    contributions = await coordinator.collect_contributions("observability.events")
    all_events = [event for contribution in contributions for event in contribution]

    assert len(all_events) == len(EXPECTED_DELEGATE_EVENTS), (
        f"Expected exactly {len(EXPECTED_DELEGATE_EVENTS)} delegate events, "
        f"got {len(all_events)}: {all_events}"
    )
    assert sorted(all_events) == sorted(EXPECTED_DELEGATE_EVENTS), (
        f"Contributed events {all_events} do not match expected {EXPECTED_DELEGATE_EVENTS}"
    )


@pytest.mark.asyncio
async def test_events_not_registered_as_capability():
    """After migration, observability.events must NOT be set via register_capability.

    Using register_capability reintroduces the singleton-ownership anti-pattern.
    After mount(), coordinator.get_capability('observability.events') must return None.
    """
    coordinator = MockCoordinator()
    await mount(coordinator, {})

    capability = coordinator.get_capability("observability.events")
    assert capability is None, (
        f"observability.events was registered as a capability (got {capability!r}). "
        "Use register_contributor instead to avoid the singleton-ownership anti-pattern."
    )
