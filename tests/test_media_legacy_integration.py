"""Same-session public Core bridge, actual legacy primary and private projection."""
from copy import deepcopy
import base64
import io
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock

from amplifier_core import AmplifierSession, HookResult, ToolResult
from amplifier_core.message_models import ChatRequest, ImageBlock, Message, ToolSpec
from PIL import Image
import pytest

from amplifier_foundation import sanitize_message
from amplifier_foundation.session.checkpoint import (
    acknowledge_legacy_checkpoint, verified_checkpoint_refs,
)
from amplifier_foundation.session.history import SessionHistoryStore
from amplifier_foundation.session.media import (
    MEDIA_NAMESPACE, MediaPolicy, SessionMediaStore, bind_session_media,
)
from amplifier_foundation.session.media_provider import MediaPreparedProvider
from amplifier_foundation.session.media_hook_bridge import install_media_hook_bridge

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX media durability")


def new_store(root, session_id):
    return SessionMediaStore(
        root, session_id, policy=MediaPolicy(1024 * 1024, 10000, 8),
        checkpoint_verifier=verified_checkpoint_refs,
    )


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


def test_diagnostic_kind_cannot_claim_versioned_checkpoint_durability(tmp_path):
    store = SessionMediaStore(
        tmp_path / "kind-only", "session", checkpoint_verifier=lambda receipt: iter(()),
    )
    try:
        result = store.acknowledge_checkpoint({
            "session_id": "session", "kind": "versioned-checkpoint-backed-v1",
        })
        assert result == {"state": "committed", "durability": "legacy-checkpoint-backed",
                          "ref_count": 0}
    finally:
        store.close()


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
@pytest.mark.parametrize("effective", ["retain", "outer-null", "modify-none", "callback-error"])
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
        if effective == "modify-none":
            return HookResult(action="modify", data=None)
        return HookResult()

    terminal_followup = []

    def observe_terminal(event_name, data):
        terminal_followup.append(deepcopy(data.get("result")))
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
        graph.participant("host.terminal").hooks.register(
            "tool:post", observe_terminal, name="after-outer-policy", priority=950,
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
        if effective not in ("retain", "modify-none"):
            assert settled["result"] is None
            assert settled["complete"] is (effective != "callback-error")
            if effective == "outer-null":
                assert terminal_followup == [None]
            await store.publish(refs, origin=origin, disposition="drop")
            assert not (path / "transcript.jsonl").exists()
            return

        assert settled["complete"] is True
        final_refs = settled["result"]["output"][MEDIA_NAMESPACE]["refs"]
        assert final_refs == refs
        assert terminal_followup[0]["output"][MEDIA_NAMESPACE]["refs"] == final_refs
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
