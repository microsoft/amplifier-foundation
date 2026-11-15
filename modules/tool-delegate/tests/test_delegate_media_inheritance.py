"""Synthetic canonical delegate forwarding; not installed or actual-host acceptance."""

from copy import deepcopy
import base64
from contextlib import ExitStack
import io
import importlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_module_tool_delegate import DelegateTool
from amplifier_foundation.session.media import MEDIA_NAMESPACE as NS


def ref(label="selected", *, mime="image/png"):
    return {
        "v": 1, "session_id": "parent", "sha256": "a" * 64, "mime": mime,
        "bytes": 42, "width": 2, "height": 3,
        "provenance": {
            "kind": "tool", "session_id": "parent", "id": label,
            "created_at": "2025-01-01T00:00:00+00:00",
            "expires_at": "2035-01-01T00:00:00+00:00",
        },
    }


def row(role, text, label, **extra):
    if role == "tool":
        extra.setdefault("tool_call_id", f"call-{label}")
    return {"role": role, "content": text,
            "metadata": {NS: {"refs": [ref(label)]}, "private": "DO NOT FORWARD"},
            "provider_state": "DO NOT FORWARD", **extra}


def harness(messages, spawn=None, *, enabled=True):
    captured = []

    async def capture(**kwargs):
        captured.append(kwargs)
        return {"output": "done", "session_id": "child", "metadata": {}}

    coordinator = MagicMock()
    coordinator.session_id = "parent"
    coordinator.config = {"agents": {"worker": {}}}
    coordinator.session_state = {}
    coordinator._tool_dispatch_context = {}
    coordinator.session = SimpleNamespace(config={})
    context = SimpleNamespace(get_messages=AsyncMock(return_value=messages))
    hooks = SimpleNamespace(emit=AsyncMock())
    coordinator.get = lambda name: {"context": context, "hooks": hooks}.get(name)
    coordinator.get_capability = lambda name: {"session.spawn": spawn or capture}.get(name)
    tool = DelegateTool(coordinator, {
        "features": {"context_inheritance": {"enabled": enabled}},
        "settings": {"exclude_tools": ["delegate"], "exclude_hooks": ["private-hook"]},
    })
    return tool, captured, hooks, context


async def execute(tool, **kwargs):
    return await tool.execute({
        "agent": "worker", "instruction": "inspect selected media",
        "context_depth": "all", "context_scope": "conversation", **kwargs,
    })


def labels(rows):
    return [r["metadata"][NS]["refs"][0]["provenance"]["id"] for r in rows]


@pytest.fixture
def actual_cli_inheritance(monkeypatch):
    """Optional source-join lane; never replaces CLI functions or their imports.

    Set AMPLIFIER_CLI_SOURCE to an existing CLI checkout. The CLI is not a
    Foundation dependency, so absence skips this lane rather than installing it.
    """
    source = os.environ.get("AMPLIFIER_CLI_SOURCE")
    if source:
        monkeypatch.syspath_prepend(source)
    # The public doctor startup route imports modules without initializing the
    # application, credentials or TLS shim. No function or class is substituted.
    with monkeypatch.context() as startup:
        startup.setattr(sys, "argv", ["amplifier", "doctor"])
        try:
            module = importlib.import_module("amplifier_app_cli.session_media_inheritance")
            media = importlib.import_module("amplifier_app_cli.session_media")
            native = importlib.import_module("amplifier_app_cli.session_store")
        except ModuleNotFoundError as error:
            if error.name == "amplifier_app_cli" and not source:
                pytest.skip("Actual CLI join requires installed CLI or AMPLIFIER_CLI_SOURCE")
            raise
    if source:
        assert Path(module.__file__).is_relative_to(Path(source).resolve())
    return module, media, native


