"""Fixture: module that emits events but doesn't register them all."""


async def mount(coordinator, config=None):
    coordinator.register_capability(
        "observability.events", ["recipe:start"]
    )


async def handle(hooks):
    await hooks.emit("recipe:start", {"id": "123"})
    # This event is emitted but NOT registered — should appear red in DOT
    await hooks.emit("recipe:unregistered", {"id": "123"})
