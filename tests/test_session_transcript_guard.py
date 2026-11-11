"""External native writes are detected without reading or replacing history."""

from pathlib import Path

import pytest

from amplifier_foundation.session.transcript_guard import (
    NativeTranscriptConflict,
    NativeTranscriptGuard,
)


def test_missing_history_rearms_after_own_save_and_detects_external_creation(tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    guard = NativeTranscriptGuard(transcript)
    guard.check()
    transcript.write_bytes(b'own saved history\n')
    guard.saved()
    guard.check()
    backup = tmp_path / "transcript.jsonl.backup"
    backup.write_bytes(b'external backup\n')
    with pytest.raises(NativeTranscriptConflict, match="Both histories were preserved"):
        guard.check()
    assert transcript.read_bytes() == b'own saved history\n'
    assert backup.read_bytes() == b'external backup\n'


@pytest.mark.parametrize("name", ["transcript.jsonl", "transcript.jsonl.backup"])
@pytest.mark.parametrize("change", ["create", "append", "delete", "replace"])
def test_external_revision_requires_conflict_resolution(tmp_path, name, change):
    path = tmp_path / name
    if change != "create":
        path.write_bytes(b'original history\n')
    guard = NativeTranscriptGuard(tmp_path / "transcript.jsonl")
    if change == "create":
        path.write_bytes(b'external history\n')
    elif change == "append":
        with path.open('ab') as stream:
            stream.write(b'external turn\n')
    elif change == "delete":
        path.unlink()
    else:
        replacement = tmp_path / "replacement"
        replacement.write_bytes(path.read_bytes())
        replacement.replace(path)
    expected = path.read_bytes() if path.exists() else None
    with pytest.raises(NativeTranscriptConflict):
        guard.check()
    assert (path.read_bytes() if path.exists() else None) == expected


@pytest.mark.parametrize("name", ["transcript.jsonl", "transcript.jsonl.backup"])
@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_nonregular_history_is_never_followed_or_changed(tmp_path, name, kind):
    path = tmp_path / name
    target = tmp_path / "external-history"
    target.write_bytes(b'untouched external history\n')
    if kind == "directory":
        path.mkdir()
    else:
        try:
            path.symlink_to(target)
        except OSError as error:
            if getattr(error, 'winerror', None) == 1314:
                pytest.skip('Creating symlinks requires Windows privilege')
            raise
    with pytest.raises(NativeTranscriptConflict, match="regular file"):
        NativeTranscriptGuard(tmp_path / "transcript.jsonl")
    assert target.read_bytes() == b'untouched external history\n'
    if kind == "directory":
        assert path.is_dir()
    else:
        assert path.is_symlink()


def test_guard_observes_metadata_only_and_propagates_unrelated_io_errors(tmp_path, monkeypatch):
    path = tmp_path / "transcript.jsonl"
    path.write_bytes(b'not parsed as a transcript')

    def forbidden(*args, **kwargs):
        pytest.fail('Conflict detection must not load transcript contents')

    monkeypatch.setattr(Path, 'open', forbidden)
    guard = NativeTranscriptGuard(path)
    guard.check()
    guard.saved()
    error = PermissionError('unreadable metadata')

    def unavailable(self):
        raise error

    monkeypatch.setattr(Path, 'lstat', unavailable)
    with pytest.raises(PermissionError) as caught:
        guard.check()
    assert caught.value is error
