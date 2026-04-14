"""Fixture: module that emits and registers events via observability.events."""


async def mount(coordinator, config=None):
    coordinator.register_capability(
        "observability.events", ["delegate:agent_spawned", "delegate:agent_completed"]
    )


async def handle(hooks):
    await hooks.emit("delegate:agent_spawned", {"agent": "explorer"})
    await hooks.emit("delegate:agent_completed", {"agent": "explorer"})