def complete_tool_parent(name, *, with_media=True):
    result = row("tool", "selected observation", "selectedrefs",
                 name=name, tool_call_id="selected-call-1")
    if not with_media:
        result.pop("metadata")
    return [
        {"role": "user", "content": "inspect the selected observation"},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "selected-call-1", "type": "function",
            "function": {"name": name, "arguments": "{}"},
        }]},
        result,
        {"role": "assistant", "content": "finished"},
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,name", [
    ("agents", "delegate"), ("agents", "task"), ("full", "bash"),
])
async def test_actual_cli_complete_tool_parent_refuses_before_spawn(actual_cli_inheritance, scope, name):
    from amplifier_foundation.session.diagnosis import diagnose_transcript

    cli, _, _ = actual_cli_inheritance
    messages = complete_tool_parent(name)
    assert diagnose_transcript(messages)["status"] == "healthy"
    original = deepcopy(messages)
    owner_lookup = MagicMock(side_effect=AssertionError("No media owner lookup permitted"))
    child_context = SimpleNamespace(add_message=AsyncMock())
    child = SimpleNamespace(session_id="child", coordinator=SimpleNamespace(
        get_capability=owner_lookup, get=lambda _: child_context,
    ))
    parent = SimpleNamespace(session_id="parent", coordinator=SimpleNamespace(
        get_capability=owner_lookup,
    ))
    forwarded = []
    cli_errors = []

    async def actual_receiver(*, parent_messages, **kwargs):
        forwarded.append(deepcopy(parent_messages))
        try:
            await cli.inherit_selected_messages(child, parent, parent_messages)
        except ValueError as error:
            cli_errors.append(str(error))
            raise
        return {"output": "done", "session_id": "child"}

    tool, _, hooks, _ = harness(messages, actual_receiver)
    result = await execute(tool, context_scope=scope)
    assert not result.success
    assert forwarded == [], f"Spawn received isolated tool rows; actual CLI refused: {cli_errors}"
    assert "unavailable" in str(result.error).lower()
    assert "tool" in str(result.error).lower()
    assert cli_errors == []  # Refusal belongs before the receiver/adoption boundary.
    owner_lookup.assert_not_called()
    child_context.add_message.assert_not_awaited()
    assert messages == original
    assert "media_inheritance" not in json.dumps(result.model_dump())
    assert "selectedrefs" not in json.dumps(hooks.emit.call_args_list, default=str)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,name", [
    ("agents", "delegate"), ("agents", "task"), ("full", "bash"),
])
async def test_mixed_user_tool_media_never_partially_forwards(scope, name):
    messages = complete_tool_parent(name)
    messages[0]["metadata"] = {NS: {"refs": [ref("user-ref")]}}
    before = deepcopy(messages)
    tool, captured, _, _ = harness(messages)
    result = await execute(tool, context_scope=scope)
    assert not result.success
    assert captured == []
    assert "unavailable" in str(result.error).lower()
    assert messages == before


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,name", [
    ("agents", "delegate"), ("agents", "task"), ("full", "bash"),
])
async def test_empty_tool_ref_envelope_keeps_text_only_semantics(scope, name):
    messages = complete_tool_parent(name)
    messages[2]["metadata"] = {NS: {"refs": []}}
    tool, captured, _, _ = harness(messages)
    result = await execute(tool, context_scope=scope)
    assert result.success
    assert "parent_messages" not in captured[0]
    assert captured[0]["instruction"].count("ASSISTANT: [") == 1


@pytest.mark.asyncio
async def test_actual_cli_group_guard_stays_intact(actual_cli_inheritance):
    """The receiver must still refuse the historical isolated tool carrier."""
    cli, _, _ = actual_cli_inheritance
    lookup = MagicMock(side_effect=AssertionError("Guard must precede owner lookup"))
    context = SimpleNamespace(add_message=AsyncMock())
    child = SimpleNamespace(coordinator=SimpleNamespace(
        get_capability=lookup, get=lambda _: context,
    ))
    parent = SimpleNamespace(coordinator=SimpleNamespace(get_capability=lookup))
    selected = row("tool", "", "selectedrefs", name="delegate", tool_call_id="selected-call-1")
    with pytest.raises(ValueError, match="complete tool-call/result groups"):
        await cli.inherit_selected_messages(child, parent, [selected])
    lookup.assert_not_called()
    context.add_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,name", [
    ("agents", "delegate"), ("agents", "task"), ("full", "bash"),
])
async def test_actual_cli_text_only_tool_parent_keeps_summary_once(actual_cli_inheritance, scope, name):
    cli, _, _ = actual_cli_inheritance
    messages = complete_tool_parent(name, with_media=False)
    context = SimpleNamespace(add_message=AsyncMock())
    lookup = MagicMock(side_effect=AssertionError("No text-only media owner lookup"))
    child = SimpleNamespace(coordinator=SimpleNamespace(
        get_capability=lookup, get=lambda _: context,
    ))
    parent = SimpleNamespace(coordinator=SimpleNamespace(get_capability=lookup))
    calls = []

    async def actual_receiver(**kwargs):
        calls.append(kwargs)
        assert "parent_messages" not in kwargs
        assert await cli.inherit_selected_messages(child, parent, kwargs.get("parent_messages")) is False
        # Ordinary explicit text-only receiver input also remains supported.
        selected = [{"role": "user", "content": kwargs["instruction"]}]
        assert await cli.inherit_selected_messages(child, parent, selected) is False
        return {"output": "done", "session_id": "child"}

    tool, _, _, _ = harness(messages, actual_receiver)
    assert (await execute(tool, context_scope=scope)).success
    assert len(calls) == 1
    assert calls[0]["instruction"].count("selected observation") == 2  # Question + result, once each.
    assert calls[0]["instruction"].count("ASSISTANT: [") == 1
    lookup.assert_not_called()
    context.add_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_actual_cli_user_ref_carrier_adopts_independent_bytes(actual_cli_inheritance, tmp_path):
    """Real CLI owner/adoption, synthetic sessions/ACK; no provider/installed claim."""
    from amplifier_core import AmplifierSession
    from amplifier_foundation.session.checkpoint import acknowledge_legacy_checkpoint
    from amplifier_foundation.session.media import MediaRefusal
    from amplifier_foundation.session.store import write_jsonl
    from PIL import Image

    cli, cli_media, cli_native = actual_cli_inheritance
    sessions = [AmplifierSession(
        {"session": {"orchestrator": "unused", "context": "unused"}},
        session_id=identity,
    ) for identity in ("parent", "child")]
    native = cli_native.SessionStore(base_dir=tmp_path / "sessions")
    owners = [cli_media.install_session_media(session, native, config={"session_media": {"enabled": True}})
              for session in sessions]
    parent, child = sessions
    source, target = owners
    added = []

    async def add_message(message):
        added.append(deepcopy(message))

    await child.coordinator.mount("context", SimpleNamespace(add_message=add_message))
    try:
        buffer = io.BytesIO()
        Image.new("RGB", (2, 3), "purple").save(buffer, "PNG")
        pixels = buffer.getvalue()
        origin = {"kind": "upload", "session_id": "parent", "id": "synthetic-upload",
                  "created_at": "2025-01-01T00:00:00+00:00",
                  "expires_at": "2035-01-01T00:00:00+00:00"}
        original_ref = (await source.store.ingest(
            pixels, source_format="image-bytes", origin=origin, policy=None,
        ))["value"]
        await source.store.publish([original_ref], origin=origin, disposition="retain")
        messages = [{"role": "user", "content": "inspect upload",
                     "metadata": {NS: {"refs": [original_ref]}}}]
        parent_dir = native.base_dir / "parent"
        write_jsonl(parent_dir / "transcript.jsonl", messages)
        source.store.acknowledge_checkpoint(acknowledge_legacy_checkpoint(
            parent_dir, session_id="parent", expected_messages=messages,
        ))
        calls = []

        async def actual_receiver(*, parent_messages, **kwargs):
            calls.append(deepcopy(parent_messages))
            assert await cli.inherit_selected_messages(child, parent, parent_messages) is True
            adopted = added[0]["metadata"][NS]["refs"][0]
            with pytest.raises(MediaRefusal):
                target.store.resolve_ref(adopted)  # Receiver publication is not an ACK.
            child_dir = native.base_dir / "child"
            write_jsonl(child_dir / "transcript.jsonl", added)
            target.store.acknowledge_checkpoint(acknowledge_legacy_checkpoint(
                child_dir, session_id="child", expected_messages=added,
            ))
            return {"output": "done", "session_id": "child"}

        tool, _, _, _ = harness(messages, actual_receiver)
        result = await execute(tool, context_depth="all", context_scope="conversation")
        assert result.success and len(calls) == 1
        assert calls[0] == [{"role": "user", "content": "", "metadata": {NS: {"refs": [original_ref]}}}]
        adopted = added[0]["metadata"][NS]["refs"][0]
        assert adopted == {**original_ref, "session_id": "child"}
        assert messages[0]["metadata"][NS]["refs"] == [original_ref]
        source.store.close()
        with target.store.resolve_ref(adopted) as stream:
            assert stream.read() == pixels
        assert result.output["metadata"]["media_inheritance"]["acceptance"] == "unknown"
    finally:
        for owner in owners:
            await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["agents", "full"])
