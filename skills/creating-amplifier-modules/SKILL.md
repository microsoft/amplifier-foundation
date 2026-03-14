---
name: creating-amplifier-modules
description: "Use when creating a new Amplifier module (tool, hook, orchestrator, context, or provider). Covers the mount() contract, protocol compliance validation, placeholder patterns, and the module directory structure. Prevents the common mistake of creating no-op mount() stubs that fail protocol_compliance validation."
---

# Creating Amplifier Modules

## Overview

Amplifier modules are Python packages that extend the runtime with tools, hooks, providers, and other capabilities. Every module has a `mount()` function that runs at session startup.

**Core principle:** `mount()` must register something with the coordinator. A `mount()` that logs and returns `None` WILL fail validation.

**This matters immediately.** Module validation runs every time a session loads. A broken `mount()` prevents any agent using the behavior from spawning — not just future agents, not "when Phase 2 is done" — right now, on every invocation.

---

## The Iron Law

```
mount() MUST call coordinator.mount() or return a Tool instance.
A mount() that returns None and calls nothing WILL FAIL with:
"protocol_compliance: No tool was mounted and mount() did not return a Tool instance"
```

"Stub" means **placeholder that satisfies the protocol** — not empty function that does nothing.

---

## Module Directory Structure

```
modules/tool-{name}/
├── pyproject.toml                        # name = "amplifier-module-tool-{name}", hatchling
└── amplifier_module_tool_{name}/
    └── __init__.py                       # async mount(coordinator, config)
```

The `pyproject.toml` entry point wires the module name to `mount()`:

```toml
[project]
name = "amplifier-module-tool-{name}"
version = "0.1.0"
description = "Description of what this tool does"
requires-python = ">=3.11"
license = { text = "MIT" }
dependencies = []   # amplifier-core is a peer dependency — do NOT declare it here

[project.entry-points."amplifier.modules"]
tool-{name} = "amplifier_module_tool_{name}:mount"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["amplifier_module_tool_{name}"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
```

---

## The mount() Contract

Every `mount()` must:
1. Instantiate a tool class
2. Call `await coordinator.mount("tools", tool, name=tool.name)`
3. Return a metadata dict (not None)

**Complete working example:**

```python
"""Amplifier tool module for {name}."""

import logging
from typing import Any

from amplifier_core import ToolResult

logger = logging.getLogger(__name__)


class MyTool:
    """Tool class — the actual capability being registered."""

    @property
    def name(self) -> str:
        return "my_tool"

    @property
    def description(self) -> str:
        return "Description of what this tool does and when to use it."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "param": {
                    "type": "string",
                    "description": "What this parameter does",
                },
            },
            "required": ["param"],
        }

    async def execute(self, input_data: dict[str, Any]) -> ToolResult:
        """Execute the tool operation."""
        result = do_the_work(input_data["param"])
        return ToolResult(success=True, output=result)


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mount the tool into the coordinator."""
    tool = MyTool()
    await coordinator.mount("tools", tool, name=tool.name)
    logger.info("tool-my-tool mounted: registered 'my_tool'")
    return {
        "name": "tool-my-tool",
        "version": "0.1.0",
        "provides": ["my_tool"],
    }
```

---

## The Placeholder Pattern

When Phase 1 needs a module skeleton before full implementation, create a **real tool class** that returns a "not yet implemented" message. The tool still has all required properties and registers with the coordinator.

```python
"""Amplifier tool module for {name} — Phase 1 placeholder."""

import logging
from typing import Any

from amplifier_core import ToolResult

logger = logging.getLogger(__name__)


class MyToolPlaceholder:
    """Placeholder tool — registers to satisfy protocol compliance (Phase 1).

    Phase 2 will replace this with full implementation.
    """

    @property
    def name(self) -> str:
        return "my_tool"

    @property
    def description(self) -> str:
        return "Description of what this tool will do. Phase 2 implementation pending."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "description": "Operation to perform",
                },
            },
            "required": ["operation"],
        }

    async def execute(self, input_data: dict[str, Any]) -> ToolResult:
        """Return not-yet-implemented message."""
        return ToolResult(
            success=False,
            output=(
                "Tool not yet implemented. Phase 2 will add full functionality. "
                "Use the shell scripts in the bundle for now."
            ),
        )


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mount placeholder tool — satisfies protocol compliance during Phase 1."""
    tool = MyToolPlaceholder()
    await coordinator.mount("tools", tool, name=tool.name)
    logger.info("tool-my-tool mounted: registered placeholder 'my_tool' (Phase 2 pending)")
    return {
        "name": "tool-my-tool",
        "version": "0.1.0",
        "provides": ["my_tool"],
    }
```

