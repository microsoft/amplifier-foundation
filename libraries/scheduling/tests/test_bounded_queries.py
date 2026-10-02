from amplifier_scheduling.store import ScheduleStore
from .test_policy_store import put

def test_bounded_pages_and_due_index_preserve_all_disk_history(tmp_path):
    store=ScheduleStore(tmp_path/'schedules.sqlite3');record=put(store)
    for i in range(250):
        store.put({**record,'id':'schedule-'+str(i),'sessionId':'other','nextDue':1000+i})
    first=store.page('other',limit=10);second=store.page('other',limit=10,after=first['nextCursor'])
    assert len(first['items'])==10 and len(second['items'])==10 and first['items'][0]['id']!=second['items'][0]['id']
    assert [r['id'] for r in store.due(100)]==['one']
    plan=store.db.execute("EXPLAIN QUERY PLAN SELECT value FROM schedules WHERE json_extract(value,'$.status')='active' AND json_extract(value,'$.nextDue')<=? ORDER BY json_extract(value,'$.nextDue') LIMIT ?",(100,50)).fetchall()
    assert 'schedules_due' in str(plan)
    for i in range(150):store.put_run({'id':str(i),'scheduleId':'one','sessionId':'session','dueAt':i,'phase':'completed'})
    assert len(store.runs('session','one'))==150
    assert len(store.run_page('session','one',limit=10)['items'])==10
    store.close()