async def test_selected_tool_slot_counterexample_refuses_without_rehoming(scope):
    """Refs must neither migrate to a summary nor form an isolated tool result."""
    selected = row("tool", "selected text", "selectedrefs",
                   name="delegate", tool_call_id="selected-call-1")
    tool, captured, _, _ = harness([selected])
    result = await execute(tool, context_scope=scope)
    assert not result.success
    assert captured == []
    assert "complete tool-call/result groups" in str(result.error)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,name", [("agents", "delegate"), ("full", "bash")])
@pytest.mark.parametrize("with_refs", [False, True])
@pytest.mark.parametrize("nested", [False, True])
async def test_serialized_inline_image_tool_payload_refuses_without_forwarding(
    scope, name, with_refs, nested,
):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "purple").save(buffer, "PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode()
    blocks = [{"type": "text", "text": "safe observation"},
              {"type": "image", "source": {
                  "type": "base64", "media_type": "image/png", "data": encoded,
              }}]
    content = json.dumps({"result": {"content": blocks}} if nested else blocks)
    message = row("tool", content, "selected", name=name, tool_call_id="selected-call-1")
    if not with_refs:
        message.pop("metadata")
    tool, captured, hooks, _ = harness([message])
    result = await execute(tool, context_scope=scope)
    assert not result.success
    assert captured == []
    visible = json.dumps([result.model_dump(), hooks.emit.call_args_list], default=str)
    assert encoded not in visible
    assert "safe observation" not in visible
    assert "media" in str(result.error).lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,name", [("agents", "delegate"), ("full", "bash")])
@pytest.mark.parametrize("with_refs", [False, True])
@pytest.mark.parametrize("damage", ["truncated", "trailing-comma"])
async def test_malformed_serialized_image_envelope_refuses_before_spawn(
    scope, name, with_refs, damage,
):
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "purple").save(buffer, "PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode()
    text = json.dumps([{"type": "image", "source": {
        "type": "base64", "media_type": "image/png", "data": encoded,
    }}])
    malformed = text[:-1] if damage == "truncated" else text[:-1] + ",]"
    message = row("tool", malformed, "selected", name=name, tool_call_id="selected-call-1")
    if not with_refs:
        message.pop("metadata")
    tool, captured, hooks, _ = harness([message])
    result = await execute(tool, context_scope=scope)
    assert not result.success
    assert captured == []
    visible = json.dumps([result.model_dump(), hooks.emit.call_args_list], default=str)
    assert encoded not in visible
    assert "media" in str(result.error).lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["conversation", "agents", "full"])
