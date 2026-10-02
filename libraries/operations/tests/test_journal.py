import pytest
from amplifier_operations import OperationJournal

def event(identity="p1", sequence=1, phase="started", **data):
    return {
        "schemaVersion": 1,
        "eventId": f"{identity}:{sequence}",
        "sequence": sequence,
        "operationId": identity,
        "ownerId": "mount1",
        "source": "tool-bash",
        "kind": "process",
        "phase": phase,
        "at": 1,
        **data,
    }


def output(identity="p1", sequence=2, cursor=0, text="hello\n"):
    return event(
        identity,
        sequence,
        "output",
        chunk={
            "cursor": cursor,
            "next_cursor": cursor + 1,
            "stream": "stdout",
            "text": text,
            "source_bytes": len(text.encode()),
            "binary_output_withheld": False,
        },
    )


def final(identity="p1", sequence=3, **status):
    return event(
        identity,
        sequence,
        "finished",
        status={
            "state": "completed",
            "returncode": 0,
            "output_complete": True,
            "total_output_bytes": 6,
            "ended_at": 2,
            **status,
        },
    )


def test_journal_reconnect_duplicate_and_restart_preserve_evidence(tmp_path):
    path = tmp_path / "operations.db"
    journal = OperationJournal(path)
    journal.ingest("chat1", "runtime1", event())
    journal.ingest("chat1", "runtime1", output())
    assert journal.ingest("chat1", "runtime1", output())[1] is False
    with pytest.raises(ValueError, match="Conflicting"):
        journal.ingest("chat1", "runtime1", output(text="changed"))
    journal.ingest("chat1", "runtime1", final())
    journal.ingest("chat1", "runtime1", event("pending"))
    completed = journal.read("chat1", "p1")
    journal.close()
    restored = OperationJournal(path)
    restored.recover()
    assert restored.read("chat1", "p1") == completed
    assert restored.status("chat1", "pending")["state"] == "outcome_unknown"
    assert restored.status("chat1", "pending")["controlAvailable"] is False
    assert restored.read("chat1", "p1", completed["nextCursor"])["chunks"] == []
    with pytest.raises(ValueError, match="conversation"):
        restored.read("chat2", "p1")
    restored.close()


def test_output_retention_and_source_delivery_gaps_are_distinct(tmp_path):
    journal = OperationJournal(tmp_path / "ops.db", max_output_bytes=6)
    journal.ingest("s", "r", event())
    journal.ingest("s", "r", output())
    journal.ingest("s", "r", output(sequence=3, cursor=1, text="world\n"))
    journal.ingest("s", "r", final(sequence=4, total_output_bytes=12))
    result = journal.read("s", "p1")
    assert result["cursorGap"] is True
    assert result["droppedOutputBytes"] == 6
    assert result["chunks"][0]["text"] == "world\n"
    assert result["captureComplete"] is True
    journal.ingest("s", "r", event("gap"))
    journal.ingest("s", "r", output("gap", sequence=3, cursor=1))
    journal.ingest("s", "r", final("gap", sequence=4, total_output_bytes=12))
    result = journal.read("s", "gap")
    assert result["captureComplete"] is False
    assert result["outputComplete"] is False
    assert result["cursorGap"] is True
    journal.close()


@pytest.mark.parametrize("flag", ["binary_output_withheld", "encoding_loss"])
def test_partial_original_output_never_becomes_complete_on_exit(tmp_path, flag):
    journal = OperationJournal(tmp_path / "ops.db")
    journal.ingest("s", "r", event())
    observation = output()
    observation["chunk"][flag] = True
    journal.ingest("s", "r", observation)
    journal.ingest("s", "r", final())
    result = journal.read("s", "p1")
    assert result["state"] == "completed"
    assert result["streamComplete"] is True
    assert result["captureComplete"] is False
    assert result["outputComplete"] is False
    journal.close()


def test_duplicate_events_cannot_change_bound_owner_or_producer(tmp_path):
    journal = OperationJournal(tmp_path / "ops.db")
    journal.ingest("s", "r", event())
    with pytest.raises(ValueError, match="owner or producer"):
        journal.ingest("s", "another-runtime", event())
    with pytest.raises(ValueError, match="owner or producer"):
        journal.ingest("s", "r", event(sequence=2, source="another-producer"))
    journal.close()

