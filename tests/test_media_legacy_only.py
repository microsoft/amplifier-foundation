"""Fresh-process same-session proof with all storage-programme imports refused."""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


@pytest.mark.skipif(os.name != "posix", reason="legacy image durability is POSIX-only")
@pytest.mark.parametrize("image_format", ["PNG", "JPEG"])
def test_image_checkpoint_reload_count_send_without_storage_programme(tmp_path, image_format):
    script = textwrap.dedent("""
        import asyncio
        import base64
        from contextlib import contextmanager
        import hashlib
        import importlib.abc
        import io
        import json
        from pathlib import Path
        import sys

        forbidden = {
            "transcript_v2", "transcript_writer", "transcript_source",
            "transcript_lifetime", "transcript_encoding", "versioned_checkpoint",
            "media_activation", "media_conversion", "media_conversion_owner",
            "_media_conversion_parser", "_media_cohort",
        }
        attempts = []
        class NoStorageProgramme(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if (fullname.startswith("amplifier_foundation.session.")
                        and fullname.rsplit(".", 1)[-1] in forbidden):
                    attempts.append(fullname)
                    raise AssertionError("out-of-scope storage import")
        sys.meta_path.insert(0, NoStorageProgramme())

        from PIL import Image
        from amplifier_core.message_models import ChatRequest, ImageBlock, Message
        from amplifier_foundation import sanitize_message
        from amplifier_foundation.session.checkpoint import (
            CheckpointRefused, acknowledge_legacy_checkpoint,
            verified_checkpoint_refs, verified_media_checkpoint_refs,
        )
        from amplifier_foundation.session.history import SessionHistoryStore
        from amplifier_foundation.session.media import (
            MEDIA_NAMESPACE, MediaRefusal, SessionMediaCapability, SessionMediaStore,
        )
        from amplifier_foundation.session.media_hook_bridge import install_media_hook_bridge
        from amplifier_foundation.session.media_provider import MediaPreparedProvider

        async def main():
            path, image_format = Path(sys.argv[1]), sys.argv[2]
            image = io.BytesIO()
            Image.new("RGB", (3, 2), "purple").save(image, format=image_format)
            original = image.getvalue()
            root = path / "media"
            store = SessionMediaStore(
                root, "same-session", checkpoint_verifier=verified_checkpoint_refs,
            )
            origin = {"kind": "tool", "session_id": "same-session", "id": "actual-call"}
            ref = store.ingest_sync(original, source_format="image-bytes",
                                    origin=origin, policy=None)["value"]
            assert ref["sha256"] == hashlib.sha256(original).hexdigest()
            assert ref["provenance"]["expires_at"] is None
            store.publish_sync([ref], origin=origin, disposition="retain")
            rows = [{
                "role": "tool", "tool_call_id": "actual-call",
                "content": [{"type": "text", "text": "[Session image " + ref["sha256"] + "]"}],
                "metadata": {MEDIA_NAMESPACE: {"refs": [ref]}},
            }]
            history = SessionHistoryStore(path)
            history.save(rows, {"session_id": "same-session"}, sanitizer=sanitize_message)
            rows = history.load_messages()
            before = (path / "transcript.jsonl").read_bytes()
            receipt = acknowledge_legacy_checkpoint(
                path, session_id="same-session", expected_messages=rows,
            )
            assert list(verified_media_checkpoint_refs(receipt)) == [ref]
            assert store.acknowledge_checkpoint(receipt) == {
                "state": "committed", "durability": "legacy-checkpoint-backed", "ref_count": 1,
            }
            with store.resolve_ref(ref) as pixels:
                assert pixels.read() == original
            store.close()

            # A new process/session owner cannot reconstruct authority from the journal.
            store = SessionMediaStore(
                root, "same-session", checkpoint_verifier=verified_checkpoint_refs,
            )
            with pytest_raises(MediaRefusal):
                store.resolve_ref(ref)
            receipt = acknowledge_legacy_checkpoint(
                path, session_id="same-session", expected_messages=history.load_messages(),
            )
            store.acknowledge_checkpoint(receipt)
            cap = SessionMediaCapability(store, enabled=True)
            canonical = ChatRequest(model="fixture", messages=[Message(**row) for row in rows])
            canonical_before = canonical.model_dump_json()
            active = []
            @contextmanager
            def scope():
                active.append(True)
                try:
                    yield lambda: active == [True]
                finally:
                    active.clear()
            class Provider:
                counted = None
                sent = None
                image_source = None
                def get_media_request_support(self, **kwargs):
                    return {"v": 2, "positions": [{"message_index": 0, "role": "tool"}],
                            "mime_types": ["image/png", "image/jpeg"]}
                def request_budget(self, private, *, request_options=None):
                    assert active == [True]
                    cap.validate(private, purpose="count", request_options=request_options)
                    if self.counted is not None:
                        assert private is self.counted
                    self.counted = private
                    block = next(item for item in private.messages[0].content
                                 if isinstance(item, ImageBlock))
                    self.image_source = block.source
                    assert base64.b64decode(block.source["data"]) == original
                    assert canonical.model_dump_json() == canonical_before
                    return {"estimated_input_tokens": 10, "input_limit_tokens": 100,
                            "context_token_budget": 100}
                async def complete(self, private, **options):
                    assert active == [True] and private is self.counted
                    cap.validate(private, purpose="transport", request_options=options)
                    self.sent = private
                    return "synthetic-send"
            provider = Provider()
            wrapper = MediaPreparedProvider(
                provider, cap, provider_identity={
                    "provider": "fixture", "account": "synthetic", "generation": 1,
                }, request_scope=scope,
            )
            try:
                await wrapper.request_budget(canonical)
                await wrapper.request_budget(canonical)
                assert await wrapper.complete(canonical) == "synthetic-send"
                assert provider.sent.messages == [] and provider.image_source == {}
                assert active == []
                assert canonical.model_dump_json() == canonical_before
                assert base64.b64encode(original) not in before
                assert (path / "transcript.jsonl").read_bytes() == before
                for value in (None, {}, {"kind": "versioned-checkpoint-backed-v1"}):
                    with pytest_raises(CheckpointRefused):
                        list(verified_media_checkpoint_refs(value))
                for name in ("adopt", "adopt_sync", "import_verified", "ingest_group_sync",
                             "validate_reingest_group"):
                    assert not hasattr(store, name)
                assert attempts == []
                assert not any(name.rsplit(".", 1)[-1] in forbidden
                               for name in sys.modules
                               if name.startswith("amplifier_foundation.session."))
                print(json.dumps({"format": image_format, "bytes": len(original),
                                  "history_bytes": len(before), "imports_refused": attempts,
                                  "count_send_same_private_object": True,
                                  "reload_fresh_ack": True, "cleanup_wiped": True}))
            finally:
                await wrapper.aclose()
                store.close()

        # Keep the subprocess independent of pytest fixtures/import side effects.
        from contextlib import contextmanager
        @contextmanager
        def pytest_raises(expected):
            try:
                yield
            except expected:
                return
            raise AssertionError("Expected local refusal")
        asyncio.run(main())
    """)
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), image_format],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
        timeout=30, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert '"count_send_same_private_object": true' in completed.stdout
    assert '"imports_refused": []' in completed.stdout