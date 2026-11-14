"""Sync host binding for the CI-owner Optimize ImageOwner protocol.

No CI imports, canonical transcript writes or live coordinator access. A host
supplies actual original-policy admission, diagnostic checkpoint verification,
reader compatibility and measured allocation accounting. These are required,
not guessed from an event timestamp, pathname, ref or archive.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterator

from .media_conversion import NAMESPACE, Unsupported, _digest, _json, _open, _validate_ref

BINDING = "foundation.capture-image-owner.v1"
GROUP_BINDING = "foundation.capture-image-owner-group.v1"
DIAGNOSTIC_LABEL = "[Session image {sha256}; diagnostic reference only; no pixels supplied]"


@dataclass(frozen=True)
class _DetachedStagedImage:
    replacement: dict
    receipt: dict
    new_asset_bytes: int


def _off_loop() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise Unsupported("Sync historical media binding must run off the live event loop")


def _sync_result(value):
    if inspect.isawaitable(value) or inspect.isgenerator(value) or inspect.isasyncgen(value):
        if inspect.iscoroutine(value) or inspect.isgenerator(value):
            value.close()
        raise Unsupported("Host media callback must return a settled synchronous result")
    return value


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise Unsupported("Duplicate historical manifest member")
        value[key] = item
    return value


def diagnostic_image_ref(block: Any) -> dict:
    """Read ONLY an already-selected diagnostic replacement, never user text.

    Returns metadata only. Owner validation remains mandatory for asset access;
    this does not turn a diagnostic ref into canonical/model reachability.
    """
    if not isinstance(block, dict) or set(block) - {"type", "text", "metadata", "visibility"}:
        raise Unsupported("Unknown diagnostic media replacement")
    metadata = block.get("metadata")
    if not isinstance(metadata, dict) or set(metadata) != {NAMESPACE}:
        raise Unsupported("Unknown diagnostic media namespace")
    namespace = metadata[NAMESPACE]
    if not isinstance(namespace, dict) or set(namespace) != {"refs"}:
        raise Unsupported("Unknown diagnostic ref carrier")
    refs = namespace["refs"]
    if not isinstance(refs, list) or len(refs) != 1:
        raise Unsupported("Diagnostic image requires one exact reference")
    _validate_ref(refs[0])
    if block.get("type") != "text" or block.get("text") != DIAGNOSTIC_LABEL.format(**refs[0]):
        raise Unsupported("Diagnostic image label/ref mismatch")
    return deepcopy(refs[0])


class CaptureMediaOwner:
    """Concrete sync adapter to F1's actual ingest/publish/resolve/ACK seams.

    CI calls sequentially in the host's stopped-writer/flush boundary, offthread.
    ``origin_for`` must authorize one exact source snapshot and return all five
    original provenance fields. ``checkpoint_receipt_for`` returns the actual
    local diagnostic-file receipt, verified by this store's checkpoint verifier.
    ``new_asset_bytes_for`` supplies independently measured unique allocation
    AFTER assets-only publication, excluding duplicates/recovery; it may not
    guess from the inline length. Allocation can exceed logical payload length.
    ``reader_ready`` is the host's qualification gate for this exact replacement
    AND a distinct diagnostic-only store that is never mounted on a canonical
    session capability. Diagnostic-file reachability is not a transcript ACK.
    """

    def __init__(
        self, store: Any, *, staged_factory: Callable[..., Any],
        origin_for: Callable[[dict], dict],
        checkpoint_receipt_for: Callable[..., Any],
        new_asset_bytes_for: Callable[[dict], int],
        reader_ready: Callable[[str], bool],
        single_image_origin: Callable[[dict, dict], bool],
    ) -> None:
        self.store = store
        self.staged_factory = staged_factory
        self.origin_for = origin_for
        self.checkpoint_receipt_for = checkpoint_receipt_for
        self.new_asset_bytes_for = new_asset_bytes_for
        self.reader_ready = reader_ready
        self.single_image_origin = single_image_origin

    def _ready(self):
        _off_loop()
        if _sync_result(self.reader_ready(BINDING)) is not True:
            raise Unsupported("Diagnostic reader has not qualified the exact media binding")

    def _origin(self, snapshot):
        original = _sync_result(self.origin_for(deepcopy(snapshot)))
        if not isinstance(original, dict) or set(original) != {
            "kind", "session_id", "id", "created_at", "expires_at",
        }:
            raise Unsupported("Historical owner must supply complete original provenance")
        if len(_json(original)) > 16384 or len(_json(snapshot)) > 16384:
            raise Unsupported("Historical origin exceeds bounded binding metadata")
        return deepcopy(original)

    def stage_image(self, image: BinaryIO, *, mime: str, origin: dict) -> Any:
        self._ready()
        snapshot = deepcopy(origin)
        original = self._origin(snapshot)
        # Current F1 byte ingest is one complete ref-set per real origin.
        # Require the owner to preflight source cardinality before ANY staging;
        # do not manufacture per-image original IDs to evade group conflicts.
        if _sync_result(self.single_image_origin(
            deepcopy(snapshot), deepcopy(original),
        )) is not True:
            raise Unsupported("Grouped/unknown image-origin cardinality requires an owner group adapter")
        if mime not in ("image/png", "image/jpeg"):
            raise Unsupported("Only qualified static PNG/JPEG historical images")
        image.seek(0)
        digest, size = _digest(image)
        image.seek(0)
        if snapshot.get("sha256") != digest or snapshot.get("bytes") != size:
            raise Unsupported("CI source image digest/length disagrees with decoded bytes")
        result = _sync_result(self.store.ingest_sync(
            {"stream": image, "mime": mime}, source_format="image-bytes",
            origin=deepcopy(original), policy=None,
        ))
        refs = result["staged_refs"]
        if not isinstance(refs, list) or len(refs) != 1:
            raise Unsupported("Historical store must stage exactly one image")
        ref = deepcopy(refs[0])
        _validate_ref(ref)
        if (ref["session_id"] != self.store.session_id or ref["provenance"] != original
                or ref["sha256"] != digest or ref["bytes"] != size or ref["mime"] != mime):
            raise Unsupported("Historical staging changed original image/observation identity")
        self.store.validate_ref(deepcopy(ref), committed=False)
        publication = _sync_result(self.store.publish_sync(
            [deepcopy(ref)], origin=deepcopy(original), disposition="retain",
        ))
        if publication["refs"] != [ref]:
            raise Unsupported("Historical staging publication changed reference")
        receipt = {"binding": BINDING, "origin": original, "ref": ref,
                   "source_origin_sha256": hashlib.sha256(_json(snapshot)).hexdigest()}
        if len(_json(receipt)) > 16384:
            raise Unsupported("Historical media owner receipt exceeds 16KiB")
        added = _sync_result(self.new_asset_bytes_for(deepcopy(ref)))
        if type(added) is not int or added < 0:
            raise Unsupported("Host must supply measured unique original asset bytes")
        replacement = {"type": "text", "text": DIAGNOSTIC_LABEL.format(**ref),
                       "metadata": {NAMESPACE: {"refs": [deepcopy(ref)]}}}
        produced = _sync_result(self.staged_factory(
            replacement=deepcopy(replacement), receipt=deepcopy(receipt), new_asset_bytes=added,
        ))
        if (getattr(produced, "replacement", None) != replacement
                or getattr(produced, "receipt", None) != receipt
                or type(getattr(produced, "new_asset_bytes", None)) is not int
                or produced.new_asset_bytes != added):
            raise Unsupported("Historical staged factory changed verified output")
        # Protocol is structural. Never return factory-owned mutable aliases,
        # even when its nominal dataclass is frozen.
        return _DetachedStagedImage(deepcopy(replacement), deepcopy(receipt), added)

    def _manifest(self, path: Path) -> Iterator[tuple[dict, dict]]:
        with _open(path) as stream:
            while True:
                # This is the bounded small owner manifest, NOT the huge capture.
                raw = stream.readline(65537)
                if not raw:
                    break
                if len(raw) > 65536 or not raw.endswith(b"\n"):
                    raise Unsupported("Unbounded/truncated historical media manifest row")
                value = json.loads(raw, object_pairs_hook=_unique)
                if not isinstance(value, dict) or set(value) != {"origin", "owner"}:
                    raise Unsupported("Unknown historical media manifest schema")
                receipt, snapshot = value["owner"], value["origin"]
                if not isinstance(receipt, dict) or set(receipt) != {
                    "binding", "origin", "ref", "source_origin_sha256",
                } or receipt["binding"] != BINDING:
                    raise Unsupported("Unknown historical media owner receipt")
                if len(_json(receipt)) > 16384:
                    raise Unsupported("Historical owner receipt exceeds bound")
                if hashlib.sha256(_json(snapshot)).hexdigest() != receipt["source_origin_sha256"]:
                    raise Unsupported("Historical manifest source-origin drift")
                original, ref = self._origin(snapshot), receipt["ref"]
                if _sync_result(self.single_image_origin(
                    deepcopy(snapshot), deepcopy(original),
                )) is not True:
                    raise Unsupported("Historical image-origin cardinality no longer qualified")
                _validate_ref(ref)
                if ref["provenance"] != original or receipt["origin"] != original:
                    raise Unsupported("Historical media provenance changed")
                if snapshot.get("sha256") != ref["sha256"] or snapshot.get("bytes") != ref["bytes"]:
                    raise Unsupported("Historical media manifest original bytes drift")
                if ref["session_id"] != self.store.session_id:
                    raise Unsupported("Historical receipt belongs to another store")
                self.store.validate_ref(deepcopy(ref), committed=False)
                yield deepcopy(ref), original

    def publish(self, manifest: Path) -> None:
        self._ready()
        for ref, original in self._manifest(manifest):
            result = _sync_result(self.store.publish_sync(
                [deepcopy(ref)], origin=deepcopy(original), disposition="retain",
            ))
            if result["refs"] != [ref]:
                raise Unsupported("Historical publication changed staged reference")
        self.verify(manifest)

    def verify(self, manifest: Path) -> None:
        self._ready()
        for ref, _ in self._manifest(manifest):
            with self.store.resolve_ref(deepcopy(ref), committed=False) as source:
                observed = _digest(source)
            if observed != (ref["sha256"], ref["bytes"]):
                raise Unsupported("Historical diagnostic binary closure mismatch")

    def acknowledge(self, manifest: Path, *, source: Path, sha256: str) -> None:
        self._ready()
        self.verify(manifest)
        with _open(source) as stream:
            if _digest(stream)[0] != sha256:
                raise Unsupported("Historical diagnostic installed hash changed")
        # Neither the manifest nor this wrapper manufactures checkpoint authority.
        # A real host verifier must bind the exact installed diagnostic body and
        # exhaust its reference closure before the F1 store can commit anything.
        receipt = _sync_result(self.checkpoint_receipt_for(
            manifest, source=source, sha256=sha256,
        ))
        _sync_result(self.store.acknowledge_checkpoint(receipt))


class CaptureGroupMediaOwner(CaptureMediaOwner):
    """Same CI ImageOwner protocol with complete real-origin stream groups.

    ``group_sources_for(snapshot, original)`` returns a context manager over an
    iterable of ``{stream, mime, origin}``, in original image-position order.
    The source owner must enumerate exactly one complete attested true-origin
    group from its fenced indexed capture. Each stream stays valid until the
    iterable advances; no full-record or pixel list is required.
    """

    def __init__(self, store, *, group_sources_for, staged_factory, origin_for,
                 checkpoint_receipt_for, new_asset_bytes_for, reader_ready):
        super().__init__(
            store, staged_factory=staged_factory, origin_for=origin_for,
            checkpoint_receipt_for=checkpoint_receipt_for,
            new_asset_bytes_for=new_asset_bytes_for, reader_ready=reader_ready,
            single_image_origin=lambda *_: False,
        )
        self.group_sources_for = group_sources_for

    def _ready(self):
        _off_loop()
        if _sync_result(self.reader_ready(GROUP_BINDING)) is not True:
            raise Unsupported("Diagnostic reader/group source owner is not qualified")

    def stage_image(self, image, *, mime, origin):
        self._ready()
        snapshot = deepcopy(origin)
        original = self._origin(snapshot)
        image.seek(0)
        observed = _digest(image)
        image.seek(0)
        if observed != (snapshot.get("sha256"), snapshot.get("bytes")):
            raise Unsupported("Selected group image differs from exact CI source")
        snapshots, facts = [], []
        limit = self.store.policy.max_images  # Existing explicit host bound.
        manager = _sync_result(self.group_sources_for(deepcopy(snapshot), deepcopy(original)))
        with manager as sources:
            def binary_sources():
                for value in sources:
                    if len(snapshots) >= limit:
                        raise Unsupported("Historical group exceeds host image count")
                    if not isinstance(value, dict) or set(value) != {"stream", "mime", "origin"}:
                        raise Unsupported("Unknown historical group binary source")
                    item = deepcopy(value["origin"])
                    if self._origin(item) != original:
                        raise Unsupported("Historical group contains a different original observation")
                    if any(_json(previous) == _json(item) for previous in snapshots):
                        raise Unsupported("Duplicate historical image position in original group")
                    if value["mime"] not in ("image/png", "image/jpeg"):
                        raise Unsupported("Unsupported original group image format")
                    stream = value["stream"]
                    stream.seek(0)
                    digest, size = _digest(stream)
                    stream.seek(0)
                    if (digest, size) != (item.get("sha256"), item.get("bytes")):
                        raise Unsupported("Historical group source image changed")
                    snapshots.append(item)
                    facts.append((digest, size, value["mime"]))
                    yield {"stream": stream, "mime": value["mime"]}
            result = _sync_result(self.store.ingest_group_sync(
                binary_sources(), origin=deepcopy(original), policy=None,
            ))
        refs = deepcopy(result["staged_refs"])
        if len(refs) != len(snapshots) or not refs:
            raise Unsupported("Historical group owner changed complete image count")
        selected = None
        for index, (ref, item, fact) in enumerate(zip(refs, snapshots, facts)):
            _validate_ref(ref)
            if (ref["session_id"] != self.store.session_id or ref["provenance"] != original
                    or (ref["sha256"], ref["bytes"], ref["mime"]) != fact):
                raise Unsupported("Historical group owner changed original identity/order")
            if _json(item) == _json(snapshot):
                if fact[2] != mime:
                    raise Unsupported("Selected historical group media type differs")
                selected = index
        if selected is None:
            raise Unsupported("Selected CI image is absent from complete original group")
        receipt = {"binding": GROUP_BINDING, "origin": original, "refs": refs,
                   "source_origins": snapshots, "selected": selected}
        if len(_json(receipt)) > 16384:
            raise Unsupported("Historical group receipt exceeds bounded manifest schema")
        publication = _sync_result(self.store.publish_sync(
            deepcopy(refs), origin=deepcopy(original), disposition="retain",
        ))
        if publication["refs"] != refs:
            raise Unsupported("Historical group publication changed complete ref set")
        ref = refs[selected]
        added = _sync_result(self.new_asset_bytes_for(deepcopy(ref)))
        if type(added) is not int or added < 0:
            raise Unsupported("Host must supply measured unique group asset allocation")
        replacement = {"type": "text", "text": DIAGNOSTIC_LABEL.format(**ref),
                       "metadata": {NAMESPACE: {"refs": [deepcopy(ref)]}}}
        produced = _sync_result(self.staged_factory(
            replacement=deepcopy(replacement), receipt=deepcopy(receipt), new_asset_bytes=added,
        ))
        if (getattr(produced, "replacement", None) != replacement
                or getattr(produced, "receipt", None) != receipt
                or type(getattr(produced, "new_asset_bytes", None)) is not int
                or produced.new_asset_bytes != added):
            raise Unsupported("Historical group factory changed verified output")
        return _DetachedStagedImage(deepcopy(replacement), deepcopy(receipt), added)

    def _manifest_groups(self, path):
        with _open(path) as stream:
            while raw := stream.readline(65537):
                if len(raw) > 65536 or not raw.endswith(b"\n"):
                    raise Unsupported("Unbounded/truncated historical group manifest")
                value = json.loads(raw, object_pairs_hook=_unique)
                if not isinstance(value, dict) or set(value) != {"origin", "owner"}:
                    raise Unsupported("Unknown historical group manifest row")
                receipt, selected_source = value["owner"], value["origin"]
                if not isinstance(receipt, dict) or set(receipt) != {
                    "binding", "origin", "refs", "source_origins", "selected",
                } or receipt["binding"] != GROUP_BINDING or len(_json(receipt)) > 16384:
                    raise Unsupported("Unknown historical group receipt")
                refs, snapshots, selected = receipt["refs"], receipt["source_origins"], receipt["selected"]
                if (not isinstance(refs, list) or not refs or len(refs) > self.store.policy.max_images
                        or not isinstance(snapshots, list) or len(refs) != len(snapshots)
                        or type(selected) is not int or not 0 <= selected < len(refs)
                        or _json(snapshots[selected]) != _json(selected_source)):
                    raise Unsupported("Historical group position/count drift")
                original = self._origin(selected_source)
                if receipt["origin"] != original:
                    raise Unsupported("Historical group provenance drift")
                for index, (ref, item) in enumerate(zip(refs, snapshots)):
                    _validate_ref(ref)
                    if (self._origin(item) != original or ref["provenance"] != original
                            or ref["session_id"] != self.store.session_id
                            or (ref["sha256"], ref["bytes"]) != (item.get("sha256"), item.get("bytes"))
                            or any(_json(other) == _json(item) for other in snapshots[:index])):
                        raise Unsupported("Historical group original identity drift")
                    self.store.validate_published_ref(deepcopy(ref))
                yield deepcopy(refs), original

    def publish(self, manifest):
        self._ready()
        for refs, original in self._manifest_groups(manifest):
            result = _sync_result(self.store.publish_sync(
                deepcopy(refs), origin=deepcopy(original), disposition="retain",
            ))
            if result["refs"] != refs:
                raise Unsupported("Historical group publication changed saved complete set")
        self.verify(manifest)

    def verify(self, manifest):
        self._ready()
        for refs, _ in self._manifest_groups(manifest):
            for ref in refs:
                with self.store.resolve_published_ref(deepcopy(ref)) as source:
                    if _digest(source) != (ref["sha256"], ref["bytes"]):
                        raise Unsupported("Historical group binary closure mismatch")