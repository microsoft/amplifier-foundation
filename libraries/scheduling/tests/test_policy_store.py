from datetime import datetime

import pytest
from amplifier_scheduling.policy import instant, normalize, preview, due_occurrence
from amplifier_scheduling.store import ScheduleStore


def daily(time='02:30', start='2026-03-07'):
    return normalize({'kind': 'daily', 'timezone': 'America/Los_Angeles', 'localTime': time, 'startDate': start})


def test_dst_nonexistent_skipped_and_fold_first_once():
    rows = preview(daily(), instant('2026-03-07T00:00:00-08:00'), 3)
    assert [row['local'] for row in rows] == ['2026-03-07T02:30:00-08:00', '2026-03-09T02:30:00-07:00', '2026-03-10T02:30:00-07:00']
    rows = preview(daily('01:30', '2026-10-31'), instant('2026-10-31T00:00:00-07:00'), 4)
    assert len([row for row in rows if row['local'].startswith('2026-11-01')]) == 1
    assert rows[1]['local'] == '2026-11-01T01:30:00-07:00'
    assert rows[2]['local'] == '2026-11-02T01:30:00-08:00'


def test_missed_run_policy_interval_is_bounded_and_exact():
    spec = normalize({'kind': 'interval', 'timezone': 'UTC', 'startAt': '2020-01-01T00:00:00Z', 'intervalSeconds': 60})
    start = instant(spec['startAt']); now = instant('2026-09-20T12:34:45Z')
    latest = due_occurrence(spec, start, now, 'latest')
    assert latest['dueAt'] == instant('2026-09-20T12:34:00Z')
    assert latest['nextDue'] == instant('2026-09-20T12:35:00Z')
    assert not latest['skip']
    skipped = due_occurrence(spec, start, now, 'skip')
    assert skipped['skip'] and skipped['nextDue'] == latest['nextDue']


def put(store, start=100):
    record = {'id': 'one', 'sessionId': 'session', 'revision': 1, 'status': 'active', 'taskId': 'task', 'taskRevision': 2, 'nextDue': start, 'missedRunPolicy': 'latest', 'spec': normalize({'kind': 'interval', 'timezone': 'UTC', 'startAt': datetime.fromtimestamp(start).astimezone().isoformat(), 'intervalSeconds': 60})}
    store.put(record)
    return record


def test_owner_lease_restart_unknown_never_replay_and_no_overlap(tmp_path):
    path = tmp_path / 'schedules.db'
    first, other = ScheduleStore(path, owner='first'), ScheduleStore(path, owner='other')
    put(first)
    assert first.acquire(100)
    assert not other.acquire(101)
    run = first.claim('session', 'one', 100)
    assert first.claim('session', 'one', 160) is None if first.acquire(160) else False
    first.transition('session', run['id'], ['claimed'], 'submitting', 160)
    assert other.acquire(191)
    restored = other.run('session', run['id'])
    assert restored['phase'] == 'unknown'
    assert other.get('session', 'one')['status'] == 'needs_review'
    assert other.claim('session', 'one', 191) is None
    assert not first.owns(191)
    first.close(); other.close()


def test_cas_idempotency_wrong_session_and_bounded_history(tmp_path):
    store = ScheduleStore(tmp_path / 'schedules.db', history_limit=2)
    record = put(store)
    args = {'sessionId': 'session', 'id': 'one', 'expectedRevision': 1}
    def pause(previous): previous['status'] = 'paused'; return previous
    result = store.mutate('pause', args, 'pause', pause)
    assert result['schedule']['revision'] == 2
    assert store.mutate('pause', args, 'pause', pause)['duplicate']
    with pytest.raises(ValueError, match='different'): store.mutate('resume', args, 'pause', pause)
    with pytest.raises(ValueError, match='revision'): store.mutate('pause', args, 'stale', pause)
    with pytest.raises(ValueError, match='belong'): store.get('elsewhere', 'one')
    record.update(status='active', revision=3); store.put(record)
    for now in (100, 160, 220, 280):
        store.acquire(now)
        run = store.claim('session', 'one', now)
        store.transition('session', run['id'], ['claimed'], 'completed', now)
    assert len(store.runs('session', 'one')) == 2
    store.close()


def test_one_shot_and_weekly_preview_share_due_policy():
    once = normalize({'kind': 'once', 'timezone': 'America/New_York', 'startDate': '2026-09-21', 'localTime': '09:00'})
    rows = preview(once, instant('2026-09-20T00:00:00Z'))
    assert len(rows) == 1 and rows[0]['utc'] == '2026-09-21T13:00:00+00:00'
    assert due_occurrence(once, rows[0]['dueAt'], rows[0]['dueAt'] + 86400, 'latest')['nextDue'] is None
    weekly = normalize({'kind': 'weekly', 'timezone': 'UTC', 'startDate': '2026-09-21', 'localTime': '09:00', 'weekdays': [4, 0]})
    assert [row['local'][:10] for row in preview(weekly, instant('2026-09-20T00:00:00Z'), 3)] == ['2026-09-21', '2026-09-25', '2026-09-28']
    with pytest.raises(ValueError, match='does not exist'):
        normalize({'kind': 'once', 'timezone': 'America/Los_Angeles', 'startDate': '2026-03-08', 'localTime': '02:30'})
