"""Private, request-scoped media projection at the actual provider boundary.

Providers own encoding and the per-attempt capability.validate witness, including
retries after backoff. This module never inspects vendor wire payloads or emits
hydrated requests to diagnostics.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


def _fingerprint(value: Any) -> str:
    """Fingerprint Core/JSON data without repr/logging potentially private data."""
    if callable(getattr(value, "model_dump", None)):
        value = value.model_dump(mode="json")
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Media candidate must be JSON serializable") from exc
    return hashlib.sha256(encoded).hexdigest()


async def _result(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


_ABSENT = object()


def _indexed_getter(provider: Any) -> Any:
    name = "get_media_request_support_for_request"
    declared = inspect.getattr_static(provider, name, _ABSENT)
    try:
        return getattr(provider, name)
    except AttributeError:
        if declared is not _ABSENT:
            raise ValueError("Indexed media support attribute resolution failed") from None
        return _ABSENT


def _accepts_options(method: Any) -> bool:
    try:
        parameters = inspect.signature(method).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        p.kind == inspect.Parameter.VAR_KEYWORD
        or (
            p.name == "request_options"
            and p.kind != inspect.Parameter.POSITIONAL_ONLY
        )
        for p in parameters
    )


@dataclass
class _Candidate:
    canonical: Any
    key: tuple[str, ...]
    base_key: tuple[str, ...]
    prepared: Any
    source: Any
    source_digest: str
    lifetime: Any
    overlay_validator: Any
    release_options: str
    option_defaults: tuple[Any, Any]
    fitted: bool = False
    measured: bool = False


class _RequestLifetime:
    """One synchronous host graph scope, never one scope per wrapper method."""

    def __init__(self, factory: Any) -> None:
        self._context = None
        self._validator = None
        self._closed = False
        if factory is not None:
            context = factory()
            if not callable(getattr(context, "__exit__", None)):
                raise ValueError("Media request scope requires a synchronous context manager")
            entered = context.__enter__()
            self._context = context
            try:
                self._validator = entered if callable(entered) else getattr(entered, "validate", None)
                if not callable(self._validator):
                    raise ValueError("Media request scope must yield a synchronous validator")
                self.validate()
            except BaseException:
                self.close()
                raise

    def validate(self) -> bool:
        if self._closed:
            raise ValueError("Media request graph scope changed")
        if self._validator is not None:
            valid = self._validator()
            if inspect.iscoroutine(valid):
                valid.close()
            if valid is not True:
                raise ValueError("Media request graph scope changed")
        return True

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            context, self._context = self._context, None
            self._validator = None
            if context is not None:
                context.__exit__(None, None, None)


class MediaPreparedProvider:
    """Wrap one actual provider and one session-bound media capability.

    ``provider_identity`` is a JSON mapping (or a zero-argument getter for live
    account/generation identity). Media support always comes from the actual
    provider's ``get_media_request_support(model=..., request_options=...)``.
    ``model`` may likewise be a zero-argument getter.
    Optional actual-provider ``get_media_request_support_for_request`` supplies
    strict v2 index/role/MIME facts for the frozen post-overlay/prehydration
    source; its presence is authoritative, including empty or invalid results.
    Optional ``request_scope`` is a source-bound synchronous context factory
    yielding a literal-True validator, held for the private candidate's lifetime.

    The host must call ``close``/``aclose`` on session teardown. They release this
    wrapper's buffers, not the underlying provider's resources. Concurrent use
    refuses rather than replacing an in-flight projection. Optional methods are
    assigned only when implemented by the actual provider.

    Optional synchronous ``get_stream_request_support(*, model,
    request_options=None)`` is forwarded only with an actual stream method.
    An exact ``{"v": 1, "tools": "chat_request"}`` result is the provider's
    promise that stream consumes canonical ``ChatRequest.tools`` without a
    duplicate ``tools=`` runtime-object argument for that model/options. The
    caller must validate this fact before selecting that route; absence or any
    other result makes no claim. This wrapper never creates the promise, changes
    get_info capabilities, strips kwargs or weakens count/dispatch identity.
    """

    def __init__(
        self,
        provider: Any,
        capability: Any,
        *,
        provider_identity: Any,
        model: Any = None,
        execution_scope: Any = None,
        prepare_overlay: Any = None,
        request_scope: Any = None,
    ) -> None:
        if not callable(getattr(provider, "complete", None)):
            raise TypeError("Media provider requires a callable complete")
        if not callable(getattr(capability, "prepare_request", None)):
            raise TypeError("Media capability requires a callable prepare_request")
        if request_scope is not None and not callable(request_scope):
            raise TypeError("request_scope must be a source-bound context factory")
        self._provider = provider
        self._capability = capability
        self._identity_source = provider_identity
        self._model_source = model
        self._scope = execution_scope
        self._prepare_overlay = prepare_overlay
        self._request_scope = request_scope
        self._lifetimes: list[_RequestLifetime] = []
        self._candidates: list[_Candidate] = []
        self._candidate: _Candidate | None = None
        self._active: list[Any] = []
        self._owned_overlays: dict[int, list[Any]] = {}
        self._streams: set[_MediaStream] = set()
        self._busy = False
        self._closed = False
        # No __getattr__: absence is meaningful to loop feature detection.
        for name, implementation in (
            ("request_budget", self._request_budget),
            ("refit_request", self._refit_request),
            ("stream", self._stream),
            ("compact_context", self._compact_context),
        ):
            if callable(getattr(provider, name, None)):
                setattr(self, name, implementation)
        support = getattr(provider, "get_media_request_support", None)
        if callable(support):
            self.get_media_request_support = support
        indexed_support = _indexed_getter(provider)
        if callable(indexed_support):
            self.get_media_request_support_for_request = indexed_support
        stream_support = getattr(provider, "get_stream_request_support", None)
        if callable(getattr(provider, "stream", None)) and callable(stream_support):
            self.get_stream_request_support = stream_support

    @property
    def name(self) -> Any:
        return self._provider.name

    @property
    def config(self) -> Any:
        return self._provider.config

    @property
    def default_model(self) -> Any:
        return self._provider.default_model

    def get_info(self) -> Any:
        return self._provider.get_info()

    async def list_models(self) -> Any:
        return await _result(self._provider.list_models())

    def parse_tool_calls(self, response: Any) -> Any:
        return self._provider.parse_tool_calls(response)

    def _begin(self) -> None:
        if self._closed:
            raise ValueError("Media provider is closed")
        if self._busy:
            raise ValueError("Media provider already has an active operation")
        self._busy = True

    def _release(self, candidate: _Candidate | None) -> None:
        if candidate is None:
            return
        if self._candidate is candidate:
            self._candidate = None
        self._candidates = [item for item in self._candidates if item is not candidate]
        prepared = candidate.prepared
        try:
            if any(item is prepared for item in self._active):
                self._active = [item for item in self._active if item is not prepared]
                prepared.release()
        finally:
            try:
                self._clear_overlays(self._owned_overlays.pop(id(prepared), []))
            finally:
                self._end_lifetime(candidate.lifetime)

    def _end_lifetime(self, lifetime: _RequestLifetime) -> None:
        self._lifetimes = [item for item in self._lifetimes if item is not lifetime]
        lifetime.close()

    @staticmethod
    def _release_options(options: dict[str, Any], defaults: tuple[Any, Any]) -> str:
        normalized = dict(options)
        for name, default in zip(("model", "reasoning_effort"), defaults):
            if normalized.get(name) == default:
                normalized.pop(name, None)
        return _fingerprint(normalized)

    def release_media_request(
        self, canonical: Any, *, request_options: Any = None,
    ) -> bool:
        """Abandon only this exact cached request/options association.

        This is cleanup, not a new witness: no live provider/graph/asset query is
        made, so invalidated or mutated canonical requests can still be released.
        An equal copy or mismatched options cannot release another candidate.
        """
        candidate = self._candidate
        if candidate is None or candidate.canonical is not canonical:
            return False
        options = self._options(request_options, {})
        if self._release_options(options, candidate.option_defaults) != candidate.release_options:
            return False
        if self._busy:
            raise ValueError("Cannot abandon media during an active operation")
        self._release(candidate)
        return True

    @staticmethod
    def _clear_overlays(requests: list[Any]) -> None:
        from amplifier_core.message_models import ChatRequest, ImageBlock
        for request in requests:
            if isinstance(request, ChatRequest):
                for message in request.messages:
                    if isinstance(message.content, list):
                        for block in message.content:
                            if isinstance(block, ImageBlock) and isinstance(block.source, dict):
                                block.source.clear()
                request.messages = []

    def close(self) -> None:
        """Revoke all prepared requests, including an active transport's witness."""
        self._closed = True
        error = None
        for candidate in tuple(self._candidates):
            try:
                self._release(candidate)
            except BaseException as exc:
                error = error or exc
        # Also cover a close during an awaited overlay/preparation. One broken
        # release callback must not strand a second private candidate/scope.
        for lifetime in tuple(self._lifetimes):
            try:
                self._end_lifetime(lifetime)
            except BaseException as exc:
                error = error or exc
        if error is not None:
            raise error

    async def aclose(self) -> None:
        """Revoke buffers and close any live underlying stream iterators."""
        error = None
        try:
            self.close()
        except BaseException as exc:
            error = exc
        for stream in tuple(self._streams):
            try:
                await stream.aclose()
            except BaseException as exc:
                error = error or exc
        if error is not None:
            raise error

    @staticmethod
    def _options(request_options: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
        if request_options is not None and not isinstance(request_options, Mapping):
            raise ValueError("request_options must be a mapping")
        return {**(request_options or {}), **kwargs}

    def _binding(
        self, request: Any, options: dict[str, Any], *, source: Any = None,
    ) -> tuple[tuple[str, ...], Any, Any]:
        model = self._model_source
        if callable(model):
            model = model()
        model = (
            options.get("model")
            or getattr(request, "model", None)
            or model
            or getattr(self._provider, "default_model", None)
        )
        identity = self._identity_source
        if callable(identity):
            identity = identity()
        if not isinstance(identity, Mapping):
            raise ValueError("provider_identity must be a mapping")
        identity = copy.deepcopy(dict(identity))
        # Caller identity cannot forge either old or indexed support facts.
        identity.pop("media_support", None)
        if source is not None:
            identity["media_support"] = self._support(source, model, options)
        normalized_options = dict(options)
        if normalized_options.get("model") == model:
            normalized_options.pop("model", None)
        if normalized_options.get("reasoning_effort") == getattr(request, "reasoning_effort", None):
            normalized_options.pop("reasoning_effort", None)
        key = tuple(
            _fingerprint(value)
            for value in (
                request, normalized_options, identity, model, self._scope,
                id(self._provider),
            )
        )
        return key, identity, model

    def _support(self, source: Any, model: Any, options: dict[str, Any]) -> Any:
        from .media_projection import _digest, validate_media_request_support

        getter = _indexed_getter(self._provider)
        if getter is _ABSENT:
            support = getattr(self._provider, "get_media_request_support", None)
            return (
                support(model=model, request_options=copy.deepcopy(options))
                if callable(support) else {"v": 1, "positions": []}
            )
        if not callable(getter):
            raise ValueError("Indexed media request support must be synchronous")
        # Read-only by contract, detached so a broken getter cannot edit either
        # canonical data or the frozen source used by subsequent retry checks.
        source_digest, options_digest = _digest(source), _digest(options)
        model_digest = _digest(model)
        snapshot, option_snapshot = copy.deepcopy(source), copy.deepcopy(options)
        model_snapshot = copy.deepcopy(model)
        from amplifier_core.message_models import ImageBlock

        # A broken callback may detach or replace blocks before we detect the
        # mutation. Own original source dictionaries independently of its graph.
        image_sources = [
            block.source for message in snapshot.messages
            if isinstance(message.content, list)
            for block in message.content
            if isinstance(block, ImageBlock) and type(block.source) is dict
        ]
        try:
            value = getter(request=snapshot, model=model_snapshot, request_options=option_snapshot)
            if inspect.isawaitable(value):
                if inspect.iscoroutine(value):
                    value.close()
                raise ValueError("Indexed media request support must be synchronous")
            if (
                _digest(snapshot) != source_digest
                or _digest(option_snapshot) != options_digest
                or _digest(model_snapshot) != model_digest
            ):
                raise ValueError("Indexed media support mutated its read-only input")
            return validate_media_request_support(value, source)
        finally:
            for image_source in image_sources:
                image_source.clear()
            snapshot.messages = []

    def _candidate_valid(self, candidate: _Candidate, options: dict[str, Any]) -> bool:
        from .media_projection import _digest

        candidate.lifetime.validate()
        if (
            self._closed
            or _digest(candidate.source) != candidate.source_digest
            or self._binding(candidate.canonical, options, source=candidate.source)[0] != candidate.key
            or (
                candidate.overlay_validator is not None
                and candidate.overlay_validator() is not True
            )
        ):
            raise ValueError("Prepared media candidate changed")
        candidate.lifetime.validate()
        if (
            self._closed
            or self._binding(candidate.canonical, options)[0] != candidate.base_key
            or _digest(candidate.source) != candidate.source_digest
        ):
            raise ValueError("Media binding changed during validation")
        return True

    async def _prepare(
        self,
        request: Any,
        options: dict[str, Any],
        *,
        purpose: str,
        dispatch: bool = False,
        private: bool = False,
    ) -> _Candidate:
        from .media_projection import _digest

        old = None if private else self._candidate
        if old is not None:
            try:
                old.lifetime.validate()
                if (
                    old.canonical is request
                    and self._binding(request, options)[0] == old.base_key
                ):
                    self._validate(old, options)
                    return old
            except BaseException:
                self._release(old)
                raise
            fitted = old.fitted
            self._release(old)
            if dispatch and fitted:
                raise ValueError("Fitted media candidate changed before dispatch")
        lifetime = _RequestLifetime(self._request_scope)
        self._lifetimes.append(lifetime)
        projected_source = None
        overlay_validator = None
        owned_overlays = []
        try:
            lifetime.validate()
            base_key, _, selected_model = self._binding(request, options)
            option_defaults = (
                selected_model, getattr(request, "reasoning_effort", None),
            )
            release_options = self._release_options(options, option_defaults)
            # Even without an overlay, retain a detached prehydration source.
            _digest(request)
            projected_source = copy.deepcopy(request)
            owned_overlays.append(projected_source)
            if self._prepare_overlay is not None:
                overlay_copy = projected_source
                overlay = await _result(self._prepare_overlay(
                    overlay_copy, purpose=purpose, request_options=copy.deepcopy(options),
                ))
                if not isinstance(overlay, Mapping) or set(overlay) != {"request", "validate"}:
                    raise ValueError("Host media overlay must return exact request/validate association")
                if not callable(overlay["validate"]):
                    raise ValueError("Host media overlay requires synchronous admission validator")
                projected_source, overlay_validator = overlay["request"], overlay["validate"]
                if projected_source is request:
                    raise ValueError("Host overlay must not return caller canonical request")
                if projected_source is not overlay_copy:
                    owned_overlays.append(projected_source)
                if overlay_validator() is not True:
                    raise ValueError("Host media overlay admission refused")
                if self._binding(request, options)[0] != base_key:
                    raise ValueError("Canonical request changed during host overlay")
            lifetime.validate()
            source_digest = _digest(projected_source)
            key, identity, model = self._binding(request, options, source=projected_source)
            if self._binding(request, options)[0] != base_key:
                raise ValueError("Media binding changed during support qualification")
            lifetime.validate()
            if self._closed or (
                overlay_validator is not None and overlay_validator() is not True
            ):
                raise ValueError("Media admission changed during support qualification")
            lifetime.validate()
            if (
                self._binding(request, options)[0] != base_key
                or _digest(projected_source) != source_digest
            ):
                raise ValueError("Media binding changed during admission validation")
            lifetime.validate()
            prepared = await _result(
                self._capability.prepare_request(
                    projected_source, provider_identity=identity, model=model,
                    request_options=copy.deepcopy(options), purpose=purpose,
                    execution_scope=copy.deepcopy(self._scope),
                )
            )
        except BaseException:
            try:
                self._clear_overlays(owned_overlays)
            finally:
                self._end_lifetime(lifetime)
            raise
        self._owned_overlays[id(prepared)] = owned_overlays
        candidate = _Candidate(
            request, key, base_key, prepared, projected_source, source_digest,
            lifetime, overlay_validator, release_options, option_defaults,
        )
        self._candidates.append(candidate)
        self._active.append(prepared)
        try:
            setter = getattr(prepared, "set_binding_validator", None)
            if callable(setter):
                setter(lambda: self._candidate_valid(candidate, options))
            self._validate(candidate, options)
        except BaseException:
            self._release(candidate)
            raise
        if not private:
            self._candidate = candidate
        return candidate

    def _validate(self, candidate: _Candidate, options: dict[str, Any]) -> None:
        self._candidate_valid(candidate, options)
        candidate.prepared.validate(request_options=options)

    @staticmethod
    def _budget_kwargs(
        method: Any, request_options: Any, kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        forwarded = dict(kwargs)
        if _accepts_options(method):
            forwarded["request_options"] = request_options
        elif request_options:
            raise ValueError("Provider cannot count effective request options")
        return forwarded

    @staticmethod
    def _measured_budget(result: Any) -> bool:
        if not isinstance(result, Mapping):
            return False
        required = (
            "estimated_input_tokens", "input_limit_tokens", "context_token_budget"
        )
        if any(
            type(result.get(name)) is not int or result[name] < 0
            for name in required
        ):
            return False
        measurement = result.get("measurement")
        return (
            isinstance(measurement, Mapping)
            and measurement.get("kind") == "provider_count"
            and isinstance(measurement.get("source"), str)
            and bool(measurement["source"])
            and type(measurement.get("input_tokens")) is int
            and 0 <= measurement["input_tokens"] <= result["estimated_input_tokens"]
        )

    def media_budget_witness(
        self, request: Any, *, request_options: Any = None
    ) -> dict[str, Any]:
        """Validate a real measured budget association; never expose pixels.

        An unavailable/estimated budget has no witness. This is a projection
        identity assertion, not an independent claim about a vendor's count.
        """
        candidate = self._candidate
        if (
            candidate is None
            or candidate.canonical is not request
            or not candidate.fitted
            or not candidate.measured
        ):
            raise ValueError("No measured media budget for this candidate")
        try:
            self._validate(candidate, self._options(request_options, {}))
            return {
                "v": 1,
                "projection_digest": candidate.prepared.projection_digest,
                "validated": True,
            }
        except BaseException:
            self._release(candidate)
            raise

    @staticmethod
    def _text_fallback_messages(request: Any) -> list[dict[str, Any]]:
        """Copy only explicit Core text fields, never provider/owner metadata.

        This is deliberately narrower than arbitrary text-capable vendor input.
        Unknown content blocks, extras and opaque provider state refuse rather
        than getting dropped from a supposedly countable fallback.
        """
        from amplifier_core.message_models import ChatRequest, Message, TextBlock

        if (
            type(request) is not ChatRequest
            or request.model_extra
            or request.metadata
            or request.tools
        ):
            raise ValueError("Text fallback cannot omit request tools or opaque state")
        warnings = {
            f"[Image unavailable: {reason}; no pixels supplied]"
            for reason in (
                "inline image is not admitted as session media",
                "session image expired",
                "source owner does not permit this session image",
                "provider or execution scope does not support this image position",
                "session image bytes are missing",
                "compatible media handling is not installed",
            )
        }
        messages = []
        warned = False
        for message in request.messages:
            if type(message) is not Message or message.model_extra:
                raise ValueError("Text fallback cannot omit opaque message state")
            metadata = message.metadata
            if metadata is not None and (
                type(metadata) is not dict
                or set(metadata) - {"experimental.media.v1"}
            ):
                raise ValueError("Text fallback cannot omit provider metadata")
            content = message.content
            if type(content) is list:
                blocks = []
                for block in content:
                    if (
                        type(block) is not TextBlock
                        or block.model_extra
                        or type(block.text) is not str
                        or block.visibility not in (None, "internal", "developer", "user")
                    ):
                        raise ValueError("Text fallback requires text-only projection")
                    warned = warned or block.text in warnings
                    blocks.append({
                        "type": "text", "text": block.text,
                        "visibility": block.visibility,
                    })
                content = blocks
            elif type(content) is not str:
                raise ValueError("Text fallback requires text-only projection")
            if (
                message.role not in ("system", "developer", "user", "assistant", "function", "tool")
                or (message.name is not None and type(message.name) is not str)
                or (message.tool_call_id is not None and type(message.tool_call_id) is not str)
            ):
                raise ValueError("Text fallback requires explicit Core message fields")
            messages.append({
                "role": message.role, "content": content,
                "name": message.name, "tool_call_id": message.tool_call_id,
            })
        if not warned:
            raise ValueError("Text fallback has no prepared unavailable warning")
        return messages

    async def media_text_fallback_witness(
        self, request: Any, *, request_options: Any = None
    ) -> dict[str, Any]:
        """Fit a warning-only private projection without a provider count/call.

        The detached text view is suitable for a caller's *local estimate*, not
        a numerical provider budget. Pass the exact count-view request/options;
        successful fitting freezes the same private candidate for dispatch.
        Canonical refs remain untouched. No images, refs, callbacks or opaque
        provider state leave this method.
        """
        self._begin()
        try:
            from amplifier_core.message_models import ChatRequest, ImageBlock

            if type(request) is not ChatRequest or not any(
                (
                    type(message.metadata) is dict
                    and type(message.metadata.get("experimental.media.v1")) is dict
                    and type(message.metadata["experimental.media.v1"].get("refs")) is list
                    and bool(message.metadata["experimental.media.v1"]["refs"])
                )
                or (
                    type(message.content) is list
                    and any(type(block) is ImageBlock for block in message.content)
                )
                for message in request.messages
            ):
                raise ValueError("Text fallback requires canonical media input")
            options = self._options(request_options, {})
            candidate = await self._prepare(request, options, purpose="count")
            self._validate(candidate, options)
            messages = self._text_fallback_messages(candidate.prepared.request)
            digest = candidate.prepared.projection_digest
            if (
                type(digest) is not str
                or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)
            ):
                raise ValueError("Text fallback requires a projection digest")
            self._validate(candidate, options)
            candidate.fitted = True
            return {
                "v": 1, "validated": True,
                "projection_digest": digest, "messages": messages,
            }
        except BaseException:
            self._release(self._candidate)
            raise
        finally:
            self._busy = False

    def _budget_result(self, candidate: _Candidate, result: Any) -> Any:
        candidate.fitted = True
        candidate.measured = self._measured_budget(result)
        # Preserve provider-owned/typed results; ordinary mappings get only
        # additive data in a new mapping, never an in-place mutation.
        if type(result) is dict and candidate.measured:
            return {
                **result,
                "experimental.media.v1": {
                    "v": 1,
                    "projection_digest": candidate.prepared.projection_digest,
                    "validated": True,
                },
            }
        return result

    async def _request_budget(
        self, request: Any, *, request_options: Any = None, **kwargs: Any
    ) -> Any:
        self._begin()
        try:
            options = self._options(
                request_options,
                {k: v for k, v in kwargs.items() if k != "context_estimate"},
            )
            candidate = await self._prepare(request, options, purpose="count")
            method = self._provider.request_budget
            self._validate(candidate, options)
            result = await _result(
                method(
                    candidate.prepared.request,
                    **self._budget_kwargs(method, request_options, kwargs),
                )
            )
            self._validate(candidate, options)
            return self._budget_result(candidate, result)
        except BaseException:
            self._release(self._candidate)
            raise
        finally:
            self._busy = False

    async def complete(self, request: Any, **kwargs: Any) -> Any:
        self._begin()
        try:
            options = self._options(
                kwargs.get("request_options"),
                {k: v for k, v in kwargs.items() if k != "request_options"},
            )
            candidate = await self._prepare(
                request, options, purpose="transport", dispatch=True
            )
            self._validate(candidate, options)
            return await _result(
                self._provider.complete(candidate.prepared.request, **kwargs)
            )
        finally:
            try:
                self._release(self._candidate)
            finally:
                self._busy = False

    async def _refit_request(
        self, request: Any, *, request_options: Any = None, **kwargs: Any
    ) -> Any:
        self._begin()
        try:
            # Refit authorizes a fresh projection, not another transport attempt.
            self._release(self._candidate)
            options = self._options(
                request_options,
                {k: v for k, v in kwargs.items() if k not in ("context_estimate", "error")},
            )
            candidate = await self._prepare(request, options, purpose="count")
            method = self._provider.refit_request
            self._validate(candidate, options)
            result = await _result(
                method(
                    candidate.prepared.request,
                    **self._budget_kwargs(method, request_options, kwargs),
                )
            )
            self._validate(candidate, options)
            if result is None:
                self._release(candidate)
                return result
            return self._budget_result(candidate, result)
        except BaseException:
            self._release(self._candidate)
            raise
        finally:
            self._busy = False

    async def _compact_context(self, request: Any, **kwargs: Any) -> Any:
        self._begin()
        candidate = None
        try:
            options = self._options(
                kwargs.get("request_options"),
                {k: v for k, v in kwargs.items() if k != "request_options"},
            )
            candidate = await self._prepare(
                request, options, purpose="transport", private=True
            )
            self._validate(candidate, options)
            return await _result(
                self._provider.compact_context(candidate.prepared.request, **kwargs)
            )
        finally:
            try:
                self._release(candidate)
            finally:
                self._busy = False

    def _stream(self, request: Any, **kwargs: Any) -> _MediaStream:
        self._begin()
        stream = _MediaStream(self, request, kwargs)
        self._streams.add(stream)
        return stream


class _MediaStream:
    """An explicitly closable iterator, even before its first iteration."""

    def __init__(
        self, owner: MediaPreparedProvider, request: Any, kwargs: dict[str, Any]
    ) -> None:
        self._owner = owner
        self._request = request
        self._kwargs = kwargs
        self._candidate = owner._candidate
        self._iterator: Any = None
        self._closed = False
        self._pending: asyncio.Task[Any] | None = None
        self._close_task: asyncio.Task[None] | None = None

    def __aiter__(self) -> _MediaStream:
        return self

    async def __anext__(self) -> Any:
        if self._closed:
            raise StopAsyncIteration
        if self._pending is not None:
            raise ValueError("Media stream is already advancing")
        pending = asyncio.create_task(self._advance())
        self._pending = pending
        try:
            return await pending
        except BaseException:
            await self.aclose()
            raise
        finally:
            self._pending = None

    async def _advance(self) -> Any:
        if self._owner._closed:
            raise ValueError("Media provider is closed")
        if self._iterator is None:
            options = self._owner._options(
                self._kwargs.get("request_options"),
                {k: v for k, v in self._kwargs.items() if k != "request_options"},
            )
            self._candidate = await self._owner._prepare(
                self._request, options, purpose="transport", dispatch=True
            )
            if self._closed:
                raise StopAsyncIteration
            self._owner._validate(self._candidate, options)
            iterator = await _result(
                self._owner._provider.stream(
                    self._candidate.prepared.request, **self._kwargs
                )
            )
            self._iterator = iterator.__aiter__()
        if self._closed:
            raise StopAsyncIteration
        result = await self._iterator.__anext__()
        if self._closed:
            raise StopAsyncIteration
        return result

    async def aclose(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._finish())
        await asyncio.shield(self._close_task)

    async def _finish(self) -> None:
        self._closed = True
        pending = self._pending
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        iterator, self._iterator = self._iterator, None
        self._request = None
        self._kwargs = {}
        candidate, self._candidate = self._candidate, None
        try:
            close = getattr(iterator, "aclose", None)
            if callable(close):
                await _result(close())
        finally:
            try:
                self._owner._release(candidate)
            finally:
                self._owner._busy = False
                self._owner._streams.discard(self)