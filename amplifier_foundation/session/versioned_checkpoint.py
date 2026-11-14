"""Process-local media reachability proof for an actual owned V2 writer commit.

Selectors and persisted control files are data, never bearer authority. Minting
requires the actual writer fence and settled current operation. Verification is
cold O(committed history), bounded by scanner tokens/one ref, not warm O(1).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any
from weakref import WeakValueDictionary

from ._media_conversion_parser import CHUNK, Scanner
from .checkpoint import CheckpointRefused
from .media import MEDIA_NAMESPACE, validate_blob_ref
from .transcript_source import _namespace, open_committed_source
from .transcript_v2 import CommittedPrefix, UnsupportedTranscript, _decode, _file, _stamp
from .transcript_writer import (
    LEGACY, CommitAck, TranscriptWriter, _PrefixView, _descriptor, _header, _identity, _receipt,
)

_SEAL = object()
_KEY = os.urandom(32)
_KIND = "versioned-checkpoint-backed-v1"
# Issuer-owned, weakly held revocation state is not carried by the receipt.
# Restoring receipt fields cannot re-enroll an object after exceptional exit.
_ISSUED = WeakValueDictionary()
_SHA = re.compile(r"[0-9a-f]{64}\Z")


class VersionedCheckpointRefused(CheckpointRefused):
    """No usable local source-bound acknowledgement exists."""


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


@dataclass
class _Life:
    valid: bool = True


def _digest_text(value):
    return type(value) is str and _SHA.fullmatch(value)


def _prefix_data(value):
    if type(value) is not dict or set(value) != set(CommittedPrefix.__dataclass_fields__):
        raise VersionedCheckpointRefused("Incomplete operation predecessor")
    for name in ("session_id", "generation", "operation_id"):
        _identity(value[name])
    if (value["body"] != f"g-{value['generation']}.jsonl"
            or not _digest_text(value["sha256"])
            or any(type(value[name]) is not int or value[name] < 0
                   for name in ("sequence", "committed_bytes", "message_count"))):
        raise VersionedCheckpointRefused("Invalid operation predecessor")
    return CommittedPrefix(**value)


def _validate_plan(plan, prefix):
    if (type(plan) is not dict
            or set(plan) != {"format", "v", "session_id", "operation_id", "source", "sources"}
            or type(plan["v"]) is not int or plan["v"] != 2
            or plan["session_id"] != prefix.session_id
            or plan["format"] != "amplifier.session.transcript"):
        raise VersionedCheckpointRefused("Invalid writer transition journal")
    _identity(plan["operation_id"])
    sources = plan["sources"]
    if (type(sources) is not dict or set(sources) != set(LEGACY)
            or plan["source"] not in (*LEGACY, None)):
        raise VersionedCheckpointRefused("Invalid writer source selection")
    for witness in sources.values():
        if witness is None:
            continue
        if (type(witness) is not dict
                or set(witness) != {"dev", "ino", "size", "mtime_ns", "ctime_ns", "sha256"}
                or not _digest_text(witness["sha256"])
                or any(type(witness[name]) is not int or witness[name] < 0
                       for name in ("dev", "ino", "size", "mtime_ns", "ctime_ns"))):
            raise VersionedCheckpointRefused("Invalid migration source witness")
    if ((plan["source"] is None and any(sources.values()))
            or (plan["source"] is not None and sources[plan["source"]] is None)):
        raise VersionedCheckpointRefused("Inconsistent migration source selection")


def _validate_intent(raw, plan_raw, plan, prefix):
    intent = _decode(raw)
    if prefix.operation_id == plan["operation_id"]:
        if (raw != plan_raw or prefix.sequence != 1
                or prefix.generation != "m-" + plan["operation_id"]):
            raise VersionedCheckpointRefused("Initial operation intent differs")
        return
    if (type(intent) is not dict
            or set(intent) != {"kind", "operation_id", "expected", "suffix_sha256",
                               "suffix_bytes", "suffix_messages"}
            or intent["kind"] not in ("append", "rewrite")
            or intent["operation_id"] != prefix.operation_id
            or not _digest_text(intent["suffix_sha256"])
            or any(type(intent[name]) is not int or intent[name] < 0
                   for name in ("suffix_bytes", "suffix_messages"))):
        raise VersionedCheckpointRefused("Invalid selected operation intent")
    previous = _prefix_data(intent["expected"])
    if (previous.session_id != prefix.session_id or previous.sequence + 1 != prefix.sequence
            or previous.operation_id == prefix.operation_id):
        raise VersionedCheckpointRefused("Operation predecessor differs")
    if intent["kind"] == "append":
        if (prefix.generation != previous.generation
                or prefix.body != previous.body
                or prefix.committed_bytes != previous.committed_bytes + intent["suffix_bytes"]
                or prefix.message_count != previous.message_count + intent["suffix_messages"]):
            raise VersionedCheckpointRefused("Append result bounds differ from intent")
    elif (prefix.generation != "r-" + prefix.operation_id
          or prefix.message_count != intent["suffix_messages"]
          or prefix.committed_bytes != len(_header(prefix.generation)) + intent["suffix_bytes"]):
        raise VersionedCheckpointRefused("Rewrite result differs from intent")
    return intent


def _verify_intended_bytes(body, intent, prefix):
    if intent is None:  # Initialization/migration source ownership is the journal.
        return
    if intent["kind"] == "append":
        previous = _prefix_data(intent["expected"])
        body.seek(0)
        remaining, digest = previous.committed_bytes, hashlib.sha256()
        while remaining:
            raw = body.read(min(CHUNK, remaining))
            if not raw:
                raise VersionedCheckpointRefused("Operation predecessor exceeds selected body")
            remaining -= len(raw)
            digest.update(raw)
        if digest.hexdigest() != previous.sha256:
            raise VersionedCheckpointRefused("Operation predecessor digest differs")
    else:
        body.seek(len(_header(prefix.generation)))
    remaining, digest = intent["suffix_bytes"], hashlib.sha256()
    while remaining:
        raw = body.read(min(CHUNK, remaining))
        if not raw:
            raise VersionedCheckpointRefused("Operation suffix exceeds selected body")
        remaining -= len(raw)
        digest.update(raw)
    if body.tell() != prefix.committed_bytes or digest.hexdigest() != intent["suffix_sha256"]:
        raise VersionedCheckpointRefused("Operation suffix differs from intent")


def _control(path: Path):
    namespace = _namespace(path.parent)
    with _file(path) as stream:
        info = os.fstat(stream.fileno())
        if (info.st_nlink != 1 or info.st_mode & 0o077
                or info.st_uid != os.getuid()):
            raise VersionedCheckpointRefused("Checkpoint witness is not owner-private")
        stamp = _stamp(info)
        raw = stream.read(CHUNK + 1)
        if len(raw) > CHUNK:
            raise VersionedCheckpointRefused("Oversized checkpoint witness")
    if _namespace(path.parent) != namespace:
        raise VersionedCheckpointRefused("Checkpoint witness namespace changed")
    return (str(path), namespace, stamp, raw)


def _capture(path: Path, prefix: CommittedPrefix, *, unsettled=False):
    root = path / "transcript.v2"
    names = (
        "current.json", "migration.json", "ready.json",
        f"operations/{prefix.operation_id}/intent.json",
        f"operations/{prefix.operation_id}/result.json",
        f"operations/{prefix.operation_id}/published.json",
    )
    # Only a missing publication may be finished by the current-op _settle.
    # Validate transition/result controls before that narrow durability effect.
    controls = tuple(
        None if unsettled and name == names[5] and not os.path.lexists(root / name)
        else _control(root / name) for name in names
    ) + tuple(
        _control(path / name / "format.json")
        for name in ("transcript.jsonl", "transcript.jsonl.backup")
    )
    # All these paths are mandatory. Reader-only fixtures deliberately lack them.
    descriptor = _descriptor(prefix)
    if any(controls[index] is not None and controls[index][3] != descriptor
           for index in (0, 4, 5)):
        raise VersionedCheckpointRefused("Current operation result/publication differs")
    plan, ready = _decode(controls[1][3]), _decode(controls[2][3])
    _validate_plan(plan, prefix)
    intent = _validate_intent(controls[3][3], controls[1][3], plan, prefix)
    if ready != {"format": plan["format"], "v": 2,
                 "session_id": prefix.session_id, "operation_id": plan["operation_id"]}:
        raise VersionedCheckpointRefused("Writer transition is not ready")
    if type(ready.get("v")) is not int:
        raise VersionedCheckpointRefused("Invalid ready version")
    for control in controls[6:]:
        marker = _decode(control[3])
        if (marker != {"format": plan["format"], "v": 2,
                       "status": "unsupported-by-legacy-entrypoint",
                       "operation_id": plan["operation_id"]}
                or type(marker.get("v")) is not int):
            raise VersionedCheckpointRefused("Invalid writer legacy fence")
    with _file(root / prefix.body) as body:
        info = os.fstat(body.fileno())
        if (info.st_nlink != 1 or info.st_mode & 0o077
                or info.st_uid != os.getuid()):
            raise VersionedCheckpointRefused("Committed body is not owner-private")
        stamp = _stamp(info)
        # The pinned owner's semantic header equality treats 2.0 == 2.
        # Supplement its bounded check here until the owner ships strict typing;
        # no alternate descriptor or source authority is created by this guard.
        header = _decode(body.readline(min(CHUNK + 1, prefix.committed_bytes)))
        if type(header) is not dict or type(header.get("v")) is not int:
            raise VersionedCheckpointRefused("Generation header version must be an exact integer")
        _verify_intended_bytes(body, intent, prefix)
    return controls, stamp


@dataclass(frozen=True)
class VersionedCheckpointReceipt:
    """Issuer-bound live proof. verify() returns JSON facts, not portable authority.

    Use only after successful acknowledgement-context exit when reporting durable
    acceptance. Any exception from the context (including late fence checks)
    revokes this object, even if a caller retained it during the context.
    """
    session_id: str
    generation: str
    body: str
    sequence: int
    committed_bytes: int
    message_count: int
    sha256: str
    operation_id: str
    _session_dir: Path = field(repr=False)
    _controls: tuple = field(repr=False)
    _body_stamp: tuple = field(repr=False)
    _life: _Life = field(repr=False, compare=False)
    _seal: object = field(repr=False, compare=False)
    _attestation: str = field(repr=False, compare=False)
    _issuer_pid: int = field(repr=False, compare=False)
    kind: str = field(default=_KIND, init=False)

    def _local(self):
        if (type(self) is not VersionedCheckpointReceipt
                or _ISSUED.get(id(self)) is not self
                or self._issuer_pid != os.getpid() or self._seal is not _SEAL
                or type(self._life) is not _Life or not self._life.valid):
            raise VersionedCheckpointRefused("Not a live local versioned checkpoint")
        try:
            valid = hmac.compare_digest(self._attestation, _attest(self))
        except (TypeError, ValueError, AttributeError):
            valid = False
        if not valid:
            raise VersionedCheckpointRefused("Invalid versioned checkpoint attestation")

    def _prefix(self):
        return CommittedPrefix(*(getattr(self, item.name) for item in fields(CommittedPrefix)))

    def verify(self) -> dict[str, Any]:
        """Cold-check committed hash/header/rows and all pinned identities/witnesses."""
        self._local()
        try:
            prefix = self._prefix()
            if _capture(self._session_dir, prefix) != (self._controls, self._body_stamp):
                raise VersionedCheckpointRefused("Versioned checkpoint source changed")
            with open_committed_source(self._session_dir, expected=prefix) as source:
                source._check()
            if _capture(self._session_dir, prefix) != (self._controls, self._body_stamp):
                raise VersionedCheckpointRefused("Versioned checkpoint changed during verification")
            self._local()
        except (OSError, ValueError) as exc:
            raise VersionedCheckpointRefused("Versioned checkpoint source refused") from exc
        return {item.name: getattr(self, item.name) for item in fields(CommittedPrefix)} | {
            "kind": self.kind,
        }

    def iter_refs(self) -> Iterator[dict[str, Any]]:
        """Stream only top canonical metadata refs; exhaust before accepting any.

        The final verification runs *after* the last yielded ref. Partial iterator
        consumption is not a completed acknowledgement. Tail bytes never enter
        the scanner, but any post-pin body change (including tail-only) refuses.
        """
        self.verify()
        try:
            with _file(self._session_dir / "transcript.v2" / self.body) as body:
                if _stamp(os.fstat(body.fileno())) != self._body_stamp:
                    raise VersionedCheckpointRefused("Versioned body changed")
                scanner = Scanner(_PrefixView(body, self.committed_bytes))
                for index, row in enumerate(scanner.records()):
                    if index == 0:
                        continue  # Header/schema/count already checked by source owner.
                    metadata = scanner.fields(row).get("metadata")
                    if metadata is None or (metadata.kind == 110 and scanner.small(metadata) is None):
                        continue
                    envelope = scanner.fields(metadata).get(MEDIA_NAMESPACE)
                    if envelope is None:
                        continue
                    members = scanner.fields(envelope)
                    if "v" in members:
                        version = scanner.small(members["v"], 128)
                        if type(version) is not int or version != 1:
                            raise VersionedCheckpointRefused("Invalid media namespace version")
                    refs = members.get("refs")
                    if refs is None:
                        raise VersionedCheckpointRefused("Media namespace has no refs")
                    for span in scanner.array(refs):
                        # A single ref is a small, strict F1 control object. Never
                        # load an entire metadata value, array or message/string.
                        ref = scanner.small(span, CHUNK)
                        validate_blob_ref(ref)
                        if ref["session_id"] != self.session_id:
                            raise VersionedCheckpointRefused("Media ref belongs to another session")
                        yield ref
            self.verify()
        except (OSError, ValueError) as exc:
            raise VersionedCheckpointRefused("Versioned media references refused") from exc


def _attest(receipt: VersionedCheckpointReceipt) -> str:
    # Bind every public/private field, including process/object/lifecycle identity.
    controls = [(name, namespace, stamp, raw.hex())
                for name, namespace, stamp, raw in receipt._controls]
    payload = [receipt.kind, *(getattr(receipt, item.name) for item in fields(CommittedPrefix)),
               str(receipt._session_dir), controls, receipt._body_stamp,
               id(receipt), id(receipt._seal), id(receipt._life), receipt._life.valid,
               receipt._issuer_pid]
    return hmac.new(_KEY, _json(payload), hashlib.sha256).hexdigest()


def verified_versioned_checkpoint_refs(receipt: VersionedCheckpointReceipt) -> Iterator[dict[str, Any]]:
    """Explicit V2-only SessionMediaStore checkpoint_verifier callback."""
    if type(receipt) is not VersionedCheckpointReceipt:
        raise VersionedCheckpointRefused("Expected exact local versioned checkpoint receipt")
    yield from receipt.iter_refs()


@contextmanager
def acknowledge_versioned_checkpoint(
    writer: TranscriptWriter, *, session_id: str, expected: CommitAck | CommittedPrefix,
) -> Iterator[VersionedCheckpointReceipt]:
    """Acknowledge actual current writer bytes under its real ownership fence.

    The unsealed expected dataclass only selects the current commit. This never
    calls admission, append, rewrite, migration or tool execution. _settle only
    finishes the current operation's existing durability barriers/publication.
    Asset admission BEFORE reachability is still a separate host obligation; this
    proof cannot retroactively attest to what a zero-argument admit() checked.
    """
    if type(writer) is not TranscriptWriter:
        raise VersionedCheckpointRefused("Expected actual TranscriptWriter")
    if type(expected) is CommitAck:
        expected = expected.commit
    if type(expected) is not CommittedPrefix:
        raise VersionedCheckpointRefused("Expected explicit committed-prefix selector")
    _identity(session_id)
    if (not writer.session_dir.is_absolute() or ".." in writer.session_dir.parts
            or expected.session_id != session_id):
        raise VersionedCheckpointRefused("Invalid checkpoint session/path selection")
    life = _Life()
    receipt = None
    try:
        with TranscriptWriter._owned(writer) as (session, check):
            with session.directory("transcript.v2") as root:
                # Always cold verify through the source owner, never trust a warm
                # writer cache or an unsealed expected object as source authority.
                with open_committed_source(writer.session_dir, expected=expected) as source:
                    prefix = source.commit
                    if _descriptor(prefix) != _descriptor(expected):
                        raise VersionedCheckpointRefused("Commit selector differs")
                    _capture(writer.session_dir, prefix, unsettled=True)
                    with root.directory("operations") as operations:
                        TranscriptWriter._settle(root, operations, prefix, check)
                        with operations.directory(prefix.operation_id) as operation:
                            if _receipt(operation, prefix.operation_id, session_id) != prefix:
                                raise VersionedCheckpointRefused("Current operation differs")
                    controls, stamp = _capture(writer.session_dir, prefix)
                    if stamp != source._stamp:
                        raise VersionedCheckpointRefused("Source changed before checkpoint pin")
                    receipt = VersionedCheckpointReceipt(
                        *(getattr(prefix, item.name) for item in fields(CommittedPrefix)),
                        writer.session_dir, controls, stamp, life, _SEAL, "", os.getpid(),
                    )
                    object.__setattr__(receipt, "_attestation", _attest(receipt))
                    _ISSUED[id(receipt)] = receipt
                    # Ref schema errors must also refuse a receipt with zero refs
                    # bound; exhaust the exact same scanner used at acceptance.
                    for _ in receipt.iter_refs():
                        pass
                    check()
                    yield receipt
                    receipt.verify()
                    source._check()
            check()
        # Fence/directory/lock exit checks can still fail. Only now is reporting
        # outside the context permitted; verify again after their successful exit.
        receipt.verify()
    except BaseException as exc:
        life.valid = False
        if receipt is not None:
            _ISSUED.pop(id(receipt), None)
        if isinstance(exc, (OSError, UnsupportedTranscript)):
            raise VersionedCheckpointRefused("Owned versioned checkpoint refused") from exc
        raise
