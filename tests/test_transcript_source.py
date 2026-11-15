"""Pinned committed range/window source: synthetic POSIX qualification."""

import hashlib
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from pathlib import PurePath

import pytest

from amplifier_foundation.session import transcript_source as s
from amplifier_foundation.session import transcript_v2 as v2
from amplifier_foundation.session import transcript_writer as w
from amplifier_foundation.session.transcript_v2 import (
    CommittedTranscriptReader,
    UnsupportedTranscript,
)

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


@pytest.mark.parametrize("control", [
    "transcript.v2/migration.json", "transcript.v2/ready.json",
    "transcript.jsonl/format.json", "transcript.jsonl.backup/format.json",
])
@pytest.mark.parametrize("version", [b"2.0", b"2e0", b"true", b"false", b"null", b'"2"', None])
@pytest.mark.parametrize("entrypoint", ["reader", "source", "live-source", "writer", "warm-writer"])
def test_noninteger_transition_control_version_refuses_without_mutation(
        tmp_path, control, version, entrypoint):
    writer, commit, _ = fixture(tmp_path, [{"role": "user", "content": "preserved"}])

    def damage():
        path = tmp_path / control
        value = json.loads(path.read_bytes())
        del value["v"]
        raw = json.dumps(value).encode()
        if version is not None:
            raw = raw[:-1] + b',"v":' + version + b"}"
        path.write_bytes(raw)

    def inventory():
        return {str(path.relative_to(tmp_path)):
                path.read_bytes() if path.is_file() else None
                for path in tmp_path.rglob("*")}

    def forbidden_admission():
        pytest.fail("malformed transition admitted new content")

    if entrypoint == "live-source":
        with s.open_committed_source(tmp_path, expected=commit) as source:
            damage()
            before = inventory()
            with pytest.raises(UnsupportedTranscript):
                source.window(0, 1)
            with pytest.raises(UnsupportedTranscript):
                source.read_range(0, 1)
    else:
        damage()
        before = inventory()
        with pytest.raises(UnsupportedTranscript):
            if entrypoint == "reader":
                CommittedTranscriptReader(tmp_path).load_messages()
            elif entrypoint == "source":
                with s.open_committed_source(tmp_path, expected=commit):
                    pytest.fail("exposed malformed transition")
            else:
                selected = writer if entrypoint == "warm-writer" else w.TranscriptWriter(
                    tmp_path, fence=fence)
                selected.append([{"role": "assistant", "content": "must-not-append"}],
                                expected=commit, operation_id="refused",
                                admit=forbidden_admission)
    assert inventory() == before


