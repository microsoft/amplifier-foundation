"""Synthetic POSIX media tests; no screenshots, transport or live data."""
import asyncio
import base64
import copy
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from amplifier_core import ToolResult
from amplifier_core.message_models import ChatRequest, Message, ImageBlock, TextBlock
from amplifier_foundation.session.media import (
    MEDIA_NAMESPACE, MediaPolicy, MediaRefusal, SessionMediaStore,
    bind_session_media, validate_blob_ref,
)


def image_bytes(fmt="PNG", color="red"):
    output = io.BytesIO()
    Image.new("RGB", (3, 2), color).save(output, format=fmt)
    return output.getvalue()


class Coordinator:
    session_id = "session1"

    def __init__(self):
        self.capabilities = {}
        self._tool_dispatch_contexts = {}

    def get_capability(self, name):
        return self.capabilities.get(name)

    def register_capability(self, name, value):
        self.capabilities[name] = value


@unittest.skipUnless(os.name == "posix", "POSIX store durability; neutral/projector portable tests separate")
class MediaTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name) / "media"
        self.policy = MediaPolicy(1024 * 1024, 10000, 10)
        self.store = SessionMediaStore(
            self.root, "session1", policy=self.policy,
            checkpoint_verifier=lambda receipt: receipt["refs"],
        )
        self.origin = {"kind": "tool", "session_id": "session1", "id": "call1"}
        self.data = image_bytes()

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    async def stage(self, origin=None, value=None):
        return (await self.store.ingest(
            value if value is not None else self.data, source_format="image-bytes",
            origin=origin or self.origin, policy=None,
        ))["value"]

    async def commit(self, ref, origin=None):
        await self.store.publish([ref], origin=origin or self.origin, disposition="retain")
        self.store.acknowledge_checkpoint({"refs": [ref]})

    async def test_original_sha_and_decoder_dimensions(self):
        ref = await self.stage()
        self.assertEqual(ref["sha256"], hashlib.sha256(self.data).hexdigest())
        self.assertEqual((ref["width"], ref["height"], ref["mime"]), (3, 2, "image/png"))

    async def test_strict_schema_boolean_unknown_and_forged(self):
        ref = await self.stage()
        for field, value in (("v", True), ("bytes", True), ("width", 0), ("v", 2)):
            changed = {**ref, field: value}
            with self.assertRaises(MediaRefusal):
                validate_blob_ref(changed)
        with self.assertRaises(MediaRefusal):
            validate_blob_ref({**ref, "url": "https://invalid/"})
        forged = copy.deepcopy(ref)
        forged["provenance"]["id"] = "another-call"
        with self.assertRaises(MediaRefusal):
            self.store.validate_ref(forged, committed=False)

    async def test_stage_publish_ack_are_distinct(self):
        ref = await self.stage()
        with self.assertRaises(MediaRefusal):
            self.store.resolve_ref(ref)
        publication = await self.store.publish([ref], origin=self.origin, disposition="retain")
        self.assertEqual(publication["publication"]["durability"], "assets-only")
        with self.assertRaises(MediaRefusal):
            self.store.resolve_ref(ref)
        self.store.acknowledge_checkpoint({"refs": [ref]})
        with self.store.resolve_ref(ref) as stream:
            self.assertEqual(stream.read(), self.data)

    async def test_no_checkpoint_no_model_access(self):
        ref = await self.stage()
        await self.store.publish([ref], origin=self.origin, disposition="retain")
        self.store.checkpoint_verifier = None
        with self.assertRaises(MediaRefusal):
            self.store.acknowledge_checkpoint({"refs": [ref]})

    async def test_report_does_not_commit_or_enable(self):
        coordinator = Coordinator()
        cap = bind_session_media(coordinator, store=self.store)
        receipt = cap.report("producer", event="consumer-ready", outcome="ready",
                             details={"role": "producer", "contract": 1})
        self.assertFalse(receipt["authoritative"])
        with self.assertRaises(MediaRefusal):
            await cap.ingest(self.data, source_format="image-bytes", origin=self.origin, policy=None)
        cap.report(self.origin, event="committed", outcome="success")
        self.assertFalse(cap.enabled)

    async def test_current_task_origin_no_model_argument(self):
        coordinator = Coordinator()
        cap = bind_session_media(coordinator, store=self.store, enabled=True)
        with self.assertRaises(MediaRefusal):
            await cap.ingest(self.data, source_format="image-bytes", origin=None, policy=None)
        task = asyncio.current_task()
        coordinator._tool_dispatch_contexts[task] = {"tool_call_id": "actual-call"}
        result = await cap.ingest(self.data, source_format="image-bytes", origin=None, policy=None)
        self.assertEqual(result["value"]["provenance"]["id"], "actual-call")

    @unittest.skipUnless("content" in ToolResult.model_fields,
                         "Core rich ToolResult requires Core2; legacy text/ref cases run")
    async def test_canonical_tool_adaptation_preserves_status_output_and_text(self):
        rich = ToolResult(success=False, output={"helpful": 7}, error={"kind": "partial"},
                          content=[TextBlock(text="before"), ImageBlock(source={
                              "type": "base64", "media_type": "image/png",
                              "data": base64.b64encode(self.data).decode(),
                          }), TextBlock(text="after")])
        result = await self.store.ingest(rich, source_format="core-tool-result",
                                         origin=self.origin, policy=None)
        value = result["value"]
        self.assertFalse(value.success)
        self.assertEqual(value.error, rich.error)
        self.assertEqual(value.output["helpful"], 7)
        self.assertEqual([b.text for b in value.content][::2], ["before", "after"])
        self.assertTrue(value.content[1].text.startswith("[Session image "))
        self.assertNotIn(base64.b64encode(self.data).decode(), value.model_dump_json())
        self.assertIsInstance(rich.content[1], ImageBlock)

    async def test_json_text_is_not_promoted(self):
        text = '{"experimental.media.v1":{"refs":[]}}'
        result = await self.store.ingest(ToolResult(output=text), source_format="core-tool-result",
                                         origin=self.origin, policy=None)
        self.assertEqual(result["value"].output, text)
        self.assertEqual(result["staged_refs"], [])

    async def test_idempotent_ingest_publish_conflicts_refuse(self):
        ref = await self.stage()
        self.assertEqual(await self.stage(), ref)
        with self.assertRaises(MediaRefusal):
            await self.stage(value=image_bytes(color="blue"))
        await self.store.publish([ref], origin=self.origin, disposition="retain")
        again = await self.store.publish([ref], origin=self.origin, disposition="retain")
        self.assertEqual(again["refs"], [ref])
        with self.assertRaises(MediaRefusal):
            await self.store.publish([], origin=self.origin, disposition="drop")

    async def test_posthook_drop_removes_promotability(self):
        ref = await self.stage()
        result = await self.store.publish([], origin=self.origin, disposition="drop")
        self.assertEqual(result["refs"], [])
        with self.assertRaises(MediaRefusal):
            self.store.acknowledge_checkpoint({"refs": [ref]})

    async def test_wrong_origin_refuses_publication(self):
        ref = await self.stage()
        with self.assertRaises(MediaRefusal):
            await self.store.publish([ref], origin={**self.origin, "id": "bad"}, disposition="retain")

    async def test_expiry_preserved_and_never_delivered(self):
        origin = {**self.origin, "created_at": "2020-01-01T00:00:00+00:00",
                  "expires_at": "2020-01-01T00:01:00+00:00"}
        ref = await self.stage(origin=origin)
        self.assertEqual(ref["provenance"], origin)
        with self.assertRaises(MediaRefusal) as cm:
            self.store.resolve_ref(ref, committed=False)
        self.assertEqual(cm.exception.code, "expired")

    async def test_host_limits_and_no_widening(self):
        for policy in ({"max_bytes": self.policy.max_bytes + 1}, {"ttl_seconds": 100},
                       {"retention": "unknown"}):
            with self.assertRaises(MediaRefusal):
                await self.store.ingest(self.data, source_format="image-bytes",
                                        origin=self.origin, policy=policy)
        with self.assertRaises(MediaRefusal):
            await self.store.ingest(self.data, source_format="image-bytes",
                                    origin=self.origin, policy={"max_bytes": 10})
        with self.assertRaises(MediaRefusal):
            await self.store.ingest(self.data, source_format="image-bytes",
                                    origin=self.origin, policy={"max_pixels": 2})

    async def test_bounded_stream_reads(self):
        class Bounded(io.BytesIO):
            def read(self, n=-1):
                if n < 0 or n > 65536:
                    raise AssertionError("unbounded image read")
                return super().read(n)
        ref = await self.stage(value={"stream": Bounded(self.data), "mime": "image/png"})
        self.assertEqual(ref["bytes"], len(self.data))

    async def test_strict_base64_and_mime_and_decoder(self):
        for source in (
            {"encoding": "base64", "data": "not base64!"},
            {"stream": io.BytesIO(self.data), "mime": "image/jpeg"},
            b"not an image", image_bytes("GIF"),
        ):
            with self.assertRaises(MediaRefusal):
                await self.stage(value=source)

    async def test_same_bytes_dedup_not_provenance(self):
        a = await self.stage()
        other = {**self.origin, "id": "call2"}
        b = await self.stage(origin=other)
        await self.commit(a)
        await self.commit(b, origin=other)
        self.assertNotEqual(a["provenance"], b["provenance"])
        self.assertEqual(len(list((self.root / "assets").iterdir())), 1)

    async def test_resume_rechecks_canonical_checkpoint(self):
        ref = await self.stage()
        await self.commit(ref)
        self.store.close()
        self.store = SessionMediaStore(self.root, "session1", policy=self.policy,
                                       checkpoint_verifier=lambda _: [])
        with self.assertRaises(MediaRefusal):
            self.store.resolve_ref(ref)

    async def test_changed_bytes_and_symlink_refuse(self):
        ref = await self.stage()
        await self.commit(ref)
        asset = self.root / "assets" / ref["sha256"]
        asset.write_bytes(b"x" * len(self.data))
        with self.assertRaises(MediaRefusal):
            self.store.resolve_ref(ref)
        asset.unlink()
        asset.symlink_to("/etc/passwd")
        with self.assertRaises(OSError):
            self.store.resolve_ref(ref)

    async def test_resolved_snapshot_not_live_file(self):
        ref = await self.stage()
        await self.commit(ref)
        with self.store.resolve_ref(ref) as stream:
            (self.root / "assets" / ref["sha256"]).write_bytes(b"x" * len(self.data))
            self.assertEqual(stream.read(), self.data)

    async def test_asset_failure_never_commits(self):
        ref = await self.stage()
        with patch("amplifier_foundation.session.media.os.fsync", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                await self.store.publish([ref], origin=self.origin, disposition="retain")
        with self.assertRaises(MediaRefusal):
            self.store.resolve_ref(ref)
        self.assertTrue(list(self.store.journal()))

    async def test_private_paths_and_session_root(self):
        self.assertEqual(os.stat(self.root).st_mode & 0o777, 0o700)
        with self.assertRaises(MediaRefusal):
            SessionMediaStore(self.root, "different", policy=self.policy)
        link = Path(self.directory.name) / "link"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            SessionMediaStore(link, "session1", policy=self.policy)

    async def test_adopt_changes_owner_not_provenance(self):
        ref = await self.stage()
        await self.commit(ref)
        target = SessionMediaStore(Path(self.directory.name) / "target", "child",
                                   policy=self.policy, checkpoint_verifier=lambda r: r["refs"])
        try:
            adopted = await target.adopt([ref], self.store)
            self.assertEqual(adopted[0]["session_id"], "child")
            self.assertEqual(adopted[0]["provenance"], ref["provenance"])
            with self.assertRaises(MediaRefusal):
                target.resolve_ref(adopted[0])
            target.acknowledge_checkpoint({"refs": adopted})
            with target.resolve_ref(adopted[0]) as stream:
                self.assertEqual(stream.read(), self.data)
        finally:
            target.close()

    async def test_real_checkpoint_receipt_is_private_and_resume_requires_fresh_ack(self):
        from amplifier_foundation.session.checkpoint import (
            acknowledge_legacy_checkpoint, verified_checkpoint_refs,
        )
        ref = await self.stage()
        await self.store.publish([ref], origin=self.origin, disposition="retain")
        messages = [{"role": "tool", "content": "image",
                     "metadata": {MEDIA_NAMESPACE: {"refs": [ref]}}}]
        session_dir = Path(self.directory.name) / "canonical"
        session_dir.mkdir()
        (session_dir / "transcript.jsonl").write_text(
            "".join(json.dumps(message) + "\n" for message in messages))
        receipt = acknowledge_legacy_checkpoint(session_dir, session_id="session1",
                                                 expected_messages=messages)
        self.store.checkpoint_verifier = verified_checkpoint_refs
        self.store.acknowledge_checkpoint(receipt)
        journal = json.dumps(list(self.store.journal()))
        self.assertNotIn("_seal", journal)
        self.assertNotIn("_attestation", journal)
        self.assertNotIn(str(session_dir), journal)
        with self.store.resolve_ref(ref) as stream:
            self.assertEqual(stream.read(), self.data)
        self.store.close()
        self.store = SessionMediaStore(self.root, "session1", policy=self.policy,
                                       checkpoint_verifier=verified_checkpoint_refs)
        with self.assertRaises(MediaRefusal):
            self.store.resolve_ref(ref)
        fresh = acknowledge_legacy_checkpoint(session_dir, session_id="session1",
                                               expected_messages=messages)
        self.store.acknowledge_checkpoint(fresh)
        with self.store.resolve_ref(ref):
            pass
        (session_dir / "transcript.jsonl").write_text('{"role":"tool","content":"changed"}\n')
        with self.assertRaises(ValueError):
            self.store.resolve_ref(ref)

    async def test_checkpoint_verifier_exhausted_before_any_commit(self):
        ref = await self.stage()
        await self.store.publish([ref], origin=self.origin, disposition="retain")
        def late_failure(_):
            yield ref
            raise MediaRefusal("source changed after yielded reference")
        self.store.checkpoint_verifier = late_failure
        with self.assertRaises(MediaRefusal):
            self.store.acknowledge_checkpoint({"refs": [ref]})
        records = [row for row in self.store.journal() if row.get("checkpoint")]
        self.assertEqual(records, [])

    async def test_host_source_policy_gate(self):
        ref = await self.stage()
        await self.commit(ref)
        self.store.policy_verifier = lambda ref, purpose: False
        with self.assertRaises(MediaRefusal) as cm:
            self.store.resolve_ref(ref)
        self.assertEqual(cm.exception.code, "source_policy")

    async def test_import_requires_authorized_exact_ref_and_published_not_commit(self):
        ref = await self.stage()
        target = SessionMediaStore(Path(self.directory.name) / "import", "target",
                                   policy=self.policy, checkpoint_verifier=lambda r: r["refs"])
        try:
            with self.assertRaises(MediaRefusal):
                target.import_verified(ref, io.BytesIO(self.data), receipt={"package": 1},
                                       verifier=lambda *args, **kwargs: False)
            adopted = target.import_verified(ref, io.BytesIO(self.data), receipt={"package": 1},
                                            verifier=lambda *args, **kwargs: True)
            self.assertEqual(adopted["provenance"], ref["provenance"])
            with self.assertRaises(MediaRefusal):
                target.resolve_ref(adopted)
            target.acknowledge_checkpoint({"refs": [adopted]})
            with target.resolve_ref(adopted):
                pass
        finally:
            target.close()

    async def test_actual_private_projection_and_per_attempt_witness(self):
        ref = await self.stage()
        await self.commit(ref)
        cap = bind_session_media(Coordinator(), store=self.store, enabled=True)
        request = ChatRequest(model="vision-model", messages=[Message(
            role="tool", tool_call_id="call1", content=[
                TextBlock(text="before"), TextBlock(text=f"[Session image {ref['sha256']}]"),
                TextBlock(text="after")],
            metadata={MEDIA_NAMESPACE: {"refs": [ref]}, "opaque": {"signature": "keep"}},
        )])
        original = request.model_dump_json()
        identity = {"provider": "fixture", "account": "test", "generation": "g1",
                    "media_support": {"v": 1, "positions": ["tool"]}}
        prepared = await cap.prepare_request(request, provider_identity=identity,
            model="vision-model", request_options={}, purpose="model", execution_scope={})
        try:
            private = prepared.request
            self.assertIsInstance(private.messages[0].content[1], ImageBlock)
            self.assertEqual(base64.b64decode(private.messages[0].content[1].source["data"]), self.data)
            self.assertEqual(request.model_dump_json(), original)
            self.assertEqual(private.messages[0].metadata["opaque"], {"signature": "keep"})
            self.assertTrue(cap.validate(private, purpose="count", request_options={})["valid"])
            self.assertTrue(cap.validate(private, purpose="transport",
                                          request_options={"model": "vision-model"})["valid"])
            with self.assertRaises(MediaRefusal):
                cap.validate(private, purpose="transport", request_options={"model": "other"})
            identity["account"] = "changed"
            with self.assertRaises(MediaRefusal):
                cap.validate(private, purpose="transport", request_options={})
        finally:
            prepared.release()
        self.assertEqual(private.messages, [])
        self.assertEqual(request.model_dump_json(), original)

    async def test_actual_unsupported_freezes_warning_before_count(self):
        ref = await self.stage()
        await self.commit(ref)
        cap = bind_session_media(Coordinator(), store=self.store, enabled=True)
        request = ChatRequest(messages=[Message(role="user", content="picture",
            metadata={MEDIA_NAMESPACE: {"refs": [ref]}})])
        with patch.object(self.store, "resolve_ref", side_effect=AssertionError("no hydration")):
            prepared = await cap.prepare_request(request, provider_identity={
                "provider": "text", "account": "test", "generation": "g1",
                "media_support": {"v": 1, "positions": []}},
                model="text", request_options={}, purpose="model", execution_scope={})
            try:
                self.assertTrue(all(not isinstance(b, ImageBlock)
                                    for b in prepared.request.messages[0].content))
                self.assertIn("no pixels supplied", prepared.request.model_dump_json())
                self.assertTrue(cap.validate(prepared.request, purpose="transport", request_options={})["valid"])
            finally:
                prepared.release()

    async def test_expired_package_adopts_metadata_only(self):
        origin = {**self.origin, "created_at": "2020-01-01T00:00:00+00:00",
                  "expires_at": "2020-01-01T00:01:00+00:00"}
        ref = await self.stage(origin=origin)
        target = SessionMediaStore(Path(self.directory.name) / "expired", "target",
                                   policy=self.policy, checkpoint_verifier=lambda r: r["refs"])
        try:
            accepted = target.import_verified(ref, None, receipt={"signed": True},
                                              verifier=lambda *args, **kwargs: True)
            self.assertEqual(accepted["provenance"], origin)
            target.acknowledge_checkpoint({"refs": [accepted]})
            self.assertEqual(list((target.root / "assets").iterdir()), [])
            self.assertEqual(target.validate_ref(accepted, check_expiry=False), accepted)
            with self.assertRaises(MediaRefusal) as cm:
                target.resolve_ref(accepted)
            self.assertEqual(cm.exception.code, "expired")
        finally:
            target.close()

    async def test_readonly_accepted_export_never_promotes_staged(self):
        ref = await self.stage()
        with self.assertRaises(MediaRefusal):
            self.store.validate_published_ref(ref)
        await self.store.publish([ref], origin=self.origin, disposition="retain")
        self.store.close()
        before = {str(p.relative_to(self.root)): p.read_bytes()
                  for p in self.root.rglob("*") if p.is_file()}
        self.store = SessionMediaStore(self.root, "session1", policy=self.policy, read_only=True,
                                       source_read_verifier=lambda ref, purpose: True)
        self.assertEqual(self.store.validate_published_ref(ref), ref)
        with self.store.resolve_published_ref(ref) as stream:
            self.assertEqual(stream.read(), self.data)
        with self.assertRaises(MediaRefusal):
            self.store.acknowledge_checkpoint({"refs": [ref]})
        with self.assertRaises(MediaRefusal):
            await self.stage(origin={**self.origin, "id": "new"})
        after = {str(p.relative_to(self.root)): p.read_bytes()
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    async def test_actual_wrapper_count_send_private_identity_and_live_account_gate(self):
        from amplifier_foundation.session.media_provider import MediaPreparedProvider
        ref = await self.stage()
        await self.commit(ref)
        cap = bind_session_media(Coordinator(), store=self.store, enabled=True)
        canonical = ChatRequest(model="qualified", messages=[Message(
            role="user", content="picture", metadata={MEDIA_NAMESPACE: {"refs": [ref]}})])
        account = {"provider": "fixture", "account": "account1", "generation": "gen1"}
        class Provider:
            counted = None
            sent = None
            mutate_during_transport = False
            def get_media_request_support(self, *, model, request_options=None):
                return {"v": 1, "positions": ["user"]}
            def request_budget(self, request, *, request_options=None):
                cap.validate(request, purpose="count", request_options=request_options)
                self.counted = request
                return {"estimated_input_tokens": 10, "input_limit_tokens": 100,
                        "context_token_budget": 90,
                        "measurement": {"kind": "provider_count", "source": "fixture",
                                        "input_tokens": 10}}
            async def complete(self, request, **kwargs):
                if self.mutate_during_transport:
                    account["account"] = "changed-after-await"
                cap.validate(request, purpose="transport", request_options=kwargs)
                self.sent = request
                return "done"
        provider = Provider()
        wrapper = MediaPreparedProvider(provider, cap, provider_identity=lambda: account)
        await wrapper.request_budget(canonical, request_options={})
        self.assertTrue(wrapper.media_budget_witness(canonical)["validated"])
        self.assertEqual(await wrapper.complete(canonical, model="qualified"), "done")
        self.assertIs(provider.counted, provider.sent)
        self.assertEqual(provider.sent.messages, [])  # released, not canonical.
        self.assertEqual(canonical.messages[0].content, "picture")
        provider.mutate_during_transport = True
        with self.assertRaises(ValueError):
            await wrapper.complete(canonical)
        wrapper.close()

    async def test_nested_refs_require_exact_dispatch_attestation(self):
        ref = await self.stage()
        await self.commit(ref)
        outer = {**self.origin, "id": "outer"}
        result = ToolResult(output={MEDIA_NAMESPACE: {"refs": [ref]}})
        with self.assertRaises(MediaRefusal):
            await self.store.ingest(result, source_format="core-tool-result",
                                    origin=outer, policy=None)
        self.store.nested_verifier = lambda parent, child: (
            parent["id"] == "outer" and child == ref
        )
        normalized = await self.store.ingest(result, source_format="core-tool-result",
                                             origin=outer, policy=None)
        self.assertEqual(normalized["staged_refs"][0]["provenance"], ref["provenance"])
        await self.store.publish([], origin=outer, disposition="drop")
        # Outer hook suppression never revokes already committed inner evidence.
        with self.store.resolve_ref(ref):
            pass

    async def test_observer_snapshot_with_live_writer_requires_source_guard(self):
        ref = await self.stage()
        await self.commit(ref)
        guarded = True
        observer = SessionMediaStore(self.root, "session1", policy=self.policy, read_only=True,
            source_read_verifier=lambda ref, purpose: guarded)
        try:
            with observer.resolve_published_ref(ref) as stream:
                self.assertEqual(stream.read(), self.data)
            guarded = False
            with self.assertRaises(MediaRefusal):
                observer.resolve_published_ref(ref)
            with self.assertRaises(MediaRefusal):
                observer.resolve_ref(ref, committed=False)
            with self.assertRaises(MediaRefusal):
                observer.publish_sync([ref], origin=self.origin, disposition="retain")
        finally:
            observer.close()

    async def test_classification_is_indexed_routing_not_delivery_authority(self):
        ref = await self.stage()
        self.store.classify_origin(ref, classification="computer-use")
        self.assertEqual(self.store.get_origin_classification(ref), "computer-use")
        with self.assertRaises(MediaRefusal):
            self.store.classify_origin(ref, classification="ordinary-upload")
        with self.assertRaises(MediaRefusal):
            self.store.resolve_ref(ref)

    async def test_host_admitted_request_only_overlay_preserved_and_revoked(self):
        request = ChatRequest(messages=[Message(role="user", content=[
            ImageBlock(source={"type": "base64", "media_type": "image/png",
                               "data": base64.b64encode(self.data).decode()})])])
        permitted = True
        def verifier(original, *, message_index, block_index, purpose):
            return original is request and (message_index, block_index) == (0, 0) and permitted
        self.store.request_only_verifier = verifier
        cap = bind_session_media(Coordinator(), store=self.store, enabled=True)
        prepared = await cap.prepare_request(request, provider_identity={
            "provider": "fixture", "account": "a", "generation": "g",
            "media_support": {"v": 1, "positions": ["user"]}}, model="vision",
            request_options={}, purpose="model", execution_scope={})
        try:
            self.assertIsInstance(prepared.request.messages[0].content[0], ImageBlock)
            self.assertTrue(cap.validate(prepared.request, purpose="count", request_options={})["valid"])
            permitted = False
            with self.assertRaises(MediaRefusal):
                cap.validate(prepared.request, purpose="transport", request_options={})
        finally:
            prepared.release()
        self.assertTrue(request.messages[0].content[0].source["data"])

    async def test_publication_strict_supplied_ref_and_interrupted_drop_reconciliation(self):
        ref = await self.stage()
        with self.assertRaises(MediaRefusal):
            await self.store.publish([{**ref, "v": True}], origin=self.origin, disposition="retain")
        original_write = self.store._write
        def fail_origin(directory, name, value):
            if directory == self.store._dirs["origins"] and value.get("publication") is not None:
                raise OSError("lost final origin publication write")
            original_write(directory, name, value)
        with patch.object(self.store, "_write", side_effect=fail_origin):
            with self.assertRaises(OSError):
                await self.store.publish([], origin=self.origin, disposition="drop")
        result = await self.store.publish([], origin=self.origin, disposition="drop")
        self.assertEqual(result["refs"], [])

    async def test_asset_fsync_failure_reconciliation_completes_barriers(self):
        ref = await self.stage()
        original = os.fsync
        failed = False
        def fault(fd):
            nonlocal failed
            if os.fstat(fd).st_size == len(self.data) and not failed:
                failed = True
                raise OSError("asset fsync interrupted")
            original(fd)
        with patch("amplifier_foundation.session.media.os.fsync", side_effect=fault):
            with self.assertRaises(OSError):
                await self.store.publish([ref], origin=self.origin, disposition="retain")
        seen = []
        def record(fd):
            seen.append((os.fstat(fd).st_size, fd == self.store._dirs["assets"]))
            original(fd)
        with patch("amplifier_foundation.session.media.os.fsync", side_effect=record):
            await self.store.publish([ref], origin=self.origin, disposition="retain")
        self.assertTrue(any(size == len(self.data) for size, _ in seen))
        self.assertTrue(any(directory for _, directory in seen))

    async def test_unsupported_mime_never_reads_pixels(self):
        ref = await self.stage()
        await self.commit(ref)
        cap = bind_session_media(Coordinator(), store=self.store, enabled=True)
        request = ChatRequest(messages=[Message(role="user", content="picture",
            metadata={MEDIA_NAMESPACE: {"refs": [ref]}})])
        for mimes in ([], ["image/jpeg"]):
            with patch.object(self.store, "resolve_ref", side_effect=AssertionError("no pixels")):
                prepared = await cap.prepare_request(request, provider_identity={
                    "provider": "jpeg-only", "account": "a", "generation": "g",
                    "media_support": {"v": 1, "positions": ["user"], "mime_types": mimes}},
                    model="fixture", request_options={}, purpose="model", execution_scope={})
                try:
                    self.assertTrue(cap.validate(prepared.request, purpose="count", request_options={})["valid"])
                    self.assertTrue(all(not isinstance(b, ImageBlock)
                                        for b in prepared.request.messages[0].content))
                finally:
                    prepared.release()

    async def test_neutral_host_no_storage_warns_inline_and_refuses_unowned_refs(self):
        from amplifier_foundation.session.media import NeutralSessionMediaStore
        store = NeutralSessionMediaStore("neutral")
        coordinator = Coordinator()
        coordinator.session_id = "neutral"
        cap = bind_session_media(coordinator, store=store)
        raw = ChatRequest(messages=[Message(role="user", content=[ImageBlock(source={
            "type": "base64", "media_type": "image/png",
            "data": base64.b64encode(self.data).decode()})])])
        prepared = await cap.prepare_request(raw, provider_identity={}, model="text",
            request_options={}, purpose="model", execution_scope={"text_only": True})
        self.assertIn("no pixels supplied", prepared.request.model_dump_json())
        self.assertTrue(cap.validate(prepared.request, purpose="count", request_options={})["valid"])
        prepared.release()
        ref = await self.stage()
        with self.assertRaises(MediaRefusal):
            await cap.prepare_request(ChatRequest(messages=[Message(role="user", content="picture",
                metadata={MEDIA_NAMESPACE: {"refs": [ref]}})]), provider_identity={},
                model="text", request_options={}, purpose="model", execution_scope={})
        store.close()

    async def test_overlay_intermediate_copies_cleared_on_success_and_late_refusal(self):
        from amplifier_foundation.session.media_provider import MediaPreparedProvider
        cap = bind_session_media(Coordinator(), store=self.store, enabled=True)
        canonical = ChatRequest(messages=[Message(role="user", content="plain")])
        retained = []
        calls = 0
        refuse_late = False
        def overlay(request, *, purpose, request_options):
            nonlocal calls
            request.messages[0].content = [ImageBlock(source={
                "type": "base64", "media_type": "image/png",
                "data": base64.b64encode(self.data).decode()})]
            retained.append((request, request.messages[0].content[0]))
            def validate():
                nonlocal calls
                calls += 1
                return not (refuse_late and calls > 1)
            return {"request": request, "validate": validate}
        self.store.request_only_verifier = lambda *args, **kwargs: True
        class Provider:
            def get_media_request_support(self, **kwargs):
                return {"v": 1, "positions": ["user"]}
            async def complete(self, request, **kwargs):
                cap.validate(request, purpose="transport", request_options=kwargs)
                return "done"
        wrapper = MediaPreparedProvider(Provider(), cap, provider_identity={
            "provider": "p", "account": "a", "generation": "g"}, prepare_overlay=overlay)
        self.assertEqual(await wrapper.complete(canonical), "done")
        self.assertEqual(retained[-1][0].messages, [])
        self.assertEqual(retained[-1][1].source, {})
        refuse_late, calls = True, 0
        with self.assertRaises(ValueError):
            await wrapper.complete(canonical)
        self.assertEqual(retained[-1][0].messages, [])
        self.assertEqual(retained[-1][1].source, {})
        self.assertEqual(canonical.messages[0].content, "plain")

    async def test_concurrent_source_classification_has_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        ref = await self.stage()
        barrier = threading.Barrier(2)
        def classify(name):
            barrier.wait()
            try:
                self.store.classify_origin(ref, classification=name)
                return "success"
            except MediaRefusal:
                return "refused"
        with ThreadPoolExecutor(2) as pool:
            a = pool.submit(classify, "computer-use")
            b = pool.submit(classify, "ordinary-upload")
            self.assertCountEqual([a.result(), b.result()], ["success", "refused"])

    async def test_whole_original_group_retains_true_origin_and_conflicts_refuse(self):
        values = [self.data, image_bytes(color="blue")]
        group = self.store.ingest_group_sync(values, origin=self.origin, policy=None)
        refs = group["staged_refs"]
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0]["provenance"], refs[1]["provenance"])
        again = self.store.ingest_group_sync(values, origin=self.origin, policy=None)
        self.assertEqual(again["staged_refs"], refs)
        with self.assertRaises(MediaRefusal):
            self.store.ingest_group_sync(values[::-1], origin=self.origin, policy=None)
        await self.store.publish(refs, origin=self.origin, disposition="retain")
        self.store.acknowledge_checkpoint({"refs": refs})
        for ref in refs:
            with self.store.resolve_ref(ref):
                pass

    async def test_published_group_requires_exact_order_members_and_original_origin(self):
        refs = self.store.ingest_group_sync(
            [self.data, image_bytes(color="blue")], origin=self.origin, policy=None
        )["staged_refs"]
        await self.store.publish(refs, origin=self.origin, disposition="retain")
        self.assertEqual(
            self.store.validate_published_group(refs, origin=self.origin),
            {"valid": True, "ref_count": 2},
        )
        self.assertTrue(self.store.validate_published_group(
            refs, origin=refs[0]["provenance"])["valid"])
        for changed in ([], refs[:1], refs[::-1], [refs[0], refs[0]]):
            with self.subTest(changed=changed), self.assertRaises(MediaRefusal):
                self.store.validate_published_group(changed, origin=self.origin)
        for origin in (
            None, {**self.origin, "id": "not-this-call"},
            {**self.origin, "session_id": "other-session"},
            {**self.origin, "extra": "not-allowed"},
            {**refs[0]["provenance"], "created_at": "2000-01-01T00:00:00+00:00"},
        ):
            with self.subTest(origin=origin), self.assertRaises(MediaRefusal):
                self.store.validate_published_group(refs, origin=origin)
        malformed = copy.deepcopy(refs)
        malformed[0]["v"] = True
        with self.assertRaises(MediaRefusal):
            self.store.validate_published_group(malformed, origin=self.origin)
        with self.assertRaises(MediaRefusal):
            self.store.validate_published_group(tuple(refs), origin=self.origin)
        with self.assertRaises(MediaRefusal):
            self.store.validate_published_group(
                [refs[0]] * (self.policy.max_images + 1), origin=self.origin)
        other_origin = {**self.origin, "id": "another-published-call"}
        other = await self.stage(origin=other_origin, value=image_bytes(color="green"))
        await self.store.publish([other], origin=other_origin, disposition="retain")
        # Every member is genuinely owned and published, but not this group.
        with self.assertRaises(MediaRefusal):
            self.store.validate_published_group([refs[0], other], origin=self.origin)

    async def test_published_group_uses_final_selection_not_original_staging_set(self):
        refs = self.store.ingest_group_sync(
            [self.data, image_bytes(color="blue")], origin=self.origin, policy=None
        )["staged_refs"]
        with self.assertRaises(MediaRefusal):
            self.store.validate_published_group(refs, origin=self.origin)
        await self.store.publish(refs[1:], origin=self.origin, disposition="retain")
        self.assertEqual(self.store.validate_published_group(
            refs[1:], origin=self.origin), {"valid": True, "ref_count": 1})
        with self.assertRaises(MediaRefusal):
            self.store.validate_published_group(refs, origin=self.origin)
        other_origin = {**self.origin, "id": "dropped-call"}
        other = await self.stage(origin=other_origin)
        await self.store.publish([other], origin=other_origin, disposition="drop")
        with self.assertRaises(MediaRefusal):
            self.store.validate_published_group([other], origin=other_origin)
        with self.assertRaises(MediaRefusal):
            self.store.validate_published_group([], origin=other_origin)

    async def test_published_group_inspection_never_writes_or_reads_pixels(self):
        refs = self.store.ingest_group_sync(
            [self.data, image_bytes(color="blue")], origin=self.origin, policy=None
        )["staged_refs"]
        await self.store.publish(refs, origin=self.origin, disposition="retain")
        before = {str(p.relative_to(self.root)): p.read_bytes()
                  for p in self.root.rglob("*") if p.is_file()}
        guard_calls = []
        def guard(ref, *, purpose):
            guard_calls.append((ref, purpose))
            return True
        observer = SessionMediaStore(
            self.root, "session1", policy=self.policy, read_only=True,
            source_read_verifier=guard,
        )
        try:
            with (
                patch.object(observer, "_write", side_effect=AssertionError("no writes")),
                patch.object(observer, "_open_verified", side_effect=AssertionError("no pixels")),
                patch.object(observer, "_origin", side_effect=AssertionError("no new origin")),
            ):
                self.assertEqual(observer.validate_published_group(
                    refs, origin=self.origin), {"valid": True, "ref_count": 2})
            self.assertEqual(len(guard_calls), 4)
            self.assertTrue(all(purpose == "transfer" for _, purpose in guard_calls))
            observer.source_read_verifier = lambda *args, **kwargs: False
            with self.assertRaises(MediaRefusal):
                observer.validate_published_group(refs, origin=self.origin)
        finally:
            observer.close()
        after = {str(p.relative_to(self.root)): p.read_bytes()
                 for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        # Publication inspection is not canonical acceptance or model delivery.
        with self.assertRaises(MediaRefusal):
            self.store.resolve_ref(refs[0])

    async def test_published_group_rejects_late_source_or_publication_change(self):
        refs = self.store.ingest_group_sync(
            [self.data, image_bytes(color="blue")], origin=self.origin, policy=None
        )["staged_refs"]
        await self.store.publish(refs, origin=self.origin, disposition="retain")
        calls = 0
        def late_denial(ref, *, purpose):
            nonlocal calls
            calls += 1
            return calls <= len(refs)
        observer = SessionMediaStore(
            self.root, "session1", policy=self.policy, read_only=True,
            source_read_verifier=late_denial,
        )
        try:
            with self.assertRaises(MediaRefusal):
                observer.validate_published_group(refs, origin=self.origin)
            origin_dir = self.store._dirs["origins"]
            origin_file = next(p for p in (self.root / "origins").iterdir()
                               if not p.name.startswith("."))
            calls = 0
            def change_final_publication(ref, *, purpose):
                nonlocal calls
                calls += 1
                if calls == 1:
                    saved = json.loads(origin_file.read_bytes())
                    saved["publication"]["refs"] = refs[::-1]
                    self.store._write(origin_dir, origin_file.name, saved)
                return True
            observer.source_read_verifier = change_final_publication
            with self.assertRaises(MediaRefusal):
                observer.validate_published_group(refs, origin=self.origin)
        finally:
            observer.close()

    async def test_import_final_owner_expiry_preserves_metadata_without_source_retry(self):
        from datetime import datetime as RealDatetime, timedelta, timezone
        cutoff = RealDatetime(2030, 1, 1, tzinfo=timezone.utc)
        now = cutoff - timedelta(seconds=1)
        class Clock(RealDatetime):
            @classmethod
            def now(cls, tz=None):
                return now
        origin = {**self.origin, "created_at": (cutoff - timedelta(hours=1)).isoformat(),
                  "expires_at": cutoff.isoformat()}
        original = await self.stage(origin=origin)
        # Mirrors an admitted host precheck/decode followed by irreversible
        # source release. The import must never request a second source copy.
        source_requests = 0
        def decode_and_release():
            nonlocal source_requests
            source_requests += 1
            return io.BytesIO(self.data)
        decoded = decode_and_release()
        verifier_calls = []
        def authorize(ref, *, receipt, target_session_id):
            nonlocal now
            verifier_calls.append((ref, receipt, target_session_id))
            now = cutoff  # Cross TTL at the actual final owner boundary.
            return True
        target = SessionMediaStore(Path(self.directory.name) / "expiry-race", "target",
                                   policy=self.policy, checkpoint_verifier=lambda r: r["refs"])
        try:
            with (
                patch("amplifier_foundation.session.media.datetime", Clock),
                patch("amplifier_foundation.session.media_projection.datetime", Clock),
                patch.object(decoded, "read", side_effect=AssertionError("expired source must not be read")),
                patch.object(target, "_stage", side_effect=AssertionError("no new pixels")),
                patch.object(target, "_publish_adopted", side_effect=AssertionError("no pixel publication")),
            ):
                accepted = target.import_verified(original, decoded, receipt={"signed": True},
                                                   verifier=authorize)
                self.assertEqual(accepted, {**original, "session_id": "target"})
                self.assertEqual(source_requests, 1)
                self.assertEqual(len(verifier_calls), 1)
                self.assertEqual(list((target.root / "assets").iterdir()), [])
                self.assertEqual(list((target.root / "staging").iterdir()), [])
                target.acknowledge_checkpoint({"refs": [accepted]})
                with self.assertRaises(MediaRefusal) as cm:
                    target.resolve_ref(accepted)
                self.assertEqual(cm.exception.code, "expired")
                cap = self._prepared_expired_import_cap(target)
                prepared = await cap.prepare_request(
                    ChatRequest(messages=[Message(role="user", content="picture",
                        metadata={MEDIA_NAMESPACE: {"refs": [accepted]}})]),
                    provider_identity={"provider": "p", "account": "a", "generation": "g",
                                       "media_support": {"v": 1, "positions": ["user"]}},
                    model="vision", request_options={}, purpose="model", execution_scope={})
                try:
                    self.assertIn("session image expired", prepared.request.model_dump_json())
                    self.assertTrue(cap.validate(prepared.request, purpose="transport",
                                                 request_options={})["valid"])
                finally:
                    prepared.release()
        finally:
            decoded.close()
            target.close()

    @staticmethod
    def _prepared_expired_import_cap(target):
        coordinator = Coordinator()
        coordinator.session_id = target.session_id
        return bind_session_media(coordinator, store=target, enabled=True)

    async def test_import_expiry_crossing_during_stage_or_publish_is_terminal_metadata(self):
        from datetime import datetime as RealDatetime, timedelta, timezone
        cutoff = RealDatetime(2030, 1, 1, tzinfo=timezone.utc)
        now = cutoff - timedelta(seconds=1)
        class Clock(RealDatetime):
            @classmethod
            def now(cls, tz=None):
                return now
        original = await self.stage(origin={
            **self.origin, "created_at": (cutoff - timedelta(hours=1)).isoformat(),
            "expires_at": cutoff.isoformat(),
        })
        for phase in ("stage", "publish"):
            with self.subTest(phase=phase):
                now = cutoff - timedelta(seconds=1)
                target = SessionMediaStore(Path(self.directory.name) / ("race-" + phase), "target",
                    policy=self.policy, checkpoint_verifier=lambda r: r["refs"])
                stream = io.BytesIO(self.data)
                method = "_stage" if phase == "stage" else "_publish_adopted"
                operation = getattr(target, method)
                calls = []
                def cross_expiry(*args, **kwargs):
                    nonlocal now
                    calls.append(phase)
                    result = operation(*args, **kwargs)
                    now = cutoff
                    return result
                try:
                    with (
                        patch("amplifier_foundation.session.media.datetime", Clock),
                        patch.object(target, method, side_effect=cross_expiry),
                    ):
                        accepted = target.import_verified(original, stream, receipt={"signed": True},
                            verifier=lambda *args, **kwargs: True)
                        self.assertEqual(calls, [phase])
                        self.assertEqual(accepted, {**original, "session_id": "target"})
                        records = [r for r in target.journal() if "checkpoint" in r]
                        self.assertEqual(len(records), 1)
                        self.assertTrue(records[0]["metadata_only"])
                        self.assertEqual(records[0]["state"], "published")
                        self.assertEqual(len(list((target.root / "assets").iterdir())),
                                         0 if phase == "stage" else 1)
                        target.acknowledge_checkpoint({"refs": [accepted]})
                        with self.assertRaises(MediaRefusal) as cm:
                            target.resolve_ref(accepted)
                        self.assertEqual(cm.exception.code, "expired")
                finally:
                    stream.close()
                    target.close()

    async def test_expired_import_still_requires_valid_ref_and_real_authorization(self):
        original = await self.stage(origin={
            **self.origin, "created_at": "2020-01-01T00:00:00+00:00",
            "expires_at": "2020-01-01T00:01:00+00:00",
        })
        target = SessionMediaStore(Path(self.directory.name) / "unauthorized-expired", "target",
                                   policy=self.policy)
        try:
            with self.assertRaises(MediaRefusal):
                target.import_verified({**original, "v": True}, io.BytesIO(self.data),
                    receipt={}, verifier=lambda *args, **kwargs: True)
            with self.assertRaises(MediaRefusal):
                target.import_verified(original, io.BytesIO(self.data),
                    receipt={}, verifier=lambda *args, **kwargs: False)
            self.assertEqual(list(target.journal()), [])
        finally:
            target.close()


if __name__ == "__main__":
    unittest.main()