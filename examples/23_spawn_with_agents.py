#!/usr/bin/env python3
"""
Example 23: Spawning Sub-Sessions with Named Agents (+ Hook Propagation)
=========================================================================

AUDIENCE: Developers wiring `session.spawn` for agent / sub-session delegation
VALUE: Learn the two sanctioned agent-config shapes and see how a parent-composed
       observability hook is automatically inherited by every spawned child.

WHAT THIS DEMONSTRATES
  1. Two sanctioned agent-config shapes a spawn capability handles:
       - Inline overlay: a partial mount-plan dict declaring only what differs
         from the parent (instruction / session / providers / tools / hooks)
       - Agent file:     a path to an agent `.md` file, resolved via
         `await load_bundle(path)` and passed to `prepared.spawn()`
  2. A `session.spawn` capability that maps an agent NAME to a Bundle and
     delegates via `prepared.spawn(child_bundle, instruction, compose=True)`.
  3. Hook propagation: a GENERIC observability hook composed into the PARENT
     bundle is inherited by every spawned child automatically — no per-child
     wiring needed — because `prepare.spawn(compose=True)` (the default)
     composes the parent bundle with each child before creating the child session.

HOW THE PRODUCTION IMPLEMENTATION WORKS
  `amplifier-app-cli`'s `session_spawner.py` resolves an agent name to its
  overlay config, merges it onto the parent config with
  `merge_configs(parent_config, agent_overlay)`, and creates an AmplifierSession
  with `parent_id` set. The same two agent shapes apply there:
    - An inline overlay dict contributes its fields directly to `merge_configs`.
    - An agent `.md` file (declared via `agents.include: [name]` in the bundle
      YAML) is resolved by foundation's composition pass and its overlay is
      exposed under the agent name — `merge_configs` consumes it identically.
  Foundation's `PreparedBundle.spawn()` is the library-level entry point used
  here; the production CLI wraps it with additional app-layer concerns
  (tool-inheritance filtering, cost bridging, subprocess routing, etc.).

Requirements:
  - ANTHROPIC_API_KEY environment variable set
  - Network access on first run (prepare() fetches provider / tool modules)

Run:
  uv run python examples/23_spawn_with_agents.py
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from amplifier_foundation import Bundle
from amplifier_foundation import load_bundle
from amplifier_foundation.bundle import PreparedBundle

# ─── Paths (sibling convention) ──────────────────────────────────────────────
FOUNDATION_REPO = Path(__file__).parent.parent
PROVIDER_FILE = FOUNDATION_REPO / "providers" / "anthropic-sonnet.yaml"

# =============================================================================
# SECTION 1: A self-contained, GENERIC observability hook module
# =============================================================================
# Written to a temp dir so this example stays a single, runnable file.
# In a real project this would be a published module referenced by `source`.
# The hook appends one JSON line per event to a log file. It is entirely
# generic — it knows nothing about any app, server, or transport.

OBS_HOOK_SOURCE = '''\
"""Generic observability hook: append one JSON line per event to a log file."""

import json
import os
from typing import Any

from amplifier_core import HookResult

# Concrete event names — there is no "*" wildcard subscription.
OBSERVED_EVENTS = (
    "session:start",
    "provider:request",
    "provider:response",
    "tool:pre",
    "tool:post",
)


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or {}
    log_path = config.get("log_path") or os.environ.get("OBS_LOG_PATH")
    label = config.get("label", "obs")

    async def log_event(event: str, data: dict[str, Any]) -> HookResult:
        # coordinator.session_id is the SESSION that mounted this hook instance.
        # When compose=True propagates this hook into a child, a new mount()
        # call runs with the CHILD's coordinator, so session_id is the child's.
        session_id = getattr(coordinator, "session_id", None)
        if not session_id and isinstance(data, dict):
            session_id = data.get("session_id")
        if log_path:
            with open(log_path, "a") as fh:
                fh.write(
                    json.dumps({"label": label, "event": event, "session_id": session_id})
                    + "\\n"
                )
        return HookResult(action="continue")

    for event_name in OBSERVED_EVENTS:
        coordinator.hooks.register(event_name, log_event, name=f"obs-{event_name}")

    return {"name": "hooks-observability", "version": "0.1.0"}
'''

OBS_HOOK_PYPROJECT = """\
[project]
name = "amplifier-module-hooks-observability"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["amplifier-core"]

