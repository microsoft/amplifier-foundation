from amplifier_scheduling import ScheduleStore


def test_original_command_result_survives_later_edits_and_restart(tmp_path):
    path = tmp_path / 'schedules.sqlite'
    store = ScheduleStore(path)
    created = store.mutate('create', {'sessionId': 'one', 'expectedRevision': 0}, 'create-one',
        lambda _: {'id': 'schedule', 'sessionId': 'one', 'status': 'active'})
    store.mutate('pause', {'sessionId': 'one', 'id': 'schedule', 'expectedRevision': 1}, 'pause-one',
        lambda old: {**old, 'status': 'paused'})
    store.close()
    store = ScheduleStore(path)
    try:
        assert store.command_receipt('one', 'create-one')['result'] == created
        assert store.command_receipt('one', 'pause-one')['result']['schedule']['revision'] == 2
        assert store.command_receipt('two', 'create-one') is None
        assert store.command_receipt('one', 'never-admitted') is None
        assert len(store.list('one')) == 1
    finally:
        store.close()


def test_report_receipt_is_visible_only_to_owning_and_destination_conversation(tmp_path):
    import json
    store = ScheduleStore(tmp_path / 'schedules.sqlite')
    try:
        original = {'run': {'id': 'run', 'sessionId': 'source', 'destinationSessionId': 'child', 'revision': 2}}
        store.db.execute('INSERT INTO schedule_commands VALUES(?,?,?)', ('report', 'signature', json.dumps(original)))
        assert store.command_receipt('source', 'report')['result'] == original
        assert store.command_receipt('child', 'report')['result'] == original
        assert store.command_receipt('other', 'report') is None
    finally:
        store.close()


def test_oversized_exact_receipt_is_retained_and_never_returned(tmp_path):
    import json
    import pytest
    store = ScheduleStore(tmp_path / 'schedules.sqlite')
    try:
        original = json.dumps({'schedule': {'sessionId': 'one', 'prompt': 'x' * (384 * 1024)}})
        store.db.execute('INSERT INTO schedule_commands VALUES(?,?,?)', ('large', 'signature', original))
        with pytest.raises(ValueError, match='retained on disk'):
            store.command_receipt('one', 'large')
        assert store.command_receipt('other', 'large') is None
        assert store.db.execute('SELECT result FROM schedule_commands WHERE id=?', ('large',)).fetchone()[0] == original
    finally:
        store.close()
