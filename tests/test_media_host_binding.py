"""Actual released Core construction + storage; deterministic offline provider."""

import asyncio
import base64
from copy import deepcopy
from importlib.metadata import version
import io
import json
import os
from types import SimpleNamespace

from amplifier_core import AmplifierSession, HookResult, ToolResult
from amplifier_core.message_models import ChatRequest, ImageBlock, Message, ToolSpec
from PIL import Image
import pytest

from amplifier_foundation import sanitize_message
from amplifier_foundation.session.checkpoint import (
    acknowledge_legacy_checkpoint, verified_checkpoint_refs,
)
from amplifier_foundation.session.history import SessionHistoryStore
from amplifier_foundation.session.media import MEDIA_NAMESPACE, MediaRefusal, SessionMediaStore
from amplifier_foundation.session.media_host import SessionMediaBinding


pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(os.name != "posix", reason="POSIX media durability"),
    pytest.mark.skipif(version("amplifier-core") != "2.0.2",
                       reason="positive construction requires exact released Core 2.0.2"),
]


class Provider:
    name = "offline"
    config = {"model": "any-provider-owned-model"}
    default_model = "any-provider-owned-model"

    def __init__(self):
        self.capability = None
        self.counted = []
        self.sent = []
        self.compacted = []
        self.stream_closed = False

    def get_media_request_support_for_request(self, *, request, model, request_options=None):
        return {
            "v": 2, "mime_types": ["image/png"],
            "positions": [{"message_index": i, "role": m.role}
                          for i, m in enumerate(request.messages) if m.role in ("tool", "user")],
        }

    def request_budget(self, request, *, request_options=None):
        self.capability.validate(request, purpose="count", request_options=request_options)
        self.counted.append(request)
        return {"estimated_input_tokens": 5, "input_limit_tokens": 100,
                "context_token_budget": 100,
                "measurement": {"kind": "provider_count", "source": "offline", "input_tokens": 5}}

    async def complete(self, request, **options):
        self.capability.validate(request, purpose="transport", request_options=options)
        self.sent.append(request)
        return "offline-complete"

    async def compact_context(self, request, **options):
        self.capability.validate(request, purpose="transport", request_options=options)
        self.compacted.append(request.model_dump(mode="json"))
        return "offline-compacted"

    async def stream(self, request, **options):
        self.capability.validate(request, purpose="transport", request_options=options)
        try:
            yield "offline-chunk"
        finally:
            self.stream_closed = True


class Loader:
    """Small explicit module plan, not a source-inventory approval mechanism."""

    def __init__(self, provider, readiness):
        self.provider, self.readiness, self.queue = provider, readiness, []

    async def load(self, module_id, config, *, source_hint=None, coordinator=None):
        async def mount(current):
            if module_id == "host-test-loop":
                await current.mount("orchestrator", object())
                if self.readiness is not None:
                    current.get_capability(MEDIA_NAMESPACE).report(
                        module_id, event="consumer-ready", outcome="inspect", details=self.readiness,
                    )
            elif module_id == "host-test-context":
                await current.mount("context", object())
            elif module_id == "host-test-provider":
                await current.mount("providers", self.provider, name="offline")
        return mount

    def enqueue_on_session_ready(self, name, callback):
        self.queue.append((name, callback))

    def get_on_session_ready_queue(self):
        return self.queue

    def clear_on_session_ready_queue(self):
        self.queue.clear()


READY = {"role": "loop", "contract": 1, "ready": True}
MODULES = ("host-test-loop", "host-test-context", "host-test-provider")


