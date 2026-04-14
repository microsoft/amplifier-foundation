"""Fixture: registers events via variable extend pattern (like tool-delegate)."""


async def mount(coordinator, config=None):
    obs_events = coordinator.get_capability("observability.events") or []
    obs_events.extend(
        [
            "delegate:agent_spawned",
            "delegate:agent_completed",
            "delegate:error",
        ]
    )
    coordinator.register_capability("observability.events", obs_events)


async def handle(hooks):
    await hooks.emit("delegate:agent_spawned", {"agent": "explorer"})
    await hooks.emit("delegate:agent_completed", {"agent": "explorer"})
    await hooks.emit("delegate:error", {"msg": "oops"})