@pytest.mark.parametrize("message", [
    row("assistant", "not an image carrier", "assistant"),
    row("tool", "", "missing-name", tool_call_id="call", name=None),
    row("tool", "", "missing-call", name="delegate", tool_call_id=None),
])
async def test_selected_unsupported_media_carriers_refuse_before_spawn(scope, message):
    tool, captured, _, _ = harness([message])
    selected = message["role"] == "assistant" or (
        message["role"] == "tool"
        and (scope == "full" or scope == "agents" and message["name"] == "delegate")
    )
    result = await execute(tool, context_scope=scope)
    assert result.success is not selected
    if selected:
        assert captured == []
        assert "carrier" in str(result.error)
    else:
        assert "parent_messages" not in captured[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["conversation", "agents", "full"])
@pytest.mark.parametrize("depth", ["none", "recent", "all"])
async def test_carrier_selection_keeps_toolslot_and_excludes_tool_calls(scope, depth):
    messages = [
        {"role": "user", "content": "old turn"},
        row("tool", "", "old", name="delegate", tool_call_id="old-call"),
        {"role": "user", "content": "new turn"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "selected-call", "type": "function",
             "function": {"name": "delegate", "arguments": "{}"}},
        ]},
        row("tool", "", "selected", name="delegate", tool_call_id="selected-call"),
        row("tool", "", "excluded", name="bash", tool_call_id="excluded-call"),
        row("system", "private", "private"),
    ]
    original = deepcopy(messages)
    tool, captured, _, context = harness(messages)
    result = await execute(tool, context_scope=scope, context_depth=depth, context_turns=1)
    expected = []
    if depth != "none" and scope != "conversation":
        if depth == "all":
            expected.append("old")
        expected.append("selected")
        if scope == "full":
            expected.append("excluded")
    if expected:
        assert not result.success
        assert captured == []
        assert "complete tool-call/result groups" in str(result.error)
    else:
        assert result.success
        assert "parent_messages" not in captured[0]
    assert messages == original
    assert "tool_calls" not in json.dumps(captured, default=str)
    if depth == "none":
        context.get_messages.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    '{"status":"blocked","reason":"needs approval"}',
    '[{"type":"text","text":"ordinary"}]',
    '{"experimental.media.v1":{"refs":["not authority"]}}',
])
async def test_safe_serialized_tool_text_is_preserved_never_discovers_refs(text):
    tool, captured, _, _ = harness([{"role": "tool", "name": "delegate", "content": text}])
    assert (await execute(tool, context_scope="agents")).success
    assert text in captured[0]["instruction"]
    assert "parent_messages" not in captured[0]


@pytest.mark.asyncio
async def test_too_deep_serialized_media_inspection_refuses_not_raw_fallback():
    text = "[" * 2000 + '{"type":"image","source":{"data":"DEEP_PIXELS"}}' + "]" * 2000
    tool, captured, hooks, _ = harness([{"role": "tool", "name": "delegate", "content": text}])
    result = await execute(tool, context_scope="agents")
    assert not result.success
    assert captured == []
    assert "DEEP_PIXELS" not in json.dumps([result.model_dump(), hooks.emit.call_args_list], default=str)


