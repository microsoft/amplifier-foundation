"""Fixture: module that emits canonical amplifier_core events (no registration needed)."""


async def handle(hooks):
    await hooks.emit("session:start", {"id": "abc"})
    await hooks.emit("llm:request", {"model": "gpt-4o"})
