"""Fixture: contains fire-and-forget pattern split across multiple lines."""

import asyncio


async def handle(hooks):
    # Multi-line fire-and-forget — should be WARNING
    asyncio.create_task(hooks.emit("session:ready", {"session_id": "abc123"}))
