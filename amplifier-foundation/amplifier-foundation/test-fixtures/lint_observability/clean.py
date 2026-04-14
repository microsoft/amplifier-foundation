"""Fixture: clean file with no observability anti-patterns."""


async def mount(coordinator, config=None):
    coordinator.register_capability(
        "observability.events", ["custom:event", "custom:done"]
    )


async def handle(hooks):
    # Properly awaited, properly registered — no issues
    await hooks.emit("custom:event", {"data": "value"})
    await hooks.emit("custom:done", {})