async def construction(tmp_path, *, owner="host.fixture", readiness=READY, writer=True, seal=True):
    provider = Provider()
    session = AmplifierSession({
        "session": {"orchestrator": MODULES[0], "context": MODULES[1]},
        "providers": [{"module": MODULES[2]}],
    }, session_id="same-session")
    session.coordinator.loader = Loader(provider, readiness)
    allowed = [True]
    store = SessionMediaStore(
        tmp_path / "media", session.session_id, checkpoint_verifier=verified_checkpoint_refs,
        policy_verifier=lambda ref, *, purpose: allowed[0],
    )
    binding = SessionMediaBinding(session, store=store, owner=owner, participants=(*MODULES, owner))
    provider.capability = binding.capability
    assert binding.capability.enabled is False
    if writer:
        # Actual host persists canonical rows; the callback is not a receipt.
        binding.registration.register_capability("session.durable_checkpoint", lambda: None)
    await session.initialize()
    wrapper = binding.wrap_provider(
        provider, provider_identity={"provider": "offline", "account": "test", "generation": "one"},
    )
    await binding.registration.mount_adapter(
        "providers", wrapper, previous=provider, name="offline",
    )
    if seal:
        binding.seal()
    return SimpleNamespace(session=session, binding=binding, store=store, provider=provider,
                           wrapper=wrapper, allowed=allowed, directory=tmp_path)


def image_result():
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "purple").save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode()
    return ToolResult(success=True, output={"text": "read result"}, content=[
        ImageBlock(source={"type": "base64", "media_type": "image/png", "data": encoded}),
    ]), encoded


async def staged_result(f):
    rich, encoded = image_result()
    # Exact current-task dispatch provenance, as installed by the real loop.
    coordinator = f.session.coordinator
    contexts = {asyncio.current_task(): {"tool_call_id": "read-call"}}
    coordinator._tool_dispatch_contexts = contexts
    try:
        staged = await f.binding.capability.ingest(
            rich, source_format="core-tool-result", origin=None, policy=None,
        )
    finally:
        contexts.clear()
    return staged, encoded


async def canonical_request(f):
    staged, encoded = await staged_result(f)
    settled = await f.binding.bridge({
        "tool_name": "read_file", "tool_call_id": "read-call",
        "result": staged["value"].model_dump(mode="json"),
    })
    assert settled["complete"] is True
    refs = settled["result"]["output"][MEDIA_NAMESPACE]["refs"]
    await f.store.publish(
        refs, origin={"kind": "tool", "session_id": f.session.session_id, "id": "read-call"},
        disposition="retain",
    )
    rows = [{"role": "tool", "tool_call_id": "read-call", "content": settled["result"]["content"],
             "metadata": {MEDIA_NAMESPACE: {"refs": refs}}}]
    history = SessionHistoryStore(f.directory)
    history.save(rows, {"session_id": f.session.session_id}, sanitizer=sanitize_message)
    saved = history.load_messages()
    receipt = acknowledge_legacy_checkpoint(
        f.directory, session_id=f.session.session_id, expected_messages=saved,
    )
    f.store.acknowledge_checkpoint(receipt)
    return ChatRequest(messages=[Message(**row) for row in saved],
                       tools=[ToolSpec(name="read_file", parameters={"type": "object"})]), encoded


@pytest.mark.parametrize("owner", ["unified-adapter", "cli-adapter"])
async def test_same_api_actual_checkpoint_private_count_send_and_payload_free_hooks(tmp_path, owner):
    f = await construction(tmp_path, owner=owner, seal=False)
    seen = []
    f.binding.registration.hooks.register(
        "tool:post", lambda event, data: seen.append(deepcopy(data)) or HookResult(),
        name="final-host-policy", priority=900,
    )
    try:
        assert f.binding.seal() is True
        assert f.session.coordinator.get("providers", "offline") is f.wrapper
        request, encoded = await canonical_request(f)
        original = request.model_dump_json()
        assert encoded not in json.dumps(seen)
        assert len(seen) == 1
        assert encoded not in (tmp_path / "transcript.jsonl").read_text()
        await f.wrapper.request_budget(request)
        private = f.provider.counted[-1]
        assert private is not request
        assert private.messages[0].role == "tool" and private.messages[0].tool_call_id == "read-call"
        assert private.tools == request.tools
        assert any(isinstance(b, ImageBlock) for b in private.messages[0].content)
        assert await f.wrapper.complete(request) == "offline-complete"
        assert f.provider.sent == [private] and private.messages == []
        assert request.model_dump_json() == original
    finally:
        await f.session.cleanup()
    assert f.binding.capability.enabled is False
    assert f.store._closed
    await f.binding.aclose()


