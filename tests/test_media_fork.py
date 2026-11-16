"""Owner-bound selected-prefix closure, not cross-session digest lookup."""

from copy import deepcopy
import json
import os

import pytest

from amplifier_foundation.session.checkpoint import acknowledge_legacy_checkpoint
from amplifier_foundation.session.fork import fork_session, fork_session_in_memory


NS = "experimental.media.v1"
REF = {"v": 1, "session_id": "parent", "sha256": "b" * 64,
       "provenance": {"created_at": "2026-01-01T00:00:00Z",
                      "expires_at": "2026-01-02T00:00:00Z"}}


@pytest.fixture
def rows():
    return [
        {"role": "user", "content": "first"},
        {"role": "tool", "tool_call_id": "call-1", "content": "image",
         "metadata": {NS: {"refs": [deepcopy(REF)]}, "provider": {"opaque": "preserve"}}},
        {"role": "user", "content": "second"},
        {"role": "assistant", "content": "reply"},
    ]


@pytest.fixture
def source(tmp_path, rows):
    path = tmp_path / "parent"
    path.mkdir()
    (path / "transcript.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
    )
    (path / "metadata.json").write_text('{"session_id":"parent"}')
    return path


def adopter(messages, *, session_id, session_dir):
    assert len(messages) == 2  # Selected prefix, never later turn.
    assert session_id != "parent"
    if session_dir is not None:
        assert not (session_dir / "transcript.jsonl").exists()
        (session_dir / "synthetic-asset").write_text("published-first")
    ref = messages[1]["metadata"][NS]["refs"][0]
    ref["session_id"] = session_id
    return messages


def checkpoint(messages, *, session_id, session_dir):
    assert (session_dir / "synthetic-asset").read_text() == "published-first"
    receipt = acknowledge_legacy_checkpoint(
        session_dir, session_id=session_id, expected_messages=messages
    )
    assert receipt.contains(messages[1]["metadata"][NS]["refs"][0])


def test_refbearing_fork_without_hosts_fails_before_destination(source, tmp_path):
    with pytest.raises(ValueError, match="host adoption"):
        fork_session(source, turn=1, new_session_id="child", include_events=False)
    assert not (tmp_path / "child").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX media checkpoint qualification")
def test_assets_before_target_transcript_then_actual_checkpoint(source, tmp_path):
    before = (source / "transcript.jsonl").read_bytes()
    result = fork_session(
        source, turn=1, new_session_id="child", include_events=False,
        media_adopter=adopter, media_checkpoint=checkpoint,
    )
    target = [json.loads(raw) for raw in
              (result.session_dir / "transcript.jsonl").read_text().splitlines()]
    assert result.message_count == 2
    assert target[1]["metadata"]["provider"] == {"opaque": "preserve"}
    assert target[1]["tool_call_id"] == "call-1"
    assert target[1]["metadata"][NS]["refs"][0]["provenance"] == REF["provenance"]
    assert (source / "transcript.jsonl").read_bytes() == before


def test_memory_fork_requires_adoption_without_mutating_parent(rows):
    before = deepcopy(rows)
    with pytest.raises(ValueError, match="host adoption"):
        fork_session_in_memory(rows, turn=1)
    result = fork_session_in_memory(rows, turn=1, media_adopter=adopter)
    assert result.messages[1]["metadata"][NS]["refs"][0]["session_id"] == result.session_id
    assert rows == before


def test_only_selected_prefix_requires_closure(source, rows):
    rows[1]["metadata"].pop(NS)
    rows[3]["metadata"] = {NS: {"refs": [REF]}}
    (source / "transcript.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows)
    )
    result = fork_session(source, turn=1, include_events=False)
    assert result.message_count == 2


@pytest.mark.skipif(os.name != "posix", reason="POSIX media checkpoint qualification")
def test_adoption_failure_preserves_parent_and_partial_assets_not_canonical(source, tmp_path):
    original = (source / "transcript.jsonl").read_bytes()

    def fail(messages, *, session_id, session_dir):
        messages[0]["content"] = "not-parent"
        (session_dir / "synthetic-asset").write_text("orphan-evidence")
        raise ValueError("synthetic copy failure")

    with pytest.raises(ValueError, match="synthetic"):
        fork_session(source, turn=1, new_session_id="failed", include_events=False,
                     media_adopter=fail, media_checkpoint=checkpoint)
    assert (source / "transcript.jsonl").read_bytes() == original
    assert not (tmp_path / "failed" / "transcript.jsonl").exists()
    assert (tmp_path / "failed" / "synthetic-asset").exists()
    with pytest.raises(FileExistsError):
        fork_session(source, turn=1, new_session_id="failed", include_events=False,
                     media_adopter=adopter, media_checkpoint=checkpoint)


@pytest.mark.parametrize("change", ["foreign", "role", "lost-row", "malformed"])
def test_bad_adopter_does_not_publish_foreign_canonical_rows(rows, change):
    before = deepcopy(rows)

    def bad(messages, **kwargs):
        if change == "role":
            messages[1]["role"] = "system"
        elif change == "lost-row":
            messages.pop()
        elif change == "malformed":
            messages[1]["metadata"][NS] = {"refs": "not-a-list"}
        return messages

    with pytest.raises(ValueError):
        fork_session_in_memory(rows, turn=1, media_adopter=bad)
    assert rows == before


@pytest.mark.skipif(os.name != "posix", reason="POSIX media checkpoint qualification")
def test_async_checkpoint_does_not_falsely_acknowledge(source):
    async def not_executed(*args, **kwargs):
        raise AssertionError("No implicit async execution")

    with pytest.raises(ValueError, match="must be synchronous"):
        fork_session(source, turn=1, include_events=False,
                     media_adopter=adopter, media_checkpoint=not_executed)


@pytest.mark.skipif(os.name != "posix", reason="POSIX media checkpoint qualification")
@pytest.mark.parametrize("kind", ["generator", "async-generator", "mapping"])
def test_lazy_or_non_none_checkpoint_does_not_falsely_acknowledge(source, kind):
    def generator(*args, **kwargs):
        raise AssertionError("No implicit generator execution")
        yield

    async def async_generator(*args, **kwargs):
        raise AssertionError("No implicit async generator execution")
        yield

    callback = {
        "generator": generator, "async-generator": async_generator,
        "mapping": lambda *args, **kwargs: {"report": "not-an-ack"},
    }[kind]
    with pytest.raises(ValueError, match="return None"):
        fork_session(source, turn=1, include_events=False,
                     media_adopter=adopter, media_checkpoint=callback)