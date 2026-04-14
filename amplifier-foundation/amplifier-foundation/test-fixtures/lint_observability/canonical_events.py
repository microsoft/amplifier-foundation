"""Fixture: only emits canonical amplifier_core events — no registration needed."""


async def handle(hooks):
    # All of these are canonical events — should NOT be flagged
    await hooks.emit("session:start", {})
    await hooks.emit("tool:result", {"result": "ok"})
    await hooks.emit("llm:request", {"model": "gpt-4"})
    await hooks.emit("provider:selected", {"name": "openai"})
    await hooks.emit("execution:complete", {})
    await hooks.emit("orchestrator:turn_start", {})
