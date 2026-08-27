"""Tests for hooks-tool-dedupe: the same-batch read coalescer.

Pure-function and hook-level tests only -- no session, no network, no
provider. Mirrors the test plan in the implementation spec (yoc-dedup-spec.md
Section 4.8) line for line: each test name below corresponds to one row of
that table.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from typing import Any

import pytest
from amplifier_module_hooks_tool_dedupe import (
    DEFAULT_MAX_BATCHES,
    DEFAULT_MIN_BYTES,
    ToolDedupeHook,
)


class FakeHooks:
    """Minimal stand-in for coordinator.hooks -- records emitted events."""

    def __init__(self) -> None:
        self.emitted: list[tuple[str, dict]] = []

    async def emit(self, event: str, data: dict) -> None:
        self.emitted.append((event, dict(data)))


def make_hook(**overrides) -> tuple[ToolDedupeHook, FakeHooks]:
    hooks = FakeHooks()
    kwargs = {
        "hooks": hooks,
        "enabled": True,
        "tools": ["read_file", "load_skill"],
        "min_bytes": DEFAULT_MIN_BYTES,
        "max_batches": DEFAULT_MAX_BATCHES,
    }
    kwargs.update(overrides)
    return ToolDedupeHook(**kwargs), hooks


def read_post_data(
    *,
    pgid: str | None = "batch-1",
    tool_call_id: str = "call-1",
    file_path: str = "/repo/module.py",
    offset: int | None = None,
    limit: int | None = None,
    content: str | None = None,
    success: bool = True,
    tool_input: dict | str | None = None,
) -> dict:
    """Build a tool:post payload shaped like loop-streaming's real emission
    (parallel path :3720-3729 / :1136-1141 in the verified source): {tool_name,
    tool_call_id, tool_input, result, parallel_group_id}, where `result` is
    `ToolResult.model_dump()` -- {"success", "output", "error"}.
    """
    body = content if content is not None else ("x" * DEFAULT_MIN_BYTES)
    if tool_input is None:
        tool_input = {"file_path": file_path}
        if offset is not None:
            tool_input["offset"] = offset
        if limit is not None:
            tool_input["limit"] = limit
    return {
        "tool_name": "read_file",
        "tool_call_id": tool_call_id,
        "tool_input": tool_input,
        "result": {
            "success": success,
            "output": ({"file_path": file_path, "content": body} if success else None),
            "error": None if success else {"message": "boom"},
        },
        "parallel_group_id": pgid,
    }


# ---------------------------------------------------------------------------
# Core safety invariant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sequential_call_never_deduped():
    """parallel_group_id=None => action == 'continue' even for a byte-identical
    repeat. The core safety invariant: no batch id, no dedupe."""
    hook, _ = make_hook()
    data1 = read_post_data(pgid=None, tool_call_id="call-1")
    data2 = read_post_data(pgid=None, tool_call_id="call-2")

    res1 = await hook.handle_tool_post("tool:post", data1)
    res2 = await hook.handle_tool_post("tool:post", data2)

    assert res1.action == "continue"
    assert res2.action == "continue"


# ---------------------------------------------------------------------------
# Basic coalescing behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_first_read_passes_through():
    """First call => continue; content untouched."""
    hook, _ = make_hook()
    data = read_post_data()
    res = await hook.handle_tool_post("tool:post", data)
    assert res.action == "continue"


@pytest.mark.asyncio
async def test_second_identical_read_in_batch_is_marked():
    """Same pgid + same key + same content => action == 'modify',
    data['result'] is a str containing '[deduped]' and the holder's id."""
    hook, _ = make_hook()
    first = read_post_data(tool_call_id="holder-1")
    second = read_post_data(tool_call_id="dup-1")

    res1 = await hook.handle_tool_post("tool:post", first)
    res2 = await hook.handle_tool_post("tool:post", second)

    assert res1.action == "continue"
    assert res2.action == "modify"
    assert res2.data is not None
    assert isinstance(res2.data["result"], str)
    assert "[deduped]" in res2.data["result"]
    assert "holder-1" in res2.data["result"]


@pytest.mark.asyncio
async def test_returns_new_object_not_mutated_input():
    """res.data is not original_data AND res.data['result'] is not
    original_data['result']. Pins the object-identity constraint -- this is
    the test that catches a silent no-op regression (spec Section 1.1)."""
    hook, _ = make_hook()
    first = read_post_data(tool_call_id="holder-1")
    second = read_post_data(tool_call_id="dup-1")

    await hook.handle_tool_post("tool:post", first)
    res2 = await hook.handle_tool_post("tool:post", second)

    assert res2.data is not None
    assert res2.data is not second
    assert res2.data["result"] is not second["result"]


