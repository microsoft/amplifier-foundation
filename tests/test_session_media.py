"""Synthetic POSIX media tests; no screenshots, transport or live data."""
import asyncio
import base64
import copy
import hashlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from amplifier_core import ToolResult
from amplifier_core.message_models import ImageBlock, TextBlock
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


if __name__ == "__main__":
    unittest.main()