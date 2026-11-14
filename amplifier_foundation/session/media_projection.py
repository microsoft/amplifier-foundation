"""Private, request-scoped session-media projection and retry witness.

Nothing in this module is a canonical carrier or a provider wire format.  A
prepared request owns the only hydrated copy, and must be released by its caller.
The store supplies publication/ownership and checkpoint authority.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from typing import Any

from amplifier_core.message_models import ChatRequest, ImageBlock, TextBlock
from pydantic import BaseModel

_NAMESPACE = "experimental.media.v1"
_UNSET = object()


def _refuse(message: str) -> Any:
    # The store imports this module at its installation seam.
    from .media import MediaRefusal

    raise MediaRefusal(message)


def _json_value(value: Any, active: set[int] | None = None) -> Any:
    """Snapshot JSON data without coercions, serializers or secret-bearing reprs."""
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            _refuse("media witness requires finite JSON numbers")
        return value
    if active is None:
        active = set()
    if id(value) in active:
        _refuse("media witness requires acyclic JSON data")
    active.add(id(value))
    try:
        if isinstance(value, BaseModel) and type(value).__module__.startswith("amplifier_core."):
            # model_dump(mode='json') may invoke arbitrary owner serializers or
            # stringify non-JSON metadata. Inspect the Core fields instead.
            result = {
                key: _json_value(getattr(value, key), active)
                for key in type(value).model_fields
            }
            for key, item in (value.model_extra or {}).items():
                if type(key) is not str:
                    _refuse("media witness requires string JSON keys")
                result[key] = _json_value(item, active)
            return result
        if type(value) is dict:
            if any(type(key) is not str for key in value):
                _refuse("media witness requires string JSON keys")
            return {key: _json_value(item, active) for key, item in value.items()}
        if type(value) is list:
            return [_json_value(item, active) for item in value]
        _refuse("media witness contains unsupported non-JSON data")
    finally:
        active.remove(id(value))


def _digest(value: Any) -> str:
    encoded = json.dumps(
        _json_value(value), sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _policy_value(store: Any) -> Any:
    policy = store.policy
    if is_dataclass(policy) and not isinstance(policy, type):
        policy = {field.name: getattr(policy, field.name) for field in fields(policy)}
    return _json_value(policy)


def _effective_options(options: Any, request: ChatRequest, model: Any) -> Any:
    """Equivalent explicit defaults normalize; changed wire options never do."""
    value = _json_value(options)
    if type(value) is dict:
        value = dict(value)
        selected_model = model if model is not None else request.model
        if "model" in value and value["model"] == selected_model:
            del value["model"]
        if "reasoning_effort" in value and value["reasoning_effort"] == request.reasoning_effort:
            del value["reasoning_effort"]
    return value


def _expired(ref: dict[str, Any]) -> bool:
    expiry = ref["provenance"]["expires_at"]
    if expiry is None:
        return False
    try:
        moment = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        if moment.tzinfo is None or moment.utcoffset() is None:
            _refuse("media expiry must include a timezone")
        return datetime.now(timezone.utc) >= moment
    except (TypeError, ValueError, AttributeError):
        _refuse("media expiry is invalid")


def _indexed_support(value: Any, request: ChatRequest) -> Any:
    """Strict v2 facts refer only to this exact prehydration request."""
    value = _json_value(value)
    if (
        type(value) is not dict or set(value) != {"v", "positions", "mime_types"}
        or type(value["v"]) is not int or value["v"] != 2
        or type(value["positions"]) is not list
        or type(value["mime_types"]) is not list
        or any(type(mime) is not str or not mime for mime in value["mime_types"])
        or len(set(value["mime_types"])) != len(value["mime_types"])
    ):
        _refuse("invalid indexed media request support")
    seen = set()
    for position in value["positions"]:
        if (
            type(position) is not dict or set(position) != {"message_index", "role"}
            or type(position["message_index"]) is not int
            or not 0 <= position["message_index"] < len(request.messages)
            or type(position["role"]) is not str
            or position["role"] not in ("user", "tool")
            or request.messages[position["message_index"]].role != position["role"]
            or position["message_index"] in seen
        ):
            _refuse("invalid indexed media request position")
        seen.add(position["message_index"])
    return value


def _can_hydrate(
    identity: Any, scope: Any, role: str, mime: str | None = None, *,
    message_index: int,
) -> bool:
    if scope in ("nested", "text_only"):
        return False
    if isinstance(scope, dict) and (scope.get("nested") is True or scope.get("text_only") is True):
        return False
    if not isinstance(identity, dict):
        return False
    if not {"provider", "account", "generation", "media_support"} <= identity.keys():
        return False
    support = identity.get("media_support")
    if type(support) is dict and type(support.get("v")) is int and support["v"] == 2:
        # Shape and role/index consistency were checked before any pixel access.
        return (
            {"message_index": message_index, "role": role} in support["positions"]
            and mime in support["mime_types"]
        )
    return (
        isinstance(support, dict)
        and type(support.get("v")) is int and support["v"] == 1
        and type(support.get("positions")) is list
        and all(type(position) is str for position in support["positions"])
        and role in support["positions"]
        and (mime is None or "mime_types" not in support or (
            type(support["mime_types"]) is list and mime in support["mime_types"]
        ))
    )


def _read_asset(store: Any, ref: dict[str, Any]) -> bytes | None:
    """Only an explicitly missing file permits an unavailable fallback."""
    try:
        stream = store.resolve_ref(ref, committed=True)
    except FileNotFoundError:
        return None
    try:
        # The owned ref has already been validated. Bound reads even if a store
        # implementation accidentally returns a growing or incorrect stream.
        chunks = []
        remaining = ref["bytes"] + 1
        while remaining:
            chunk = stream.read(min(remaining, 64 * 1024))
            if not isinstance(chunk, bytes):
                _refuse("media asset is not a binary stream")
            if not chunk:
                break
            if len(chunk) > remaining:
                _refuse("media asset length changed")
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) != ref["bytes"] or hashlib.sha256(data).hexdigest() != ref["sha256"]:
            _refuse("media asset identity changed")
        return data
    finally:
        stream.close()


def _warning(reason: str) -> TextBlock:
    return TextBlock(text=f"[Image unavailable: {reason}; no pixels supplied]")


def _source_allowed(store: Any, ref: dict[str, Any]) -> bool:
    from .media import MediaRefusal

    try:
        store.validate_ref(ref, committed=True, check_expiry=True)
    except MediaRefusal as error:
        if getattr(error, "code", None) == "source_policy":
            return False
        raise
    return True


def _place(message: Any, refs: list[dict[str, Any]], blocks: list[Any]) -> None:
    """Match only whole marker blocks in a valid carrier, preserving role/order."""
    remaining = list(range(len(refs)))
    original = message.content
    string_content = isinstance(original, str)
    content = [TextBlock(text=original)] if string_content else original
    output = []
    for block in content:
        match = None
        if not string_content and isinstance(block, TextBlock):
            for index in remaining:
                if block.text == f"[Session image {refs[index]['sha256']}]":
                    match = index
                    break
        if match is None:
            output.append(block)
        else:
            replacement = blocks[match]
            replacement.visibility = block.visibility
            output.append(replacement)
            remaining.remove(match)
    output.extend(blocks[index] for index in remaining)
    message.content = output


class PreparedMediaRequestV1:
    """An in-memory association between one canonical and one private request.

    Public validation never returns pixels. Mutations invalidate this witness;
    they never trigger a silent re-projection after count.
    """

    def __init__(
        self, store: Any, canonical: ChatRequest, projected: ChatRequest, *,
        provider_identity: Any, model: Any, request_options: Any,
        purpose: Any, execution_scope: Any, assets: list[tuple[dict[str, Any], str]],
    ) -> None:
        self._store = store
        self._canonical = canonical
        self._request = projected
        self._provider_identity = provider_identity
        self._model = model
        self._request_options = request_options
        self._purpose = purpose
        self._execution_scope = execution_scope
        self._assets = assets
        self._image_blocks = [
            block for message in projected.messages
            if isinstance(message.content, list)
            for block in message.content if isinstance(block, ImageBlock)
        ]
        self._canonical_digest = _digest(canonical)
        self._projection_digest = _digest(projected)
        self._binding_digest = self._current_binding_digest()
        self._released = False
        self._unregistered = False
        self._binding_validator = None
        self._request_only_slots: list[tuple[int, int]] = []

    def set_binding_validator(self, validator: Any) -> None:
        """Host-only live selected-provider gate, never canonical metadata."""
        if self._released or not callable(validator):
            _refuse("invalid live media binding validator")
        self._binding_validator = validator

    def _current_binding_digest(self) -> str:
        return _digest({
            "session_id": self._store.session_id,
            "epoch": self._store.epoch,
            "policy": _policy_value(self._store),
            "provider_identity": self._provider_identity,
            "model": self._model,
            "request_options": self._request_options,
            "purpose": self._purpose,
            "execution_scope": self._execution_scope,
        })

    @property
    def request(self) -> ChatRequest:
        return self._request

    @property
    def projection_digest(self) -> str:
        return self._projection_digest

    @property
    def released(self) -> bool:
        return self._released

    def validate(self, *, request_options: Any = _UNSET) -> dict[str, bool]:
        if self._released:
            _refuse("media preparation is released")
        if self._binding_validator is not None:
            if self._binding_validator() is not True:
                _refuse("live selected-provider binding changed")
        for message_index, block_index in self._request_only_slots:
            verifier = getattr(self._store, "request_only_verifier", None)
            if not callable(verifier) or verifier(
                self._canonical, message_index=message_index,
                block_index=block_index, purpose=self._purpose
            ) is not True:
                _refuse("host request-only media admission changed")
        if self._current_binding_digest() != self._binding_digest:
            _refuse("media preparation binding changed")
        if request_options is not _UNSET and _digest(
            _effective_options(request_options, self._request, self._model)
        ) != _digest(_effective_options(self._request_options, self._request, self._model)):
            _refuse("media request options changed")
        if _digest(self._canonical) != self._canonical_digest:
            _refuse("canonical media request changed")
        if _digest(self._request) != self._projection_digest:
            _refuse("private media projection changed")
        for ref, state in self._assets:
            normalized = self._store.validate_ref(ref, committed=True, check_expiry=False)
            if _digest(normalized) != _digest(ref):
                _refuse("media reference identity changed")
            self._store.verify_checkpoint(ref)
            if state == "expired":
                continue  # This warning, not pixels, was fitted before count.
            if _expired(ref):
                _refuse("media reference expired after preparation")
            allowed = _source_allowed(self._store, ref)
            if not allowed and state == "source_policy":
                continue  # The same unavailable source warning was fitted.
            if not allowed or state == "source_policy":
                _refuse("media source policy changed after preparation")
            if state == "unsupported":
                continue  # This route must not hydrate even for inspection.
            data = _read_asset(self._store, ref)
            if (data is None) != (state == "missing"):
                _refuse("media asset availability changed after preparation")
        # Store/host verifiers are synchronous callbacks; do not let callback
        # side effects alter the fitted request while a witness is being checked.
        if (
            self._current_binding_digest() != self._binding_digest
            or _digest(self._canonical) != self._canonical_digest
            or _digest(self._request) != self._projection_digest
        ):
            _refuse("media preparation changed during validation")
        return {"valid": True}

    def release(self) -> None:
        if self._released and self._unregistered:
            return
        self._released = True
        # Wipe sources too, for code retaining a block from our private copy.
        try:
            for block in self._image_blocks:
                if type(block.source) is dict:
                    block.source.clear()
        finally:
            self._image_blocks.clear()
            # Invalidated projections may have arbitrary mutations. Cleanup
            # must not traverse that graph or retain the canonical conversation.
            self._request.messages = []
            self._assets.clear()
            self._binding_validator = None
            self._request_only_slots.clear()
            self._canonical = None  # type: ignore[assignment]
            self._store.unregister_prepared(self)
            self._unregistered = True

    def __repr__(self) -> str:
        return f"PreparedMediaRequestV1(released={self._released})"


async def prepare_media_request(
    store: Any, request: ChatRequest, *, provider_identity: Any, model: Any,
    request_options: Any, purpose: Any, execution_scope: Any,
) -> PreparedMediaRequestV1:
    """Freeze original-position media or truthful warnings before token count."""
    if not isinstance(request, ChatRequest):
        _refuse("media preparation requires a Core ChatRequest")
    # Check before deepcopy: arbitrary metadata objects must not get to execute
    # copy hooks, JSON serializers, or repr while constructing a witness.
    canonical_digest = _digest(request)
    support = provider_identity.get("media_support") if type(provider_identity) is dict else None
    if type(support) is dict and support.get("v") == 2:
        _indexed_support(support, request)
    initial_binding = _digest({
        "session_id": store.session_id, "epoch": store.epoch,
        "policy": _policy_value(store), "provider_identity": provider_identity,
        "model": model, "request_options": request_options,
        "purpose": purpose, "execution_scope": execution_scope,
    })
    projected = copy.deepcopy(request)
    assets: list[tuple[dict[str, Any], str]] = []
    image_blocks: list[ImageBlock] = []
    request_only_slots = []
    data = None
    try:
        for message_index, message in enumerate(projected.messages):
            if isinstance(message.content, list):
                # Legacy images were never admitted into this session's
                # ownership/checkpoint graph. Do not silently ingest them.
                content = []
                for block_index, block in enumerate(message.content):
                    if not isinstance(block, ImageBlock):
                        content.append(block)
                        continue
                    verifier = getattr(store, "request_only_verifier", None)
                    admitted = (
                        _can_hydrate(provider_identity, execution_scope, message.role,
                                     block.source.get("media_type"), message_index=message_index)
                        and callable(verifier) and verifier(
                            request, message_index=message_index,
                            block_index=block_index, purpose=purpose
                        ) is True
                    )
                    if admitted:
                        content.append(block)
                        image_blocks.append(block)
                        request_only_slots.append((message_index, block_index))
                    else:
                        # Clear the detached deep copy as well as replacing it.
                        block.source.clear()
                        content.append(_warning("inline image is not admitted as session media").model_copy(
                            update={"visibility": block.visibility}
                        ))
                message.content = content
            metadata = message.metadata or {}
            if _NAMESPACE not in metadata:
                continue
            carrier = metadata[_NAMESPACE]
            if type(carrier) is not dict or set(carrier) != {"refs"} or type(carrier["refs"]) is not list:
                _refuse("canonical media carrier is malformed")
            refs = []
            blocks = []
            for original in carrier["refs"]:
                # Ownership must be checked before expiry can become a warning.
                ref = store.validate_ref(original, committed=True, check_expiry=False)
                if _digest(ref) != _digest(original):
                    _refuse("canonical media reference is not normalized")
                ref = copy.deepcopy(ref)
                store.verify_checkpoint(ref)
                expired = _expired(ref)
                allowed = False if expired else _source_allowed(store, ref)
                supported = _can_hydrate(
                    provider_identity, execution_scope, message.role, ref["mime"],
                    message_index=message_index,
                )
                # Unsupported positions/nested/text-only never read pixels.
                data = _read_asset(store, ref) if allowed and supported else None
                state = (
                    "expired" if expired else "source_policy" if not allowed
                    else "unsupported" if not supported
                    else "missing" if data is None else "available"
                )
                assets.append((ref, state))
                refs.append(ref)
                if expired:
                    block = _warning("session image expired")
                elif not allowed:
                    block = _warning("source owner does not permit this session image")
                elif not supported:
                    block = _warning("provider or execution scope does not support this image position")
                elif data is None:
                    block = _warning("session image bytes are missing")
                else:
                    block = ImageBlock(source={
                        "type": "base64", "media_type": ref["mime"],
                        "data": base64.b64encode(data).decode("ascii"),
                    })
                    image_blocks.append(block)
                data = None
                blocks.append(block)
            if refs:
                _place(message, refs, blocks)
        if _digest(request) != canonical_digest:
            _refuse("canonical media request changed during preparation")
        prepared = PreparedMediaRequestV1(
            store, request, projected, provider_identity=provider_identity,
            model=model, request_options=request_options, purpose=purpose,
            execution_scope=execution_scope, assets=assets,
        )
        if prepared._binding_digest != initial_binding:
            _refuse("media preparation binding changed during preparation")
        prepared._request_only_slots = request_only_slots
        store.register_prepared(prepared)
        return prepared
    except BaseException:
        # A failed preparation must not leave a partially hydrated candidate.
        data = None
        for image in image_blocks:
            image.source.clear()
        for message in projected.messages:
            if isinstance(message.content, list):
                for block in message.content:
                    if isinstance(block, ImageBlock):
                        block.source.clear()
        projected.messages.clear()
        raise
