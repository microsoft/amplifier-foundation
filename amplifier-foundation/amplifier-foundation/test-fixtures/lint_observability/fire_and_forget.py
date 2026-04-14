"""Fixture: contains fire-and-forget asyncio.create_task(hooks.emit(...)) pattern."""

import asyncio


async def mount(coordinator, config=None):
    coordinator.register_capability("observability.events", ["custom:event"])


async def handle(hooks):
    # This is the anti-pattern — should be WARNING
    asyncio.create_task(hooks.emit("custom:event", {"data": "value"}))