@pytest.mark.asyncio
async def test_result_is_str_not_dict():
    """isinstance(res.data['result'], str). A dict/list would get json.dumps'd
    by loop-streaming, wrapping the marker in JSON noise (spec Section 1.1)."""
    hook, _ = make_hook()
    await hook.handle_tool_post("tool:post", read_post_data(tool_call_id="holder-1"))
    res2 = await hook.handle_tool_post(
        "tool:post", read_post_data(tool_call_id="dup-1")
    )
    assert res2.data is not None
    assert isinstance(res2.data["result"], str)


@pytest.mark.asyncio
async def test_divergent_content_passes_through():
    """Same key, different content => continue, and dedupe:divergent
    emitted. The file changed between two reads inside one batch."""
    hook, hooks = make_hook()
    first = read_post_data(tool_call_id="call-1", content="a" * DEFAULT_MIN_BYTES)
    second = read_post_data(tool_call_id="call-2", content="b" * DEFAULT_MIN_BYTES)

    res1 = await hook.handle_tool_post("tool:post", first)
    res2 = await hook.handle_tool_post("tool:post", second)

    assert res1.action == "continue"
    assert res2.action == "continue"
    assert any(evt == "dedupe:divergent" for evt, _ in hooks.emitted)


@pytest.mark.asyncio
async def test_different_offset_not_deduped():
    """(p,1,2000) vs (p,500,100) are distinct keys."""
    hook, _ = make_hook()
    first = read_post_data(offset=1, limit=2000, tool_call_id="call-1")
    second = read_post_data(offset=500, limit=100, tool_call_id="call-2")

    res1 = await hook.handle_tool_post("tool:post", first)
    res2 = await hook.handle_tool_post("tool:post", second)

    assert res1.action == "continue"
    assert res2.action == "continue"


@pytest.mark.asyncio
async def test_offset_limit_defaults_normalised():
    """read(p) and read(p, offset=1, limit=2000) collide -- both normalise to
    the tool's own defaults (read.py:116-117)."""
    hook, _ = make_hook()
    bare = read_post_data(tool_call_id="call-1")  # no offset/limit given
    explicit = read_post_data(offset=1, limit=2000, tool_call_id="call-2")

    res1 = await hook.handle_tool_post("tool:post", bare)
    res2 = await hook.handle_tool_post("tool:post", explicit)

    assert res1.action == "continue"
    assert res2.action == "modify"


@pytest.mark.asyncio
async def test_different_batches_isolated():
    """Same key, different pgid => continue."""
    hook, _ = make_hook()
    first = read_post_data(pgid="batch-1", tool_call_id="call-1")
    second = read_post_data(pgid="batch-2", tool_call_id="call-2")

    res1 = await hook.handle_tool_post("tool:post", first)
    res2 = await hook.handle_tool_post("tool:post", second)

    assert res1.action == "continue"
    assert res2.action == "continue"


@pytest.mark.asyncio
async def test_below_min_bytes_passes_through():
    """200-byte content => continue (marker would cost more than it saves)."""
    hook, _ = make_hook(min_bytes=1000)
    first = read_post_data(content="x" * 200, tool_call_id="call-1")
    second = read_post_data(content="x" * 200, tool_call_id="call-2")

    res1 = await hook.handle_tool_post("tool:post", first)
    res2 = await hook.handle_tool_post("tool:post", second)

    assert res1.action == "continue"
    assert res2.action == "continue"


@pytest.mark.asyncio
async def test_failed_result_passes_through():
    """success=False => continue. An error is not a cacheable answer."""
    hook, _ = make_hook()
    first = read_post_data(success=False, tool_call_id="call-1")
    second = read_post_data(success=False, tool_call_id="call-2")

    res1 = await hook.handle_tool_post("tool:post", first)
    res2 = await hook.handle_tool_post("tool:post", second)

    assert res1.action == "continue"
    assert res2.action == "continue"


