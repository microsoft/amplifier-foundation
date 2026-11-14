"""Structural provider tests: no paid calls, image services, or real assets."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import unittest
from types import MappingProxyType

from amplifier_core import ChatRequest
from amplifier_core import Message
from amplifier_core.message_models import ImageBlock, TextBlock, ThinkingBlock

from amplifier_foundation.session.media_provider import MediaPreparedProvider


def request(text="hello"):
    return ChatRequest(messages=[Message(role="user", content=text)])


class Prepared:
    def __init__(self, capability, canonical, binding):
        self.capability = capability
        self.canonical = canonical
        self.original = canonical.model_dump(mode="json")
        self.request = canonical.model_copy(deep=True)
        self.projected = self.request.model_dump(mode="json")
        self.binding = copy.deepcopy(binding)
        self.released = False
        self.release_calls = 0
        self.projection_digest = "synthetic-private-projection"
        self.validations = 0

    def validate(self, *, request_options=None):
        self.validations += 1
        if (
            self.released
            or not self.capability.assets_valid
            or self.canonical.model_dump(mode="json") != self.original
            or self.request.model_dump(mode="json") != self.projected
            or (request_options or {}) != self.binding["request_options"]
        ):
            raise ValueError("Synthetic media refusal")
        return {"valid": True}

    def release(self):
        self.release_calls += 1
        self.released = True


class Capability:
    def __init__(self):
        self.prepared = []
        self.assets_valid = True
        self.witnesses = []
        self.during_prepare = None

    async def prepare_request(self, canonical, **binding):
        prepared = Prepared(self, canonical, binding)
        self.prepared.append(prepared)
        if self.during_prepare:
            await self.during_prepare()
        return prepared

    def validate(self, projected, *, purpose, request_options=None):
        prepared = next(
            (p for p in self.prepared if p.request is projected), None
        )
        if prepared is None:
            raise ValueError("Unknown projection")
        self.witnesses.append((purpose, projected))
        return prepared.validate(request_options=request_options)


class FallbackCapability(Capability):
    """Synthetic projector; actual Foundation projection is qualified separately."""

    async def prepare_request(self, canonical, **binding):
        prepared = await super().prepare_request(canonical, **binding)
        for message in prepared.request.messages:
            refs = (message.metadata or {}).get("experimental.media.v1", {}).get("refs", [])
            if isinstance(message.content, list):
                message.content = [
                    TextBlock(
                        text="[Image unavailable: inline image is not admitted as session media; no pixels supplied]",
                        visibility=block.visibility,
                    ) if isinstance(block, ImageBlock) else
                    TextBlock(
                        text="[Image unavailable: provider or execution scope does not support this image position; no pixels supplied]",
                        visibility=block.visibility,
                    ) if isinstance(block, TextBlock) and any(
                        block.text == f"[Session image {ref['sha256']}]" for ref in refs
                    ) else block
                    for block in message.content
                ]
        prepared.projected = prepared.request.model_dump(mode="json")
        prepared.projection_digest = hashlib.sha256(
            json.dumps(prepared.projected, sort_keys=True).encode()
        ).hexdigest()
        return prepared


class PlainProvider:
    name = "synthetic"
    config = {"model": "fixture"}
    default_model = "fixture"

    def __init__(self, capability):
        self.capability = capability
        self.sent = []
        self.budgeted = []
        self.compacted = []
        self.refitted = []
        self.on_complete = None
        self.on_budget = None
        self.on_refit = None
        self.on_compact = None

    def get_info(self):
        return {"id": self.name}

    async def list_models(self):
        return ["fixture"]

    def parse_tool_calls(self, response):
        return response

    async def complete(self, projected, **kwargs):
        self.capability.validate(
            projected,
            purpose="transport",
            request_options={**(kwargs.pop("request_options", None) or {}), **kwargs},
        )
        self.sent.append(projected)
        if self.on_complete:
            return await self.on_complete(projected)
        return "complete"


class Provider(PlainProvider):
    def get_media_request_support(self, *, model, request_options=None):
        return {"v": 1, "positions": ["user", "tool"]}

    def request_budget(self, projected, *, context_estimate, request_options=None):
        self.capability.validate(
            projected, purpose="count", request_options=request_options
        )
        self.budgeted.append(projected)
        if self.on_budget:
            return self.on_budget(projected)
        return {"estimated_input_tokens": context_estimate}

    async def refit_request(
        self, projected, *, error, context_estimate, request_options=None
    ):
        self.capability.validate(
            projected, purpose="count", request_options=request_options
        )
        self.refitted.append(projected)
        if self.on_refit:
            return await self.on_refit(projected)
        return {"estimated_input_tokens": context_estimate}

    async def stream(self, projected, **kwargs):
        self.capability.validate(
            projected, purpose="transport", request_options=kwargs
        )
        self.sent.append(projected)
        yield "first"
        if self.on_complete:
            await self.on_complete(projected)
        yield "second"

    async def compact_context(self, projected, **kwargs):
        self.capability.validate(
            projected, purpose="transport", request_options=kwargs
        )
        self.compacted.append(projected)
        if self.on_compact:
            return await self.on_compact(projected)
        return {"compaction": "text-only"}


class MediaProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.capability = Capability()
        self.provider = Provider(self.capability)
        self.identity = {
            "provider": "synthetic",
            "account": "fixture",
            "generation": 1,
        }
        self.wrapper = self.wrap(self.provider)
        self.canonical = request()

    def wrap(self, provider, **kwargs):
        return MediaPreparedProvider(
            provider, self.capability, provider_identity=self.identity, **kwargs
        )

    async def budget(self, canonical=None, **options):
        return await self.wrapper.request_budget(
            canonical or self.canonical, context_estimate=3, request_options=options
        )

    async def test_count_complete_share_exact_private_object_and_canonical_is_unchanged(self):
        original = self.canonical.model_dump(mode="json")
        await self.budget(temperature=0.2)
        projected = self.capability.prepared[0].request
        self.assertIsNot(projected, self.canonical)
        self.assertEqual(await self.wrapper.complete(self.canonical, temperature=0.2), "complete")
        self.assertIs(self.provider.budgeted[0], self.provider.sent[0])
        self.assertEqual(self.canonical.model_dump(mode="json"), original)
        self.assertTrue(self.capability.prepared[0].released)
        self.assertEqual(len(self.capability.prepared), 1)

    async def test_nested_and_flat_options_normalize_identically(self):
        await self.budget(temperature=0.1)
        await self.wrapper.complete(
            self.canonical, request_options={"temperature": 0.8}, temperature=0.1
        )
        self.assertEqual(len(self.capability.prepared), 1)

    async def test_repeated_count_reuses_and_revalidates(self):
        await self.budget()
        initial = self.capability.prepared[0].validations
        await self.budget()
        self.assertGreater(self.capability.prepared[0].validations, initial)
        self.assertEqual(len(self.capability.prepared), 1)
        self.wrapper.close()

    async def test_count_replacement_even_equal_object_releases_previous(self):
        await self.budget()
        await self.budget(self.canonical.model_copy(deep=True))
        self.assertEqual(len(self.capability.prepared), 2)
        self.assertTrue(self.capability.prepared[0].released)
        self.wrapper.close()

    async def test_count_mutation_reprepares_but_dispatch_mutation_refuses(self):
        await self.budget()
        self.canonical.messages[0].content = "replacement before refit"
        await self.budget()
        self.assertTrue(self.capability.prepared[0].released)
        self.canonical.messages[0].content = "mutation after count"
        with self.assertRaises(ValueError):
            await self.wrapper.complete(self.canonical)
        self.assertEqual(len(self.capability.prepared), 2)
        self.assertTrue(self.capability.prepared[-1].released)
        self.assertFalse(self.provider.sent)

    async def test_dispatch_replacement_even_equal_object_refuses(self):
        await self.budget()
        with self.assertRaises(ValueError):
            await self.wrapper.complete(self.canonical.model_copy(deep=True))
        self.assertEqual(len(self.capability.prepared), 1)
        self.assertTrue(self.capability.prepared[0].released)

    async def test_changed_options_refuse_without_repreparing(self):
        await self.budget(temperature=0.1)
        with self.assertRaises(ValueError):
            await self.wrapper.complete(self.canonical, temperature=0.2)
        self.assertEqual(len(self.capability.prepared), 1)
        self.assertTrue(self.capability.prepared[0].released)

    async def test_changed_identity_or_model_refuses(self):
        for change in ("identity", "model"):
            with self.subTest(change=change):
                await self.budget()
                if change == "identity":
                    self.identity["generation"] += 1
                else:
                    self.provider.default_model = "another-model"
                with self.assertRaises(ValueError):
                    await self.wrapper.complete(self.canonical)
                self.assertTrue(self.capability.prepared[-1].released)
        self.assertFalse(self.provider.sent)

    async def test_actual_media_support_overrides_claimed_identity(self):
        self.identity["media_support"] = {"v": 1, "positions": ["forged"]}
        await self.budget()
        prepared = self.capability.prepared[-1]
        self.assertEqual(
            prepared.binding["provider_identity"]["media_support"],
            self.provider.get_media_request_support(model="fixture"),
        )
        self.wrapper.close()

    async def test_unknown_support_is_empty(self):
        wrapper = self.wrap(PlainProvider(self.capability))
        await wrapper.complete(self.canonical)
        self.assertEqual(
            self.capability.prepared[-1].binding["provider_identity"]["media_support"],
            {"v": 1, "positions": []},
        )

    async def test_model_precedence_and_explicit_scope(self):
        scope = {"kind": "nested", "text_only": True}
        wrapper = self.wrap(self.provider, model="host", execution_scope=scope)
        self.canonical.model = "request-model"
        await wrapper.complete(self.canonical, model="override")
        binding = self.capability.prepared[-1].binding
        self.assertEqual(binding["model"], "override")
        self.assertEqual(binding["execution_scope"], scope)

    async def test_expiry_asset_and_projection_changes_refuse_without_rehydration(self):
        for change in ("assets", "projection"):
            with self.subTest(change=change):
                self.capability.assets_valid = True
                await self.budget()
                prepared = self.capability.prepared[-1]
                if change == "assets":
                    self.capability.assets_valid = False
                else:
                    prepared.request.messages[0].content = "tampered"
                with self.assertRaises(ValueError):
                    await self.wrapper.complete(self.canonical)
                self.assertTrue(prepared.released)
        self.assertFalse(self.provider.sent)

    async def test_complete_errors_and_cancellation_release_without_retry(self):
        for error in (RuntimeError("fixture"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                async def fail(_):
                    raise error

                self.provider.on_complete = fail
                await self.budget()
                with self.assertRaises(type(error)):
                    await self.wrapper.complete(self.canonical)
                self.assertTrue(self.capability.prepared[-1].released)
        self.assertEqual(len(self.provider.sent), 2)

    async def test_budget_error_and_cancellation_release(self):
        for error in (RuntimeError("fixture"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                def fail(_):
                    raise error

                self.provider.on_budget = fail
                with self.assertRaises(type(error)):
                    await self.budget()
                self.assertTrue(self.capability.prepared[-1].released)

    async def test_coroutine_budget_is_awaited_and_cancellation_releases(self):
        async def fail(_):
            await asyncio.sleep(0)
            raise asyncio.CancelledError

        self.provider.on_budget = fail
        with self.assertRaises(asyncio.CancelledError):
            await self.budget()
        self.assertTrue(self.capability.prepared[0].released)

    async def test_refit_fresh_projection_preserves_decision_and_does_not_retry(self):
        await self.budget()
        old = self.capability.prepared[0]
        decision = await self.wrapper.refit_request(
            self.canonical, error=ValueError("local refusal"), context_estimate=2
        )
        self.assertEqual(decision, {"estimated_input_tokens": 2})
        self.assertTrue(old.released)
        self.assertFalse(self.provider.sent)
        await self.wrapper.complete(self.canonical)
        self.assertIs(self.provider.refitted[0], self.provider.sent[0])

    async def test_refit_decline_error_and_cancel_release(self):
        for outcome in (None, RuntimeError("fixture"), asyncio.CancelledError()):
            with self.subTest(outcome=type(outcome).__name__):
                async def refit(_):
                    if isinstance(outcome, BaseException):
                        raise outcome
                    return outcome

                self.provider.on_refit = refit
                if isinstance(outcome, BaseException):
                    with self.assertRaises(type(outcome)):
                        await self.wrapper.refit_request(
                            self.canonical, error=ValueError(), context_estimate=2
                        )
                else:
                    self.assertIsNone(await self.wrapper.refit_request(
                        self.canonical, error=ValueError(), context_estimate=2
                    ))
                self.assertTrue(self.capability.prepared[-1].released)
        self.assertFalse(self.provider.sent)

    async def test_compaction_has_private_candidate_and_preserves_fitted_one(self):
        await self.budget()
        fitted = self.capability.prepared[0]
        self.assertEqual(
            await self.wrapper.compact_context(request("compact")),
            {"compaction": "text-only"},
        )
        self.assertIsNot(fitted.request, self.provider.compacted[0])
        self.assertFalse(fitted.released)
        self.assertTrue(self.capability.prepared[1].released)
        await self.wrapper.complete(self.canonical)
        self.assertIs(self.provider.sent[0], fitted.request)

    async def test_compaction_error_and_cancel_release_own_projection(self):
        for error in (RuntimeError("fixture"), asyncio.CancelledError()):
            async def fail(_):
                raise error

            self.provider.on_compact = fail
            with self.assertRaises(type(error)):
                await self.wrapper.compact_context(self.canonical)
            self.assertTrue(self.capability.prepared[-1].released)

    async def test_stream_reuses_counted_projection_and_exhaustion_releases(self):
        await self.budget()
        self.assertEqual(
            [item async for item in self.wrapper.stream(self.canonical)],
            ["first", "second"],
        )
        self.assertIs(self.provider.budgeted[0], self.provider.sent[0])
        self.assertTrue(self.capability.prepared[0].released)

    async def test_stream_close_before_iteration_releases_counted_projection(self):
        await self.budget()
        stream = self.wrapper.stream(self.canonical)
        await stream.aclose()
        self.assertTrue(self.capability.prepared[0].released)
        self.assertFalse(self.provider.sent)
        await stream.aclose()

    async def test_stream_early_close_releases_and_closes_underlying_generator(self):
        await self.budget()
        stream = self.wrapper.stream(self.canonical)
        self.assertEqual(await anext(stream), "first")
        await stream.aclose()
        self.assertTrue(self.capability.prepared[0].released)
        with self.assertRaises(StopAsyncIteration):
            await anext(stream)

    async def test_stream_error_and_cancellation_release(self):
        for error in (RuntimeError("fixture"), asyncio.CancelledError()):
            async def fail(_):
                raise error

            self.provider.on_complete = fail
            stream = self.wrapper.stream(self.canonical)
            await anext(stream)
            with self.assertRaises(type(error)):
                await anext(stream)
            self.assertTrue(self.capability.prepared[-1].released)

    async def test_coroutine_returning_stream_is_supported(self):
        original = self.provider.stream

        async def stream(*args, **kwargs):
            return original(*args, **kwargs)

        self.provider.stream = stream
        self.assertEqual(
            [item async for item in self.wrapper.stream(self.canonical)],
            ["first", "second"],
        )
        self.assertTrue(self.capability.prepared[-1].released)

    async def test_session_close_releases_and_prevents_new_operations(self):
        await self.budget()
        await self.wrapper.aclose()
        self.assertTrue(self.capability.prepared[0].released)
        with self.assertRaises(ValueError):
            await self.wrapper.complete(self.canonical)
        with self.assertRaises(ValueError):
            self.wrapper.stream(self.canonical)
        self.wrapper.close()

    async def test_session_close_closes_live_stream(self):
        stream = self.wrapper.stream(self.canonical)
        await anext(stream)
        await self.wrapper.aclose()
        self.assertTrue(self.capability.prepared[0].released)
        with self.assertRaises(StopAsyncIteration):
            await anext(stream)

    async def test_close_during_prepare_releases_late_result(self):
        async def close():
            self.wrapper.close()

        self.capability.during_prepare = close
        with self.assertRaises(ValueError):
            await self.wrapper.complete(self.canonical)
        self.assertTrue(self.capability.prepared[0].released)
        self.assertFalse(self.provider.sent)

    async def test_concurrent_operation_cannot_replace_stream_candidate(self):
        stream = self.wrapper.stream(self.canonical)
        await anext(stream)
        with self.assertRaises(ValueError):
            await self.budget(request("another candidate"))
        self.assertFalse(self.capability.prepared[0].released)
        await stream.aclose()

    async def test_per_retry_witness_remains_provider_owned(self):
        async def retry(projected):
            self.capability.assets_valid = False
            self.capability.validate(projected, purpose="transport", request_options={})
            self.fail("stale retry must not proceed")

        self.provider.on_complete = retry
        with self.assertRaises(ValueError):
            await self.wrapper.complete(self.canonical)
        self.assertEqual(len(self.provider.sent), 1)
        self.assertEqual(len(self.capability.witnesses), 2)
        self.assertTrue(self.capability.prepared[0].released)

    async def test_legacy_budget_signature_is_not_given_new_keyword(self):
        def legacy(projected, *, context_estimate):
            return {"estimated_input_tokens": context_estimate}

        self.provider.request_budget = legacy
        self.assertEqual(await self.budget(), {"estimated_input_tokens": 3})
        await self.wrapper.complete(self.canonical)

    async def test_explicit_provider_properties_and_methods_are_preserved(self):
        self.assertEqual(self.wrapper.name, self.provider.name)
        self.assertIs(self.wrapper.config, self.provider.config)
        self.assertEqual(self.wrapper.get_info(), self.provider.get_info())
        self.assertEqual(await self.wrapper.list_models(), ["fixture"])
        self.assertEqual(self.wrapper.parse_tool_calls(["tool"]), ["tool"])

    async def test_optional_methods_are_absent_not_spoofed(self):
        plain = self.wrap(PlainProvider(self.capability))
        for name in ("request_budget", "refit_request", "stream", "compact_context"):
            self.assertFalse(hasattr(plain, name), name)
            self.assertTrue(callable(getattr(self.wrapper, name, None)), name)
        self.assertFalse(hasattr(plain, "get_media_request_support"))
        self.assertNotIn("__getattr__", MediaPreparedProvider.__dict__)

    async def test_noncallable_optional_attributes_are_not_exposed(self):
        provider = PlainProvider(self.capability)
        for name in ("request_budget", "refit_request", "stream", "compact_context"):
            setattr(provider, name, False)
        wrapper = self.wrap(provider)
        for name in ("request_budget", "refit_request", "stream", "compact_context"):
            self.assertFalse(hasattr(wrapper, name), name)

    async def test_stream_close_during_prepare_cannot_dispatch_late_candidate(self):
        entered, unblock = asyncio.Event(), asyncio.Event()

        async def prepare():
            entered.set()
            # Exercise a capability that completes despite cancellation.
            try:
                await unblock.wait()
            except asyncio.CancelledError:
                return

        self.capability.during_prepare = prepare
        stream = self.wrapper.stream(self.canonical)
        advancing = asyncio.create_task(anext(stream))
        await entered.wait()
        await stream.aclose()
        with self.assertRaises((asyncio.CancelledError, StopAsyncIteration)):
            await advancing
        self.assertTrue(self.capability.prepared[0].released)
        self.assertFalse(self.provider.sent)
        self.assertFalse(self.wrapper._active)

    async def test_overlapping_first_stream_reads_refuse_without_replacing_projection(self):
        entered, unblock = asyncio.Event(), asyncio.Event()

        async def prepare():
            entered.set()
            await unblock.wait()

        self.capability.during_prepare = prepare
        stream = self.wrapper.stream(self.canonical)
        advancing = asyncio.create_task(anext(stream))
        await entered.wait()
        with self.assertRaises(ValueError):
            await anext(stream)
        self.assertEqual(len(self.capability.prepared), 1)
        unblock.set()
        self.assertEqual(await advancing, "first")
        await stream.aclose()
        self.assertTrue(self.capability.prepared[0].released)
        self.assertFalse(self.wrapper._active)

    async def test_close_while_stream_read_pending_cancels_owned_read_and_releases(self):
        entered = asyncio.Event()

        async def wait(_):
            entered.set()
            await asyncio.Event().wait()

        self.provider.on_complete = wait
        stream = self.wrapper.stream(self.canonical)
        self.assertEqual(await anext(stream), "first")
        advancing = asyncio.create_task(anext(stream))
        await entered.wait()
        await stream.aclose()
        with self.assertRaises(asyncio.CancelledError):
            await advancing
        self.assertTrue(self.capability.prepared[0].released)
        self.assertFalse(self.wrapper._active)

    @staticmethod
    def measured():
        return {
            "estimated_input_tokens": 5,
            "input_limit_tokens": 100,
            "context_token_budget": 10,
            "measurement": {
                "kind": "provider_count",
                "source": "fixture.count_tokens",
                "input_tokens": 4,
            },
        }

    async def test_measured_budget_adds_metadata_only_witness_without_mutating_result(self):
        original = self.measured()
        self.provider.on_budget = lambda _: original
        result = await self.budget(temperature=0.3)
        self.assertNotIn("experimental.media.v1", original)
        witness = self.wrapper.media_budget_witness(
            self.canonical, request_options={"temperature": 0.3}
        )
        self.assertEqual(result["experimental.media.v1"], witness)
        self.assertEqual(
            witness,
            {"v": 1, "projection_digest": "synthetic-private-projection", "validated": True},
        )
        await self.wrapper.complete(self.canonical, temperature=0.3)
        with self.assertRaises(ValueError):
            self.wrapper.media_budget_witness(
                self.canonical, request_options={"temperature": 0.3}
            )

    async def test_immutable_budget_uses_structural_witness_without_rewriting(self):
        original = MappingProxyType(self.measured())
        self.provider.on_budget = lambda _: original
        self.assertIs(await self.budget(), original)
        self.assertTrue(self.wrapper.media_budget_witness(self.canonical)["validated"])
        self.wrapper.close()

    async def test_unavailable_estimated_or_malformed_budget_has_no_witness(self):
        for result in (
            None,
            {"estimated_input_tokens": 1},
            {**self.measured(), "measurement": {"kind": "estimate"}},
            {**self.measured(), "input_limit_tokens": True},
            {**self.measured(), "context_token_budget": -1},
            {**self.measured(), "measurement": {
                "kind": "provider_count", "source": "fixture", "input_tokens": True
            }},
        ):
            with self.subTest(result=result):
                self.provider.on_budget = lambda _: result
                observed = await self.budget()
                if observed is not None:
                    self.assertNotIn("experimental.media.v1", observed)
                with self.assertRaises(ValueError):
                    self.wrapper.media_budget_witness(self.canonical)
        self.wrapper.close()

    async def test_witness_revalidates_assets_options_and_exact_canonical_object(self):
        self.provider.on_budget = lambda _: self.measured()
        await self.budget()
        with self.assertRaises(ValueError):
            self.wrapper.media_budget_witness(self.canonical.model_copy(deep=True))
        self.capability.assets_valid = False
        with self.assertRaises(ValueError):
            self.wrapper.media_budget_witness(self.canonical)
        self.assertTrue(self.capability.prepared[-1].released)
        self.capability.assets_valid = True
        await self.budget()
        with self.assertRaises(ValueError):
            self.wrapper.media_budget_witness(
                self.canonical, request_options={"temperature": 0.5}
            )
        self.assertTrue(self.capability.prepared[-1].released)


class TextFallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.capability = FallbackCapability()
        self.provider = PlainProvider(self.capability)
        self.identity = {"provider": "fixture", "account": "account", "generation": 1}
        self.wrapper = MediaPreparedProvider(
            self.provider, self.capability, provider_identity=self.identity
        )
        self.canonical = ChatRequest(messages=[Message(
            role="tool", name="camera", tool_call_id="actual-call",
            content=[
                TextBlock(text="before"),
                ImageBlock(source={"type": "base64", "data": "not-real-pixels"}),
                TextBlock(text="after"),
            ],
        )])

    async def asyncTearDown(self):
        await self.wrapper.aclose()

    async def witness(self, **kwargs):
        return await self.wrapper.media_text_fallback_witness(self.canonical, **kwargs)

    async def test_plain_provider_freezes_warning_view_without_numeric_budget_or_provider_call(self):
        original = self.canonical.model_dump(mode="json")
        view = await self.witness(request_options={"extended_thinking": True})
        self.assertEqual(set(view), {"v", "validated", "projection_digest", "messages"})
        self.assertEqual(len(view["projection_digest"]), 64)
        self.assertTrue(view["validated"])
        self.assertEqual(
            [block["text"] for block in view["messages"][0]["content"]],
            ["before",
             "[Image unavailable: inline image is not admitted as session media; no pixels supplied]",
             "after"],
        )
        self.assertEqual(view["messages"][0]["role"], "tool")
        self.assertEqual(view["messages"][0]["tool_call_id"], "actual-call")
        self.assertEqual(view["messages"][0]["name"], "camera")
        self.assertEqual(self.canonical.model_dump(mode="json"), original)
        self.assertFalse(self.provider.sent)
        self.assertFalse(hasattr(self.wrapper, "request_budget"))
        with self.assertRaises(ValueError):
            self.wrapper.media_budget_witness(
                self.canonical, request_options={"extended_thinking": True}
            )
        await self.wrapper.complete(self.canonical, extended_thinking=True)
        self.assertIs(self.provider.sent[0], self.capability.prepared[0].request)
        self.assertEqual(len(self.capability.prepared), 1)
        self.assertTrue(self.capability.prepared[0].released)

    async def test_unavailable_budget_reuses_exact_private_warning_projection(self):
        provider = Provider(self.capability)
        provider.on_budget = lambda _: None
        wrapper = MediaPreparedProvider(
            provider, self.capability, provider_identity=self.identity
        )
        try:
            self.assertIsNone(await wrapper.request_budget(
                self.canonical, context_estimate=2,
                request_options={"extended_thinking": True},
            ))
            view = await wrapper.media_text_fallback_witness(
                self.canonical, request_options={"extended_thinking": True}
            )
            self.assertTrue(view["validated"])
            await wrapper.complete(self.canonical, extended_thinking=True)
            self.assertIs(provider.budgeted[0], provider.sent[0])
            self.assertEqual(len(self.capability.prepared), 1)
        finally:
            await wrapper.aclose()

    async def test_detached_view_does_not_export_refs_or_share_private_buffers(self):
        ref = {"v": 1, "sha256": "a" * 64}
        self.canonical.messages[0].metadata = {"experimental.media.v1": {"refs": [ref]}}
        self.canonical.messages[0].content[1] = TextBlock(text=f"[Session image {ref['sha256']}]")
        original = self.canonical.model_dump(mode="json")
        view = await self.witness()
        self.assertNotIn("metadata", view["messages"][0])
        self.assertNotIn("experimental.media.v1", json.dumps(view))
        prepared = self.capability.prepared[0]
        snapshot = prepared.request.model_dump(mode="json")
        view["messages"][0]["content"][0]["text"] = "caller-mutated"
        view["messages"].clear()
        self.assertEqual(prepared.request.model_dump(mode="json"), snapshot)
        self.assertEqual(self.canonical.model_dump(mode="json"), original)
        await self.wrapper.complete(self.canonical)

    async def test_exact_fallback_options_are_required_at_dispatch(self):
        await self.witness(request_options={"extended_thinking": True})
        with self.assertRaises(ValueError):
            await self.wrapper.complete(self.canonical)
        self.assertEqual(len(self.capability.prepared), 1)
        self.assertTrue(self.capability.prepared[0].released)
        self.assertFalse(self.provider.sent)

    async def test_repeated_fallback_revalidates_same_candidate_without_measured_claim(self):
        first = await self.witness()
        validations = self.capability.prepared[0].validations
        self.assertEqual(await self.witness(), first)
        self.assertEqual(len(self.capability.prepared), 1)
        self.assertGreater(self.capability.prepared[0].validations, validations)
        with self.assertRaises(ValueError):
            self.wrapper.media_budget_witness(self.canonical)

    async def test_fallback_does_not_make_hydrated_projection_locally_countable(self):
        self.capability = Capability()
        self.wrapper = MediaPreparedProvider(
            self.provider, self.capability, provider_identity=self.identity
        )
        with self.assertRaises(ValueError):
            await self.witness()
        self.assertTrue(self.capability.prepared[0].released)
        self.assertFalse(self.provider.sent)

    async def test_nontext_blocks_and_unknown_metadata_refuse_instead_of_undercounting(self):
        mutations = (
            lambda r: r.messages[0].content.append(ThinkingBlock(thinking="private reasoning")),
            lambda r: setattr(r.messages[0], "metadata", {"reasoning": "opaque state"}),
            lambda r: setattr(r, "metadata", {"provider_state": "opaque state"}),
            lambda r: setattr(r.messages[0], "provider_extra", "opaque state"),
            lambda r: setattr(r.messages[0].content[0], "provider_extra", "opaque state"),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                canonical = self.canonical.model_copy(deep=True)
                mutate(canonical)
                with self.assertRaises(ValueError):
                    await self.wrapper.media_text_fallback_witness(canonical)
                self.assertTrue(self.capability.prepared[-1].released)
        self.assertFalse(self.provider.sent)

    async def test_plain_literal_warning_is_not_a_media_fallback(self):
        canonical = request("[Image unavailable: session image expired; no pixels supplied]")
        with self.assertRaises(ValueError):
            await self.wrapper.media_text_fallback_witness(canonical)
        self.assertFalse(self.capability.prepared)

    async def test_live_identity_model_or_canonical_mutation_refuses_dispatch(self):
        for change in ("identity", "model", "canonical"):
            with self.subTest(change=change):
                await self.witness()
                if change == "identity":
                    self.identity["generation"] += 1
                elif change == "model":
                    self.provider.default_model = "changed"
                else:
                    self.canonical.messages[0].content[0].text = "changed"
                with self.assertRaises(ValueError):
                    await self.wrapper.complete(self.canonical)
                self.assertTrue(self.capability.prepared[-1].released)
        self.assertFalse(self.provider.sent)

    async def test_fallback_error_cancellation_and_session_close_release(self):
        for outcome in ("assets", "cancel", "closed"):
            with self.subTest(outcome=outcome):
                self.capability.assets_valid = True
                self.capability.during_prepare = None
                if outcome == "assets":
                    self.capability.assets_valid = False
                elif outcome == "cancel":
                    # The capability owns cleanup before it returns an association.
                    async def prepare(canonical, **binding):
                        raise asyncio.CancelledError
                    original = self.capability.prepare_request
                    self.capability.prepare_request = prepare
                    try:
                        with self.assertRaises(asyncio.CancelledError):
                            await self.witness()
                    finally:
                        self.capability.prepare_request = original
                    continue
                else:
                    self.wrapper.close()
                with self.assertRaises(ValueError):
                    await self.witness()
                if outcome == "assets":
                    self.assertTrue(self.capability.prepared[-1].released)


class StreamRequestSupportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.capability = Capability()
        self.identity = {"provider": "fixture", "account": "fixture", "generation": 1}

    def wrap(self, provider):
        return MediaPreparedProvider(
            provider, self.capability, provider_identity=self.identity
        )

    async def test_no_default_canonical_tools_promise_or_get_info_rewrite(self):
        provider = Provider(self.capability)
        wrapper = self.wrap(provider)
        self.assertFalse(hasattr(wrapper, "get_stream_request_support"))
        self.assertEqual(wrapper.get_info(), provider.get_info())
        await wrapper.aclose()

    async def test_only_actual_stream_and_support_callables_expose_fact(self):
        for stream in (None, False):
            provider = PlainProvider(self.capability)
            provider.stream = stream
            provider.get_stream_request_support = lambda **_: {
                "v": 1, "tools": "chat_request"
            }
            wrapper = self.wrap(provider)
            self.assertFalse(hasattr(wrapper, "get_stream_request_support"))
            await wrapper.aclose()
        provider = Provider(self.capability)
        provider.get_stream_request_support = False
        wrapper = self.wrap(provider)
        self.assertFalse(hasattr(wrapper, "get_stream_request_support"))
        await wrapper.aclose()

    async def test_selected_model_options_and_actual_provider_result_forward_unchanged(self):
        provider = Provider(self.capability)
        seen, fact = [], {"v": 1, "tools": "chat_request"}

        def support(*, model, request_options=None):
            seen.append((model, request_options))
            return fact

        provider.get_stream_request_support = support
        wrapper = self.wrap(provider)
        options = {"extended_thinking": True}
        self.assertIs(
            wrapper.get_stream_request_support(model="fixture", request_options=options),
            fact,
        )
        self.assertEqual(seen, [("fixture", options)])
        self.assertIs(seen[0][1], options)
        self.assertFalse(self.capability.prepared)
        self.assertFalse(provider.sent)
        await wrapper.aclose()

    async def test_forwarder_does_not_turn_decline_malformed_or_exception_into_claim(self):
        provider = Provider(self.capability)
        result = None

        def support(*, model, request_options=None):
            if isinstance(result, BaseException):
                raise result
            return result

        provider.get_stream_request_support = support
        wrapper = self.wrap(provider)
        for result in (None, False, {"v": True, "tools": "chat_request"},
                       {"v": 2, "tools": "chat_request"}, {"v": 1, "tools": "runtime"}):
            self.assertIs(wrapper.get_stream_request_support(model="fixture"), result)
        result = ValueError("provider-owned support refusal")
        with self.assertRaises(ValueError):
            wrapper.get_stream_request_support(model="fixture")
        await wrapper.aclose()

    async def test_advertisement_never_strips_actual_dispatch_tools_or_weakens_fit(self):
        provider = Provider(self.capability)
        provider.get_stream_request_support = lambda **_: {
            "v": 1, "tools": "chat_request"
        }
        wrapper = self.wrap(provider)
        canonical = request()
        await wrapper.request_budget(canonical, context_estimate=1, request_options={})
        with self.assertRaises(ValueError):
            async for _ in wrapper.stream(canonical, tools=[]):
                self.fail("uncounted duplicate tools must not dispatch")
        self.assertFalse(provider.sent)
        self.assertTrue(self.capability.prepared[0].released)
        await wrapper.request_budget(canonical, context_estimate=1, request_options={})
        self.assertEqual(
            [chunk async for chunk in wrapper.stream(canonical)],
            ["first", "second"],
        )
        self.assertIs(provider.budgeted[-1], provider.sent[0])
        self.assertTrue(self.capability.prepared[-1].released)
        await wrapper.aclose()


if __name__ == "__main__":
    unittest.main()