@pytest.mark.asyncio
@pytest.mark.parametrize("depth,scope,name", [
    ("none", "full", "delegate"), ("all", "conversation", "delegate"),
    ("all", "agents", "bash"), ("recent", "full", "delegate"),
])
async def test_excluded_serialized_payload_is_never_inspected_or_forwarded(depth, scope, name):
    payload = json.dumps([{"type": "image", "source": {
        "type": "base64", "media_type": "image/png", "data": "EXCLUDED_PIXELS",
    }}])
    messages = [{"role": "user", "content": "old turn"},
                row("tool", payload, "excluded", name=name),
                {"role": "user", "content": "new turn"}]
    tool, captured, _, context = harness(messages)
    assert (await execute(tool, context_depth=depth, context_scope=scope, context_turns=1)).success
    assert "parent_messages" not in captured[0]
    assert "EXCLUDED_PIXELS" not in json.dumps(captured, default=str)
    if depth == "none":
        context.get_messages.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,expected", [
    ("conversation", ["user"]),
    ("agents", ["user", "delegate", "task"]),
    ("full", ["user", "delegate", "task", "bash"]),
])
async def test_execute_selected_refs_only_scope_order_roles_and_no_text_duplication(scope, expected):
    messages = [
        row("system", "SECRET SYSTEM", "system"),
        row("user", [{"type": "text", "text": "question"},
                     {"type": "image", "source": {"data": "PIXEL SENTINEL"}}], "user"),
        row("tool", "agent answer", "delegate", name="delegate"),
        row("tool", "task answer", "task", name="task"),
        row("tool", "shell answer", "bash", name="bash"),
        {"role": "assistant", "content": "answer"},
        row("assistant", "", "tool-only", tool_calls=[{"name": "bash"}]),
        row("assistant", "private result", "call-id", tool_call_id="hidden"),
        row("developer", "SECRET DEVELOPER", "developer"),
    ]
    original = deepcopy(messages)
    tool, captured, hooks, _ = harness(messages)
    result = await execute(tool, context_scope=scope)
    if scope != "conversation":
        assert not result.success
        assert captured == []  # Never partially forward the user row on tool refusal.
        assert messages == original
        assert "complete tool-call/result groups" in str(result.error)
        return
    assert result.success
    assert len(captured) == 1
    kwargs = captured[0]
    forwarded = kwargs["parent_messages"]
    assert labels(forwarded) == expected
    assert [r["role"] for r in forwarded] == ["user"] + ["tool"] * (len(expected) - 1)
    assert all(set(r) == (
        {"role", "content", "metadata", "name", "tool_call_id"} if r["role"] == "tool"
        else {"role", "content", "metadata"}
    ) and r["content"] == ""
               and set(r["metadata"]) == {NS} for r in forwarded)
    assert kwargs["instruction"].count("question") == 1
    assert kwargs["instruction"].count("answer") == len(expected)
    assert kwargs["tool_inheritance"] == {"exclude_tools": ["delegate"]}
    assert kwargs["hook_inheritance"] == {"exclude_hooks": ["private-hook"]}
    assert result.output["metadata"]["media_inheritance"] == {
        "status": "forwarded", "acceptance": "unknown",
        "message_count": len(expected), "ref_count": len(expected),
    }
    assert messages == original
    forwarded[0]["metadata"][NS]["refs"][0]["provenance"]["id"] = "child mutation"
    assert messages == original  # Ref/provenance detached from parent context.
    visible = json.dumps([kwargs["instruction"], result.model_dump(),
                          hooks.emit.call_args_list], default=str)
    assert "PIXEL SENTINEL" not in visible
    assert "DO NOT FORWARD" not in json.dumps(kwargs, default=str)
    assert "SECRET" not in visible


@pytest.mark.asyncio
@pytest.mark.parametrize("depth,turns,expected", [
    ("none", 1, []), ("recent", 1, ["new"]), ("recent", 2, ["old", "new"]),
    ("all", 1, ["old", "new"]), ("recent", 0, []),
])
async def test_explicit_depth_turns_and_unselected_malformed_refs(depth, turns, expected):
    messages = [row("user", "old question", "old"), row("user", "new question", "new")]
    if depth in {"none", "recent"} and turns <= 1:
        messages[0]["metadata"][NS] = {"refs": "invalid unselected"}
    tool, captured, _, context = harness(messages)
    result = await execute(tool, context_depth=depth, context_turns=turns)
    assert result.success
    assert labels(captured[0].get("parent_messages", [])) == expected
    if depth == "none":
        context.get_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_inheritance_does_not_read_or_forward_refs():
    tool, captured, _, context = harness([row("user", "", "not selected")], enabled=False)
    assert (await execute(tool)).success
    assert "parent_messages" not in captured[0]
    context.get_messages.assert_not_awaited()


@pytest.mark.asyncio
async def test_media_only_row_keeps_ref_without_empty_text_wrapper():
    tool, captured, _, _ = harness([row("user", "", "image only")])
    assert (await execute(tool)).success
    assert labels(captured[0]["parent_messages"]) == ["image only"]
    assert captured[0]["instruction"] == "inspect selected media"


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["conversation", "agents", "full"])
async def test_text_only_spawn_is_byte_identical_and_legacy_abi_is_untouched(scope):
    messages = [{"role": "user", "content": "question"},
                {"role": "assistant", "content": "answer"}]
    tool, captured, _, _ = harness(messages)
    assert (await execute(tool, context_scope=scope)).success
    assert "parent_messages" not in captured[0]
    assert captured[0]["instruction"] == (
        "[PARENT CONVERSATION CONTEXT]\n"
        "The following is recent conversation history from the parent session:\n\n"
        "USER: question\n\nASSISTANT: answer\n\n[END PARENT CONTEXT]\n\n"
        "[YOUR TASK]\ninspect selected media"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    None, {"refs": "not-a-list"}, {"refs": [None]}, {"refs": [], "pixels": "SECRET"},
    {"refs": [{**ref(), "v": 2}]}, {"refs": [{**ref(), "mime": "image/gif"}]},
    {"refs": [{**ref(), "base64": "SECRET"}]},
    {"refs": [{**ref(), "provenance": {**ref()["provenance"], "authority": "SECRET"}}]},
])
async def test_selected_malformed_schema_refuses_before_spawn_without_payload_echo(bad):
    messages = [row("user", "question", "bad")]
    messages[0]["metadata"][NS] = bad
    tool, captured, hooks, _ = harness(messages)
    result = await execute(tool)
    assert not result.success
    assert captured == []
    assert "media" in str(result.error).lower()
    assert "SECRET" not in json.dumps([result.model_dump(), hooks.emit.call_args_list], default=str)


