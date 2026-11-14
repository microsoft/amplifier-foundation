"""Synthetic streamed-candidate boundary checks on the existing rewrite engine."""

import asyncio
import hashlib
import io
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from amplifier_foundation.session import transcript_writer as w
from amplifier_foundation.session.transcript_v2 import CommittedTranscriptReader

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX serialized candidate boundary")
ROWS = [{"role": "user", "content": "preserve"},
        {"role": "tool", "content": "result", "metadata": {"opaque": ["signature", 42]}}]


@contextmanager
def fence(path):
    with (path / ".host-owner").open("a+b") as lease:
        w.fcntl.flock(lease.fileno(), w.fcntl.LOCK_EX)
        yield lambda: None


def owner(path):
    path.mkdir()
    writer = w.TranscriptWriter(path, fence=fence)
    initial = writer.initialize(session_id="fixture", operation_id="init", admit=lambda: None).commit
    return writer, writer.append(ROWS, expected=initial, operation_id="initial",
                                 admit=lambda: None).commit


def candidate(path, rows=ROWS, *, raw=None):
    if raw is None:
        # Deliberately noncanonical spaces/order/UTF8: witness is raw input bytes,
        # operation identity is normalized canonical rows, never this header.
        raw = w._header("candidate") + b"".join(
            (json.dumps(row, ensure_ascii=False) + "\n").encode() for row in rows)
    path.write_bytes(raw)
    prefix = w.CommittedPrefix(
        "fixture", "candidate", "g-candidate.jsonl", 1, len(raw), len(rows),
        hashlib.sha256(raw).hexdigest(), "candidate-build")
    return prefix


