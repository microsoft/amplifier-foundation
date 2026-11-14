"""Pinned candidate pairing through real stores, sanitizer, fork and public seams."""

from copy import deepcopy
import base64
import io
import json
import os
import shutil
import tracemalloc
from contextlib import contextmanager

from amplifier_core import AmplifierSession, ToolResult
from amplifier_core.message_models import ChatRequest, ImageBlock, Message
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