@pytest.mark.asyncio
async def test_unselected_scope_does_not_validate_or_forward_tool_media():
    messages = [row("user", "question", "selected"),
                row("tool", "hidden", "hidden", name="bash")]
    messages[1]["metadata"][NS] = {"refs": "malformed excluded tool"}
    tool, captured, _, _ = harness(messages)
    assert (await execute(tool, context_scope="agents")).success
    assert labels(captured[0]["parent_messages"]) == ["selected"]


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,tail", [
    ("conversation", ["new"]), ("agents", ["new", "agent"]),
    ("full", ["new", "agent", "shell"]),
])
@pytest.mark.parametrize("depth", ["none", "recent", "all"])
async def test_depth_scope_cross_product_has_no_hidden_widening(scope, tail, depth):
    messages = [row("system", "private", "system"), row("user", "old", "old"),
                row("user", "new", "new"), row("tool", "agent", "agent", name="delegate"),
                row("tool", "shell", "shell", name="bash")]
    tool, captured, _, _ = harness(messages)
    result = await execute(tool, context_depth=depth, context_scope=scope, context_turns=1)
    expected = [] if depth == "none" else (["old"] if depth == "all" else []) + tail
    if depth != "none" and scope != "conversation":
        assert not result.success
        assert captured == []
        return
    assert result.success
    assert labels(captured[0].get("parent_messages", [])) == expected


@pytest.mark.asyncio
async def test_selected_refs_do_not_override_actual_source_acceptance(tmp_path):
    from amplifier_foundation.session.media import SessionMediaStore

    with ExitStack() as stack:
        source = SessionMediaStore(tmp_path / "source", "parent")
        target = SessionMediaStore(tmp_path / "target", "child")
        stack.callback(source.close)
        stack.callback(target.close)
        calls = []

        async def host(*, parent_messages, **kwargs):
            calls.append(1)
            # The source-shaped ref is not a published/acknowledged original.
            await target.adopt(parent_messages[0]["metadata"][NS]["refs"], source)
            raise AssertionError("Unaccepted source must never reach child execution")

        tool, _, _, _ = harness([row("user", "question", "unaccepted")], host)
        result = await execute(tool)
        assert not result.success
        assert len(calls) == 1  # No fallback/retry without refs after host refusal.
        assert list((target.root / "assets").iterdir()) == []


@pytest.mark.asyncio
async def test_unsupported_spawn_abi_refuses_media_but_allows_text_only():
    called = []

    async def legacy_spawn(*, agent_name, instruction, parent_session, agent_configs,
                           sub_session_id, tool_inheritance, hook_inheritance,
                           orchestrator_config, provider_preferences,
                           self_delegation_depth, session_metadata):
        called.append(instruction)
        return {"output": "done", "session_id": "child"}

    tool, _, _, _ = harness([row("user", "question", "selected")], legacy_spawn)
    result = await execute(tool)
    assert not result.success
    assert called == []
    assert "parent_messages" in str(result.error)
    tool, _, _, _ = harness([{"role": "user", "content": "question"}], legacy_spawn)
    assert (await execute(tool)).success
    assert len(called) == 1


@pytest.mark.asyncio
async def test_unknown_spawn_abi_refuses_without_invocation(monkeypatch):
    import amplifier_module_tool_delegate as delegate

    tool, captured, _, _ = harness([row("user", "question", "selected")])
    def unknown(_):
        raise ValueError("cannot inspect")
    monkeypatch.setattr(delegate.inspect, "signature", unknown)
    result = await execute(tool)
    assert not result.success
    assert "support unknown" in str(result.error)
    assert captured == []


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,name", [("agents", "delegate"), ("full", "bash")])
@pytest.mark.parametrize("content", [
    [{"type": "image", "source": {"data": "PIXEL SENTINEL"}},
     {"type": "text", "text": "safe text"}],
    {"image": "PIXEL SENTINEL"},
])
async def test_tool_payloads_are_never_stringified_into_text_or_output(scope, name, content):
    messages = [{"role": "user", "content": "question"},
                row("tool", content, "image", name=name)]
    tool, captured, hooks, _ = harness(messages)
    result = await execute(tool, context_scope=scope)
    assert not result.success
    assert captured == []
    visible = json.dumps([captured, result.model_dump(), hooks.emit.call_args_list], default=str)
    assert "PIXEL SENTINEL" not in visible
    assert "safe text" not in visible


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,name", [("agents", "delegate"), ("full", "bash")])
async def test_inline_tool_images_without_refs_still_do_not_leak(scope, name):
    content = [{"type": "image", "source": {"data": "PIXEL SENTINEL"}},
               {"type": "text", "text": "safe text"}]
    tool, captured, hooks, _ = harness([{"role": "tool", "name": name, "content": content}])
    result = await execute(tool, context_scope=scope)
    assert result.success
    assert "parent_messages" not in captured[0]
    visible = json.dumps([captured, result.model_dump(), hooks.emit.call_args_list], default=str)
    assert "PIXEL SENTINEL" not in visible
    assert "safe text" in captured[0]["instruction"]


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,name,prefix", [
    ("agents", "delegate", "Agent"), ("agents", "task", "Agent"),
    ("full", "bash", "Tool"),
])
@pytest.mark.parametrize("content", [
    {"status": "blocked", "reason": "needs approval"},
    ["plain", "text"], [{"type": "text", "text": "safe text"}], "x" * 5000,
])
async def test_non_media_tool_content_retains_original_format(scope, name, prefix, content):
    tool, captured, _, _ = harness([{"role": "tool", "name": name, "content": content}])
    assert (await execute(tool, context_scope=scope)).success
    assert "parent_messages" not in captured[0]
    original_content = content
    if scope == "full" and len(original_content) > 4000:
        original_content = original_content[:4000] + "... [truncated]"
    text = f"[{prefix} Result from {name}]: {original_content}"
    if len(text) > 2000:
        text = text[:2000] + "... [truncated]"
    assert captured[0]["instruction"] == (
        "[PARENT CONVERSATION CONTEXT]\n"
        "The following is recent conversation history from the parent session:\n\n"
        f"ASSISTANT: {text}\n\n[END PARENT CONTEXT]\n\n"
        "[YOUR TASK]\ninspect selected media"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [None, False, 0])