@pytest.mark.parametrize("readiness,writer", [(None, True), (READY, False),
                                            ({**READY, "contract": True}, True),
                                            ({**READY, "ready": False}, True)])
async def test_missing_cooperative_loop_or_writer_never_enables(tmp_path, readiness, writer):
    f = await construction(tmp_path, readiness=readiness, writer=writer)
    try:
        assert f.binding.capability.enabled is False
        rich, _ = image_result()
        with pytest.raises(MediaRefusal, match="not enabled"):
            await f.binding.capability.ingest(
                rich, source_format="core-tool-result", origin=None, policy=None,
            )
    finally:
        await f.session.cleanup()


@pytest.mark.parametrize("effective", ["null", "deny", "error", "remove-output"])
async def test_late_policy_cannot_reattach_ref_or_execute_twice(tmp_path, effective):
    f = await construction(tmp_path, seal=False)
    seen = []

    def final(event, data):
        seen.append(event)
        if effective == "error":
            raise RuntimeError("offline policy refused")
        if effective == "deny":
            return HookResult(action="deny", reason="offline refusal")
        result = None if effective == "null" else {"success": True, "output": None}
        return HookResult(action="modify", data={"result": result})

    f.binding.registration.hooks.register("tool:post", final, name="late-policy", priority=900)
    try:
        assert f.binding.seal()
        staged, _ = await staged_result(f)
        settled = await f.binding.bridge({
            "tool_name": "read_file", "tool_call_id": "read-call",
            "result": staged["value"].model_dump(mode="json"),
        })
        assert seen == ["tool:post"]
        assert settled["result"] is None or settled["result"]["output"] is None
        assert settled["complete"] is (effective != "error")
        assert not (tmp_path / "transcript.jsonl").exists()
    finally:
        await f.session.cleanup()


async def test_current_task_and_same_session_provenance_not_inferred_from_markers(tmp_path):
    f = await construction(tmp_path)
    try:
        rich, _ = image_result()
        with pytest.raises(MediaRefusal, match="current-task"):
            await f.binding.capability.ingest(
                rich, source_format="core-tool-result", origin=None, policy=None,
            )
        with pytest.raises(MediaRefusal, match="this session"):
            await f.binding.capability.ingest(
                rich, source_format="core-tool-result",
                origin={"kind": "tool", "session_id": "other", "id": "read-call"}, policy=None,
            )
    finally:
        await f.session.cleanup()


@pytest.mark.parametrize("marker", ["operation", "canonical", "overlay"])
@pytest.mark.parametrize("scope", [None, {"text_only": False, "nested": False}, "other-scope"])
async def test_compaction_never_reads_stored_or_admitted_inline_pixels(tmp_path, monkeypatch, marker, scope):
    f = await construction(tmp_path)
    try:
        request, encoded = await canonical_request(f)
        if marker == "canonical":
            request.metadata = {"purpose": "context-compaction"}

        def overlay(private, **kwargs):
            private.metadata = {"purpose": "context-compaction"} if marker == "overlay" else {}
            return {"request": private, "validate": lambda: True}

        rich, _ = image_result()
        request.messages.append(Message(role="user", content=rich.content))
        f.store.request_only_verifier = lambda *args, **kwargs: True

        def no_pixels(*args, **kwargs):
            raise AssertionError("compaction must not resolve pixels")

        monkeypatch.setattr(f.store, "resolve_ref", no_pixels)
        wrapper = f.binding.wrap_provider(
            f.provider, provider_identity={"provider": "offline", "account": "test", "generation": "two"},
            execution_scope=scope,
            prepare_overlay=overlay,
        )
        original = request.model_dump_json()
        if marker == "operation":
            await wrapper.compact_context(request)
            observed = f.provider.compacted[-1]
        else:
            # Snapshot inside transport; cleanup wipes the actual private request.
            complete = f.provider.complete

            async def snapshot(private, **options):
                f.provider.compacted.append(private.model_dump(mode="json"))
                return await complete(private, **options)

            monkeypatch.setattr(f.provider, "complete", snapshot)
            await wrapper.request_budget(request)
            await wrapper.complete(request)
            observed = f.provider.compacted[-1]
        assert encoded not in json.dumps(observed)
        assert all(block.get("type") != "image" for row in observed["messages"]
                   for block in row["content"] if isinstance(block, dict))
        assert request.model_dump_json() == original
    finally:
        await f.session.cleanup()