def inventory(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def row_digest(rows):
    return hashlib.sha256(b"".join(
        b"".join(w.canonical_bytes(row)) + b"\n" for row in rows)).hexdigest()


def test_prepared_witness_same_bytes_admit_and_existing_reconcile(tmp_path):
    writer, previous = owner(tmp_path / "session")
    rows = [{"role": "assistant", "content": "é😀", "metadata": {"z": [1, True]}}]
    path = tmp_path / "candidate"
    prefix = candidate(path, rows)
    before = inventory(writer.session_dir)
    with path.open("rb") as stream:
        stream.seek(7)
        with w.prepare_serialized_candidate(
            stream, candidate_prefix=prefix, candidate_check=lambda: None,
        ) as prepared:
            assert stream.tell() == 7
            assert prepared.candidate_prefix == prefix
            assert prepared.message_count == 1
            assert prepared.canonical_sha256 == row_digest(rows) != prefix.sha256
            assert inventory(writer.session_dir) == before
            saved_digest = prepared.canonical_sha256

            def admit():
                assert CommittedTranscriptReader(writer.session_dir).load_messages() == ROWS
                with prepared.open_verified() as verified:
                    assert verified.tell() == 0
                    raw = b"".join(iter(lambda: verified.read(37), b""))
                    assert raw == path.read_bytes()
                    assert not hasattr(verified, "write") and not hasattr(verified, "fileno")
                prepared.check()

            ack = writer.rewrite_prepared(prepared, expected=previous,
                                          operation_id="rewrite", admit=admit)
            assert stream.tell() == 7
            assert writer.reconcile(operation_id="rewrite", expected=previous,
                                    suffix_sha256=saved_digest, kind="rewrite").commit == ack.commit
            # Same logical canonical intent through the preexisting dict API is
            # idempotent, not a second operation/engine.
            assert writer.rewrite(rows, expected=previous, operation_id="rewrite",
                                  admit=lambda: pytest.fail("replayed admission")).reconciled
        with pytest.raises(w.TranscriptConflict):
            prepared.check()
    assert CommittedTranscriptReader(writer.session_dir).load_messages() == rows
    assert inventory(writer.session_dir)["transcript.v2/" + previous.body] == before[
        "transcript.v2/" + previous.body]
    intent = json.loads((writer.session_dir / "transcript.v2/operations/rewrite/intent.json").read_bytes())
    assert set(intent) == {"kind", "operation_id", "expected", "suffix_sha256",
                           "suffix_bytes", "suffix_messages"}
    assert intent["kind"] == "rewrite" and intent["suffix_sha256"] == saved_digest


@pytest.mark.parametrize("damage", [
    "digest", "count", "bound", "session", "body", "bool-count", "float-version",
    "bare-rows", "torn", "trailing", "duplicate", "invalid-role", "nonfinite",
])
def test_candidate_integrity_schema_and_exact_eof_refuse_before_admit(tmp_path, damage):
    writer, previous = owner(tmp_path / "session")
    path = tmp_path / "candidate"
    raw = w._header("candidate") + b'{"role":"user","content":"new"}\n'
    if damage == "float-version":
        raw = raw.replace(b'"v":2', b'"v":2.0', 1)
    elif damage == "bare-rows":
        raw = b'{"role":"user","content":"new"}\n'
    elif damage == "torn":
        raw = raw[:-1]
    elif damage == "trailing":
        raw += b'{"role":"user","content":"unselected"}\n'
    elif damage == "duplicate":
        raw = w._header("candidate") + b'{"role":"user","metadata":{"x":1,"x":2}}\n'
    elif damage == "invalid-role":
        raw = w._header("candidate") + b'{"role":""}\n'
    elif damage == "nonfinite":
        raw = w._header("candidate") + b'{"role":"user","value":NaN}\n'
    prefix = candidate(path, [{"role": "user"}], raw=raw)
    changes = {
        "digest": {"sha256": "0"*64}, "count": {"message_count": 99},
        "bound": {"committed_bytes": len(raw) + 1}, "session": {"session_id": "foreign"},
        "body": {"body": "../escape"}, "bool-count": {"message_count": True},
    }
    prefix = replace(prefix, **changes.get(damage, {}))
    before = inventory(writer.session_dir)
    with path.open("rb") as stream, pytest.raises((ValueError, TypeError)):
        writer.rewrite_serialized(
            stream, candidate_prefix=prefix, expected=previous, operation_id="bad",
            admit=lambda: pytest.fail("admitted invalid candidate"), candidate_check=lambda: None)
    assert inventory(writer.session_dir) == before
    assert writer._warm is None


@pytest.mark.parametrize("kind", ["bytesio", "pipe", "closed", "forged-prepared"])
def test_no_arbitrary_bytes_loader_or_constructed_prepared_authority(tmp_path, kind):
    writer, previous = owner(tmp_path / "session")
    prefix = candidate(tmp_path / "candidate")
    before = inventory(writer.session_dir)
    if kind == "forged-prepared":
        with pytest.raises(w.TranscriptConflict):
            writer.rewrite_prepared(w.PreparedTranscriptCandidate(), expected=previous,
                                    operation_id="bad", admit=lambda: None)
    elif kind == "bytesio":
        with pytest.raises((ValueError, OSError)):
            writer.rewrite_serialized(io.BytesIO(b"x"), candidate_prefix=prefix,
                                      expected=previous, operation_id="bad", admit=lambda: None,
                                      candidate_check=lambda: None)
    elif kind == "closed":
        stream = (tmp_path / "candidate").open("rb")
        stream.close()
        with pytest.raises(ValueError):
            writer.rewrite_serialized(stream, candidate_prefix=prefix, expected=previous,
                                      operation_id="bad", admit=lambda: None, candidate_check=lambda: None)
    else:
        read_fd, write_fd = os.pipe()
        os.close(write_fd)
        with os.fdopen(read_fd, "rb") as stream, pytest.raises(w.TranscriptConflict):
            writer.rewrite_serialized(stream, candidate_prefix=prefix, expected=previous,
                                      operation_id="bad", admit=lambda: None, candidate_check=lambda: None)
    assert inventory(writer.session_dir) == before


@pytest.mark.parametrize("stage", ["prepare", "admit", "pre-cas", "exit"])
def test_input_guard_failure_or_replacement_never_acknowledged(tmp_path, monkeypatch, stage):
    writer, previous = owner(tmp_path / "session")
    path = tmp_path / "candidate"
    prefix = candidate(path, [{"role": "user", "content": "new"}])
    old_body = (writer.session_dir / "transcript.v2" / previous.body).read_bytes()
    valid = True

    def check():
        if not valid:
            raise RuntimeError("candidate lease expired")

    original_replace = w._Dir.replace
    with path.open("rb") as stream:
        if stage == "prepare":
            valid = False
            with pytest.raises(RuntimeError):
                with w.prepare_serialized_candidate(stream, candidate_prefix=prefix, candidate_check=check):
                    pytest.fail("expired candidate prepared")
            return
        with pytest.raises(w.TranscriptConflict), w.prepare_serialized_candidate(
            stream, candidate_prefix=prefix, candidate_check=check,
        ) as prepared:
            digest = prepared.canonical_sha256

            def admit():
                nonlocal valid
                if stage == "admit":
                    valid = False

            def pre_cas(directory, *args, **kwargs):
                nonlocal valid
                if stage == "pre-cas":
                    valid = False
                result = original_replace(directory, *args, **kwargs)
                if stage == "exit":
                    valid = False
                return result

            monkeypatch.setattr(w._Dir, "replace", pre_cas)
            with pytest.raises(RuntimeError):
                writer.rewrite_prepared(prepared, expected=previous, operation_id="new", admit=admit)
            assert writer._warm is None
            valid = True
            # The local prepared object remains revoked; recovery is through
            # persisted actual operation witnesses, never object flag reset.
            with pytest.raises(w.TranscriptConflict):
                prepared.check()
            result = writer.reconcile(operation_id="new", expected=previous,
                                      suffix_sha256=digest, kind="rewrite")
            assert (result is not None) == (stage == "exit")
            # Avoid a second failure from deliberately revoked outer context.
            with pytest.raises(w.TranscriptConflict):
                prepared.check()
        # Context exit must also refuse revoked input.
    assert (writer.session_dir / "transcript.v2" / previous.body).read_bytes() == old_body


def test_candidate_changed_during_admission_never_publishes(tmp_path):
    writer, previous = owner(tmp_path / "session")
    path = tmp_path / "candidate"
    prefix = candidate(path)
    before = (writer.session_dir / "transcript.v2/current.json").read_bytes()
    with path.open("rb") as stream:
        with pytest.raises(w.TranscriptConflict), w.prepare_serialized_candidate(
            stream, candidate_prefix=prefix, candidate_check=lambda: None,
        ) as prepared:
            def admit():
                path.write_bytes(b"changed-source")
            with pytest.raises(w.TranscriptConflict):
                writer.rewrite_prepared(prepared, expected=previous, operation_id="bad", admit=admit)
    assert (writer.session_dir / "transcript.v2/current.json").read_bytes() == before


@pytest.mark.parametrize("failure", [OSError, asyncio.CancelledError])
def test_lost_ack_uses_prepared_canonical_digest_without_replay(tmp_path, monkeypatch, failure):
    writer, previous = owner(tmp_path / "session")
    rows = [{"role": "user", "content": "new"}]
    path = tmp_path / "candidate"
    prefix = candidate(path, rows)
    original = os.replace

    def lost(*args, **kwargs):
        original(*args, **kwargs)
        raise failure("synthetic lost pointer acknowledgement")

    with path.open("rb") as stream:
        with w.prepare_serialized_candidate(stream, candidate_prefix=prefix, candidate_check=lambda: None) as prepared:
            digest = prepared.canonical_sha256
            with monkeypatch.context() as patch:
                patch.setattr(os, "replace", lost)
                with pytest.raises(failure):
                    writer.rewrite_prepared(prepared, expected=previous,
                                            operation_id="lost", admit=lambda: None)
            assert writer._warm is None
            assert writer.reconcile(operation_id="lost", expected=previous,
                                    suffix_sha256=digest, kind="rewrite") is not None
    assert CommittedTranscriptReader(writer.session_dir).load_messages() == rows


def test_verified_view_refusal_bounds_and_scope(tmp_path):
    path = tmp_path / "candidate"
    prefix = candidate(path)
    with path.open("rb") as stream:
        with w.prepare_serialized_candidate(stream, candidate_prefix=prefix, candidate_check=lambda: None) as prepared:
            with prepared.open_verified() as view:
                with pytest.raises(ValueError):
                    view.read(-1)
                with pytest.raises(ValueError):
                    view.seek(prefix.committed_bytes + 1)
                view.seek(-1, os.SEEK_END)
                assert view.read(1) == b"\n"
            with pytest.raises(w.TranscriptConflict):
                view.read(1)
    with pytest.raises(w.TranscriptConflict):
        prepared.canonical_sha256


def test_candidate_late_parse_and_precommit_errors_preserve_original_tail(tmp_path):
    writer, previous = owner(tmp_path / "session")
    body = writer.session_dir / "transcript.v2" / previous.body
    with body.open("ab") as out:
        out.write(b"\xff\x00old-uncommitted")
    before = body.read_bytes()
    path = tmp_path / "candidate"
    raw = w._header("candidate") + b'{"role":"user","content":"' + b"x"*1048576 + b'"}\n{"role":"user","x":'
    prefix = candidate(path, ROWS, raw=raw)
    with path.open("rb") as stream, pytest.raises(ValueError):
        writer.rewrite_serialized(stream, candidate_prefix=prefix, expected=previous,
                                  operation_id="bad", admit=lambda: pytest.fail("late-invalid admission"),
                                  candidate_check=lambda: None)
    assert body.read_bytes() == before


def test_anonymous_candidate_cursor_read_ahead_and_zero_rows(tmp_path):
    import tempfile
    writer, previous = owner(tmp_path / "session")
    raw = w._header("candidate")
    prefix = candidate(tmp_path / "empty", [], raw=raw)
    with tempfile.TemporaryFile("w+b") as stream:
        stream.write(raw)
        stream.flush()  # Public boundary requires buffered writes settled.
        stream.seek(2)
        with w.prepare_serialized_candidate(stream, candidate_prefix=prefix, candidate_check=lambda: None) as prepared:
            assert prepared.canonical_bytes == prepared.message_count == 0
            assert prepared.canonical_sha256 == hashlib.sha256(b"").hexdigest()
            assert stream.tell() == 2
            ack = writer.rewrite_prepared(prepared, expected=previous,
                                          operation_id="empty", admit=lambda: None)
            assert ack.commit.message_count == 0
    assert CommittedTranscriptReader(writer.session_dir).load_messages() == []


def test_maximum_operation_generation_round_trips_through_preparation(tmp_path):
    writer, previous = owner(tmp_path / "session")
    ack = writer.rewrite(ROWS, expected=previous, operation_id="x"*180, admit=lambda: None)
    assert len(ack.commit.generation) == 182
    assert CommittedTranscriptReader(writer.session_dir).verify() == ack.commit
    with (writer.session_dir / "transcript.v2" / ack.commit.body).open("rb") as stream:
        with w.prepare_serialized_candidate(
            stream, candidate_prefix=ack.commit, candidate_check=lambda: None,
        ) as prepared:
            assert prepared.message_count == len(ROWS)
            assert prepared.canonical_sha256 == row_digest(ROWS)


def test_multiple_link_candidate_and_rebound_fd_refuse(tmp_path):
    path = tmp_path / "candidate"
    prefix = candidate(path)
    os.link(path, tmp_path / "alias")
    with path.open("rb") as stream, pytest.raises(w.TranscriptConflict):
        with w.prepare_serialized_candidate(stream, candidate_prefix=prefix, candidate_check=lambda: None):
            pytest.fail("hard-linked candidate admitted")
    (tmp_path / "alias").unlink()
    with path.open("rb") as stream:
        with pytest.raises(w.TranscriptConflict), w.prepare_serialized_candidate(
            stream, candidate_prefix=prefix, candidate_check=lambda: None,
        ) as prepared:
            other = tmp_path / "replacement"
            other.write_bytes(path.read_bytes())
            with other.open("rb") as replacement:
                os.dup2(replacement.fileno(), stream.fileno())
            with pytest.raises(w.TranscriptConflict):
                prepared.check()


def test_prepared_object_copy_and_fork_cannot_transfer_authority(tmp_path):
    import copy
    path = tmp_path / "candidate"
    prefix = candidate(path)
    with path.open("rb") as stream, w.prepare_serialized_candidate(
        stream, candidate_prefix=prefix, candidate_check=lambda: None,
    ) as prepared:
        for forged in (copy.copy(prepared), copy.deepcopy(prepared)):
            with pytest.raises(w.TranscriptConflict):
                forged.check()
        pid = os.fork()
        if pid == 0:
            try:
                prepared.check()
            except w.TranscriptConflict:
                os._exit(0)
            os._exit(1)
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        prepared.check()  # Fork refusal did not revoke the parent's object.


@pytest.mark.parametrize("phase", ["copy", "parse"])
def test_copy_parse_cancellation_never_exposes_prepared_candidate(tmp_path, monkeypatch, phase):
    path = tmp_path / "candidate"
    prefix = candidate(path)
    original = os.pread if phase == "copy" else w.transduce
    if phase == "copy":
        def interrupt(*args, **kwargs):
            original(*args, **kwargs)
            raise asyncio.CancelledError()
        monkeypatch.setattr(os, "pread", interrupt)
    else:
        def interrupt(*args, **kwargs):
            for raw in original(*args, **kwargs):
                yield raw
                raise asyncio.CancelledError()
        monkeypatch.setattr(w, "transduce", interrupt)
    with path.open("rb") as stream, pytest.raises(asyncio.CancelledError):
        stream.seek(5)
        try:
            with w.prepare_serialized_candidate(stream, candidate_prefix=prefix, candidate_check=lambda: None):
                pytest.fail("cancelled candidate exposed")
        finally:
            assert stream.tell() == 5


def test_late_target_fence_exit_prevents_ack_but_can_reconcile(tmp_path):
    writer, previous = owner(tmp_path / "session")
    path = tmp_path / "candidate"
    prefix = candidate(path)
    fail_exit = True

    @contextmanager
    def failing_fence(path):
        with fence(path) as check:
            yield check
            if fail_exit:
                raise RuntimeError("target ownership exit failed")

    writer.fence = failing_fence
    with path.open("rb") as stream, w.prepare_serialized_candidate(
        stream, candidate_prefix=prefix, candidate_check=lambda: None,
    ) as prepared:
        digest = prepared.canonical_sha256
        with pytest.raises(RuntimeError):
            writer.rewrite_prepared(prepared, expected=previous, operation_id="late", admit=lambda: None)
        assert writer._warm is None
        fail_exit = False
        assert writer.reconcile(operation_id="late", expected=previous,
                                suffix_sha256=digest, kind="rewrite") is not None


def test_prepared_cas_conflict_and_different_input_same_id_refuse(tmp_path):
    writer, previous = owner(tmp_path / "session")
    path = tmp_path / "candidate"
    prefix = candidate(path)
    with path.open("rb") as stream, w.prepare_serialized_candidate(
        stream, candidate_prefix=prefix, candidate_check=lambda: None,
    ) as prepared:
        advanced = writer.append(ROWS, expected=previous, operation_id="other", admit=lambda: None).commit
        with pytest.raises(w.TranscriptConflict):
            writer.rewrite_prepared(prepared, expected=previous, operation_id="rewrite", admit=lambda: None)
        ack = writer.rewrite_prepared(prepared, expected=advanced, operation_id="rewrite", admit=lambda: None)
        assert ack.commit.message_count == 2
    alternate = tmp_path / "different"
    prefix = candidate(alternate, [{"role": "user", "content": "different"}])
    with alternate.open("rb") as stream, pytest.raises(w.TranscriptConflict):
        writer.rewrite_serialized(stream, candidate_prefix=prefix, expected=advanced,
                                  operation_id="rewrite", admit=lambda: None, candidate_check=lambda: None)


def test_streamed_candidate_large_individual_record_memory(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "tests/qualify_transcript_serialized.py"),
         "--work", str(tmp_path / "benchmark")],
        env={**os.environ, "PYTHONPATH": str(root)}, capture_output=True,
        text=True, check=True, timeout=120)
    report = json.loads(result.stdout)
    assert [case["string_bytes"] for case in report["cases"]] == [2*1024**2, 8*1024**2]
    assert all(case["python_peak"] < 2*1024**2 for case in report["cases"])