@pytest.mark.parametrize("with_refs", [False, True])
async def test_full_scope_falsy_tool_content_keeps_refs_without_length_error(content, with_refs):
    message = row("tool", content, "selected", name="bash")
    if not with_refs:
        message.pop("metadata")
    tool, captured, _, _ = harness([message])
    result = await execute(tool, context_scope="full")
    if with_refs:
        assert not result.success
        assert captured == []
        assert "complete tool-call/result groups" in str(result.error)
        return
    assert result.success
    assert captured[0]["instruction"] == "inspect selected media"
    assert labels(captured[0].get("parent_messages", [])) == (
        ["selected"] if with_refs else []
    )


@pytest.mark.asyncio
async def test_multiple_refs_keep_original_order_and_detachment_with_host_mutation():
    messages = [row("user", "question", "first")]
    messages[0]["metadata"][NS]["refs"].append(ref("second", mime="image/jpeg"))
    original = deepcopy(messages)
    observed = []

    async def host(*, parent_messages, **kwargs):
        observed.extend(deepcopy(parent_messages[0]["metadata"][NS]["refs"]))
        parent_messages.clear()
        return {"output": "done", "session_id": "child"}

    tool, _, _, _ = harness(messages, host)
    result = await execute(tool)
    assert result.success
    assert [r["provenance"]["id"] for r in observed] == ["first", "second"]
    assert messages == original
    assert result.output["metadata"]["media_inheritance"]["ref_count"] == 2


@pytest.mark.asyncio
async def test_kwargs_host_ignoring_refs_is_not_reported_as_media_acceptance():
    tool, captured, _, _ = harness([row("user", "question", "selected")])
    result = await execute(tool)
    assert result.success and captured
    assert result.output["metadata"]["media_inheritance"]["acceptance"] == "unknown"


