"""Pinned candidate pairing through real stores, sanitizer, fork and public seams."""

from copy import deepcopy
import base64
import io
import json
import os
import shutil
import tracemalloc
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

from amplifier_core import AmplifierSession, HookResult, ToolResult
from amplifier_core.message_models import ChatRequest, ImageBlock, Message, ToolSpec
from PIL import Image
import pytest

from amplifier_foundation import Bundle, sanitize_message
from amplifier_foundation.bundle import PreparedBundle, BundleModuleResolver
from amplifier_foundation.session.checkpoint import (
    acknowledge_legacy_checkpoint, acknowledge_candidate_checkpoint,
    verified_checkpoint_refs,
)
from amplifier_foundation.session.fork import fork_session
from amplifier_foundation.session.history import SessionHistoryStore
from amplifier_foundation.session.media import (
    MEDIA_NAMESPACE, MediaPolicy, SessionMediaStore, bind_session_media,
)
from amplifier_foundation.session.media_conversion import (
    adopt_selected_media, build_candidate, preview,
)
from amplifier_foundation.session.media_provider import MediaPreparedProvider
from amplifier_foundation.session.media_hook_bridge import install_media_hook_bridge

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX candidate graph")


def pixels():
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "purple").save(buffer, format="PNG")
    return buffer.getvalue()


def new_store(root, session_id):
    return SessionMediaStore(
        root, session_id, policy=MediaPolicy(1024 * 1024, 10000, 8),
        checkpoint_verifier=verified_checkpoint_refs,
    )


def ack(store, path, rows):
    receipt = acknowledge_legacy_checkpoint(
        path, session_id=store.session_id, expected_messages=rows
    )
    store.acknowledge_checkpoint(receipt)


def test_unqualified_platform_refuses_store_before_any_root_effect(tmp_path, monkeypatch):
    from amplifier_foundation.session import media
    unopened, uncreated = Mock(), Mock()
    monkeypatch.setattr(media, "os", SimpleNamespace(name="nt", open=unopened, mkdir=uncreated))
    root = tmp_path / "absent-store"
    with pytest.raises(media.MediaRefusal, match="qualified POSIX descriptors"):
        media.SessionMediaStore(root, "unqualified")
    unopened.assert_not_called()
    uncreated.assert_not_called()
    assert not root.exists()


@pytest.mark.parametrize("image_format", ["PNG", "JPEG"])
def test_over_limit_image_header_refuses_before_verify_or_pixel_load(monkeypatch, image_format):
    from amplifier_foundation.session import media
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), "purple").save(stream, format=image_format)
    stream.seek(0)
    opener = Mock(wraps=Image.open)
    loader = Mock(side_effect=AssertionError("Over-limit header must not decode pixels"))
    monkeypatch.setattr(Image, "open", opener)
    monkeypatch.setattr(Image.Image, "load", loader)
    with pytest.raises(media.MediaRefusal, match="pixel limit"):
        media._decode_image(stream, media.MediaPolicy(1000, 3, 1))
    assert opener.call_count == 1
    loader.assert_not_called()


class _JointGraphLoader:
    """Fixed Core fixture, not a claim about an arbitrary host/plugin graph."""

    def __init__(self, observer):
        self.queue, self.observer = [], observer

    async def load(self, module_id, config, *, source_hint=None, coordinator=None):
        async def mount(current):
            if module_id == "joint-loop":
                await current.mount("orchestrator", object())
            elif module_id == "joint-context":
                await current.mount("context", object())
            elif module_id == "joint-hooks":
                current.hooks.register("tool:post", self.observer, name="retained-observer")
        return mount

    def enqueue_on_session_ready(self, name, callback):
        self.queue.append((name, callback))

    def get_on_session_ready_queue(self):
        return self.queue

    def clear_on_session_ready_queue(self):
        self.queue.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("image_format", ["PNG", "JPEG"])
