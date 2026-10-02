"""Idempotent admission receipts; an uncertain request is never replayed."""

import hashlib
import json
import time


class OperationRequests:
    def __init__(self, journal):
        self.journal = journal
        with journal.lock, journal.db:
            journal.db.execute(
                "CREATE TABLE IF NOT EXISTS operation_requests (session_id TEXT NOT NULL, id TEXT NOT NULL, signature TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(session_id,id))"
            )
            journal.db.execute("CREATE INDEX IF NOT EXISTS operation_requests_state ON operation_requests(json_extract(value,'$.state'))")
            for sid, identity, value in journal.db.execute(
                "SELECT session_id,id,value FROM operation_requests WHERE json_extract(value,'$.state')='admitting'"
            ).fetchall():
                record = json.loads(value)
                if record["state"] == "admitting":
                    record.update(
                        state="outcome_unknown",
                        message="The host restarted before confirming admission. This request was not replayed.",
                        updatedAt=time.time(),
                    )
                    journal.db.execute(
                        "UPDATE operation_requests SET value=? WHERE session_id=? AND id=?",
                        (json.dumps(record), sid, identity),
                    )

    def existing(self, sid, action, args, actor):
        with self.journal.lock:
            found = self.journal.db.execute(
                "SELECT 1 FROM operation_requests WHERE session_id=? AND id=?",
                (sid, args["requestId"]),
            ).fetchone()
        if found:
            return self.begin(sid, action, args, actor)[0]
        return None

    def begin(self, sid, action, args, actor):
        identity = args["requestId"]
        signature = hashlib.sha256(
            json.dumps(
                {"action": action, "args": args, "actor": actor},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        with self.journal.lock, self.journal.db:
            prior = self.journal.db.execute(
                "SELECT signature,value FROM operation_requests WHERE session_id=? AND id=?",
                (sid, identity),
            ).fetchone()
            if prior:
                if prior[0] != signature:
                    raise ValueError(
                        "Request identity already belongs to different operation arguments or actor"
                    )
                return json.loads(prior[1]), False
            record = {
                "requestId": identity,
                "sessionId": sid,
                "action": action,
                "state": "admitting",
                "createdAt": time.time(),
                "updatedAt": time.time(),
            }
            self.journal.db.execute(
                "INSERT INTO operation_requests VALUES (?,?,?,?)",
                (sid, identity, signature, json.dumps(record)),
            )
            return record, True

    def finish(self, record, state, **fields):
        record = {**record, **fields, "state": state, "updatedAt": time.time()}
        with self.journal.lock, self.journal.db:
            self.journal.db.execute(
                "UPDATE operation_requests SET value=? WHERE session_id=? AND id=?",
                (json.dumps(record), record["sessionId"], record["requestId"]),
            )
        return record

    def read(self, sid, identity):
        with self.journal.lock:
            value = self.journal.db.execute(
                "SELECT value FROM operation_requests WHERE session_id=? AND id=?",
                (sid, identity),
            ).fetchone()
        if value is None:
            raise ValueError("Operation request not found in this conversation")
        return json.loads(value[0])

    def list(self, sid):
        with self.journal.lock:
            rows = self.journal.db.execute(
                "SELECT value FROM operation_requests WHERE session_id=? ORDER BY rowid DESC LIMIT 100",
                (sid,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]
