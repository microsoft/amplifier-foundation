"""Fixture: emits an event that is not registered via observability.events."""


async def mount(coordinator, config=None):
    coordinator.register_capability("observability.events", ["custom:ready"])


async def handle(hooks):
    await hooks.emit("custom:ready", {})
    # This event is NOT registered — should be ERROR
    await hooks.emit("custom:unregistered", {"detail": "oops"})