async def test_source_policy_is_host_owned_and_rechecked_before_dispatch(tmp_path):
    f = await construction(tmp_path)
    try:
        verifier = f.store.policy_verifier
        request, _ = await canonical_request(f)
        await f.wrapper.request_budget(request)
        private = f.provider.counted[-1]
        f.allowed[0] = False
        with pytest.raises(ValueError, match="source policy changed"):
            await f.wrapper.complete(request)
        assert not f.provider.sent and private.messages == []
        assert f.store.policy_verifier is verifier
        # A new fit can honestly warn, but cannot regain access itself.
        await f.wrapper.request_budget(request)
        assert not any(isinstance(b, ImageBlock) for b in f.provider.counted[-1].messages[0].content)
    finally:
        await f.session.cleanup()


async def test_owned_transition_fences_fitted_request_and_preserves_successor(tmp_path):
    f = await construction(tmp_path)
    try:
        request, _ = await canonical_request(f)
        old = f.binding.bridge.graph_receipt
        await f.wrapper.request_budget(request)
        with pytest.raises(ValueError, match="idle graph"):
            with f.binding.configure():
                pytest.fail("active candidate must fence configuration")
        assert f.binding.capability.enabled is True
        await f.wrapper.complete(request)
        with f.binding.configure() as successor:
            assert f.binding.capability.enabled is False
            successor.participant(f.binding.graph.owner).hooks.register(
                "tool:post", lambda event, data: HookResult(), name="new-policy",
            )
        assert f.binding.graph is successor and f.binding.capability.enabled
        assert not f.binding.bridge.validate_receipt(old)
        assert f.binding.bridge.validate_receipt(f.binding.bridge.graph_receipt)
    finally:
        await f.session.cleanup()


async def test_failed_transition_leaves_producers_disabled(tmp_path):
    f = await construction(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="failed"):
            with f.binding.configure():
                raise RuntimeError("actual settings update failed")
        assert not f.binding.capability.enabled
        assert not f.binding.bridge.validate_receipt(f.binding.bridge.graph_receipt)
    finally:
        await f.session.cleanup()


@pytest.mark.parametrize("replacement", ["writer", "capability", "bridge", "disable"])
async def test_changed_host_owner_cannot_send_counted_candidate(tmp_path, replacement):
    f = await construction(tmp_path)
    try:
        request, _ = await canonical_request(f)
        await f.wrapper.request_budget(request)
        private = f.provider.counted[-1]
        if replacement == "disable":
            f.binding.capability.enabled = False
        else:
            key = {"writer": "session.durable_checkpoint", "capability": MEDIA_NAMESPACE,
                   "bridge": "session.media.tool_post"}[replacement]
            f.session.coordinator.register_capability(key, lambda: None)
        with pytest.raises(ValueError, match="scope changed"):
            await f.wrapper.complete(request)
        assert not f.provider.sent and private.messages == []
    finally:
        await f.session.cleanup()


async def test_cleanup_releases_fitted_stream_buffers_and_graph_lease(tmp_path):
    f = await construction(tmp_path)
    request, _ = await canonical_request(f)
    await f.wrapper.request_budget(request)
    private = f.provider.counted[-1]
    stream = f.wrapper.stream(request)
    assert await anext(stream) == "offline-chunk"
    await f.session.cleanup()
    assert f.provider.stream_closed and private.messages == []
    assert not f.binding.capability.enabled and f.store._closed
    with pytest.raises(ValueError, match="closed or replaced"):
        f.binding.seal()


