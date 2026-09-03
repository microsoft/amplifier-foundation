"""Behavioural probe for the per-delegate timeout contract.

Runs against WHATEVER ``amplifier_module_tool_delegate`` is importable, so the
same script can be executed on the parent commit and on the patched tree and
the two outputs diffed. It asserts nothing: it prints observed facts.

Probes:
  A. the timed-out delegate's own model-visible output (spawn path)
  B. the same for the resume path
  C. sibling survival under ``asyncio.gather`` (k64 gate G-D1)
  D. a NORMAL completion's model-visible output -- the byte-identity baseline

Usage:
    uv run python docs/lanes/bp0-delegate-timeout-partial-consumer/probe_timeout_contract.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import AsyncMock, MagicMock

from amplifier_module_tool_delegate import DelegateTool


def _make_tool(*, timeout, spawn_fn=None, resume_fn=None, partial_fn=None):
    coordinator = MagicMock()
    coordinator.session_id = "parent-session-123"
    coordinator.config = {"agents": {"test-agent": {}}}
    coordinator.session_state = {}
    coordinator._tool_dispatch_context = {}
    coordinator._tool_dispatch_contexts = {}
    capabilities = {
        "session.spawn": spawn_fn or AsyncMock(),
        "session.resume": resume_fn or AsyncMock(),
        "self_delegation_depth": 0,
    }
    if partial_fn is not None:
        capabilities["session.partial"] = partial_fn
    coordinator.get_capability = lambda name: capabilities.get(name)
    coordinator.get = MagicMock(return_value=None)
    parent_session = MagicMock()
    parent_session.config = {"session": {"orchestrator": {}}}
    coordinator.session = parent_session
    return DelegateTool(
        coordinator,
        {"features": {}, "settings": {"exclude_tools": [], "timeout": timeout}},
    )


def _hooks():
    hooks = MagicMock()
    hooks.emit = AsyncMock()
    return hooks


def _emissions(hooks):
    return [(a[0], a[1]) for a, _k in hooks.emit.call_args_list]


async def _never_finishes(**_kwargs):
    await asyncio.Future()


async def _completes(**_kwargs):
    return {"output": "the whole answer", "session_id": "sub-1", "turn_count": 3}


async def _spawn(tool, hooks):
    return await tool._spawn_new_session(
        agent_name="test-agent",
        instruction="Do something",
        context_depth="none",
        context_scope="conversation",
        context_turns=5,
        provider_preferences=None,
        hooks=hooks,
        tool_call_id="call-timeout",
        parallel_group_id="parallel-timeout",
    )


def _normalise(output):
    """Blank out values that legitimately vary run to run."""
    if not isinstance(output, dict):
        return output
    out = dict(output)
    if isinstance(out.get("session_id"), str) and out["session_id"].startswith("sub_"):
        out["session_id"] = "<generated>"
    meta = out.get("metadata")
    if isinstance(meta, dict) and "elapsed_s" in meta:
        meta = dict(meta)
        meta["elapsed_s"] = "<elapsed>"
        out["metadata"] = meta
    return out


def _show(label, value):
    print(f"--- {label} ---")
    print(json.dumps(value, indent=2, default=str, sort_keys=True))
    print()


async def main() -> int:
    partial = lambda sid: {"text": "straggler got this far", "segments": 7}  # noqa: E731

    # A. spawn timeout, WITH a session.partial capability registered
    tool = _make_tool(
        timeout=0.01,
        spawn_fn=AsyncMock(side_effect=_never_finishes),
        partial_fn=partial,
    )
    hooks = _hooks()
    res = await _spawn(tool, hooks)
    _show("A. spawn timeout: result.success", res.success)
    _show("A. spawn timeout: result.output", _normalise(res.output))
    err = next((p for n, p in _emissions(hooks) if n == "delegate:error"), None)
    if err is not None:
        err = dict(err)
        err["sub_session_id"] = "<generated>"
        err.pop("elapsed_s", None)
    _show("A. spawn timeout: delegate:error payload (elapsed_s dropped)", err)
    print(
        "A. CONTRACT: status=={} | 'response' absent=={} | "
        "'partial_available' present=={}\n".format(
            (res.output or {}).get("status"),
            "response" not in (res.output or {}),
            "partial_available" in (res.output or {}),
        )
    )

    # B. resume timeout
    tool = _make_tool(
        timeout=0.01,
        resume_fn=AsyncMock(side_effect=_never_finishes),
        partial_fn=partial,
    )
    res = await tool._resume_existing_session(
        session_id="child-session-001_test-agent",
        instruction="Continue",
        hooks=_hooks(),
        tool_call_id="call-resume-timeout",
        parallel_group_id="parallel-resume-timeout",
    )
    _show("B. resume timeout: result.output", _normalise(res.output))
    print(
        "B. CONTRACT: status=={} | 'response' absent=={} | "
        "'partial_available' present=={}\n".format(
            (res.output or {}).get("status"),
            "response" not in (res.output or {}),
            "partial_available" in (res.output or {}),
        )
    )

    # C. G-D1: do completed siblings survive a straggler in the same gather?
    fast = _make_tool(timeout=60, spawn_fn=AsyncMock(side_effect=_completes))
    slow = _make_tool(
        timeout=0.01,
        spawn_fn=AsyncMock(side_effect=_never_finishes),
        partial_fn=partial,
    )
    try:
        results = await asyncio.gather(
            _spawn(fast, _hooks()), _spawn(fast, _hooks()), _spawn(slow, _hooks())
        )
        survived = sum(1 for r in results if r.success)
        print(
            f"--- C. G-D1 sibling survival ---\ngather returned; "
            f"completed siblings surviving = {survived} of 2; "
            f"discarded-completed-sibling count = {2 - survived}\n"
        )
    except BaseException as exc:  # noqa: BLE001 - the harm being probed
        print(
            f"--- C. G-D1 sibling survival ---\ngather RAISED "
            f"{type(exc).__name__}: {exc} -> ALL completed siblings discarded\n"
        )

    # D. byte-identity baseline for a NORMAL completion
    tool = _make_tool(
        timeout=60, spawn_fn=AsyncMock(side_effect=_completes), partial_fn=partial
    )
    res = await _spawn(tool, _hooks())
    _show("D. normal completion: result.success", res.success)
    _show("D. normal completion: result.output", _normalise(res.output))
    print("D. normal completion: serialized output")
    print(res.get_serialized_output())
    print()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
