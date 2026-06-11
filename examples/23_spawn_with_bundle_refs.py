#!/usr/bin/env python3
"""
Example 23: Spawning Sub-Sessions from Bundle-Ref Agents (+ Hook Propagation)
=============================================================================

AUDIENCE: Developers wiring `session.spawn` for agent / sub-session delegation
VALUE: Avoid a real latent crash when an agent is a lazy bundle-ref, and learn
       how parent-composed hooks automatically reach every spawned child.

WHAT THIS DEMONSTRATES
  1. The TWO agent-config shapes a spawn capability must handle:
       - inline config: a dict of bundle fields (instruction/tools/providers/...)
       - bundle-ref:    a single-key dict {"bundle": "<uri>"} that must be loaded
  2. A SAFE spawn capability that branches on those two shapes (the fix).
  3. Hook propagation: a GENERIC observability hook composed into the PARENT
     bundle is inherited by every spawned child via `prepared.spawn(compose=True)`.

THE TRAP THIS PREVENTS (read this!)
  A common spawn implementation builds the child unconditionally:

      child_bundle = Bundle(
          session=config.get("session", {}),
          providers=config.get("providers", []),
          tools=config.get("tools", []),
          hooks=config.get("hooks", []),
          instruction=config.get("instruction"),
      )

  That is fine for an INLINE config. But when the agent entry is a lazy
  bundle-ref -- `{"bundle": "<uri>"}` -- there are no inline fields, so every
  `.get(...)` returns empty. The result is a STRUCTURALLY EMPTY child bundle:
  no orchestrator, no provider, no tools. It inherits the parent's orchestrator
  via compose and lands on a no-tools / unconfigured backend, surfacing as an
  ImportError or a silent fallback at runtime. The fix is to detect the
  bundle-ref shape and `load_bundle(...)` it first.

Requirements:
  - ANTHROPIC_API_KEY environment variable set
  - Network access on first run (prepare() fetches provider/tool modules)

Run:
  uv run python examples/23_spawn_with_bundle_refs.py
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

# This example lives inside the foundation repo, so load foundation + a provider
# from the local checkout. In your own app you would load from git instead, e.g.
#   FOUNDATION = "git+https://github.com/microsoft/amplifier-foundation@main"
FOUNDATION_REPO = Path(__file__).parent.parent
PROVIDER_FILE = FOUNDATION_REPO / "providers" / "anthropic-sonnet.yaml"


# =============================================================================
# SECTION 1: A self-contained, GENERIC observability hook module
# =============================================================================
# We write a tiny hook module to a temp dir so this example stays a single,
# runnable file. The hook appends one JSON line per event to a log file. It is
# completely generic -- it knows nothing about any app, server, or transport.
# In a real project this would be a published module referenced by `source`.

OBS_HOOK_SOURCE = '''\
"""Generic observability hook: append one JSON line per event to a log file."""

import json
import os
from typing import Any

from amplifier_core import HookResult

# A representative slice of amplifier-core events (see amplifier_core ALL_EVENTS).
# NOTE: register concrete event names -- there is no "*" wildcard subscription.
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
        session_id = getattr(coordinator, "session_id", None)
        if not session_id and isinstance(data, dict):
            session_id = data.get("session_id")
        if log_path:
            with open(log_path, "a") as fh:
                fh.write(json.dumps({"label": label, "event": event, "session_id": session_id}) + "\\n")
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

# A trivial agent bundle that the bundle-ref agent points at. It carries only an
# instruction; its provider/orchestrator come from the parent via compose=True.
ECHO_AGENT_BUNDLE = """\
bundle:
  name: echo-agent
  version: 1.0.0
  description: A terse echo agent used to demonstrate bundle-ref spawning.
instruction: |
  You are a terse echo agent. Reply with exactly the single word the user
  requests -- no punctuation, no extra words.