@pytest.mark.asyncio
async def test_load_skill_deduped_same_batch():
    """Mechanism (4) via the tool allowlist, no separate code path: adding
    'load_skill' to `tools` is the entire skill-dedupe mechanism."""
    hook, _ = make_hook()
    body = "s" * DEFAULT_MIN_BYTES
    first = {
        "tool_name": "load_skill",
        "tool_call_id": "call-1",
        "tool_input": {"skill_name": "brainstorming"},
        "result": {
            "success": True,
            "output": {"content": body, "skill_name": "brainstorming"},
            "error": None,
        },
        "parallel_group_id": "batch-1",
    }
    second = dict(first, tool_call_id="call-2")

    res1 = await hook.handle_tool_post("tool:post", first)
    res2 = await hook.handle_tool_post("tool:post", second)

    assert res1.action == "continue"
    assert res2.action == "modify"
    assert res2.data is not None
    assert "brainstorming" in res2.data["result"]


@pytest.mark.asyncio
async def test_unknown_tool_passes_through():
    """bash => continue. Not in the allowlist."""
    hook, _ = make_hook()
    data = {
        "tool_name": "bash",
        "tool_call_id": "call-1",
        "tool_input": {"command": "ls"},
        "result": {
            "success": True,
            "output": {"content": "x" * DEFAULT_MIN_BYTES},
            "error": None,
        },
        "parallel_group_id": "batch-1",
    }
    res = await hook.handle_tool_post("tool:post", dict(data))
    res2 = await hook.handle_tool_post("tool:post", dict(data, tool_call_id="call-2"))
    assert res.action == "continue"
    assert res2.action == "continue"


@pytest.mark.asyncio
async def test_malformed_tool_input_passes_through():
    """tool_input an unparseable string => continue, no raise."""
    hook, _ = make_hook()
    data = read_post_data()
    data["tool_input"] = "{not json"
    res = await hook.handle_tool_post("tool:post", data)
    assert res.action == "continue"


@pytest.mark.asyncio
async def test_missing_content_key_passes_through():
    """Output dict without 'content' => continue."""
    hook, _ = make_hook()
    data = read_post_data()
    data["result"]["output"] = {"file_path": "/repo/module.py"}
    res = await hook.handle_tool_post("tool:post", data)
    assert res.action == "continue"


@pytest.mark.asyncio
async def test_concurrent_batch_single_content_holder():
    """4 handlers asyncio.gather'd on one key => exactly 1 continue, 3
    modify. Pins the no-await-between-lookup-and-store invariant (spec
    Section 4.4): the read-check-write critical section must be atomic
    under asyncio's cooperative scheduling."""
    hook, _ = make_hook()
    calls = [read_post_data(tool_call_id=f"call-{i}") for i in range(4)]

    results = await asyncio.gather(
        *[hook.handle_tool_post("tool:post", c) for c in calls]
    )

    continues = [r for r in results if r.action == "continue"]
    modifies = [r for r in results if r.action == "modify"]
    assert len(continues) == 1
    assert len(modifies) == 3


@pytest.mark.asyncio
async def test_max_batches_lru_eviction():
    """9 batches with max_batches=8 => oldest evicted, no unbounded growth."""
    hook, _ = make_hook(max_batches=8)
    for i in range(9):
        data = read_post_data(pgid=f"batch-{i}", tool_call_id=f"call-{i}")
        await hook.handle_tool_post("tool:post", data)

    assert len(hook._batches) == 8
    assert "batch-0" not in hook._batches
    assert "batch-8" in hook._batches


@pytest.mark.asyncio
async def test_disabled_is_total_noop():
    """enabled: false => continue for every input above."""
    hook, _ = make_hook(enabled=False)
    first = read_post_data(tool_call_id="call-1")
    second = read_post_data(tool_call_id="call-2")

    res1 = await hook.handle_tool_post("tool:post", first)
    res2 = await hook.handle_tool_post("tool:post", second)

    assert res1.action == "continue"
    assert res2.action == "continue"
    assert hook._batches == {}


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coalesced_event_emitted_with_expected_fields():
    """dedupe:coalesced carries tool_name, parallel_group_id, holder/duplicate
    ids, target, bytes_saved, sha256 (spec Section 4.7)."""
    hook, hooks = make_hook()
    body = "x" * DEFAULT_MIN_BYTES
    first = read_post_data(tool_call_id="holder-1", content=body)
    second = read_post_data(tool_call_id="dup-1", content=body)

    await hook.handle_tool_post("tool:post", first)
    await hook.handle_tool_post("tool:post", second)

    coalesced = [d for evt, d in hooks.emitted if evt == "dedupe:coalesced"]
    assert len(coalesced) == 1
    payload = coalesced[0]
    assert payload["tool_name"] == "read_file"
    assert payload["parallel_group_id"] == "batch-1"
    assert payload["holder_tool_call_id"] == "holder-1"
    assert payload["duplicate_tool_call_id"] == "dup-1"
    assert payload["target"] == "/repo/module.py"
    assert payload["bytes_saved"] == len(body)
    assert payload["sha256"] == hashlib.sha256(body.encode()).hexdigest()[:12]


