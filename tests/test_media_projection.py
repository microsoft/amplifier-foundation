"""Synthetic stdlib tests for the private request-scoped media witness."""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import sys
import types
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from amplifier_core.message_models import ChatRequest, ImageBlock, Message, TextBlock, ToolCallBlock

from amplifier_foundation.session.media_projection import prepare_media_request

NAMESPACE = "experimental.media.v1"
PIXELS = b"synthetic immutable asset; the store owns image decoding"
SHA = hashlib.sha256(PIXELS).hexdigest()


class Refusal(ValueError):
    def __init__(self, message, *, code="refused"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Policy:
    max_bytes: int = 1000
    retention: str = "durable"


def make_ref(*, call="call-1", expires=None):
    return {
        "v": 1, "session_id": "session-1", "sha256": SHA,
        "mime": "image/png", "bytes": len(PIXELS), "width": 1, "height": 1,
        "provenance": {
            "kind": "tool", "session_id": "session-1", "id": call,
            "created_at": "2025-01-01T00:00:00+00:00", "expires_at": expires,
        },
    }


class FakeStore:
    def __init__(self, refs):
        self.session_id = "session-1"
        self.policy = Policy()
        self.epoch = 0
        self.refs = copy.deepcopy(refs)
        self.data = PIXELS
        self.checkpoint = True
        self.prepared = []
        self.handles = []
        self.resolved = []
        self.model_use_allowed = True
        self.policy_refusal_code = "refused"
        self.on_resolve = None

    def validate_ref(self, ref, *, committed=True, check_expiry=True):
        if ref not in self.refs or type(ref) is not dict:
            raise Refusal("unowned or malformed reference")
        if not committed:
            raise AssertionError("uncommitted reference access")
        if check_expiry and not self.model_use_allowed:
            raise Refusal("host policy revoked model use", code=self.policy_refusal_code)
        return copy.deepcopy(ref)

    def verify_checkpoint(self, ref):
        if not self.checkpoint:
            raise Refusal("checkpoint changed")

    def resolve_ref(self, ref, *, committed=True):
        self.resolved.append(copy.deepcopy(ref))
        if self.on_resolve is not None:
            self.on_resolve()
        if self.data is None:
            raise FileNotFoundError("missing")
        stream = io.BytesIO(self.data)
        self.handles.append(stream)
        return stream

    def register_prepared(self, prepared):
        self.prepared.append(prepared)

    def unregister_prepared(self, prepared):
        self.prepared.remove(prepared)


def request_for(refs, *, role="tool", content=None):
    if content is None:
        content = [TextBlock(text="before"), TextBlock(text=f"[Session image {SHA}]"), TextBlock(text="after")]
    return ChatRequest(
        messages=[Message(
            role=role, content=content, name="owner", tool_call_id="call-1",
            metadata={NAMESPACE: {"refs": copy.deepcopy(refs)}, "owner": {"nested": [1, "kept"]}},
        )],
        metadata={"owner": {"trace": "call-1"}},
        conversation_id="conversation-1", model="model-1",
    )


class ProjectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Keep tests independent of the store worker's not-yet-integrated module.
        self.media = types.ModuleType("amplifier_foundation.session.media")
        self.media.MediaRefusal = Refusal
        self.patcher = patch.dict(sys.modules, {self.media.__name__: self.media})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.identity = {
            "provider": "synthetic", "account": "private-account", "generation": 1,
            "media_support": {"v": 1, "positions": ["tool", "user"]},
        }
        self.options = {"max_output_tokens": 8, "owner": ["unchanged"]}
        self.scope = {"nested": False, "text_only": False}

    async def prepare(self, store, request, **kwargs):
        args = dict(
            provider_identity=self.identity, model="model-1", request_options=self.options,
            purpose="count", execution_scope=self.scope,
        )
        args.update(kwargs)
        return await prepare_media_request(store, request, **args)

    async def test_indexed_support_hydrates_only_exact_message_not_same_digest_history(self):
        ref = make_ref()
        store = FakeStore([ref])
        canonical = request_for([ref], role="user")
        canonical.messages.append(copy.deepcopy(canonical.messages[0]))
        self.identity["media_support"] = {
            "v": 2, "positions": [{"message_index": 1, "role": "user"}],
            "mime_types": ["image/png"],
        }
        prepared = await self.prepare(store, canonical)
        self.assertIn("no pixels", prepared.request.messages[0].content[1].text)
        self.assertIsInstance(prepared.request.messages[1].content[1], ImageBlock)
        self.assertEqual(len(store.resolved), 1)
        prepared.validate()
        self.assertEqual(len(store.resolved), 2)  # Only selected image on validation too.
        self.assertEqual(canonical.messages[0], canonical.messages[1])
        prepared.release()

    async def test_empty_or_non_image_selected_position_never_searches_backwards(self):
        ref = make_ref()
        for positions in ([], [{"message_index": 1, "role": "user"}]):
            store = FakeStore([ref])
            canonical = request_for([ref], role="user")
            canonical.messages.append(Message(role="user", content="latest without image"))
            self.identity["media_support"] = {
                "v": 2, "positions": positions, "mime_types": ["image/png"],
            }
            prepared = await self.prepare(store, canonical)
            self.assertIn("no pixels", prepared.request.messages[0].content[1].text)
            self.assertEqual(prepared.request.messages[1].content, "latest without image")
            prepared.validate()
            self.assertEqual(store.resolved, [])
            prepared.release()

    async def test_indexed_inline_gate_precedes_request_only_verifier(self):
        store = FakeStore([])
        verified = []
        store.request_only_verifier = lambda *args, **kwargs: verified.append(kwargs) or True
        inline = ImageBlock(source={"type": "base64", "media_type": "image/png", "data": "cGl4ZWxz"})
        canonical = ChatRequest(messages=[
            Message(role="user", content=[copy.deepcopy(inline)]),
            Message(role="tool", content=[copy.deepcopy(inline)]),
        ])
        self.identity["media_support"] = {
            "v": 2, "positions": [{"message_index": 1, "role": "tool"}],
            "mime_types": ["image/png"],
        }
        prepared = await self.prepare(store, canonical)
        self.assertEqual([call["message_index"] for call in verified], [1])
        self.assertIn("no pixels", prepared.request.messages[0].content[0].text)
        self.assertIsInstance(prepared.request.messages[1].content[0], ImageBlock)
        prepared.validate()
        self.assertEqual([call["message_index"] for call in verified], [1, 1])
        prepared.release()

    async def test_invalid_indexed_role_or_index_refuses_before_verifier_or_asset_reads(self):
        ref = make_ref()
        store = FakeStore([ref])
        store.request_only_verifier = lambda *args, **kwargs: self.fail("verifier called")
        canonical = request_for([ref])
        for position in (
            {"message_index": True, "role": "tool"},
            {"message_index": -1, "role": "tool"},
            {"message_index": 1, "role": "tool"},
            {"message_index": 0, "role": "user"},
            {"message_index": 0, "role": "tool", "unknown": True},
        ):
            self.identity["media_support"] = {
                "v": 2, "positions": [position], "mime_types": ["image/png"],
            }
            with self.assertRaises(Refusal):
                await self.prepare(store, canonical)
            self.assertEqual(store.resolved, [])
            self.assertEqual(store.prepared, [])

    async def test_indexed_mime_gate_applies_before_any_pixels(self):
        ref = make_ref()
        store = FakeStore([ref])
        self.identity["media_support"] = {
            "v": 2, "positions": [{"message_index": 0, "role": "tool"}],
            "mime_types": ["image/jpeg"],
        }
        prepared = await self.prepare(store, request_for([ref]))
        prepared.validate()
        self.assertIn("no pixels", prepared.request.messages[0].content[1].text)
        self.assertEqual(store.resolved, [])
        prepared.release()

    async def test_deepcopy_preserves_role_call_metadata_and_exact_position(self):
        ref = make_ref()
        store = FakeStore([ref])
        canonical = request_for([ref])
        before = canonical.model_dump()
        prepared = await self.prepare(store, canonical)
        message = prepared.request.messages[0]
        self.assertEqual(canonical.model_dump(), before)
        self.assertIsNot(prepared.request, canonical)
        self.assertEqual(message.role, "tool")
        self.assertEqual(message.tool_call_id, "call-1")
        self.assertEqual(message.name, "owner")
        self.assertEqual(message.metadata, canonical.messages[0].metadata)
        self.assertIsNot(message.metadata, canonical.messages[0].metadata)
        self.assertEqual([block.type for block in message.content], ["text", "image", "text"])
        self.assertEqual(message.content[1].source, {
            "type": "base64", "media_type": "image/png", "data": base64.b64encode(PIXELS).decode(),
        })
        self.assertEqual(prepared.validate(request_options=copy.deepcopy(self.options)), {"valid": True})
        self.assertEqual(len(prepared.projection_digest), 64)
        self.assertTrue(all(handle.closed for handle in store.handles))

    async def test_ordered_same_digest_observations_keep_separate_expiry(self):
        expired = make_ref(call="old", expires="2020-01-01T00:00:00Z")
        live = make_ref(call="new")
        store = FakeStore([expired, live])
        request = request_for([expired, live], content=[
            TextBlock(text=f"[Session image {SHA}]"), TextBlock(text="between"),
            TextBlock(text=f"[Session image {SHA}]"),
        ])
        prepared = await self.prepare(store, request)
        blocks = prepared.request.messages[0].content
        self.assertIn("expired", blocks[0].text)
        self.assertEqual(blocks[1].text, "between")
        self.assertIsInstance(blocks[2], ImageBlock)
        self.assertEqual(prepared.validate(), {"valid": True})

    async def test_marker_matching_is_exact_and_requires_carrier(self):
        ref = make_ref()
        store = FakeStore([ref])
        request = request_for([ref], content=[
            TextBlock(text=f"prefix [Session image {SHA}]"),
            TextBlock(text=f"[Session image {SHA}] suffix"),
        ])
        request.messages.append(Message(role="user", content=f"[Session image {SHA}]"))
        prepared = await self.prepare(store, request)
        self.assertEqual(len(prepared.request.messages[0].content), 3)
        self.assertIsInstance(prepared.request.messages[0].content[-1], ImageBlock)
        self.assertEqual(prepared.request.messages[1].content, f"[Session image {SHA}]")
        prepared = await self.prepare(
            store, request_for([ref], content=f"[Session image {SHA}]")
        )
        self.assertEqual(
            prepared.request.messages[0].content[0].text, f"[Session image {SHA}]"
        )
        self.assertIsInstance(prepared.request.messages[0].content[1], ImageBlock)

    async def test_no_marker_appends_to_same_role_not_synthetic_user(self):
        ref = make_ref()
        store = FakeStore([ref])
        for role in ("tool", "assistant", "system"):
            with self.subTest(role=role):
                prepared = await self.prepare(store, request_for([ref], role=role, content="useful text"))
                self.assertEqual(len(prepared.request.messages), 1)
                self.assertEqual(prepared.request.messages[0].role, role)
                blocks = prepared.request.messages[0].content
                self.assertEqual(blocks[0].text, "useful text")
                if role == "tool":
                    self.assertIsInstance(blocks[1], ImageBlock)
                else:
                    self.assertIn("no pixels supplied", blocks[1].text)
                prepared.release()

    async def test_unqualified_roles_and_scopes_freeze_warning_before_count(self):
        ref = make_ref()
        cases = [
            {"provider_identity": {"provider": "unknown"}},
            {"provider_identity": {"media_support": {"v": True, "positions": ["tool"]}}},
            {"provider_identity": {"media_support": {"v": 1, "positions": "tool"}}},
            {"execution_scope": {"nested": True}},
            {"execution_scope": {"text_only": True}},
            {"execution_scope": "nested"},
            {"execution_scope": "text_only"},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                prepared = await self.prepare(FakeStore([ref]), request_for([ref]), **kwargs)
                blocks = prepared.request.messages[0].content
                self.assertEqual([block.type for block in blocks], ["text"] * 3)
                self.assertIn("no pixels supplied", blocks[1].text)
                self.assertEqual(prepared.validate(), {"valid": True})

    async def test_missing_bytes_freeze_warning_but_availability_change_invalidates(self):
        ref = make_ref()
        store = FakeStore([ref])
        store.data = None
        prepared = await self.prepare(store, request_for([ref]))
        self.assertIn("missing", prepared.request.messages[0].content[1].text)
        self.assertEqual(prepared.validate(), {"valid": True})
        store.data = PIXELS
        with self.assertRaises(Refusal):
            prepared.validate()
        self.assertIn("missing", prepared.request.messages[0].content[1].text)

    async def test_owned_expired_ref_warns_but_forged_expired_ref_refuses(self):
        ref = make_ref(expires="2020-01-01T00:00:00+00:00")
        prepared = await self.prepare(FakeStore([ref]), request_for([ref]))
        self.assertIn("expired", prepared.request.messages[0].content[1].text)
        self.assertEqual(prepared.validate(), {"valid": True})
        forged = copy.deepcopy(ref)
        forged["provenance"]["id"] = "forged"
        with self.assertRaises(Refusal):
            await self.prepare(FakeStore([ref]), request_for([forged]))

    async def test_tampered_assets_never_become_missing_warning(self):
        ref = make_ref()
        store = FakeStore([ref])
        store.data = b"tampered"
        with self.assertRaises(Refusal):
            await self.prepare(store, request_for([ref]))
        self.assertEqual(store.prepared, [])
        self.assertTrue(all(handle.closed for handle in store.handles))

    async def test_asset_loss_or_tamper_after_count_refuses_without_replacement(self):
        ref = make_ref()
        for replacement in (None, b"different bytes"):
            with self.subTest(replacement=replacement):
                store = FakeStore([ref])
                prepared = await self.prepare(store, request_for([ref]))
                store.data = replacement
                with self.assertRaises(Refusal):
                    prepared.validate()
                self.assertIsInstance(prepared.request.messages[0].content[1], ImageBlock)

    async def test_expiry_crossing_after_count_refuses_without_replacement(self):
        future = datetime.now(timezone.utc) + timedelta(days=1)
        ref = make_ref(expires=future.isoformat())
        prepared = await self.prepare(FakeStore([ref]), request_for([ref]))
        with patch("amplifier_foundation.session.media_projection.datetime") as clock:
            clock.fromisoformat.side_effect = datetime.fromisoformat
            clock.now.return_value = future + timedelta(seconds=1)
            with self.assertRaises(Refusal):
                prepared.validate()
        self.assertIsInstance(prepared.request.messages[0].content[1], ImageBlock)

    async def test_canonical_or_projection_changes_fail(self):
        for target in ("canonical", "projected"):
            with self.subTest(target=target):
                ref = make_ref()
                canonical = request_for([ref])
                prepared = await self.prepare(FakeStore([ref]), canonical)
                request = canonical if target == "canonical" else prepared.request
                request.metadata["owner"]["trace"] = "changed"
                with self.assertRaises(Refusal):
                    prepared.validate()

    async def test_provider_options_scope_policy_epoch_and_checkpoint_changes_fail(self):
        ref = make_ref()
        for mutation in ("provider", "options", "scope", "policy", "epoch", "checkpoint", "session"):
            with self.subTest(mutation=mutation):
                self.setUp_bindings()
                store = FakeStore([ref])
                prepared = await self.prepare(store, request_for([ref]))
                if mutation == "provider":
                    self.identity["generation"] = 2
                elif mutation == "options":
                    self.options["owner"].append("changed")
                elif mutation == "scope":
                    self.scope["nested"] = True
                elif mutation == "policy":
                    store.policy = Policy(max_bytes=500)
                elif mutation == "epoch":
                    store.epoch += 1
                elif mutation == "checkpoint":
                    store.checkpoint = False
                else:
                    store.session_id = "other"
                with self.assertRaises(Refusal):
                    prepared.validate()

    def setUp_bindings(self):
        self.identity = {
            "provider": "synthetic", "account": "private-account", "generation": 1,
            "media_support": {"v": 1, "positions": ["tool", "user"]},
        }
        self.options = {"max_output_tokens": 8, "owner": ["unchanged"]}
        self.scope = {"nested": False, "text_only": False}

    async def test_effective_options_must_match(self):
        ref = make_ref()
        prepared = await self.prepare(FakeStore([ref]), request_for([ref]))
        for options in (None, {}, {"max_output_tokens": 9}, {"stream": True}):
            with self.subTest(options=options), self.assertRaises(Refusal):
                prepared.validate(request_options=options)
        self.assertEqual(prepared.validate(request_options=copy.deepcopy(self.options)), {"valid": True})
        prepared = await self.prepare(FakeStore([ref]), request_for([ref]), request_options=None)
        self.assertEqual(prepared.validate(request_options=None), {"valid": True})

    async def test_release_wipes_private_blocks_unregisters_and_is_idempotent(self):
        ref = make_ref()
        store = FakeStore([ref])
        canonical = request_for([ref])
        before = canonical.model_dump()
        prepared = await self.prepare(store, canonical)
        block = prepared.request.messages[0].content[1]
        encoded = block.source["data"]
        self.assertNotIn(encoded, repr(prepared))
        self.assertNotIn("private-account", repr(prepared))
        prepared.request.messages[0].content.pop(1)
        prepared.request.messages.append(None)
        prepared.release()
        prepared.release()
        self.assertTrue(prepared.released)
        self.assertEqual(prepared.request.messages, [])
        self.assertEqual(block.source, {})
        self.assertEqual(store.prepared, [])
        self.assertEqual(canonical.model_dump(), before)
        with self.assertRaises(Refusal):
            prepared.validate()

    async def test_text_only_request_still_gets_fenced_witness(self):
        request = ChatRequest(messages=[Message(role="user", content="hello")])
        store = FakeStore([])
        prepared = await self.prepare(store, request)
        self.assertEqual(prepared.validate(), {"valid": True})
        self.assertEqual(store.prepared, [prepared])
        request.messages.append(Message(role="assistant", content="changed"))
        with self.assertRaises(Refusal):
            prepared.validate()

    async def test_malformed_carriers_fail_not_text_fallback(self):
        ref = make_ref()
        for carrier in ({}, {"refs": "bad"}, {"refs": [ref], "extra": 1}, None):
            with self.subTest(carrier=carrier):
                request = request_for([ref])
                request.messages[0].metadata[NAMESPACE] = carrier
                with self.assertRaises(Refusal):
                    await self.prepare(FakeStore([ref]), request)

    async def test_non_json_owner_metadata_refuses_without_repr_or_copy_hooks(self):
        class Secret:
            def __repr__(self):
                raise AssertionError("secret repr was invoked")

            def __deepcopy__(self, memo):
                raise AssertionError("secret copy hook was invoked")

        for value in (Secret(), b"secret", {1: "not a string key"}, float("nan"), (1, 2)):
            with self.subTest(kind=type(value).__name__):
                request = ChatRequest(messages=[Message(role="user", content="hello")], metadata={"owner": value})
                with self.assertRaises(Refusal):
                    await self.prepare(FakeStore([]), request)
        cycle = []
        cycle.append(cycle)
        request = ChatRequest(messages=[Message(role="user", content="hello")], metadata={"owner": cycle})
        with self.assertRaises(Refusal):
            await self.prepare(FakeStore([]), request)

    async def test_tool_call_identity_and_text_visibility_are_preserved(self):
        ref = make_ref()
        request = request_for([ref])
        request.messages[0].content[1].visibility = "internal"
        request.messages.insert(0, Message(role="assistant", content=[
            ToolCallBlock(id="call-1", name="capture", input={"owner": [1]}),
        ], metadata={"owner": "assistant"}))
        prepared = await self.prepare(FakeStore([ref]), request)
        self.assertEqual(prepared.request.messages[0], request.messages[0])
        self.assertEqual(prepared.request.messages[1].content[1].visibility, "internal")

    async def test_host_model_use_policy_is_checked_before_fit_and_each_attempt(self):
        ref = make_ref()
        store = FakeStore([ref])
        store.model_use_allowed = False
        with self.assertRaises(Refusal):
            await self.prepare(store, request_for([ref]))
        store.model_use_allowed = True
        prepared = await self.prepare(store, request_for([ref]))
        store.model_use_allowed = False
        with self.assertRaises(Refusal):
            prepared.validate()

    async def test_host_callback_cannot_mutate_binding_or_request_during_validation(self):
        ref = make_ref()
        for kind in ("binding", "canonical", "projection"):
            with self.subTest(kind=kind):
                store = FakeStore([ref])
                canonical = request_for([ref])
                prepared = await self.prepare(store, canonical)
                if kind == "binding":
                    store.on_resolve = lambda: setattr(store, "epoch", 10)
                elif kind == "canonical":
                    store.on_resolve = lambda: canonical.messages.clear()
                else:
                    store.on_resolve = lambda: prepared.request.messages.clear()
                with self.assertRaises(Refusal):
                    prepared.validate()

    async def test_scope_policy_or_canonical_change_during_preparation_refuses(self):
        ref = make_ref()
        for kind in ("binding", "canonical"):
            with self.subTest(kind=kind):
                store = FakeStore([ref])
                canonical = request_for([ref])
                if kind == "binding":
                    store.on_resolve = lambda: setattr(store, "epoch", 10)
                else:
                    store.on_resolve = lambda: canonical.messages.clear()
                with self.assertRaises(Refusal):
                    await self.prepare(store, canonical)
                self.assertEqual(store.prepared, [])

    async def test_raw_inline_images_are_replaced_without_ingest_in_all_scopes(self):
        for scope in (None, "nested", {"text_only": True}):
            with self.subTest(scope=scope):
                raw = ImageBlock(
                    source={"type": "base64", "media_type": "image/png", "data": "private-pixels"},
                    visibility="internal",
                )
                canonical = ChatRequest(messages=[Message(role="user", content=[
                    TextBlock(text="before"), raw, TextBlock(text="after"),
                ])])
                before = canonical.model_dump()
                store = FakeStore([])
                prepared = await self.prepare(store, canonical, execution_scope=scope)
                blocks = prepared.request.messages[0].content
                self.assertEqual([block.type for block in blocks], ["text"] * 3)
                self.assertEqual(blocks[1].text, (
                    "[Image unavailable: inline image is not admitted as session media; no pixels supplied]"
                ))
                self.assertEqual(blocks[1].visibility, "internal")
                self.assertEqual(canonical.model_dump(), before)
                self.assertEqual(store.resolved, [])
                self.assertEqual(prepared.validate(), {"valid": True})
                prepared.release()
                self.assertEqual(raw.source["data"], "private-pixels")

    async def test_source_policy_warning_is_frozen_and_never_silently_hydrates(self):
        ref = make_ref()
        store = FakeStore([ref])
        store.model_use_allowed = False
        store.policy_refusal_code = "source_policy"
        prepared = await self.prepare(store, request_for([ref]))
        self.assertIn("source owner", prepared.request.messages[0].content[1].text)
        self.assertEqual(store.resolved, [])
        self.assertEqual(prepared.validate(), {"valid": True})
        store.model_use_allowed = True
        with self.assertRaises(Refusal):
            prepared.validate()
        prepared = await self.prepare(store, request_for([ref]))
        store.model_use_allowed = False
        with self.assertRaises(Refusal):
            prepared.validate()

    async def test_unregistration_failure_leaves_pixels_empty_and_allows_cleanup_retry(self):
        ref = make_ref()
        store = FakeStore([ref])
        prepared = await self.prepare(store, request_for([ref]))
        block = prepared.request.messages[0].content[1]
        unregister = store.unregister_prepared
        store.unregister_prepared = lambda _: (_ for _ in ()).throw(RuntimeError("cleanup"))
        with self.assertRaises(RuntimeError):
            prepared.release()
        self.assertTrue(prepared.released)
        self.assertEqual(prepared.request.messages, [])
        self.assertEqual(block.source, {})
        self.assertIsNone(prepared._canonical)
        store.unregister_prepared = unregister
        prepared.release()
        prepared.release()
        self.assertEqual(store.prepared, [])


if __name__ == "__main__":
    unittest.main()