async def test_constructor_checks_session_and_duplicate_before_install(tmp_path):
    f = await construction(tmp_path)
    other = AmplifierSession(
        {"session": {"orchestrator": "loop-basic", "context": "context-simple"}}, session_id="other",
    )
    try:
        with pytest.raises(MediaRefusal, match="another session"):
            SessionMediaBinding(other, store=f.store, owner="host", participants=("host",))
        assert other.coordinator.get_capability("session.media.tool_post") is None
        with pytest.raises(MediaRefusal, match="already has media"):
            SessionMediaBinding(f.session, store=f.store, owner="host", participants=("host",))
    finally:
        await f.session.cleanup()
        await other.cleanup()


async def test_core_abi_check_is_not_bypassed_by_host_helper(tmp_path, monkeypatch):
    from amplifier_foundation.session import media_hook_bridge
    session = AmplifierSession(
        {"session": {"orchestrator": "loop-basic", "context": "context-simple"}}, session_id="same-session",
    )
    store = SessionMediaStore(
        tmp_path / "media", session.session_id, checkpoint_verifier=verified_checkpoint_refs,
        policy_verifier=lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(media_hook_bridge, "package_version", lambda name: "2.0.3-unqualified")
    try:
        with pytest.raises(ValueError, match="qualified Core ABI 2.0.2"):
            SessionMediaBinding(session, store=store, owner="host",
                                participants=("host", "loop-basic", "context-simple"))
        assert session.coordinator.get_capability(MEDIA_NAMESPACE) is None
    finally:
        store.close()
        await session.cleanup()


@pytest.mark.parametrize("missing", ["checkpoint", "policy", "owner"])
async def test_constructor_requires_explicit_policy_writer_verifier_and_owner(tmp_path, missing):
    session = AmplifierSession(
        {"session": {"orchestrator": "loop-basic", "context": "context-simple"}}, session_id="same-session",
    )
    store = SessionMediaStore(
        tmp_path / "media", session.session_id,
        checkpoint_verifier=None if missing == "checkpoint" else verified_checkpoint_refs,
        policy_verifier=None if missing == "policy" else lambda *args, **kwargs: True,
    )
    try:
        with pytest.raises(ValueError, match="explicit participant|verifiers"):
            SessionMediaBinding(
                session, store=store, owner="host",
                participants=("loop-basic", "context-simple") if missing == "owner" else
                ("host", "loop-basic", "context-simple"),
            )
        assert session.coordinator.get_capability("session.media.tool_post") is None
        assert session.coordinator.get_capability(MEDIA_NAMESPACE) is None
    finally:
        store.close()
        await session.cleanup()


async def test_preseal_configuration_keeps_producers_disabled_until_explicit_seal(tmp_path):
    f = await construction(tmp_path, seal=False)
    try:
        with f.binding.configure() as graph:
            assert graph is f.binding.graph and not f.binding.capability.enabled
            f.binding.registration.hooks.register(
                "tool:post", lambda event, data: HookResult(), name="saved-settings-policy",
            )
        assert f.binding.bridge.graph_receipt is None
        assert f.binding.capability.enabled is False
        assert f.binding.seal() is True
    finally:
        await f.session.cleanup()


async def test_loop_report_and_booleans_cannot_qualify_an_unowned_hook_chain(tmp_path):
    f = await construction(tmp_path, seal=False)
    f.session.coordinator.hooks.register(
        "tool:post", lambda event, data: HookResult(), name="unowned-policy",
    )
    f.binding.capability.enabled = True
    try:
        with pytest.raises(ValueError, match="incomplete or inconsistent"):
            f.binding.seal()
        assert not f.binding.capability.enabled
        assert not f.binding.bridge.validate_receipt(f.binding.bridge.graph_receipt)
    finally:
        await f.session.cleanup()