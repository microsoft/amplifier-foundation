"""Synthetic canonical delegate forwarding; not installed or actual-host acceptance."""

from copy import deepcopy
import base64
from contextlib import ExitStack
import io
import json
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


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["agents", "full"])
async def test_selected_tool_slot_counterexample(scope):
    """Exact ff923 counterexample: refs must not migrate to an assistant summary."""
    selected = row("tool", "selected text", "selectedrefs",
                   name="delegate", tool_call_id="selected-call-1")
    tool, captured, _, _ = harness([selected])
    assert (await execute(tool, context_scope=scope)).success
    assert captured[0]["parent_messages"] == [{
        "role": "tool", "name": "delegate", "tool_call_id": "selected-call-1",
        "content": "", "metadata": {NS: {"refs": [ref("selectedrefs")]}},
    }]
    assert captured[0]["instruction"].count("selected text") == 1


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
    assert (await execute(tool, context_scope=scope, context_depth=depth, context_turns=1)).success
    expected = []
    if depth != "none" and scope != "conversation":
        if depth == "all":
            expected.append("old")
        expected.append("selected")
        if scope == "full":
            expected.append("excluded")
    forwarded = captured[0].get("parent_messages", [])
    assert labels(forwarded) == expected
    assert all(r["role"] == "tool" and set(r) == {
        "role", "name", "tool_call_id", "content", "metadata",
    } and r["content"] == "" for r in forwarded)
    assert all(r["tool_call_id"] == {"old": "old-call", "selected": "selected-call",
                                    "excluded": "excluded-call"}[label]
               for r, label in zip(forwarded, expected))
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
    assert (await execute(tool, context_depth=depth, context_scope=scope, context_turns=1)).success
    expected = [] if depth == "none" else (["old"] if depth == "all" else []) + tail
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
    assert result.success
    visible = json.dumps([captured, result.model_dump(), hooks.emit.call_args_list], default=str)
    assert "PIXEL SENTINEL" not in visible
    assert labels(captured[0]["parent_messages"]) == ["image"]
    assert ("safe text" in captured[0]["instruction"]) == isinstance(content, list)


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
async def test_execute_to_real_adoption_and_actual_checkpoint(
    tmp_path, expired, image_format, mime, carrier_role,
):
    """Real public media APIs on synthetic roots; the spawn host is a test double."""
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
            forwarded = deepcopy(parent_messages)
            assert forwarded[0]["role"] == carrier_role
            if carrier_role == "tool":
                assert forwarded[0]["name"] == "delegate"
                assert forwarded[0]["tool_call_id"] == "selected-call-1"
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
                    if carrier_role == "tool":
                        assert prepared.request.messages[0].tool_call_id == "selected-call-1"
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