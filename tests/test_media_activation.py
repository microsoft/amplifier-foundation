"""Offline POSIX activation, real converter/checkpoint/media store, no live roots."""

import asyncio
import base64
import io
import json
import os
import tracemalloc
from contextlib import contextmanager

import pytest
from PIL import Image

from amplifier_foundation.session import media_activation as activation
from amplifier_foundation.session.checkpoint import verified_checkpoint_refs
from amplifier_foundation.session.media import MediaPolicy, SessionMediaStore
from amplifier_foundation.session.media_conversion import build_candidate, preview

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX durability only")


class Fixture:
    def __init__(self, root):
        self.session = root / "session"
        self.session.mkdir()
        self.path = self.session / "transcript.jsonl"
        self.operation = root / "operation"
        self.held = False
        self.allowed = True
        self.cancel = False
        self.replaces = 0
        self.acks = 0
        self.store = SessionMediaStore(
            root / "media", "fixture", policy=MediaPolicy(1024 * 1024, 10000, 10),
            checkpoint_verifier=verified_checkpoint_refs,
        )
        self.origin = {"kind": "migration", "session_id": "fixture", "id": "observation-1",
                       "created_at": "2025-01-01T00:00:00+00:00", "expires_at": None}
        self.pixels = io.BytesIO()
        Image.new("RGB", (3, 2), "red").save(self.pixels, format="PNG")
        self.pixels = self.pixels.getvalue()
        self.path.write_text(json.dumps({
            "role": "tool", "tool_call_id": "original-call", "status": "failed",
            "usage": {"input": 9}, "provider_state": "opaque-signature",
            "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                          "data": base64.b64encode(self.pixels).decode()}}],
        }) + "\n")
        self.original = self.path.read_bytes()
        self.backup = self.session / "transcript.jsonl.backup"
        self.backup.write_bytes(b"unrelated legacy backup\n")
        self.metadata = self.session / "metadata.json"
        self.metadata.write_bytes(b'{"title":"current name","status":"held"}')
        self.kwargs = {"session_id": "fixture", "owner": "actual-host", "fence": self.fence,
                       "admit": self.admit, "asset_reader": self.reader,
                       "commit_media": self.commit, "cancelled": lambda: self.cancel}

    @contextmanager
    def fence(self, owner):
        assert owner == "actual-host" and not self.held
        self.held = True
        try:
            yield
        finally:
            self.held = False

    def admit(self):
        assert self.held
        return self.allowed

    def reader(self, ref):
        return self.store.resolve_ref(ref, committed=False)

    def commit(self, receipt):
        assert self.held
        self.acks += 1
        return self.store.acknowledge_checkpoint(receipt)

    def build(self, selected_records=None):
        plan = preview(self.path, owner="actual-host", session_id="fixture", fence=self.fence,
                       backups=(self.backup,), selected_records=selected_records)
        asyncio.run(build_candidate(plan, self.operation, fence=self.fence,
                                   capability=self.store, origin_for=lambda *_: self.origin,
                                   asset_reader=self.reader))

    def apply(self, **kwargs):
        return activation.apply_candidate(self.operation, self.session, **(self.kwargs | kwargs))

    def resume(self, **kwargs):
        return activation.resume_candidate(self.operation, self.session, **(self.kwargs | kwargs))

    def reconcile(self):
        return activation.reconcile_activation(self.operation, self.session,
                                               session_id="fixture", owner="actual-host")

    def preserved(self):
        assert (self.operation / "original-0").read_bytes() == self.original
        assert (self.operation / "original-1").read_bytes() == b"unrelated legacy backup\n"
        assert self.backup.read_bytes() == b"unrelated legacy backup\n"
        assert self.metadata.read_bytes() == b'{"title":"current name","status":"held"}'


@pytest.fixture
def fixture(tmp_path):
    fixture = Fixture(tmp_path)
    yield fixture
    fixture.store.close()