@pytest.mark.asyncio
async def test_divergent_event_emitted_with_expected_fields():
    hook, hooks = make_hook()
    first = read_post_data(tool_call_id="call-1", content="a" * DEFAULT_MIN_BYTES)
    second = read_post_data(tool_call_id="call-2", content="b" * DEFAULT_MIN_BYTES)

    await hook.handle_tool_post("tool:post", first)
    await hook.handle_tool_post("tool:post", second)

    divergent = [d for evt, d in hooks.emitted if evt == "dedupe:divergent"]
    assert len(divergent) == 1
    assert divergent[0]["tool_name"] == "read_file"
    assert divergent[0]["parallel_group_id"] == "batch-1"
    assert divergent[0]["target"] == "/repo/module.py"


@pytest.mark.asyncio
async def test_no_telemetry_emitted_when_disabled():
    """A disabled hook must not touch coordinator.hooks.emit at all."""
    hook, hooks = make_hook(enabled=False)
    first = read_post_data(tool_call_id="call-1")
    second = read_post_data(tool_call_id="call-2")
    await hook.handle_tool_post("tool:post", first)
    await hook.handle_tool_post("tool:post", second)
    assert hooks.emitted == []


# ---------------------------------------------------------------------------
# mount() wiring
# ---------------------------------------------------------------------------


class FakeCoordinator:
    def __init__(self) -> None:
        self.hooks = FakeHooks()
        self.registered: list[tuple[str, Any, int, str | None]] = []
        self.contributors: list[tuple[str, str]] = []
        self._contributor_callback: Callable[[], list[str]] | None = None

        outer = self

        class _Hooks(FakeHooks):
            def register(self, event, handler, priority=0, name=None):
                outer.registered.append((event, handler, priority, name))

        self.hooks = _Hooks()

    def register_contributor(self, channel, name, callback):
        self.contributors.append((channel, name))
        self._contributor_callback = callback


@pytest.mark.asyncio
async def test_mount_registers_tool_post_at_priority_5():
    """priority=5 is load-bearing: must run before hooks-tool-truncation's
    priority=10 (spec Section 4.3)."""
    coordinator = FakeCoordinator()
    result = await __import__("amplifier_module_hooks_tool_dedupe").mount(
        coordinator, {"enabled": True}
    )

    assert len(coordinator.registered) == 1
    event, _handler, priority, name = coordinator.registered[0]
    assert event == "tool:post"
    assert priority == 5
    assert name == "hooks-tool-dedupe"
    assert result["name"] == "hooks-tool-dedupe"


@pytest.mark.asyncio
async def test_mount_declares_observability_events():
    coordinator = FakeCoordinator()
    from amplifier_module_hooks_tool_dedupe import mount

    await mount(coordinator, {"enabled": True})

    assert len(coordinator.contributors) == 1
    channel, name = coordinator.contributors[0]
    assert channel == "observability.events"
    assert name == "hooks-tool-dedupe"
    assert coordinator._contributor_callback is not None
    assert coordinator._contributor_callback() == [
        "dedupe:coalesced",
        "dedupe:divergent",
    ]


@pytest.mark.asyncio
async def test_mount_defaults_to_disabled():
    """Stage 1 ships default-OFF: mounting with no config must be a total
    no-op at runtime."""
    coordinator = FakeCoordinator()
    from amplifier_module_hooks_tool_dedupe import mount

    result = await mount(coordinator, None)
    assert result["config"]["enabled"] is False

    _, handler, _, _ = coordinator.registered[0]
    first = read_post_data(tool_call_id="call-1")
    second = read_post_data(tool_call_id="call-2")
    res1 = await handler("tool:post", first)
    res2 = await handler("tool:post", second)
    assert res1.action == "continue"
    assert res2.action == "continue"


def test_tool_input_json_string_is_parsed():
    """tool_input may arrive as a JSON string -- parse defensively."""
    hook, _ = make_hook()
    data = read_post_data(tool_call_id="call-1")
    data["tool_input"] = json.dumps(data["tool_input"])
    # Exercised via the async handler in an event loop.
    result = asyncio.run(hook.handle_tool_post("tool:post", data))
    assert result.action == "continue"
