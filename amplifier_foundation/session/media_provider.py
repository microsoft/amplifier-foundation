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
    prepared: Any
    fitted: bool = False
    measured: bool = False


class MediaPreparedProvider:
    """Wrap one actual provider and one session-bound media capability.

    ``provider_identity`` is a JSON mapping (or a zero-argument getter for live
    account/generation identity). Media support always comes from the actual
    provider's ``get_media_request_support(model=..., request_options=...)``.
    ``model`` may likewise be a zero-argument getter.

    The host must call ``close``/``aclose`` on session teardown. They release this
    wrapper's buffers, not the underlying provider's resources. Concurrent use
    refuses rather than replacing an in-flight projection. Optional methods are
    assigned only when implemented by the actual provider.
    """

    def __init__(
        self,
        provider: Any,
        capability: Any,
        *,
        provider_identity: Any,
        model: Any = None,
        execution_scope: Any = None,
    ) -> None:
        if not callable(getattr(provider, "complete", None)):
            raise TypeError("Media provider requires a callable complete")
        if not callable(getattr(capability, "prepare_request", None)):
            raise TypeError("Media capability requires a callable prepare_request")
        self._provider = provider
        self._capability = capability
        self._identity_source = provider_identity
        self._model_source = model
        self._scope = execution_scope
        self._candidate: _Candidate | None = None
        self._active: list[Any] = []
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
        prepared = candidate.prepared
        if not any(item is prepared for item in self._active):
            return
        self._active = [item for item in self._active if item is not prepared]
        prepared.release()

    def close(self) -> None:
        """Revoke all prepared requests, including an active transport's witness."""
        self._closed = True
        self._candidate = None
        active, self._active = self._active, []
        for prepared in active:
            prepared.release()

    async def aclose(self) -> None:
        """Revoke buffers and close any live underlying stream iterators."""
        self.close()
        for stream in tuple(self._streams):
            await stream.aclose()

    @staticmethod
    def _options(request_options: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
        if request_options is not None and not isinstance(request_options, Mapping):
            raise ValueError("request_options must be a mapping")
        return {**(request_options or {}), **kwargs}

    def _binding(
        self, request: Any, options: dict[str, Any]
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
        support = getattr(self._provider, "get_media_request_support", None)
        identity["media_support"] = (
            support(model=model, request_options=copy.deepcopy(options))
            if callable(support) else {"v": 1, "positions": []}
        )
        key = tuple(
            _fingerprint(value)
            for value in (request, options, identity, model, self._scope)
        )
        return key, identity, model

    async def _prepare(
        self,
        request: Any,
        options: dict[str, Any],
        *,
        purpose: str,
        dispatch: bool = False,
        private: bool = False,
    ) -> _Candidate:
        key, identity, model = self._binding(request, options)
        old = None if private else self._candidate
        if old is not None:
            if old.canonical is request and old.key == key:
                old.prepared.validate(request_options=options)
                return old
            fitted = old.fitted
            self._release(old)
            if dispatch and fitted:
                raise ValueError("Fitted media candidate changed before dispatch")
        prepared = await _result(
            self._capability.prepare_request(
                request,
                provider_identity=identity,
                model=model,
                request_options=copy.deepcopy(options),
                purpose=purpose,
                execution_scope=copy.deepcopy(self._scope),
            )
        )
        candidate = _Candidate(request, key, prepared)
        self._active.append(prepared)
        try:
            setter = getattr(prepared, "set_binding_validator", None)
            if callable(setter):
                setter(lambda: not self._closed and self._binding(request, options)[0] == key)
            if self._closed or self._binding(request, options)[0] != key:
                raise ValueError("Media candidate changed during preparation")
            prepared.validate(request_options=options)
        except BaseException:
            self._release(candidate)
            raise
        if not private:
            self._candidate = candidate
        return candidate

    def _validate(self, candidate: _Candidate, options: dict[str, Any]) -> None:
        if self._closed or self._binding(candidate.canonical, options)[0] != candidate.key:
            raise ValueError("Prepared media candidate changed")
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
            self._release(self._candidate)
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
            self._release(candidate)
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
            self._owner._release(candidate)
            self._owner._busy = False
            self._owner._streams.discard(self)