def test_real_apply_readback_actual_ack_resume_and_no_second_replace(fixture, monkeypatch):
    f = fixture
    f.build()
    original_mtime = f.path.stat().st_mtime_ns
    assert f.reconcile().state == "not-started"
    replace = os.replace

    def observed(source, target, **kwargs):
        if target == "transcript.jsonl":
            f.replaces += 1
            journal = json.loads((f.operation / "activation.json").read_bytes())
            assert journal["phase"] == "replace-intent"
            assert f.held and f.acks == 0
        return replace(source, target, **kwargs)

    monkeypatch.setattr(os, "replace", observed)
    result = f.apply()
    assert result.state == "acknowledged" and f.replaces == f.acks == 1
    assert f.path.stat().st_mtime_ns == original_mtime
    assert result.checkpoint.verify()["sha256"] == result.sha256
    ref = next(verified_checkpoint_refs(result.checkpoint))
    with f.store.resolve_ref(ref) as pixels:
        assert pixels.read() == f.pixels
    assert f.reconcile().state == "acknowledged-on-disk-reverify-required"
    assert f.resume().state == "acknowledged"
    assert f.replaces == 1 and f.acks == 2
    with pytest.raises(activation.ActivationRefused, match="replay"):
        f.apply()
    f.preserved()


@pytest.mark.parametrize("after", [False, True])
def test_unknown_replace_never_replayed(fixture, monkeypatch, after):
    f = fixture
    f.build()
    replace = os.replace
    calls = []

    def fail(source, target, **kwargs):
        if target == "transcript.jsonl":
            calls.append(target)
            if after:
                replace(source, target, **kwargs)
            raise OSError("synthetic lost replace reply")
        return replace(source, target, **kwargs)

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        f.apply()
    assert calls == ["transcript.jsonl"] and f.acks == 0
    if after:
        assert f.reconcile().state == "installed-awaiting-ack"
        assert f.resume().state == "acknowledged"
    else:
        assert f.reconcile().state == "replace-unconfirmed-source-present"
        assert f.resume().state == "replace-unconfirmed-source-present"
        assert f.path.read_bytes() == f.original
    assert calls == ["transcript.jsonl"]
    f.preserved()


@pytest.mark.parametrize("point", ["copy", "stage-readback", "installed-readback", "checkpoint", "media", "final-journal"])
def test_failure_boundaries_preserve_and_resume(fixture, monkeypatch, point):
    f = fixture
    f.build()
    original = activation._verify
    journal = activation._journal
    copy = activation._copy

    def verify(operation, path, *args):
        if (point == "stage-readback" and path.name.startswith(".media-activation-")) or (
            point == "installed-readback" and path.name == "transcript.jsonl"
        ):
            raise OSError("synthetic readback failure")
        return original(operation, path, *args)

    def failcopy(*args):
        copy(*args)
        raise OSError("synthetic copy failure")

    def failack(*args, **kwargs):
        raise OSError("synthetic ACK failure")

    def failjournal(fd, value):
        if value["phase"] == "acknowledged":
            raise OSError("synthetic final journal failure")
        return journal(fd, value)

    with monkeypatch.context() as scoped:
        scoped.setattr(activation, "_verify", verify)
        if point == "copy":
            scoped.setattr(activation, "_copy", failcopy)
        if point == "final-journal":
            scoped.setattr(activation, "_journal", failjournal)
        kwargs = {"acknowledge": failack} if point == "checkpoint" else {"commit_media": failack} if point == "media" else {}
        with pytest.raises(OSError):
            f.apply(**kwargs)
    assert f.resume().state == "acknowledged"
    f.preserved()


