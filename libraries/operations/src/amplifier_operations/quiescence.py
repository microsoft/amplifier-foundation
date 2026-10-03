"""Durable intake fences for one externally exclusive owner.

Callers own authentication, execution accounting and their lifetime process lock.
A stored fence never expires or causes work to replay. Trusted proof is supplied
by the coordinator, never accepted directly from an untrusted action request.
"""
import json
import sqlite3
from pathlib import Path

class DurableIntakeFence:
    def __init__(self,path):
        path=Path(path)
        self.db=sqlite3.connect(path);path.chmod(0o600)
        self.db.executescript('''PRAGMA journal_mode=WAL;PRAGMA synchronous=FULL;
          CREATE TABLE IF NOT EXISTS fence(id INTEGER PRIMARY KEY CHECK(id=1),value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS releases(fence TEXT PRIMARY KEY,value TEXT NOT NULL);''')
        row=self.db.execute('SELECT value FROM fence WHERE id=1').fetchone();self.fence=json.loads(row[0]) if row else None
        self.calls=0;self.background=0
    def context(self,value):
        if not isinstance(value,dict):raise ValueError('Trusted quiescence mapping required')
        required=('fenceId','commandId','purpose','instanceId','dataScope')
        if any(not isinstance(value.get(key),str) or not 1<=len(value[key])<=200 or any(ord(c)<32 for c in value[key]) for key in required):raise ValueError('Bounded trusted quiescence context required')
        return {key:value[key] for key in required}
    def acquire(self,value,*,pending=0):
        context=self.context(value)
        if type(pending) is not int or pending<0:raise ValueError('Nonnegative active work count required')
        if self.fence:
            if self.fence!=context:raise ValueError('Owner intake already belongs to another exact fence')
            return {'acquired':True,'intakeClosed':True,'fenceId':context['fenceId']}
        if self.db.execute('SELECT 1 FROM releases WHERE fence=?',(context['fenceId'],)).fetchone():raise ValueError('A released fence cannot acquire owner intake again')
        if self.calls or self.background or pending:return {'acquired':False,'executed':False,'reason':'Owner has active admitted work'}
        with self.db:self.db.execute('INSERT INTO fence VALUES(1,?)',(json.dumps(context),))
        self.fence=context
        return {'acquired':True,'intakeClosed':True,'fenceId':context['fenceId']}
    def release(self,value):
        context=self.context(value);outcome=value.get('outcome');proof=value.get('proof')
        if isinstance(proof,dict) and proof.get('verified') is True:
            for key in ('instanceId','receiptId'):
                if not isinstance(proof.get(key),str) or not 1<=len(proof[key])<=200 or any(ord(c)<32 for c in proof[key]):raise ValueError('Bounded release proof identity required')
            proof={key:proof.get(key) for key in ('verified','fenceId','commandId','outcome','instanceId','dataScope','receiptId')}
        receipt={'context':context,'outcome':outcome,'proof':proof}
        if self.fence is None:
            row=self.db.execute('SELECT value FROM releases WHERE fence=?',(context['fenceId'],)).fetchone()
            if row and json.loads(row[0])==receipt:return {'released':True,'intakeClosed':False}
            raise ValueError('No exact retained owner release receipt')
        if self.fence!=context:raise ValueError('Owner release does not identify the current fence')
        if outcome=='unknown':return {'released':False,'intakeClosed':True}
        known=outcome=='unchanged' and proof=={'kind':'admission-refused'}
        if not known:
            if not isinstance(proof,dict) or proof.get('verified') is not True or outcome not in {'ready','unchanged'} or any(proof.get(key)!=context[key] for key in ('fenceId','commandId','dataScope')) or proof.get('outcome')!=outcome or not proof.get('receiptId'):raise ValueError('Exact host-verified release proof required')
            if not isinstance(proof.get('instanceId'),str) or (proof['instanceId']==context['instanceId'])!=(outcome=='unchanged'):raise ValueError('Release running instance does not match its outcome')
        if self.calls or self.background:raise ValueError('Owner work is still in flight')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO releases VALUES(?,?)',(context['fenceId'],json.dumps(receipt)))
            self.db.execute('DELETE FROM fence WHERE id=1')
        self.fence=None;return {'released':True,'intakeClosed':False}
    def close(self):self.db.close()
