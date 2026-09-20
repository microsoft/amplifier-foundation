"""Cross-host metadata interleavings against real native files."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from amplifier_foundation.session.history import (
    SessionHistoryError,
    SessionHistoryStore,
)
from amplifier_foundation.session.metadata import (
    SessionMetadataStore,
    has_generated_or_manual_name,
)


def test_read_missing_does_not_create_custom_hierarchy(tmp_path):
    path = tmp_path / "another-app" / "session"
    assert SessionMetadataStore(path).read() == {}
    assert not path.exists()


def test_checkpoint_after_other_host_rename_preserves_name_and_unknown_fields(tmp_path):
    history = SessionHistoryStore(tmp_path)
    names = SessionMetadataStore(tmp_path, create=True)
    history.save([{"role": "user", "content": "original"}], {"host_specific": {"x": 1}})
    names.set_name("Web name")
    stale = names.read()
    names.set_name("CLI rename")
    history.save(
        [{"role": "user", "content": "continued"}],
        {**stale, "turn_count": 2},
        merge_metadata=True,
    )
    actual = names.read()
    assert actual["name"] == "CLI rename"
    assert actual["turn_count"] == 2 and actual["host_specific"] == {"x": 1}
    assert history.load_messages()[0]["content"] == "continued"


def test_fallback_generated_then_explicit_name_and_late_result(tmp_path):
    names = SessionMetadataStore(tmp_path, create=True)
    fallback = names.set_name("First prompt", source="fallback")
    assert not has_generated_or_manual_name(fallback)
    generated = names.set_name("Useful name", source="generated", expected_revision=1)
    assert has_generated_or_manual_name(generated)
    names.set_name("User choice")
    actual = names.set_name("Late generation", source="generated", expected_revision=2)
    assert actual["name"] == "User choice" and actual["name_source"] == "manual"
    assert (
        names.set_name("Stale migration", only_if_missing=True)["name"] == "User choice"
    )


def test_legacy_name_is_not_overridden_and_updates_preserve_other_fields(tmp_path):
    history = SessionHistoryStore(tmp_path)
    history.save_metadata({"name": "Legacy CLI choice", "custom": [1, 2]})
    names = SessionMetadataStore(tmp_path, create=True)
    assert (
        names.set_name("Generated", source="generated")["name"] == "Legacy CLI choice"
    )
    names.update({"turn_count": 2})
    assert names.read()["custom"] == [1, 2]


def test_competing_field_patches_do_not_drop_fields(tmp_path):
    def update(index):
        SessionMetadataStore(tmp_path, create=True).update({f"host_{index}": index})

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(update, range(30)))
    assert SessionMetadataStore(tmp_path, create=True).read() == {
        f"host_{i}": i for i in range(30)
    }


def test_invalid_metadata_is_not_silently_replaced(tmp_path):
    (tmp_path / "metadata.json").write_text("{broken")
    with pytest.raises(SessionHistoryError):
        SessionMetadataStore(tmp_path, create=True).set_name("Must not erase")
    assert (tmp_path / "metadata.json").read_text() == "{broken"


def test_naming_does_not_write_transcript_or_events(tmp_path):
    events = tmp_path / "events.jsonl"
    events.write_text('{"event":"tool:post","opaque":true}\n')
    original = events.read_bytes()
    SessionMetadataStore(tmp_path, create=True).set_name("Readable")
    assert events.read_bytes() == original
    assert not (tmp_path / "transcript.jsonl").exists()
    assert json.loads((tmp_path / "metadata.json").read_text())["name"] == "Readable"


def test_late_rename_cannot_recreate_a_deleted_session(tmp_path):
    import shutil

    from amplifier_foundation.session.metadata import metadata_lock

    directory = tmp_path / "session"
    SessionMetadataStore(directory, create=True).set_name("Original")
    writer = SessionMetadataStore(directory)
    with metadata_lock(directory):
        shutil.rmtree(directory)
    with pytest.raises(FileNotFoundError):
        writer.set_name("Too late")
    assert not directory.exists()