@pytest.mark.parametrize("point", ["stage", "replace", "ack"])
def test_fsync_failures_never_success(fixture, monkeypatch, point):
    f = fixture
    f.build()
    fsync = os.fsync
    replace = os.replace
    installed = False

    def observe(source, target, **kwargs):
        nonlocal installed
        answer = replace(source, target, **kwargs)
        if target == "transcript.jsonl":
            installed = True
        return answer

    def fail(fd):
        name = os.readlink(f"/proc/self/fd/{fd}")
        if ((point == "stage" and ".media-activation-" in name) or
                (point == "replace" and installed and name == str(f.session)) or
                (point == "ack" and installed and name == str(f.path))):
            raise OSError("synthetic fsync failure")
        return fsync(fd)

    with monkeypatch.context() as scoped:
        scoped.setattr(os, "replace", observe)
        scoped.setattr(os, "fsync", fail)
        with pytest.raises((OSError, ValueError)):
            f.apply()
    assert f.resume().state == "acknowledged"
    f.preserved()


@pytest.mark.parametrize("change", ["source", "backup", "pending", "layout", "candidate", "manifest", "original", "admission"])
def test_preflight_conflicts_do_not_replace(fixture, change):
    f = fixture
    f.build()
    if change == "source":
        f.path.write_bytes(f.original + b"\n")
    elif change == "backup":
        f.backup.write_bytes(b"changed backup")
    elif change == "pending":
        (f.session / "transcript.jsonl.append-pending").write_text("{}")
    elif change == "layout":
        (f.session / "transcript.commit.json").write_text("{}")
    elif change == "admission":
        f.allowed = False
    else:
        name = {"candidate": "candidate.jsonl", "manifest": "manifest.sqlite", "original": "original-0"}[change]
        with (f.operation / name).open("ab") as output:
            output.write(b"changed")
    current = f.path.read_bytes()
    with pytest.raises((ValueError, OSError)):
        f.apply()
    assert f.path.read_bytes() == current and f.acks == 0


def test_current_admission_and_source_rechecked_before_replace(fixture):
    f = fixture
    f.build()

    def admit():
        if (f.operation / "activation.json").exists():
            journal = json.loads((f.operation / "activation.json").read_bytes())
            if journal["phase"] == "replace-intent":
                return False
        return True

    with pytest.raises(activation.ActivationRefused, match="admission"):
        f.apply(admit=admit)
    assert f.path.read_bytes() == f.original and f.acks == 0
    assert f.resume().state == "replace-unconfirmed-source-present"


@pytest.mark.parametrize("revocation", ["cancel", "admission"])
def test_revoke_during_final_source_hash_never_replaces(fixture, monkeypatch, revocation):
    f = fixture
    f.build()
    check = activation.conversion._check

    def revoke(expected):
        check(expected)
        if expected.path == str(f.path) and (f.operation / "activation.json").exists():
            saved = json.loads((f.operation / "activation.json").read_bytes())
            if saved["phase"] == "replace-intent":
                if revocation == "cancel":
                    f.cancel = True
                else:
                    f.allowed = False

    with monkeypatch.context() as scoped:
        scoped.setattr(activation.conversion, "_check", revoke)
        if revocation == "cancel":
            assert f.apply().state == "replace-unconfirmed-source-present"
        else:
            with pytest.raises(activation.ActivationRefused, match="admission"):
                f.apply()
    assert f.path.read_bytes() == f.original and f.acks == 0
    f.cancel, f.allowed = False, True
    assert f.resume().state == "replace-unconfirmed-source-present"
    f.preserved()


def test_oversized_owner_binding_refused_before_activation_writes(fixture):
    f = fixture
    owner = "x" * 16384

    @contextmanager
    def fence(actual):
        assert actual == owner
        yield

    plan = preview(f.path, owner=owner, session_id="fixture", fence=fence, backups=(f.backup,))
    asyncio.run(build_candidate(plan, f.operation, fence=fence, capability=f.store,
                                origin_for=lambda *_: f.origin, asset_reader=f.reader))
    before = sorted(item.name for item in f.operation.iterdir())
    with pytest.raises(activation.ActivationRefused, match="recovery read bound"):
        f.apply(owner=owner, fence=fence)
    assert sorted(item.name for item in f.operation.iterdir()) == before
    assert not list(f.session.glob(".media-activation-*"))
    assert f.path.read_bytes() == f.original and f.acks == 0
    f.preserved()


