"""Opt-in POSIX canonical-v1 candidate activation under the actual host writer.

No writer lock, admission policy, asset authority or live-data root is invented
here. The caller supplies those owners. Journals contain facts, never authority;
resuming always reacquires admission and a fresh local checkpoint/media ACK.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import stat
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import asdict
from pathlib import Path

from . import media_conversion as conversion
from .checkpoint import LegacyCheckpointReceipt, acknowledge_candidate_checkpoint

_JOURNAL = "activation.json"
_FORMAT = "foundation.canonical-media-activation.v1"
_BLOCK = 64 * 1024
_PHASES = {"preparing", "ready", "replace-intent", "ack-intent", "acknowledged"}


class ActivationRefused(ValueError):
    """Activation could not safely proceed; preserve every recovery file."""


@dataclass(frozen=True)
class ActivationResult:
    state: str
    sha256: str
    bytes: int
    records: int
    checkpoint: LegacyCheckpointReceipt | None = None


@contextmanager
def _directory(path: str | Path) -> Iterator[tuple[Path, int, Callable]]:
    path = Path(path)
    if os.name != "posix" or not path.is_absolute() or ".." in path.parts:
        raise ActivationRefused("Explicit absolute POSIX directories required")
    descriptors = []
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        current = os.open(path.anchor, flags)
        descriptors.append(current)
        for component in path.parts[1:]:
            current = os.open(component, flags, dir_fd=current)
            descriptors.append(current)

        def check():
            for index in range(1, len(descriptors)):
                named = os.stat(path.parts[index], dir_fd=descriptors[index - 1],
                                follow_symlinks=False)
                opened = os.fstat(descriptors[index])
                if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
                    raise ActivationRefused("Activation directory identity changed")

        yield path, current, check
        check()
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _small(directory: int, name: str, limit: int = 16384) -> dict:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=directory)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ActivationRefused("Activation metadata must be a regular file")
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ActivationRefused("Activation metadata exceeds its owner bound")

    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ActivationRefused("Duplicate activation metadata member")
            value[key] = item
        return value

    value = json.loads(raw, object_pairs_hook=pairs)
    if not isinstance(value, dict):
        raise ActivationRefused("Invalid activation metadata")
    return value


def _journal_bytes(value: dict) -> bytes:
    raw = json.dumps(value, sort_keys=True, allow_nan=False).encode()
    if len(raw) > 16384:
        raise ActivationRefused("Activation journal exceeds its recovery read bound")
    return raw


def _journal(directory: int, value: dict) -> None:
    # Atomic small fact update. A failed/unknown write must be read back, not
    # inferred absent. Temporary journal bytes are retained on failure.
    raw = _journal_bytes(value)  # Refuse before even creating a temporary.
    name = ".activation-journal-" + uuid.uuid4().hex
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
    with os.fdopen(descriptor, "wb") as output:
        output.write(raw)
        output.flush()
        os.fsync(output.fileno())
    os.replace(name, _JOURNAL, src_dir_fd=directory, dst_dir_fd=directory)
    os.fsync(directory)


def _pending(directory: int) -> None:
    for name in ("transcript.jsonl.append-pending", "transcript.commit.json"):
        try:
            os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            continue
        raise ActivationRefused("Pending append or separately gated transcript layout")


def _guard(admit, cancelled, checks) -> bool:
    for check in checks:
        check()
    if admit() is not True:
        raise ActivationRefused("Current host writer/source/recovery admission refused")
    for check in checks:
        check()
    result = cancelled()
    if type(result) is not bool:
        raise ActivationRefused("Cancellation callback must return a synchronous boolean")
    return result


def _facts(operation: Path, opfd: int, session: Path, session_id: str, owner: str):
    if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,220}", session_id):
        raise ActivationRefused("Invalid session identity")
    if not isinstance(owner, str) or not owner:
        raise ActivationRefused("Explicit host owner required")
    conversion.reconcile(operation)  # Candidate, manifest, plan AND originals.
    verified = _small(opfd, "verified.json")
    plan = _small(opfd, "plan.json", 1024 * 1024)
    source = conversion.SourceIdentity(**plan["source"])
    if (plan["adapter"] != conversion.ADAPTER or verified["adapter"] != conversion.ADAPTER
            or plan["owner"] != owner or plan["session_id"] != session_id
            or Path(source.path) != session / "transcript.jsonl"
            or plan["selected_records"] != plan["records"]
            or verified["records"] != plan["records"]):
        raise ActivationRefused("Only the exact full same-owner native-v1 primary may activate")
    for key in ("bytes", "records"):
        if type(verified[key]) is not int or verified[key] < 0:
            raise ActivationRefused("Invalid candidate facts")
    if not isinstance(verified["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", verified["sha256"]):
        raise ActivationRefused("Invalid candidate digest")
    binding = {
        "format": _FORMAT, "operation": str(operation), "session": str(session),
        "session_id": session_id, "owner": owner,
        "candidate_sha256": verified["sha256"], "candidate_bytes": verified["bytes"],
        "records": verified["records"], "source": plan["source"],
        "plan_sha256": verified["plan_sha256"],
        "manifest_sha256": verified["manifest_sha256"],
    }
    # Check the largest phase plus fixed-width stage before any activation
    # write, so every journal we can create can also be recovered.
    _journal_bytes({"binding": binding, "phase": "replace-intent",
                    "stage": ".media-activation-" + "0" * 32 + ".tmp"})
    return binding, plan, source


def _saved(opfd: int, binding: dict) -> dict | None:
    try:
        saved = _small(opfd, _JOURNAL)
    except FileNotFoundError:
        return None
    if saved.get("binding") != binding or saved.get("phase") not in _PHASES:
        raise ActivationRefused("Activation journal conflicts with this exact operation")
    if set(saved) != {"binding", "phase", "stage"} or not isinstance(saved["stage"], str) or not re.fullmatch(
        r"\.media-activation-[0-9a-f]{32}\.tmp", saved["stage"]
    ):
        raise ActivationRefused("Invalid activation journal")
    return saved


def _backups(plan):
    for item in plan["backups"]:
        conversion._check(conversion.SourceIdentity(**item))


def _verify(operation, installed, session_id, asset_reader):
    count = 0
    for _ in conversion.iter_verified_candidate_refs(
        operation, installed=installed, session_id=session_id, asset_reader=asset_reader,
    ):
        count += 1
    return count


def _durable_reader(reader):
    @contextmanager
    def opened(ref):
        # The real publication owner has already fsynced asset directories.
        # Verify and flush the actual regular asset descriptor, not a path label.
        with reader(ref) as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ActivationRefused("Asset reader must yield a published regular file")
            observed = conversion._digest(stream)
            after = os.fstat(stream.fileno())
            if (conversion._stamp(stream) != (
                before.st_dev, before.st_ino, before.st_size,
                before.st_mtime_ns, before.st_ctime_ns,
            ) or observed != (ref["sha256"], ref["bytes"])):
                raise ActivationRefused("Published asset changed before activation")
            os.fsync(stream.fileno())
            stream.seek(0)
            yield stream
            if conversion._stamp(stream) != (
                after.st_dev, after.st_ino, after.st_size,
                after.st_mtime_ns, after.st_ctime_ns,
            ):
                raise ActivationRefused("Published asset changed during activation")
    return opened


def _copy(operation, sessionfd, stage, expected, cancelled, mtime_ns):
    descriptor = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=sessionfd)
    digest, size = hashlib.sha256(), 0
    with os.fdopen(descriptor, "wb") as output, conversion._open(operation / "candidate.jsonl") as source:
        while chunk := source.read(_BLOCK):
            if cancelled() is True:
                return False
            output.write(chunk)
            digest.update(chunk)
            size += len(chunk)
        output.flush()
        # Optimize is not new conversation activity. Preserve original primary
        # mtime without writing metadata or renewing any media observation TTL.
        os.utime(output.fileno(), ns=(mtime_ns, mtime_ns))
        os.fsync(output.fileno())
    if (digest.hexdigest(), size) != expected:
        raise ActivationRefused("Candidate changed during bounded staging copy")
    os.fsync(sessionfd)
    return True


def _result(binding, state, receipt=None):
    return ActivationResult(state, binding["candidate_sha256"],
                            binding["candidate_bytes"], binding["records"], receipt)


def reconcile_activation(operation_dir: Path, session_dir: Path, *, session_id: str,
                         owner: str) -> ActivationResult:
    """Read-only reconciliation. Never replace, fsync, invoke ACK or infer authority."""
    with _directory(operation_dir) as (operation, opfd, _), _directory(session_dir) as (session, sessionfd, _):
        binding, _, source = _facts(operation, opfd, session, session_id, owner)
        saved = _saved(opfd, binding)
        _pending(sessionfd)
        current = conversion._identity(session / "transcript.jsonl")
        matched = (current.sha256, current.size) == (binding["candidate_sha256"], binding["candidate_bytes"])
        if saved is None:
            state = "not-started" if current == source else "conflict"
        elif saved["phase"] in {"preparing", "ready"}:
            state = "ready-to-resume" if current == source else "conflict"
        elif matched:
            state = "acknowledged-on-disk-reverify-required" if saved["phase"] == "acknowledged" else "installed-awaiting-ack"
        elif current == source:
            state = "replace-unconfirmed-source-present"
        else:
            state = "conflict"
        return _result(binding, state)


def apply_candidate(operation_dir: Path, session_dir: Path, *, session_id: str,
                    owner: str, fence, admit: Callable[[], bool], asset_reader,
                    commit_media, acknowledge=acknowledge_candidate_checkpoint,
                    cancelled: Callable[[], bool] = lambda: False) -> ActivationResult:
    """Install once, then real checkpoint + media-store ACK. Existing journal refuses.

    fence(owner) must own/quiesce the actual host writer, not merely flock a file.
    admit() rechecks current owner/source/recovery and published asset-directory
    durability. commit_media is the bound trusted store.acknowledge_checkpoint.
    """
    return _activate(operation_dir, session_dir, session_id=session_id, owner=owner,
                     fence=fence, admit=admit, asset_reader=asset_reader,
                     commit_media=commit_media, acknowledge=acknowledge,
                     cancelled=cancelled, resume=False)


def resume_candidate(operation_dir: Path, session_dir: Path, *, session_id: str,
                     owner: str, fence, admit: Callable[[], bool], asset_reader,
                     commit_media, acknowledge=acknowledge_candidate_checkpoint,
                     cancelled: Callable[[], bool] = lambda: False) -> ActivationResult:
    """Resume safe preparation or ACK exact installed bytes; NEVER replay a replace.

    An intent with original primary still present is explicitly unconfirmed and
    requires owner reconciliation outside this helper, not a second os.replace.
    """
    return _activate(operation_dir, session_dir, session_id=session_id, owner=owner,
                     fence=fence, admit=admit, asset_reader=asset_reader,
                     commit_media=commit_media, acknowledge=acknowledge,
                     cancelled=cancelled, resume=True)


def _activate(operation_dir, session_dir, *, session_id, owner, fence, admit,
              asset_reader, commit_media, acknowledge, cancelled, resume):
    callbacks = (fence, admit, asset_reader, commit_media, acknowledge, cancelled)
    if any(not callable(item) or inspect.iscoroutinefunction(item) or inspect.isgeneratorfunction(item)
           for item in callbacks[1:2] + callbacks[3:]):
        raise ActivationRefused("Synchronous admission/ACK/cancellation callbacks required")
    with fence(owner), _directory(operation_dir) as (operation, opfd, checkop), _directory(session_dir) as (session, sessionfd, checksess):
        checks = (checkop, checksess)
        binding, plan, source = _facts(operation, opfd, session, session_id, owner)
        saved = _saved(opfd, binding)
        if saved is not None and not resume:
            raise ActivationRefused("Saved activation exists; reconcile/resume, never replay apply")
        if _guard(admit, cancelled, checks):
            return _result(binding, "cancelled-preserved")
        _pending(sessionfd)
        _backups(plan)
        installed = session / "transcript.jsonl"
        reader = _durable_reader(asset_reader)
        if (saved is None or saved["phase"] in {"preparing", "ready"}) and (
            source.sha256, source.size
        ) == (binding["candidate_sha256"], binding["candidate_bytes"]):
            # Byte-identical candidates need a fresh real ACK, not a pointless
            # rewrite/new inode/activity timestamp.
            conversion._check(source)
            _verify(operation, installed, session_id, reader)
            if _guard(admit, cancelled, checks):
                return _result(binding, "cancelled-preserved")
            saved = {"binding": binding, "phase": "ack-intent",
                     "stage": ".media-activation-" + uuid.uuid4().hex + ".tmp"}
            _journal(opfd, saved)
        if saved is None or saved["phase"] in {"preparing", "ready"}:
            conversion._check(source)
            _verify(operation, operation / "candidate.jsonl", session_id, reader)
            if _guard(admit, cancelled, checks):
                return _result(binding, "cancelled-preserved")
            # Every retry before replace intent gets a fresh stage. Old partial
            # stages and their small provenance notes remain for owner recovery.
            stage = ".media-activation-" + uuid.uuid4().hex + ".tmp"
            saved = {"binding": binding, "phase": "preparing", "stage": stage}
            note = "activation-stage-" + stage.removeprefix(".media-activation-").removesuffix(".tmp") + ".json"
            fd = os.open(note, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=opfd)
            with os.fdopen(fd, "wb") as output:
                output.write(json.dumps({"stage": stage, "session": str(session)}).encode())
                output.flush()
                os.fsync(output.fileno())
            _journal(opfd, saved)
            if not _copy(operation, sessionfd, stage,
                         (binding["candidate_sha256"], binding["candidate_bytes"]), cancelled,
                         source.mtime_ns):
                return _result(binding, "cancelled-preserved")
            saved["phase"] = "ready"
            _journal(opfd, saved)
            # Copy readback and complete closure, not just bytes counted on write.
            _verify(operation, session / stage, session_id, reader)
            staged = conversion._identity(session / stage)
            if _guard(admit, cancelled, checks):
                return _result(binding, "cancelled-preserved")
            _pending(sessionfd)
            _backups(plan)
            conversion._check(source)
            saved["phase"] = "replace-intent"
            _journal(opfd, saved)  # Durable BEFORE the sole uncertain mutation.
            if _guard(admit, cancelled, checks):
                return _result(binding, "replace-unconfirmed-source-present")
            conversion._check(staged)
            _backups(plan)
            conversion._check(source)
            _pending(sessionfd)
            # Hash scans may be lengthy. Revoke/cancel during them must prevent
            # the mutation, not merely be noticed after it has happened.
            if _guard(admit, cancelled, checks):
                return _result(binding, "replace-unconfirmed-source-present")
            _pending(sessionfd)
            os.replace(stage, "transcript.jsonl", src_dir_fd=sessionfd, dst_dir_fd=sessionfd)
            os.fsync(sessionfd)
        else:
            current = conversion._identity(installed)
            if (current.sha256, current.size) != (binding["candidate_sha256"], binding["candidate_bytes"]):
                if current == source:
                    return _result(binding, "replace-unconfirmed-source-present")
                raise ActivationRefused("Actual primary conflicts with saved replacement intent")
        # Includes recovery after replace/fsync/readback/ACK failure or lost reply.
        # No primary mutation occurs on this path; ACK is a local revalidation,
        # never a tool replay or a second history save.
        if _guard(admit, cancelled, checks):
            return _result(binding, "installed-awaiting-ack")
        _pending(sessionfd)
        _backups(plan)
        ref_count = _verify(operation, installed, session_id, reader)
        os.fsync(sessionfd)
        saved["phase"] = "ack-intent"
        _journal(opfd, saved)
        if _guard(admit, cancelled, checks):
            return _result(binding, "installed-awaiting-ack")
        receipt = acknowledge(operation, session, session_id=session_id, asset_reader=reader)
        if type(receipt) is not LegacyCheckpointReceipt or receipt._session_dir != session:
            raise ActivationRefused("ACK must return the actual local canonical checkpoint receipt")
        facts = receipt.verify()
        if (facts["session_id"], facts["sha256"], facts["prefix_bytes"], facts["message_count"]) != (
            session_id, binding["candidate_sha256"], binding["candidate_bytes"], binding["records"],
        ):
            raise ActivationRefused("ACK does not bind the actual installed candidate")
        if _guard(admit, cancelled, checks):
            return _result(binding, "installed-awaiting-ack")
        committed = commit_media(receipt)
        if (not isinstance(committed, dict) or committed.get("state") != "committed"
                or committed.get("durability") != "legacy-checkpoint-backed"
                or type(committed.get("ref_count")) is not int
                or committed["ref_count"] != ref_count):
            raise ActivationRefused("Synchronous durable media-store ACK required")
        receipt.verify()
        _verify(operation, installed, session_id, reader)
        _backups(plan)
        if _guard(admit, cancelled, checks):
            return _result(binding, "installed-awaiting-ack")
        saved["phase"] = "acknowledged"
        _journal(opfd, saved)
        return _result(binding, "acknowledged", receipt)


@dataclass(frozen=True)
class CommittedActivationResult:
    """Actual T2 result: installed commit is distinct from candidate identity."""

    state: str
    commit: object | None
    checkpoint: object | None = None
    inline_removed_bytes: int = 0
    held_records: int = 0


_COMMITTED_JOURNAL = "committed-activation.json"


def _committed_facts(operation, binding, expected, operation_id, owner):
    from .transcript_lifetime import VersionedSessionBinding
    from .transcript_v2 import CommittedPrefix
    if type(binding) is not VersionedSessionBinding or type(expected) is not CommittedPrefix:
        raise ActivationRefused("Actual prospective binding and exact committed predecessor required")
    conversion._prefix_types(expected)
    if (type(operation_id) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,179}", operation_id)
            or not owner):
        raise ActivationRefused("Stable original operation identity and owner required")
    binding.check()
    facts = conversion.candidate_facts(operation)
    plan = conversion._saved_plan(operation)
    target = plan.target_prefix is not None
    selected_prefix = plan.target_prefix if target else plan.committed_prefix
    selected_directory = plan.target_session_dir if target else plan.committed_session_dir
    if (plan.owner != owner or selected_prefix != asdict(expected)
            or selected_directory != str(binding.directory)
            or expected.session_id != binding.session_id
            or (not target and plan.selected_records != plan.records)):
        raise ActivationRefused("Only exact full same-owner prospective source may activate")
    return facts, plan


def _committed_result(facts, state, binding, receipt=None):
    return CommittedActivationResult(
        state, None if receipt is None else binding.expected, receipt,
        facts["inline_removed_bytes"], facts.get("held_records", 0),
    )


def _committed_receipt(binding, receipt, expected_result=None):
    from .versioned_checkpoint import VersionedCheckpointReceipt
    if type(receipt) is not VersionedCheckpointReceipt:
        raise ActivationRefused("Actual source-bound sealed versioned checkpoint required")
    facts = receipt.verify()
    expected_result = binding.expected if expected_result is None else expected_result
    if (binding.expected != expected_result
            or facts != asdict(expected_result) | {"kind": "versioned-checkpoint-backed-v1"}):
        raise ActivationRefused("Sealed checkpoint differs from actual binding result")
    # Independently exhaust; no success from a lazy receipt verifier.
    for _ in receipt.iter_refs():
        pass
    return receipt


def _committed_journal(opfd, value):
    raw = _journal_bytes(value)
    name = ".committed-activation-" + uuid.uuid4().hex
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=opfd)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(name, _COMMITTED_JOURNAL, src_dir_fd=opfd, dst_dir_fd=opfd)
    os.fsync(opfd)


def apply_committed_candidate(operation_dir: Path, *, binding, expected, operation_id: str,
                              owner: str, media_store, admit,
                              cancelled=lambda: False) -> CommittedActivationResult:
    """Use only the real prospective binding's serialized writer and sealed ACK.

    A saved intent is never replayed here. Callback admission must settle real
    host authorization/publication. It is not source/writer exclusion by itself.
    """
    return _activate_committed(operation_dir, binding=binding, expected=expected,
                               operation_id=operation_id, owner=owner, media_store=media_store,
                               admit=admit, cancelled=cancelled, resume=False)


def resume_committed_candidate(operation_dir: Path, *, binding, expected, operation_id: str,
                               owner: str, media_store, admit,
                               cancelled=lambda: False) -> CommittedActivationResult:
    """Reconcile only the original exact intent; never rebuild/rewrite or replay."""
    return _activate_committed(operation_dir, binding=binding, expected=expected,
                               operation_id=operation_id, owner=owner, media_store=media_store,
                               admit=admit, cancelled=cancelled, resume=True)


def reconcile_committed_candidate(operation_dir: Path, *, binding, expected, operation_id: str,
                                  owner: str) -> CommittedActivationResult:
    """Read-only current-source facts; no writer reconcile, ACK or authority."""
    from .transcript_source import open_committed_source
    with _directory(operation_dir) as (operation, opfd, checkop):
        facts, _ = _committed_facts(operation, binding, expected, operation_id, owner)
        try:
            saved = _small(opfd, _COMMITTED_JOURNAL)
        except FileNotFoundError:
            saved = None
        with open_committed_source(binding.directory) as source:
            current = source.commit
            if saved is None:
                state = "not-started" if current == expected else "conflict"
            else:
                identity = saved.get("identity", {})
                if (identity.get("expected") != asdict(expected)
                        or identity.get("operation_id") != operation_id
                        or identity.get("owner") != owner
                        or identity.get("candidate_sha256") != facts["sha256"]):
                    raise ActivationRefused("Saved committed activation conflicts with selected operation")
                if saved.get("kind") == "checkpoint":
                    matched = current == expected
                else:
                    start = source.message_range(0).start if current.message_count else current.committed_bytes
                    digest = hashlib.sha256()
                    size = 0
                    for raw in source.iter_bytes(start):
                        digest.update(raw)
                        size += len(raw)
                    matched = (
                        current.operation_id == operation_id and current.message_count == saved["records"]
                        and (digest.hexdigest(), size) == (saved["canonical_sha256"], saved["canonical_bytes"])
                    )
                if matched:
                    state = "installed-awaiting-versioned-ack"
                else:
                    state = "intent-unconfirmed-no-replay" if current == expected else "conflict"
            source.read_range(0, 0)
        binding.check()
        checkop()
        return CommittedActivationResult(state, current, None, facts["inline_removed_bytes"],
                                         facts.get("held_records", 0))


def _activate_committed(operation_dir, *, binding, expected, operation_id, owner, media_store,
                        admit, cancelled, resume):
    from .transcript_writer import prepare_serialized_candidate
    from .transcript_v2 import CommittedPrefix
    for callback in (admit, cancelled):
        if (not callable(callback) or inspect.iscoroutinefunction(callback)
                or inspect.isgeneratorfunction(callback)):
            raise ActivationRefused("Settled synchronous admission and cancellation required")
    with _directory(operation_dir) as (operation, opfd, checkop):
        facts, plan = _committed_facts(operation, binding, expected, operation_id, owner)
        if facts.get("held_records", 0) and not facts["images"]:
            return _committed_result(facts, "held-original-policy", binding)

        def guard():
            checkop()
            binding.check()
            if admit() is not True:
                raise ActivationRefused("Current prospective activation admission refused")
            decision = cancelled()
            if type(decision) is not bool:
                raise ActivationRefused("Cancellation must be a synchronous boolean")
            if decision:
                raise InterruptedError("Committed conversion cancelled; reconcile saved intent if present")

        identity = {
            "format": "foundation.committed-media-activation.v1",
            "session_id": binding.session_id, "session_dir": str(binding.directory),
            "owner": owner, "expected": asdict(expected), "operation_id": operation_id,
            "candidate_sha256": facts["sha256"], "candidate_bytes": facts["bytes"],
            "plan_sha256": facts["plan_sha256"], "manifest_sha256": facts["manifest_sha256"],
        }
        try:
            saved = _small(opfd, _COMMITTED_JOURNAL)
        except FileNotFoundError:
            saved = None
        if saved is not None and (saved.get("identity") != identity or set(saved) != {
            "identity", "kind", "canonical_sha256", "canonical_bytes", "records", "phase",
        }):
            raise ActivationRefused("Saved committed activation conflicts with original intent")
        if saved is not None and not resume:
            raise ActivationRefused("Saved activation exists; reconcile/resume, never replay apply")
        if resume and saved is None:
            raise ActivationRefused("No original committed activation intent to reconcile")
        guard()
        # Recovery facts are checked against fresh format-owner preparation,
        # never trusted because a mutable JSON journal says "checkpoint".
        with conversion.open_verified_candidate(
            operation, session_id=binding.session_id,
            expected_source=CommittedPrefix(**plan.committed_prefix),
            asset_reader=lambda ref: media_store.resolve_ref(ref, committed=False),
        ) as (witness_stream, witness_prefix, witness_check):
            with prepare_serialized_candidate(
                witness_stream, candidate_prefix=witness_prefix,
                candidate_check=lambda: (guard(), witness_check()),
            ) as witness:
                intent = {
                    "kind": "checkpoint" if (facts["sha256"], facts["bytes"]) == (
                        expected.sha256, expected.committed_bytes
                    ) else "rewrite",
                    "canonical_sha256": witness.canonical_sha256,
                    "canonical_bytes": witness.canonical_bytes,
                    "records": witness.message_count,
                }
        if saved is not None and any(
            type(saved.get(key)) is not type(value) or saved[key] != value
            for key, value in intent.items()
        ):
            raise ActivationRefused("Saved activation intent differs from actual prepared candidate")
        receipt = None
        if saved is not None:
            if saved["phase"] not in {"intent", "acknowledged"}:
                raise ActivationRefused("Unknown committed activation state")
            if saved["kind"] == "checkpoint":
                receipt = binding.checkpoint(expected=expected, media_store=media_store)
            elif saved["kind"] == "rewrite":
                ack = binding.reconcile(operation_id=operation_id, expected=expected,
                                        suffix_sha256=saved["canonical_sha256"], kind="rewrite",
                                        media_store=media_store)
                if ack is None:
                    return _committed_result(facts, "unconfirmed-no-replay", binding)
                if ack.commit != binding.expected:
                    return CommittedActivationResult(
                        "superseded-original-operation", ack.commit, None,
                        facts["inline_removed_bytes"], facts.get("held_records", 0),
                    )
                receipt = binding.last_receipt
            else:
                raise ActivationRefused("Unknown original versioned operation kind")
            _committed_receipt(binding, receipt)
            guard()
        else:
            with conversion.open_verified_candidate(
                operation, session_id=binding.session_id,
                expected_source=CommittedPrefix(**plan.committed_prefix),
                asset_reader=lambda ref: media_store.resolve_ref(ref, committed=False),
            ) as (stream, prefix, candidate_check):
                # The format owner defines canonical intent bytes. Header SHA
                # cannot be substituted for its row fingerprint.
                def checked():
                    guard()
                    candidate_check()
                with prepare_serialized_candidate(
                    stream, candidate_prefix=prefix, candidate_check=checked,
                ) as prepared:
                    saved = {
                        "identity": identity,
                        "kind": "checkpoint" if (facts["sha256"], facts["bytes"]) == (
                            expected.sha256, expected.committed_bytes
                        ) else "rewrite",
                        "canonical_sha256": prepared.canonical_sha256,
                        "canonical_bytes": prepared.canonical_bytes,
                        "records": prepared.message_count, "phase": "intent",
                    }
                guard()
                _committed_journal(opfd, saved)  # Before ANY canonical mutation.
                guard()  # Cancellation during journal fsync must stop before ACK/write.
                if saved["kind"] == "checkpoint":
                    receipt = binding.checkpoint(expected=expected, media_store=media_store)
                else:
                    # This method owns its own immutable preparation, strict
                    # actual-store admission and typed-ACK context through exit.
                    binding.rewrite_serialized(
                        stream, candidate_prefix=prefix, candidate_check=checked,
                        expected=expected, operation_id=operation_id,
                        admit=guard, media_store=media_store,
                    )
                    receipt = binding.last_receipt
                _committed_receipt(binding, receipt)
                guard()
            # An outer candidate-context failure must never produce success.
        guard()
        saved["phase"] = "acknowledged"
        _committed_journal(opfd, saved)
        return _committed_result(facts, "acknowledged", binding, receipt)