@pytest.mark.asyncio
async def test_missing_public_validator_refuses_media_but_not_text(monkeypatch):
    import builtins

    original_import = builtins.__import__
    def without_media(name, *args, **kwargs):
        if name == "amplifier_foundation.session.media":
            raise ImportError("not installed on this hypothetical host")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", without_media)
    tool, captured, _, _ = harness([row("user", "question", "selected")])
    result = await execute(tool)
    assert not result.success
    assert "validator unavailable" in str(result.error)
    assert captured == []
    tool, captured, _, _ = harness([{"role": "user", "content": "question"}])
    assert (await execute(tool)).success
    assert "parent_messages" not in captured[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("expired", [False, True])
@pytest.mark.parametrize("image_format,mime", [("PNG", "image/png"), ("JPEG", "image/jpeg")])
@pytest.mark.parametrize("carrier_role", ["user", "tool"])
async def test_user_adopts_or_tool_refuses_before_adoption(
    tmp_path, expired, image_format, mime, carrier_role,
):
    """User public API joint; selected tool refs must not reach the synthetic host."""
    from PIL import Image
    from amplifier_foundation.session.checkpoint import (
        acknowledge_legacy_checkpoint, verified_media_checkpoint_refs,
    )
    from amplifier_foundation.session.media import MediaRefusal, SessionMediaStore
    from amplifier_foundation.session.media_conversion import adopt_selected_media
    from amplifier_foundation.session.media_projection import (
        prepare_media_request, validate_media_request_support,
    )
    from amplifier_foundation.session.store import write_jsonl
    from amplifier_core.message_models import ChatRequest, ImageBlock, Message

    buffer = io.BytesIO()
    Image.new("RGB", (2, 3), "purple").save(buffer, image_format)
    pixels = buffer.getvalue()
    parent_path, child_path = tmp_path / "parent", tmp_path / "child"
    parent_path.mkdir(mode=0o700)
    child_path.mkdir(mode=0o700)
    with ExitStack() as stack:
        parent = SessionMediaStore(parent_path / "media", "parent",
                                   checkpoint_verifier=verified_media_checkpoint_refs)
        child = SessionMediaStore(child_path / "media", "child",
                                  checkpoint_verifier=verified_media_checkpoint_refs)
        stack.callback(parent.close)
        stack.callback(child.close)
        origin = {"kind": "tool", "session_id": "parent", "id": "synthetic-image",
                  "created_at": "2025-01-01T00:00:00+00:00",
                  "expires_at": "2035-01-01T00:00:00+00:00"}
        original_ref = (await parent.ingest(pixels, source_format="image-bytes",
                                           origin=origin, policy=None))["value"]
        await parent.publish([original_ref], origin=origin, disposition="retain")
        messages = [row(carrier_role, "question", "placeholder",
                        **({"name": "delegate", "tool_call_id": "selected-call-1"}
                           if carrier_role == "tool" else {}))]
        messages[0]["metadata"] = {NS: {"refs": [original_ref]}}
        write_jsonl(parent_path / "transcript.jsonl", messages)
        receipt = acknowledge_legacy_checkpoint(parent_path, session_id="parent",
                                                expected_messages=messages)
        parent.acknowledge_checkpoint(receipt)
        # Expiry belongs to the existing adopter, not a delegate clock/parser.
        if expired:
            import amplifier_foundation.session.media as media
            from datetime import datetime, timezone
            class Later(datetime):
                @classmethod
                def now(cls, tz=None):
                    return datetime(2036, 1, 1, tzinfo=timezone.utc)
            # Module-local clock only, restored before this case leaves.
            patcher = pytest.MonkeyPatch()
            patcher.setattr(media, "datetime", Later)
            stack.callback(patcher.undo)
        observed = []

        async def synthetic_host(*, parent_messages, **kwargs):
            assert carrier_role == "user", "Tool refs must refuse before any host/adoption"
            forwarded = deepcopy(parent_messages)
            assert forwarded[0]["role"] == carrier_role
            assert forwarded[0]["metadata"][NS]["refs"] == [original_ref]
            if expired:
                # Reuse the predecessor expiry/metadata-only API control.
                adopted = await child.adopt(forwarded[0]["metadata"][NS]["refs"], parent)
                forwarded[0]["metadata"][NS]["refs"] = adopted
            else:
                forwarded = adopt_selected_media(
                    forwarded, source_store=parent, target_store=child,
                    target_session_id="child",
                )
                adopted = forwarded[0]["metadata"][NS]["refs"]
            with pytest.raises(MediaRefusal):
                child.resolve_ref(adopted[0])  # Publication alone is not ACK.
            write_jsonl(child_path / "transcript.jsonl", forwarded)
            ack = acknowledge_legacy_checkpoint(child_path, session_id="child",
                                                expected_messages=forwarded)
            child.acknowledge_checkpoint(ack)
            if not expired:
                canonical = ChatRequest(messages=[Message(**r) for r in forwarded])
                before = canonical.model_dump_json()
                # Explicit synthetic indexed facts, never provider inference.
                support = {"v": 2, "positions": [
                    {"message_index": 0, "role": carrier_role},
                ], "mime_types": [mime]}
                assert validate_media_request_support(support, canonical) == support
                wrong = {**support, "positions": [{"message_index": 0, "role": "assistant"}]}
                with pytest.raises(MediaRefusal):
                    validate_media_request_support(wrong, canonical)
                prepared = await prepare_media_request(
                    child, canonical, provider_identity={
                        "provider": "synthetic", "account": "fixture",
                        "generation": "toolslot", "media_support": support,
                    }, model="synthetic", request_options=None, purpose="count",
                    execution_scope={"nested": False, "text_only": False},
                )
                try:
                    assert prepared.request.messages[0].role == carrier_role
                    images = [b for b in prepared.request.messages[0].content
                              if isinstance(b, ImageBlock)]
                    assert len(images) == 1
                    assert base64.b64decode(images[0].source["data"]) == pixels
                    prepared.validate()
                    assert canonical.model_dump_json() == before
                finally:
                    prepared.release()
            observed.extend(adopted)
            return {"output": "done", "session_id": "child"}

        tool, _, hooks, _ = harness(messages, synthetic_host)
        result = await execute(
            tool, context_scope="agents" if carrier_role == "tool" else "conversation",
        )
        if carrier_role == "tool":
            assert not result.success
            assert "complete tool-call/result groups" in str(result.error)
            assert observed == []
            assert list((child.root / "assets").iterdir()) == []
            assert not (child_path / "transcript.jsonl").exists()
            return
        assert result.success
        assert len(observed) == 1
        adopted = observed[0]
        assert adopted["session_id"] == "child"
        assert adopted["mime"] == mime
        assert adopted["provenance"] == original_ref["provenance"]
        assert messages[0]["metadata"][NS]["refs"] == [original_ref]
        assert pixels not in (child_path / "transcript.jsonl").read_bytes()
        visible = json.dumps([result.model_dump(), hooks.emit.call_args_list], default=str)
        assert base64.b64encode(pixels).decode() not in visible
        if expired:
            assert list((child.root / "assets").iterdir()) == []
            with pytest.raises(MediaRefusal, match="expired"):
                child.resolve_ref(adopted)
        else:
            parent.close()  # Child owns independent bytes after adoption + ACK.
            with child.resolve_ref(adopted) as stream:
                assert stream.read() == pixels