@pytest.mark.parametrize("effective", ["retain", "outer-null", "callback-error"])
async def test_public_graph_final_refs_checkpoint_private_request_joint(
    tmp_path, image_format, effective,
):
    """Actual Core/public bridge and F1 join; host lifecycle is qualified separately."""
    if not hasattr(ToolResult(success=True), "content"):
        pytest.skip("Actual rich result/public bridge requires Core2")
    from importlib.metadata import version
    if version("amplifier-core") != "2.0.2":
        pytest.skip("Participating fixture is qualified against exact Core2.0.2 ABI")
    path = tmp_path / "joint"
    path.mkdir()
    store = new_store(path / "media", "joint")
    session = AmplifierSession(
        {"session": {"orchestrator": "joint-loop", "context": "joint-context"},
         "hooks": [{"module": "joint-hooks"}]}, session_id="joint",
    )
    seen, late_seen = [], []

    def observe(event_name, data):
        seen.append(deepcopy(data["result"]["output"][MEDIA_NAMESPACE]["refs"]))
        return HookResult()

    def terminal(event_name, data):
        late_seen.append(deepcopy(data["result"]))
        if effective == "callback-error":
            raise RuntimeError("Participating image policy failed")
        if effective == "outer-null":
            return HookResult(action="modify", data={"result": None})
        return HookResult()

    session.coordinator.loader = _JointGraphLoader(observe)
    cap = bind_session_media(session.coordinator, store=store, enabled=True)
    bridge = install_media_hook_bridge(session.coordinator)
    graph = bridge.begin_participating_graph(
        session, owner="foundation.public-core2-checkpoint-private-joint",
        participants=("joint-loop", "joint-context", "joint-hooks", "host.terminal"),
    )
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "purple").save(buffer, format=image_format)
    data = buffer.getvalue()
    encoded = base64.b64encode(data).decode()
    origin = {"kind": "tool", "session_id": "joint", "id": "image-call"}
    wrapper = None
    try:
        await session.initialize()
        graph.participant("host.terminal").hooks.register(
            "tool:post", terminal, name="outer-policy", priority=900,
        )
        receipt = graph.seal()
        rich = ToolResult(success=True, output={"useful": "original image"}, content=[
            ImageBlock(source={"type": "base64", "media_type": f"image/{image_format.lower()}",
                               "data": encoded}),
        ])
        staged = await store.ingest(rich, source_format="core-tool-result", origin=origin, policy=None)
        refs = staged["staged_refs"]
        settled = await bridge({"tool_name": "image", "tool_call_id": "image-call",
                                "result": staged["value"].model_dump(mode="json")})
        assert bridge.validate_receipt(receipt)
        assert seen == [refs] and len(late_seen) == 1
        assert encoded not in json.dumps(late_seen)
        if effective != "retain":
            assert settled["result"] is None
            assert settled["complete"] is (effective == "outer-null")
            await store.publish(refs, origin=origin, disposition="drop")
            assert not (path / "transcript.jsonl").exists()
            return

        assert settled["complete"] is True
        final_refs = settled["result"]["output"][MEDIA_NAMESPACE]["refs"]
        assert final_refs == refs
        await store.publish(final_refs, origin=origin, disposition="retain")
        rows = [{"role": "tool", "tool_call_id": "image-call",
                 "content": settled["result"]["content"],
                 "metadata": {MEDIA_NAMESPACE: {"refs": final_refs}}}]
        history = SessionHistoryStore(path)
        history.save(rows, {"session_id": "joint"}, sanitizer=sanitize_message)
        saved = history.load_messages()
        changed = deepcopy(saved)
        changed[0]["metadata"][MEDIA_NAMESPACE]["refs"][0]["sha256"] = "0" * 64
        with pytest.raises(ValueError):
            acknowledge_legacy_checkpoint(path, session_id="joint", expected_messages=changed)
        checkpoint = acknowledge_legacy_checkpoint(path, session_id="joint", expected_messages=saved)
        assert list(verified_checkpoint_refs(checkpoint)) == final_refs
        store.acknowledge_checkpoint(checkpoint)
        canonical = ChatRequest(model="fixture", messages=[Message(**row) for row in saved],
                                tools=[ToolSpec(name="inspect", parameters={"type": "object"})])
        original = canonical.model_dump_json()
        options = {"extended_thinking": True}

        class PrivateProvider:
            counted = None
            sent = None

            def get_media_request_support(self, *, model, request_options=None):
                return {"v": 1, "positions": ["tool"], "mime_types": ["image/png", "image/jpeg"]}

            def request_budget(self, request, *, request_options=None):
                cap.validate(request, purpose="count", request_options=request_options)
                assert request is not canonical and canonical.model_dump_json() == original
                self.counted = request
                assert request.messages[0].metadata[MEDIA_NAMESPACE]["refs"] == final_refs
                image = next(b for b in request.messages[0].content if isinstance(b, ImageBlock))
                assert base64.b64decode(image.source["data"]) == data
                return {"estimated_input_tokens": 10, "input_limit_tokens": 100,
                        "context_token_budget": 100}

            async def complete(self, request, **kwargs):
                cap.validate(request, purpose="transport", request_options=kwargs)
                assert request is not canonical and canonical.model_dump_json() == original
                assert request is self.counted and kwargs == options
                self.sent = request
                return "qualified-fixture"

        provider = PrivateProvider()
        wrapper = MediaPreparedProvider(provider, cap, provider_identity={
            "provider": "synthetic", "account": "fixture", "generation": "joint",
        }, request_scope=bridge.request_scope)
        await wrapper.request_budget(canonical, request_options=options)
        with pytest.raises(ValueError, match="idle graph"):
            with graph.transition():
                raise AssertionError("Counted private request must hold graph generation")
        canonical.messages[0].metadata[MEDIA_NAMESPACE]["refs"][0]["sha256"] = "0" * 64
        with pytest.raises(ValueError, match="changed before dispatch"):
            await wrapper.complete(canonical, **options)
        assert provider.sent is None and len(seen) == len(late_seen) == 1
        canonical.messages[0].metadata[MEDIA_NAMESPACE]["refs"] = deepcopy(final_refs)
        await wrapper.request_budget(canonical, request_options=options)
        assert await wrapper.complete(canonical, **options) == "qualified-fixture"
        assert provider.sent.messages == []  # private buffers released after dispatch
        assert canonical.model_dump_json() == original and encoded not in original
        assert encoded.encode() not in (path / "transcript.jsonl").read_bytes()
        await wrapper.request_budget(canonical, request_options=options)
        abandoned = provider.counted
        assert not wrapper.release_media_request(deepcopy(canonical), request_options=options)
        assert wrapper.release_media_request(canonical, request_options=options)
        assert abandoned.messages == []
        assert not wrapper.release_media_request(canonical, request_options=options)
        with graph.transition():
            pass
        assert not bridge.validate_receipt(receipt)
        assert bridge.validate_receipt(bridge.graph_receipt)
    finally:
        if wrapper is not None:
            await wrapper.aclose()
        await session.cleanup()
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("rich_result", [False, True], ids=["binary-owner", "core-rich"])
async def test_actual_rich_result_sanitize_save_adopt_fork_resume_private_projection(
    tmp_path, rich_result,
):
    if rich_result and not hasattr(ToolResult(success=True), "content"):
        pytest.skip("Core rich ToolResult requires Core2; binary-owner pairing still runs")
    parent_dir = tmp_path / "parent"
    parent_dir.mkdir()
    parent = new_store(parent_dir / "media", "parent")
    child = None
    data = pixels()
    encoded = base64.b64encode(data).decode()
    origin = {"kind": "tool", "session_id": "parent", "id": "call-1"}
    try:
        if rich_result:
            rich = ToolResult(success=True, output={"preserve": 8}, content=[
                ImageBlock(source={"type": "base64", "media_type": "image/png", "data": encoded}),
            ])
            staged = await parent.ingest(rich, source_format="core-tool-result",
                                         origin=origin, policy=None)
            blocks = [block.model_dump() for block in staged["value"].content]
        else:
            staged = await parent.ingest(data, source_format="image-bytes",
                                         origin=origin, policy=None)
            blocks = [{"type": "text", "text": f"[Session image {staged['value']['sha256']}]"}]
        refs = staged["staged_refs"]
        await parent.publish(refs, origin=origin, disposition="retain")
        canonical = [
            {"role": "user", "content": "inspect"},
            {"role": "tool", "tool_call_id": "call-1",
             "content": blocks,
             "metadata": {MEDIA_NAMESPACE: {"refs": refs},
                          "provider": {"opaque": "do-not-change"}}},
        ]
        SessionHistoryStore(parent_dir).save(canonical, {"session_id": "parent"},
                                             sanitizer=sanitize_message)
        saved = SessionHistoryStore(parent_dir).load_messages()
        assert saved[1]["metadata"][MEDIA_NAMESPACE]["refs"][0]["provenance"]["expires_at"] is None
        ack(parent, parent_dir, saved)
        before = deepcopy(saved)

        def adopt(rows, *, session_id, session_dir):
            nonlocal child
            child = new_store(session_dir / "media", session_id)
            return adopt_selected_media(rows, source_store=parent, target_store=child,
                                        target_session_id=session_id)

        def target_checkpoint(rows, *, session_id, session_dir):
            ack(child, session_dir, rows)

        result = fork_session(parent_dir, include_events=False, new_session_id="child",
                              media_adopter=adopt, media_checkpoint=target_checkpoint)
        target = SessionHistoryStore(result.session_dir).load_messages()
        target_ref = target[1]["metadata"][MEDIA_NAMESPACE]["refs"][0]
        assert target_ref["session_id"] == "child"
        assert target_ref["sha256"] == refs[0]["sha256"]
        assert target_ref["provenance"] == refs[0]["provenance"]
        assert saved == before
        child.close()
        parent.close()
        # Independent closure: parent is unavailable while child resumes.
        resumed = new_store(result.session_dir / "media", "child")
        child = resumed
        ack(resumed, result.session_dir, target)
        with resumed.resolve_ref(target_ref) as source:
            assert source.read() == data

        class Coordinator:
            session_id = "child"
            def __init__(self):
                self.capabilities = {}
            def get_capability(self, key):
                return self.capabilities.get(key)
            def register_capability(self, key, value):
                self.capabilities[key] = value

        cap = bind_session_media(Coordinator(), store=resumed, enabled=True)
        canonical_request = ChatRequest(messages=[Message(**row) for row in target])
        original = canonical_request.model_dump()
        prepared = await cap.prepare_request(
            canonical_request,
            provider_identity={"provider": "fixture", "account": "one", "generation": "g1",
                               "media_support": {"v": 1, "positions": ["tool"],
                                                 "mime_types": ["image/png"]}},
            model="fixture-vision", request_options=None, purpose="model",
            execution_scope={},
        )
        assert canonical_request.model_dump() == original
        assert encoded not in json.dumps(original)
        projected = prepared.request.model_dump()
        assert encoded in json.dumps(projected)
        cap.validate(prepared.request, purpose="transport", request_options=None)
        prepared.release()
        # No persistent body or journal contains the transport encoding.
        for path in result.session_dir.rglob("*"):
            if path.is_file():
                assert encoded.encode() not in path.read_bytes()
    finally:
        if child is not None:
            child.close()
        parent.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("resumed", [False, True])
