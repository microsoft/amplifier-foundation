"""Pinned committed range/window source: synthetic POSIX qualification."""

import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

from amplifier_foundation.session import transcript_source as s
from amplifier_foundation.session import transcript_writer as w
from amplifier_foundation.session.transcript_v2 import UnsupportedTranscript

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX source qualification")


@contextmanager
def fence(path):
    with (path / ".host-owner").open("a+b") as lock:
        w.fcntl.flock(lock.fileno(), w.fcntl.LOCK_EX)
        yield lambda: None


def fixture(path, rows=None):
    writer = w.TranscriptWriter(path, fence=fence)
    commit = writer.initialize(session_id="source", operation_id="init", admit=lambda: None).commit
    if rows is None:
        rows = [{"role": "user", "content": f"message-{index}"} for index in range(100)]
    if rows:
        commit = writer.append(rows, expected=commit, operation_id="append", admit=lambda: None).commit
    return writer, commit, rows


def test_forward_recent_message_and_byte_ranges(tmp_path):
    _, commit, rows = fixture(tmp_path)
    with s.open_committed_source(tmp_path, expected=commit) as source:
        assert source.commit == commit
        result = source.window(17, 5)
        assert (result.start, result.stop, result.messages) == (17, 22, rows[17:22])
        assert source.recent(5).messages == rows[-5:]
        assert source.recent(5, before=50).messages == rows[45:50]
        assert source.window(100, 0).messages == []
        bounds = source.message_range(37)
        assert bounds.index == 37
        raw = b"".join(source.iter_bytes(bounds.start, bounds.end, chunk_size=7))
        assert json.loads(raw) == rows[37]
        complete = b"".join(source.iter_bytes(chunk_size=777))
        assert len(complete) == commit.committed_bytes
        assert hashlib.sha256(complete).hexdigest() == commit.sha256
    with pytest.raises(UnsupportedTranscript):
        source.window(0, 1)
    with pytest.raises(UnsupportedTranscript):
        list(source.iter_bytes(0, 0))


def test_shared_source_pages_are_thread_safe(tmp_path):
    _, _, rows = fixture(tmp_path)
    with s.open_committed_source(tmp_path) as source:
        def page(index):
            return source.window(index, 3).messages
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(page, range(90)))
        assert results == [rows[index:index + 3] for index in range(90)]


def test_multiple_pages_do_not_hash_or_semantically_scan_history(tmp_path, monkeypatch):
    _, commit, rows = fixture(tmp_path)
    with s.open_committed_source(tmp_path) as source:
        def forbidden(*args, **kwargs):
            raise AssertionError("range reverified history")
        monkeypatch.setattr(s.hashlib, "sha256", forbidden)
        monkeypatch.setattr(s.Scanner, "records", forbidden)
        reads = []
        original = s._file

        @contextmanager
        def tracked(path):
            with original(path) as stream:
                class Wrapped:
                    def __getattr__(self, name):
                        return getattr(stream, name)

                    def read(self, count=-1):
                        raw = stream.read(count)
                        reads.append((path.name, len(raw)))
                        return raw
                yield Wrapped()
        monkeypatch.setattr(s, "_file", tracked)
        for index in range(0, 100, 10):
            assert source.window(index, 10).messages == rows[index:index + 10]
        assert sum(count for _, count in reads) < commit.committed_bytes
        assert source.recent(3).messages == rows[-3:]


def test_uncommitted_complete_and_torn_tail_never_exposed(tmp_path):
    _, commit, rows = fixture(tmp_path)
    body = tmp_path / "transcript.v2" / commit.body
    with body.open("ab") as stream:
        stream.write(b'{"role":"user","content":"complete-uncommitted"}\n\x00torn')
    with s.open_committed_source(tmp_path) as source:
        assert source.recent(3).messages == rows[-3:]
        raw = b"".join(source.iter_bytes())
        assert b"uncommitted" not in raw
        with pytest.raises(ValueError):
            source.read_range(0, body.stat().st_size)


@pytest.mark.parametrize("change", [
    "append", "descriptor", "body", "same-bytes-replacement", "symlink",
    "directory-replacement", "fence",
])
def test_changed_authority_or_namespace_invalidates_before_row_exposure(tmp_path, change):
    writer, commit, _ = fixture(tmp_path)
    root = tmp_path / "transcript.v2"
    body = root / commit.body
    with s.open_committed_source(tmp_path) as source:
        if change == "append":
            writer.append([{"role": "user", "content": "next"}], expected=commit,
                          operation_id="next", admit=lambda: None)
        elif change == "descriptor":
            data = json.loads((root / "current.json").read_bytes())
            data["sequence"] += 1
            (root / "current.json").write_bytes(w._small(data))
        elif change == "body":
            body.write_bytes(body.read_bytes().replace(b"message", b"changed"))
        elif change in ("same-bytes-replacement", "symlink"):
            body.rename(root / "retained-body")
            if change == "symlink":
                body.symlink_to(root / "retained-body")
            else:
                body.write_bytes((root / "retained-body").read_bytes())
        elif change == "directory-replacement":
            root.rename(tmp_path / "retained-root")
            root.mkdir()
        else:
            (tmp_path / "transcript.jsonl" / "format.json").write_text("{}")
        with pytest.raises((ValueError, OSError)):
            source.window(0, 1)
        with pytest.raises(UnsupportedTranscript):
            source.read_range(0, 1)