[project.entry-points."amplifier.modules"]
hooks-observability = "amplifier_module_hooks_observability:mount"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["amplifier_module_hooks_observability"]
"""


def scaffold_workspace() -> tuple[Path, Path]:
    """Write the generic hook module to a temp workspace.

    Returns:
        (work_dir, log_file) — work_dir holds the installed hook module;
        log_file is where the hook appends events.
    """
    work = Path(tempfile.mkdtemp(prefix="example23-"))

    pkg_dir = work / "obs_hook" / "amplifier_module_hooks_observability"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text(OBS_HOOK_SOURCE)
    (work / "obs_hook" / "pyproject.toml").write_text(OBS_HOOK_PYPROJECT)

    log_file = work / "events.log"
    return work, log_file


# =============================================================================
# SECTION 2: Agent definitions — two sanctioned shapes
# =============================================================================

# Shape 1 — INLINE OVERLAY
# --------------------------
# A dict of mount-plan fields declaring only what this agent changes relative
# to the parent. Fields absent here (orchestrator, context, providers, tools,
# hooks) are inherited from the parent when compose=True is used.
#
# In YAML bundles this looks like:
#   agents:
#     terse-responder:
#       system:
#         instruction: "Reply with exactly the word requested — nothing else."
#
# In the Python API used here, `instruction` at the top level of the Bundle
# constructor maps directly to the system instruction.

INLINE_AGENTS: dict[str, dict[str, Any]] = {
    "terse-responder": {
        "instruction": (
            "You reply with exactly the single word requested — no punctuation, "
            "no extra words. Just the one word."
        ),
    },
    "cheerful-responder": {
        "instruction": (
            "You reply cheerfully in exactly one short sentence that includes "
            "the word requested and ends with an exclamation mark."
        ),
    },
}

# Shape 2 — AGENT FILE
# ----------------------
# A path to an agent `.md` file whose YAML frontmatter uses `meta:` (not
# `bundle:`). The spawn capability calls `await load_bundle(path)` to resolve
# the file into a Bundle and then passes it to `prepared.spawn()`.
#
# In production (app-cli), agent files are declared with:
#   agents:
#     include:
#       - file-responder          # loads agents/file-responder.md
# Foundation's composition pass resolves the file and exposes its overlay
# under the agent name for `merge_configs` to consume.

FILE_AGENTS: dict[str, str] = {
    # Path is relative to this file's location; convert to str for display.
    "file-responder": str(Path(__file__).parent / "agents" / "file-responder.md"),
}


# =============================================================================
# SECTION 3: Spawn capability
# =============================================================================


async def resolve_agent_bundle(agent_name: str) -> Bundle:
    """Resolve an agent name to a Bundle.

    Handles both sanctioned shapes:
      - Inline overlay dict  →  Bundle(name=..., instruction=...)
      - Agent .md file path  →  await load_bundle(path)

    Args:
        agent_name: Key used in INLINE_AGENTS or FILE_AGENTS.

    Returns:
        A Bundle ready for PreparedBundle.spawn().

    Raises:
        ValueError: If agent_name is not registered in either lookup.
    """
    if agent_name in INLINE_AGENTS:
        # Shape 1: build a Bundle from the inline overlay fields.
        config = INLINE_AGENTS[agent_name]
        return Bundle(
            name=agent_name,
            version="1.0.0",
            session=config.get("session", {}),
            providers=config.get("providers", []),
            tools=config.get("tools", []),
            hooks=config.get("hooks", []),
            instruction=config.get("instruction")
            or (config.get("system") or {}).get("instruction"),
        )

    if agent_name in FILE_AGENTS:
        # Shape 2: load the agent .md file as a Bundle.
        # load_bundle() understands `meta:` frontmatter (agent files) just as
        # it understands `bundle:` frontmatter.
        return await load_bundle(FILE_AGENTS[agent_name])

    raise ValueError(
        f"Unknown agent {agent_name!r}. "
        f"Registered inline agents: {list(INLINE_AGENTS)}. "
        f"Registered file agents: {list(FILE_AGENTS)}."
    )


def register_spawn_capability(
    coordinator: Any,
    prepared: PreparedBundle,
) -> None:
    """Register `session.spawn` on the coordinator.

    The capability resolves agent_name → Bundle, then delegates via
    `prepared.spawn()`. This must be called AFTER `create_session()` and
    BEFORE any call to `execute()` or `spawn()`. Registering too early (no
    session yet) or too late (after the orchestrator loop has started) causes
    the orchestrator to silently fall back to a no-tools backend.

    Args:
        coordinator: The parent session's coordinator (session.coordinator).
        prepared:    The parent's PreparedBundle (holds the base bundle and
                     module resolver used when compose=True spawns children).
    """

    async def spawn_capability(
        agent_name: str,
        instruction: str,
        parent_session: Any,
        **kwargs: Any,  # absorb extra kwargs without breaking future extensions
    ) -> dict[str, Any]:
        child_bundle = await resolve_agent_bundle(agent_name)

        # compose=True (the default) composes the PARENT bundle with the child
        # bundle before creating the child session. Everything the parent
        # contributes — orchestrator, context manager, providers, hooks — is
        # therefore inherited by the child automatically.
        return await prepared.spawn(
            child_bundle=child_bundle,
            instruction=instruction,
            parent_session=parent_session,
            compose=True,
        )

    coordinator.register_capability("session.spawn", spawn_capability)


# =============================================================================
# MAIN
# =============================================================================


async def main() -> None:
    print("=" * 70)
    print("Example 23: Spawn named agents + hook propagation")
    print("=" * 70)

    work, log_file = scaffold_workspace()

    # ─── Build the parent bundle ──────────────────────────────────────────────
    # Load foundation + provider, then COMPOSE the generic observability hook
    # into the parent bundle. Because PreparedBundle.spawn() defaults to
    # compose=True, this hook is inherited by every spawned child with no
    # per-child wiring required.

    foundation = await load_bundle(str(FOUNDATION_REPO))
    provider = await load_bundle(str(PROVIDER_FILE))
    observability = Bundle(
        name="observability-behavior",
        version="1.0.0",
        hooks=[
            {
                "module": "hooks-observability",
                "source": f"file://{work}/obs_hook",
                "config": {"log_path": str(log_file), "label": "observability"},
            }
        ],
    )
    composed = foundation.compose(provider).compose(observability)

    # ─── Prepare (downloads modules once, cached on subsequent runs) ──────────
    print("\n[1/4] Preparing parent bundle (downloads modules if needed)...")
    prepared = await composed.prepare()

    # ─── Create the parent session ────────────────────────────────────────────
    print("[2/4] Creating parent session...")
    session = await prepared.create_session()

    # Register session.spawn AFTER create_session() and BEFORE any execute/spawn.
    register_spawn_capability(session.coordinator, prepared)
    spawn = session.coordinator.get_capability("session.spawn")

    # ─── Spawn named agents ───────────────────────────────────────────────────
    print("[3/4] Spawning three named agents (2 inline overlays + 1 agent file)...")
    async with session:
        terse_result = await spawn(
            "terse-responder",
            "Reply with the word PING",
            session,
        )
        cheerful_result = await spawn(
            "cheerful-responder",
            "Reply with the word PONG",
            session,
        )
        file_result = await spawn(
            "file-responder",
            "Reply with the word ECHO",
            session,
        )

    # ─── Show results ─────────────────────────────────────────────────────────
    print("\n[4/4] Results:")
    print(f"  terse-responder    -> {terse_result.get('output')!r}")
    print(f"  cheerful-responder -> {cheerful_result.get('output')!r}")
    print(f"  file-responder     -> {file_result.get('output')!r}")

    # ─── Prove hook propagation ───────────────────────────────────────────────
    # The parent-composed observability hook fires inside each spawned child
    # (not just the parent) because prepare().spawn(compose=True) re-mounts
    # the hook in each child session with the child's coordinator. Each child's
    # mount() call captures the child's session_id in its closure.
    records = [
        json.loads(line) for line in log_file.read_text().splitlines() if line.strip()
    ]
    print("\nHook propagation (events captured by the parent-composed hook):")
    for label, result in (
        ("terse-responder    ", terse_result),
        ("cheerful-responder ", cheerful_result),
        ("file-responder     ", file_result),
    ):
        sid = result.get("session_id")
        events = [r["event"] for r in records if r.get("session_id") == sid]
        status = "OK     " if events else "MISSING"
        print(f"  [{status}] {label} (sid={sid}): {events}")

    print("\n" + "=" * 70)
    print("KEY TAKEAWAYS")
    print("=" * 70)
    print("1. Inline agents   → Bundle(name=..., instruction=...) built directly.")
    print("2. Agent-file agents → await load_bundle(path) resolves the .md file.")
    print("3. Both shapes converge on prepared.spawn(child_bundle, ..., compose=True).")
    print("4. Hooks in the parent bundle reach every child automatically.")
    print("5. Register session.spawn AFTER create_session() and BEFORE execute().")


if __name__ == "__main__":
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: Set ANTHROPIC_API_KEY to run this example.")
        raise SystemExit(1)
    asyncio.run(main())