@pytest.mark.parametrize("version", [b"2.0", b"2e0", b"true", b"false", b"null", b'"2"'])
@pytest.mark.parametrize("entrypoint", ["reader", "source", "writer"])
def test_noninteger_body_header_version_refuses_even_with_matching_witnesses(
        tmp_path, version, entrypoint):
    _, commit, _ = fixture(tmp_path, [{"role": "user", "content": "preserved"}])
    root = tmp_path / "transcript.v2"
    body = root / commit.body
    header, rows = body.read_bytes().split(b"\n", 1)
    assert b'"v":2' in header
    raw = header.replace(b'"v":2', b'"v":' + version) + b"\n" + rows
    body.write_bytes(raw)
    selected = replace(commit, committed_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    descriptor = w._descriptor(selected)
    # Digest/size/current/publication all agree: refusal must be the header
    # schema, not a stale caller witness or mismatch in the operation journal.
    (root / "current.json").write_bytes(descriptor)
    operation = root / "operations" / commit.operation_id
    (operation / "result.json").write_bytes(descriptor)
    (operation / "published.json").write_bytes(descriptor)
    before = {str(path.relative_to(tmp_path)): path.read_bytes()
              for path in tmp_path.rglob("*") if path.is_file()}
    with pytest.raises(UnsupportedTranscript, match="header"):
        if entrypoint == "reader":
            CommittedTranscriptReader(tmp_path).verify()
        elif entrypoint == "source":
            with s.open_committed_source(tmp_path, expected=selected):
                pytest.fail("exposed a source with noninteger header version")
        else:
            # Cold owner, not a previously verified warm prefix state.
            w.TranscriptWriter(tmp_path, fence=fence).append(
                [{"role": "assistant", "content": "must-not-append"}],
                expected=selected, operation_id="refused", admit=lambda: None)
    assert {str(path.relative_to(tmp_path)): path.read_bytes()
            for path in tmp_path.rglob("*") if path.is_file()} == before


def test_empty_initialized_source_and_expected_witness(tmp_path):
    _, commit, _ = fixture(tmp_path, [])
    with s.open_committed_source(tmp_path, expected=commit) as source:
        assert source.window(0, 10).messages == []
        assert source.recent(10).messages == []
        with pytest.raises(IndexError):
            source.message_range(0)
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


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("transition", [False, True])
@pytest.mark.skipif(not hasattr(PurePath, "_parse_path"),
                    reason="lexical parse probe requires Python 3.12+")
def test_checked_ranges_reuse_lexical_paths_not_authority(
        tmp_path, monkeypatch, record_property, empty, transition):
    """Count real forwarded construction/parsing, not timing or intern capacity."""
    _, commit, _ = fixture(tmp_path, [] if empty else None)
    if not transition:
        for name in ("migration.json", "ready.json", "operations"):
            (tmp_path / "transcript.v2" / name).rename(tmp_path / name)
    expected = (tmp_path / "transcript.v2" / commit.body).read_bytes()
    with s.open_committed_source(tmp_path, expected=commit) as source:
        counts = {"construct": 0, "parse": 0, "namespace": 0, "descriptor": 0}
        files = []
        original_init = PurePath.__init__
        original_parse = PurePath._parse_path.__func__
        original_namespace = s._namespace
        original_descriptor = source._reader._descriptor
        original_file = v2._file

        def construct(self, *args, **kwargs):
            counts["construct"] += 1
            original_init(self, *args, **kwargs)

        def parse(cls, *args):
            counts["parse"] += 1
            return original_parse(cls, *args)

        def namespace(*args):
            counts["namespace"] += 1
            return original_namespace(*args)

        def descriptor():
            counts["descriptor"] += 1
            return original_descriptor()

        @contextmanager
        def checked_file(path):
            files.append(path.name)
            with original_file(path) as stream:
                yield stream

        reports = []
        # No warmup reads. Opening the source is the one cold verification.
        with monkeypatch.context() as patch:
            patch.setattr(PurePath, "__init__", construct)
            patch.setattr(PurePath, "_parse_path", classmethod(parse))
            patch.setattr(s, "_namespace", namespace)
            patch.setattr(source._reader, "_descriptor", descriptor)
            patch.setattr(s, "_file", checked_file)
            patch.setattr(v2, "_file", checked_file)
            for reads in (1, 4, 16):
                before = dict(counts)
                file_start = len(files)
                for index in range(reads):
                    start = (index * 7) % len(expected)
                    stop = min(start + 13, len(expected))
                    assert source.read_range(start, stop) == expected[start:stop]
                    assert source.read_range(start, start) == b""
                delta = {key: counts[key] - before[key] for key in counts}
                actual_reads = reads * 2
                assert delta["namespace"] == delta["descriptor"] == 2 * actual_reads
                opened = files[file_start:]
                assert opened.count(commit.body) == actual_reads
                assert opened.count("current.json") == 2 * actual_reads
                assert opened.count("migration.json") == (2 * actual_reads if transition else 0)
                assert opened.count("ready.json") == (2 * actual_reads if transition else 0)
                assert opened.count("format.json") == (4 * actual_reads if transition else 0)
                reports.append({"reads": actual_reads, **delta, "file_checks": len(opened)})
        record_property("lexical_cost", json.dumps(reports, sort_keys=True))
        assert [(r["construct"], r["parse"]) for r in reports] == [(0, 0)] * 3, reports


@pytest.mark.parametrize("selection", ["commit", "root", "session_dir"])
def test_mutable_selection_cannot_retarget_cached_paths(tmp_path, selection):
    _, commit, _ = fixture(tmp_path)
    with s.open_committed_source(tmp_path) as source:
        if selection == "commit":
            source.commit = replace(commit, operation_id="different")
        else:
            setattr(source._reader, selection, tmp_path / "different")
        with pytest.raises(UnsupportedTranscript):
            source.read_range(0, 1)
        # Restoring the selection must not revive a failed source.
        source.commit = commit
        source._reader.root = tmp_path / "transcript.v2"
        source._reader.session_dir = tmp_path
        with pytest.raises(UnsupportedTranscript):
            source.read_range(0, 1)


def test_relative_source_cwd_change_refuses_permanently(tmp_path, monkeypatch):
    fixture(tmp_path)
    monkeypatch.chdir(tmp_path.parent)
    with s.open_committed_source(Path(tmp_path.name)) as source:
        assert source.read_range(0, 1) == b"{"
        monkeypatch.chdir(tmp_path)
        with pytest.raises(UnsupportedTranscript):
            source.read_range(0, 1)
        monkeypatch.chdir(tmp_path.parent)
        with pytest.raises(UnsupportedTranscript):
            source.read_range(0, 1)


def test_absolute_source_does_not_depend_on_cwd(tmp_path, monkeypatch):
    fixture(tmp_path)
    with s.open_committed_source(tmp_path.absolute()) as source:
        monkeypatch.chdir(tmp_path.parent)
        assert source.read_range(0, 1) == b"{"
        monkeypatch.chdir(tmp_path)
        assert source.read_range(0, 1) == b"{"


def test_parent_components_and_ancestor_symlinks_refuse(tmp_path):
    fixture(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    for path in (alias, tmp_path / ".." / tmp_path.name):
        with pytest.raises((UnsupportedTranscript, OSError)):
            with s.open_committed_source(path):
                pytest.fail("accepted an alias or parent component")


def test_descriptor_raw_bytes_still_authority(tmp_path):
    fixture(tmp_path)
    current = tmp_path / "transcript.v2" / "current.json"
    with s.open_committed_source(tmp_path) as source:
        # Semantically equal prefix, different exact descriptor.
        current.write_bytes(current.read_bytes() + b" ")
        with pytest.raises(UnsupportedTranscript):
            source.read_range(0, 1)
        with pytest.raises(UnsupportedTranscript):
            source.read_range(0, 1)


@pytest.mark.parametrize("control", [
    "transcript.v2/current.json", "transcript.v2/migration.json",
    "transcript.v2/ready.json", "transcript.jsonl/format.json",
    "transcript.jsonl.backup/format.json",
])
def test_control_file_change_during_range_refuses(tmp_path, monkeypatch, control):
    fixture(tmp_path)
    target = tmp_path / control
    with s.open_committed_source(tmp_path) as source:
        original = s._file

        @contextmanager
        def changing(path):
            with original(path) as stream:
                yield stream
                target.write_bytes(b"{}")
        monkeypatch.setattr(s, "_file", changing)
        with pytest.raises(UnsupportedTranscript):
            source.read_range(0, 1)
        with pytest.raises(UnsupportedTranscript):
            source.read_range(0, 1)


@pytest.mark.parametrize("during_read", [False, True])
def test_ancestor_swap_with_same_body_inode_refuses(tmp_path, monkeypatch, during_read):
    import shutil

    session = tmp_path / "ancestor" / "session"
    session.mkdir(parents=True)
    _, commit, _ = fixture(session)
    prepared = tmp_path / "prepared"
    # Prepare all links before pinning so link-count/ctime changes do not mask
    # directory-identity refusal, even though every selected file inode agrees.
    shutil.copytree(session.parent, prepared, copy_function=os.link)
    assert os.stat(session / "transcript.v2" / commit.body).st_ino == os.stat(
        prepared / "session" / "transcript.v2" / commit.body).st_ino

    def swap():
        session.parent.rename(tmp_path / "retained")
        prepared.rename(session.parent)

    with s.open_committed_source(session) as source:
        if during_read:
            original = s._file

            @contextmanager
            def changing(path):
                with original(path) as stream:
                    class Wrapped:
                        def __getattr__(self, name):
                            return getattr(stream, name)

                        def read(self, count=-1):
                            raw = stream.read(count)
                            swap()
                            return raw
                    yield Wrapped()
            monkeypatch.setattr(s, "_file", changing)
        else:
            swap()
        with pytest.raises(UnsupportedTranscript):
            source.read_range(0, 1)
        with pytest.raises(UnsupportedTranscript):
            source.read_range(0, 1)


@pytest.mark.parametrize("created", ["ready.json", "operations"])
def test_missing_transition_is_not_sticky(tmp_path, created):
    fixture(tmp_path)
    root = tmp_path / "transcript.v2"
    for name in ("migration.json", "ready.json", "operations"):
        (root / name).rename(tmp_path / name)
    with s.open_committed_source(tmp_path) as source:
        assert source.read_range(0, 1) == b"{"
        (tmp_path / created).rename(root / created)
        with pytest.raises(UnsupportedTranscript):
            source.read_range(0, 1)
        with pytest.raises(UnsupportedTranscript):
            source.read_range(0, 1)


def test_inherited_source_refuses_before_lock_and_parent_stays_usable(tmp_path):
    fixture(tmp_path)
    with s.open_committed_source(tmp_path) as source:
        # An inherited source includes a shared offset FD and possibly a lock
        # owned by a vanished thread. Refuse before touching either resource.
        with ThreadPoolExecutor(max_workers=1) as pool:
            import threading
            locked, release = threading.Event(), threading.Event()

            def hold():
                with source._lock:
                    locked.set()
                    release.wait()
            holder = pool.submit(hold)
            assert locked.wait(5)
            pid = os.fork()
            if pid == 0:
                import signal
                signal.alarm(3)
                try:
                    source.read_range(0, 1)
                except UnsupportedTranscript:
                    os._exit(0)
                except BaseException:
                    os._exit(2)
                os._exit(1)
            try:
                _, status = os.waitpid(pid, 0)
            finally:
                release.set()
                holder.result(timeout=5)
        assert os.waitstatus_to_exitcode(status) == 0
        assert source.read_range(0, 1) == b"{"