"""


def scaffold_workspace() -> tuple[Path, Path, Path]:
    """Write the generic hook module + echo-agent bundle to a temp workspace."""
    work = Path(tempfile.mkdtemp(prefix="example23-"))

    pkg_dir = work / "obs_hook" / "amplifier_module_hooks_observability"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text(OBS_HOOK_SOURCE)
    (work / "obs_hook" / "pyproject.toml").write_text(OBS_HOOK_PYPROJECT)

    agent_file = work / "echo-agent.yaml"
    agent_file.write_text(ECHO_AGENT_BUNDLE)

    log_file = work / "events.log"
    return work, agent_file, log_file


# =============================================================================
# SECTION 2: The SAFE spawn capability (the fix)
# =============================================================================


def register_spawn_capability(
    session: Any,
    prepared: PreparedBundle,
    agent_configs: dict[str, dict[str, Any]],
) -> None:
    """Register `session.spawn` so the parent can delegate to child sessions.

    Handles BOTH agent-config shapes (see module docstring): an inline config
    dict, or a lazy bundle-ref `{"bundle": "<uri>"}` that must be loaded.
    """

    async def spawn_capability(
        agent_name: str,
        instruction: str,
        parent_session: Any,
        agent_configs: dict[str, dict[str, Any]],
        sub_session_id: str | None = None,
        **kwargs: Any,  # future-proof: accept new kwargs without crashing
    ) -> dict[str, Any]:
        config = agent_configs[agent_name]

        # --- THE FIX: branch on the agent-config shape -----------------------
        # 1. Lazy bundle-ref: a single-key {"bundle": "<uri>"}. It has NO inline
        #    fields, so we MUST load_bundle() it. Building Bundle(**config.get(...))
        #    here would yield a structurally empty child that inherits the
        #    parent's orchestrator and falls to a no-tools backend (ImportError /
        #    silent fallback at runtime).
        # 2. Inline config: a dict of bundle fields -- build the Bundle directly.
        if "bundle" in config and len(config) == 1:
            child_bundle = await load_bundle(config["bundle"])
        else:
            child_bundle = Bundle(
                name=agent_name,
                version="1.0.0",
                session=config.get("session", {}),
                providers=config.get("providers", []),
                tools=config.get("tools", []),
                hooks=config.get("hooks", []),
                instruction=config.get("instruction")
                or config.get("system", {}).get("instruction"),
            )

        # compose=True (the default) composes the PARENT bundle with the child,
        # so parent-level hooks/providers/tools propagate to the child session.
        return await prepared.spawn(
            child_bundle=child_bundle,
            instruction=instruction,
            session_id=sub_session_id,
            parent_session=parent_session,
            compose=True,
        )

    session.coordinator.register_capability("session.spawn", spawn_capability)


# =============================================================================
# MAIN
# =============================================================================


async def main() -> None:
    print("=" * 70)
    print("Example 23: Spawn from bundle-ref agents + hook propagation")
    print("=" * 70)

    work, agent_file, log_file = scaffold_workspace()

    # Load foundation + provider, then compose the GENERIC observability hook
    # into the PARENT bundle. Because spawn() composes parent+child, this hook
    # is inherited by every spawned child (no per-child wiring needed).
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

    print("\n[1/4] Preparing parent bundle (downloads modules if needed)...")
    prepared = await composed.prepare()

    # The two agent-config shapes, side by side:
    agent_configs: dict[str, dict[str, Any]] = {
        # Shape 1 -- INLINE: a dict of bundle fields.
        "inline-echo": {"instruction": "Reply with exactly the single word requested."},
        # Shape 2 -- BUNDLE-REF: a single-key dict pointing at a bundle to load.
        "ref-echo": {"bundle": str(agent_file)},
    }

    print("[2/4] Creating session...")
    session = await prepared.create_session(session_cwd=work)

    # ORDER MATTERS (T2): register session.spawn AFTER create_session and
    # BEFORE execute/spawn. If you register it too early (no session yet) or too
    # late (after execution starts), the orchestrator silently falls back to a
    # no-tools backend and your delegation never gets full sub-sessions.
    register_spawn_capability(session, prepared, agent_configs)

    spawn = session.coordinator.get_capability("session.spawn")

    print("[3/4] Spawning children (both config shapes)...")
    async with session:
        inline_result = await spawn(
            "inline-echo", "Reply with the word PING", session, agent_configs
        )
        ref_result = await spawn(
            "ref-echo", "Reply with the word PONG", session, agent_configs
        )

    print("\n[4/4] Results:")
    print(
        f"  inline-echo -> {inline_result.get('output')!r}  ({inline_result.get('status')})"
    )
    print(
        f"  ref-echo    -> {ref_result.get('output')!r}  ({ref_result.get('status')})"
    )

    # Prove hook propagation (T4): the parent-composed observability hook fired
    # inside the spawned children. Show the events captured for each child sid.
    records = [
        json.loads(line) for line in log_file.read_text().splitlines() if line.strip()
    ]
    print("\nHook propagation (events captured by the parent-composed hook):")
    for label, result in (("inline-echo", inline_result), ("ref-echo", ref_result)):
        sid = result.get("session_id")
        events = [r["event"] for r in records if r.get("session_id") == sid]
        ok = "OK" if events else "MISSING"
        print(f"  [{ok}] child {label} (sid={sid}): {events}")

    print("\n" + "=" * 70)
    print("KEY TAKEAWAYS")
    print("=" * 70)
    print("1. Bundle-ref agents ({'bundle': '<uri>'}) must be load_bundle()'d --")
    print("   do NOT build them from config.get(...) or you get an empty child.")
    print("2. Hooks composed into the PARENT bundle reach every child via")
    print("   prepared.spawn(compose=True) -- no per-child wiring required.")
    print("3. Register session.spawn AFTER create_session and BEFORE execute.")


if __name__ == "__main__":
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ERROR: Set ANTHROPIC_API_KEY to run this example.")
        raise SystemExit(1)
    asyncio.run(main())