@pytest.mark.parametrize("when", ["before", "copy", "after-replace"])
def test_cancellation_safe_checkpoint_states(fixture, monkeypatch, when):
    f = fixture
    f.build()
    copy = activation._copy
    replace = os.replace
    if when == "before":
        f.cancel = True
    elif when == "copy":
        def cancelled_copy(*args):
            f.cancel = True
            return copy(*args)
        monkeypatch.setattr(activation, "_copy", cancelled_copy)
    else:
        def replaced(source, target, **kwargs):
            value = replace(source, target, **kwargs)
            if target == "transcript.jsonl":
                f.cancel = True
            return value
        monkeypatch.setattr(os, "replace", replaced)
    result = f.apply()
    assert result.state == ("installed-awaiting-ack" if when == "after-replace" else "cancelled-preserved")
    assert f.acks == 0
    f.cancel = False
    monkeypatch.setattr(activation, "_copy", copy)
    assert f.resume().state == "acknowledged"
    f.preserved()


@pytest.mark.parametrize("callback", [None, {}, lambda: None])
def test_fake_checkpoint_callbacks_do_not_ack(fixture, callback):
    f = fixture
    f.build()
    with pytest.raises((activation.ActivationRefused, TypeError)):
        f.apply(acknowledge=(lambda *args, **kwargs: callback))
    assert f.acks == 0
    assert f.resume().state == "acknowledged"


def test_wrong_or_lazy_media_ack_is_not_success(fixture):
    f = fixture
    f.build()
    with pytest.raises(activation.ActivationRefused, match="media-store ACK"):
        f.apply(commit_media=lambda receipt: iter([receipt]))
    assert f.resume().state == "acknowledged"


def test_identical_candidate_ack_does_not_replace_inode_or_activity(fixture, monkeypatch):
    f = fixture
    f.path.write_bytes(b'{"role":"user","content":"no conversion needed"}\n')
    f.original = f.path.read_bytes()
    f.build()
    before = f.path.stat()
    replace = os.replace

    def no_primary(source, target, **kwargs):
        assert target != "transcript.jsonl"
        return replace(source, target, **kwargs)

    monkeypatch.setattr(os, "replace", no_primary)
    assert f.apply().state == f.resume().state == "acknowledged"
    after = f.path.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)
    f.preserved()


def test_prefix_foreign_owner_and_symlink_refused(fixture, tmp_path):
    f = fixture
    f.path.write_bytes(f.original + b'{"role":"user","content":"not selected"}\n')
    f.build(selected_records=1)
    with pytest.raises(activation.ActivationRefused, match="full same-owner"):
        f.apply()
    linked = tmp_path / "link"
    linked.symlink_to(f.session, target_is_directory=True)
    with pytest.raises(OSError):
        activation.apply_candidate(f.operation, linked, **f.kwargs)


def test_bounded_huge_individual_nonimage_string_actual_apply_and_ack(fixture):
    f = fixture
    # Avoid constructing a huge Python string even while preparing this fixture.
    with f.path.open("ab") as output:
        output.write(b'{"role":"assistant","content":"')
        for _ in range(256):
            output.write(b"x" * 65536)
        output.write(b'","metadata":{"signature":"opaque"}}\n')
    f.original = f.path.read_bytes()  # Fixture-only resident caller cost excluded.
    f.build()
    tracemalloc.start()
    try:
        result = f.apply()
        assert result.checkpoint.verify()["message_count"] == 2
        assert f.resume().state == "acknowledged"
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 3 * 1024 * 1024
    assert result.state == "acknowledged"
    f.preserved()