**A placeholder tool IS a real tool.** It has `name`, `description`, `input_schema`, and `execute()`. It registers with `coordinator.mount()`. It just tells callers it's not implemented yet.

---

## Behavior YAML Reference

How the module is referenced in a behavior YAML:

```yaml
tools:
  - module: tool-{name}
    source: git+https://github.com/org/repo@main#subdirectory=modules/tool-{name}
```

For local development (relative path from bundle root):

```yaml
tools:
  - module: tool-{name}
    source: ./modules/tool-{name}
```

---

## Writing Tests

Test that `mount()` registers the tool — not that it returns `None`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from amplifier_module_tool_my_tool import mount


@pytest.mark.asyncio
async def test_mount_registers_tool():
    """mount() must register a tool via coordinator.mount()."""
    coordinator = MagicMock()
    coordinator.mount = AsyncMock()

    result = await mount(coordinator)

    # Verify coordinator.mount was called (the Iron Law)
    coordinator.mount.assert_called_once()
    call_args = coordinator.mount.call_args
    assert call_args[0][0] == "tools"  # first positional arg is "tools"

    # Verify return value is metadata dict, not None
    assert result is not None
    assert "name" in result
    assert "provides" in result


@pytest.mark.asyncio
async def test_tool_has_required_properties():
    """Tool class must have name, description, input_schema, execute."""
    coordinator = MagicMock()
    coordinator.mount = AsyncMock()

    await mount(coordinator)

    # Get the tool that was registered
    tool = coordinator.mount.call_args[0][1]
    assert isinstance(tool.name, str) and tool.name
    assert isinstance(tool.description, str) and tool.description
    assert isinstance(tool.input_schema, dict)
    assert callable(tool.execute)
```

---

## Anti-Rationalization Table

| Excuse | Reality |
|--------|---------|
| "It's just a stub, it doesn't need to register anything" | Protocol validation runs on every module load. No-op stubs fail immediately. |
| "Phase 2 will fill it in" | Phase 2 may be weeks away. The module loads NOW and fails NOW. |
| "I'll add a TODO comment" | The validator doesn't read comments. It checks `coordinator.mount()` calls. |
| "The tests pass with `result is None`" | Tests that assert `result is None` are testing the bug, not the behavior. |
| "The mount() signature is all that matters" | The signature is necessary but not sufficient. Registration is also required. |
| "It works locally without registering" | It silently fails the protocol check. You won't see the error until an agent spawns. |

---

## Red Flags — STOP and Use the Placeholder Pattern

If you find yourself thinking any of these, STOP:

- "mount() can just log and return None" → **NO.** It must register a tool.
- "I'll skip the tool class since there's nothing to implement yet" → **NO.** Create a placeholder class.
- "The test should assert `result is None`" → **NO.** The test should verify `coordinator.mount()` was called.
- "It's a stub so it should be empty" → **NO.** Use the placeholder pattern above.
- "Phase 2 will make it real, for now I'll just return None" → **NO.** Phase 2 doesn't exist yet. The module loads today.

---

## Validation Checklist

After creating a module, verify it will pass protocol compliance:

- [ ] Does `mount()` call `await coordinator.mount("tools", tool, name=tool.name)`?
- [ ] Does the tool class have a `name` property (string)?
- [ ] Does the tool class have a `description` property (string)?
- [ ] Does the tool class have an `input_schema` property (dict)?
- [ ] Does the tool class have a callable `execute()` method?
- [ ] Does `mount()` return a metadata dict (not `None`)?
- [ ] Does `pyproject.toml` declare the `amplifier.modules` entry point?
- [ ] Do tests verify `coordinator.mount()` was called (not that result is `None`)?

All eight boxes must be checked before committing.

---

## Quick Reference

**Minimum viable module `__init__.py`:**

```python
from amplifier_core import ToolResult

class MyTool:
    name = "my_tool"
    description = "What this tool does"
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, input_data):
        return ToolResult(success=False, output="Not yet implemented")

async def mount(coordinator, config=None):
    tool = MyTool()
    await coordinator.mount("tools", tool, name=tool.name)
    return {"name": "tool-my-tool", "version": "0.1.0", "provides": ["my_tool"]}
```

This is the minimum. Every line is required. Nothing can be removed.