def test_mutation_during_range_read_never_returns_bytes(tmp_path, monkeypatch):
    _, commit, _ = fixture(tmp_path)
    body = tmp_path / "transcript.v2" / commit.body
    with s.open_committed_source(tmp_path) as source:
        original = s._file

        @contextmanager
        def mutation(path):
            with original(path) as stream:
                class Wrapped:
                    def __getattr__(self, name):
                        return getattr(stream, name)

                    def read(self, size=-1):
                        raw = stream.read(size)
                        with body.open("ab") as output:
                            output.write(b"x")
                        return raw
                yield Wrapped()
        monkeypatch.setattr(s, "_file", mutation)
        with pytest.raises(UnsupportedTranscript):
            source.read_range(0, 10)


def test_prepared_namespace_swap_with_same_body_inode_refuses(tmp_path):
    _, commit, _ = fixture(tmp_path)
    root = tmp_path / "transcript.v2"
    prepared = tmp_path / "prepared-root"
    prepared.mkdir()
    for name in ("migration.json", "ready.json", "current.json"):
        (prepared / name).write_bytes((root / name).read_bytes())
    os.link(root / commit.body, prepared / commit.body)
    with s.open_committed_source(tmp_path) as source:
        root.rename(tmp_path / "retained-root")
        prepared.rename(root)
        with pytest.raises(UnsupportedTranscript):
            source.window(0, 1)


@pytest.mark.parametrize("damage", ["hash", "short", "nested-duplicate", "count"])
def test_cold_open_hash_semantic_and_bound_closure(tmp_path, damage):
    _, commit, _ = fixture(tmp_path)
    root = tmp_path / "transcript.v2"
    body = root / commit.body
    data = json.loads((root / "current.json").read_bytes())
    raw = body.read_bytes()
    if damage == "hash":
        raw = raw.replace(b"message", b"changed")
    elif damage == "short":
        raw = raw[:-1]
    elif damage == "nested-duplicate":
        raw += b'{"role":"user","meta":{"x":1,"x":2}}\n'
        data.update(message_count=101, committed_bytes=len(raw),
                    sha256=hashlib.sha256(raw).hexdigest())
    else:
        data["message_count"] = 101
    body.write_bytes(raw)
    (root / "current.json").write_bytes(w._small(data))
    with pytest.raises(ValueError):
        with s.open_committed_source(tmp_path):
            pytest.fail("exposed an invalid source")


def test_empty_initialized_source_and_expected_witness(tmp_path):
    _, commit, _ = fixture(tmp_path, [])
    with s.open_committed_source(tmp_path, expected=commit) as source:
        assert source.window(0, 10).messages == []
        assert source.recent(10).messages == []
        with pytest.raises(IndexError):
            source.message_range(0)
    from dataclasses import replace
    with pytest.raises(UnsupportedTranscript):
        with s.open_committed_source(tmp_path, expected=replace(commit, sequence=999)):
            pytest.fail("accepted wrong witness")


@pytest.mark.parametrize("action", ["read", "window", "recent", "index", "iterator"])
def test_explicit_range_limits(tmp_path, action):
    _, _, _ = fixture(tmp_path)
    with s.open_committed_source(tmp_path) as source:
        with pytest.raises((ValueError, IndexError)):
            if action == "read":
                source.read_range(-1, 100)
            elif action == "window":
                source.window(0, 1025)
            elif action == "recent":
                source.recent(1, before=-1)
            elif action == "index":
                source.message_range(True)
            else:
                list(source.iter_bytes(chunk_size=s.MAX_RANGE_BYTES + 1))


def test_cold_huge_individual_string_is_bounded_and_streamable(
        tmp_path, record_property):
    # A fresh interpreter isolates the feature measurement from pytest's global
    # pathlib/sys.intern table resizes. No cold-source verification is warmed.
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "tests/qualify_transcript_source.py"),
         "--work", str(tmp_path / "benchmark")],
        env={**os.environ, "PYTHONPATH": str(root)}, capture_output=True,
        text=True, check=True, timeout=120)
    report = json.loads(result.stdout)
    for case in report["cases"]:
        record_property(str(case["huge_string_bytes"]) + "_python_peak", case["python_peak"])
        assert case["python_peak"] < 2 * 1024 * 1024


def test_budget_refusal_preserves_source_for_stream_fallback(tmp_path):
    fixture(tmp_path, [{"role": "user", "content": "x" * 8192}])
    with s.open_committed_source(tmp_path) as source:
        with pytest.raises(ValueError):
            source.window(0, 1, max_bytes=1024)  # Refusal, not truncation.
        # Budget refusal doesn't poison a verified source's streaming fallback.
        bounds = source.message_range(0)
        assert source.read_range(bounds.start, bounds.start + 1) == b"{"