async def test_real_create_and_resume_install_fresh_cap_before_module_initialize(
    tmp_path, monkeypatch, resumed,
):
    stores, capabilities = [], []

    async def before(session):
        store = new_store(tmp_path / session.session_id / "media", session.session_id)
        stores.append(store)
        capabilities.append(bind_session_media(session.coordinator, store=store, enabled=False))

    async def initialize(session):
        cap = session.coordinator.get_capability(MEDIA_NAMESPACE)
        assert cap is capabilities[-1]
        assert cap.session_id == session.session_id

    monkeypatch.setattr(AmplifierSession, "initialize", initialize)
    bundle = Bundle(name="fixture", session={"orchestrator": "fixture", "context": "fixture"})
    prepared = PreparedBundle(mount_plan=bundle.to_mount_plan(), bundle=bundle,
                              resolver=BundleModuleResolver({}))
    try:
        await prepared.create_session(session_id="root", is_resumed=resumed,
                                      session_cwd=tmp_path, before_initialize=before)
        await prepared.create_session(session_id="another", session_cwd=tmp_path,
                                      before_initialize=before)
        assert capabilities[0] is not capabilities[1]
    finally:
        for store in stores:
            store.close()


@pytest.mark.asyncio
async def test_bounded_actual_historical_candidate_ack_without_decoding_huge_row(tmp_path):
    session = tmp_path / "session"
    session.mkdir()
    store = new_store(session / "media", "session")
    origin = {"kind": "tool", "session_id": "session", "id": "historical-call"}
    try:
        ref = (await store.ingest(pixels(), source_format="image-bytes",
                                  origin=origin, policy=None))["value"]
        await store.publish([ref], origin=origin, disposition="retain")
        # Huge non-image owner scalar stays untouched; this is intentionally not
        # an inline image that conversion could shrink away before ACK.
        rows = [{"role": "tool", "tool_call_id": "historical-call",
                 "content": [{"type": "text", "text": f"[Session image {ref['sha256']}]"}],
                 "metadata": {MEDIA_NAMESPACE: {"refs": [ref]},
                              "opaque-large-owner-string": "q" * (16 * 1024 * 1024)}}]
        primary = session / "transcript.jsonl"
        primary.write_text(json.dumps(rows[0]) + "\n")

        @contextmanager
        def fence(owner):
            assert owner == "synthetic-owner"
            yield

        @contextmanager
        def reader(item):
            with store.resolve_ref(item, committed=False) as source:
                yield source

        plan = preview(primary, owner="synthetic-owner", session_id="session", fence=fence)
        operation = tmp_path / "operation"
        await build_candidate(plan, operation, fence=fence, capability=store,
                              origin_for=lambda *_: origin, asset_reader=reader)
        shutil.copyfile(operation / "candidate.jsonl", primary)
        tracemalloc.start()
        try:
            receipt = acknowledge_candidate_checkpoint(
                operation, session, session_id="session", asset_reader=reader
            )
            store.acknowledge_checkpoint(receipt)
            assert receipt.contains(ref)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert receipt.message_count == 1
        assert peak < 2 * 1024 * 1024
        assert (operation / "original-0").stat().st_size > 16 * 1024 * 1024
    finally:
        store.close()