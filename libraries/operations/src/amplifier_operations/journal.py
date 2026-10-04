"""SQLite operation receipts/output with bounded reads and explicit uncertainty."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import threading
import time
from pathlib import Path

ACTIVE = {"queued", "running", "cancel_requested"}
TERMINAL = {"completed", "failed", "cancelled", "outcome_unknown", "interrupted"}

_TABLES = {
    "operations": "CREATE TABLE operations(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,value TEXT NOT NULL)",
    "operation_events": "CREATE TABLE operation_events(operation_id TEXT NOT NULL,sequence INTEGER NOT NULL,event_id TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(operation_id,sequence),UNIQUE(operation_id,event_id))",
    "operation_output": "CREATE TABLE operation_output(operation_id TEXT NOT NULL,cursor INTEGER NOT NULL,bytes INTEGER NOT NULL,value TEXT NOT NULL,PRIMARY KEY(operation_id,cursor))",
}
_REQUESTS_SQL = "CREATE TABLE operation_requests(session_id TEXT NOT NULL,id TEXT NOT NULL,signature TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(session_id,id))"


def _check_schema(db, *, require_requests=False, requests_only=False):
    names = tuple(_TABLES) + ("operation_requests",)
    found = {name: (kind, sql) for name, kind, sql in db.execute(
        "SELECT name,type,substr(sql,1,4097) FROM sqlite_master WHERE name IN (?,?,?,?)", names
    )}
    expected = {} if requests_only else dict(_TABLES)
    if require_requests or "operation_requests" in found:
        expected["operation_requests"] = _REQUESTS_SQL
    normalize = lambda sql: re.sub(r"\s+", "", sql).lower().replace("ifnotexists", "")
    for name, sql in expected.items():
        actual = found.get(name)
        if (actual is None or actual[0] != "table" or not isinstance(actual[1], str)
                or len(actual[1]) >= 4097 or normalize(actual[1]) != normalize(sql)):
            raise ValueError("Existing operation store is missing required authority schema: " + name)
        db.execute("SELECT * FROM " + name + " LIMIT 0")


class OperationJournal:
    def __init__(self, path, *, max_output_bytes=10_000_000, require_requests=False):
        if type(require_requests) is not bool:
            raise ValueError("require_requests must select a declared boolean profile")
        self.path = Path(path).absolute()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        for candidate in (self.path, *(Path(str(self.path) + suffix) for suffix in ("-wal", "-shm", "-journal"))):
            if os.path.lexists(candidate) and not stat.S_ISREG(candidate.lstat().st_mode):
                raise ValueError("Authority database and sidecars must be regular files, without symlinks")
        new = not os.path.lexists(self.path)
        if new and any(os.path.lexists(str(self.path) + suffix)
                       for suffix in ("-wal", "-shm", "-journal")):
            raise ValueError("Operation store retains sidecars without its main database")
        if not new:
            reader = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
            try:
                _check_schema(reader, require_requests=require_requests)
            finally:
                reader.close()
        else:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        self.db = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, check_same_thread=False)
        try:
            self.path.chmod(0o600)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            if new:
                tables = list(_TABLES.values()) + ([_REQUESTS_SQL] if require_requests else [])
                self.db.executescript("BEGIN;" + ";".join(tables) + ";COMMIT;")
            self.db.executescript("""
            CREATE INDEX IF NOT EXISTS operations_session ON operations(session_id);
            CREATE INDEX IF NOT EXISTS operations_order ON operations(session_id,json_extract(value,'$.createdAt') DESC,id DESC);
            CREATE INDEX IF NOT EXISTS operations_state ON operations(json_extract(value,'$.state'));
            """)
        except BaseException:
            self.db.close()
            raise
        # The request mechanism can attach to a genuinely new journal in this
        # same lifetime. Existing standalone journals never imply migration.
        self._new_requests_allowed = new and not require_requests
        self.lock = threading.RLock()
        self.max_output_bytes = max_output_bytes

    def _record(self, identity, session_id):
        row = self.db.execute(
            "SELECT session_id,value FROM operations WHERE id=?", (identity,)
        ).fetchone()
        if row is None or row[0] != session_id:
            raise ValueError("Operation not found in this conversation")
        return json.loads(row[1])

    def _save(self, record):
        self.db.execute(
            "INSERT OR REPLACE INTO operations VALUES (?,?,?)",
            (record["id"], record["sessionId"], json.dumps(record)),
        )

    def recover(
        self, session_id=None, reason="The host restarted; work was not replayed."
    ):
        """Invalidate only unobserved work. Preserve exact terminal evidence."""
        with self.lock, self.db:
            rows = self.db.execute(
                "SELECT value FROM operations WHERE json_extract(value,'$.state') IN ('queued','running','cancel_requested')"
                + (" AND session_id=?" if session_id else ""),
                (session_id,) if session_id else (),
            ).fetchall()
            for row in rows:
                value = json.loads(row[0])
                if value["state"] in ACTIVE:
                    value.update(
                        state="outcome_unknown",
                        interruptionReason=reason,
                        updatedAt=time.time(),
                        revision=value["revision"] + 1,
                        outputComplete=False,
                        controlAvailable=False,
                    )
                    self._save(value)

    def ingest(self, session_id, runtime_session_id, event):
        if (
            event.get("schemaVersion") != 1
            or not isinstance(event.get("source"), str)
            or not isinstance(event.get("kind"), str)
            or not 1 <= len(event["source"]) <= 100
            or not 1 <= len(event["kind"]) <= 50
        ):
            raise ValueError("Unsupported operation observation schema")
        identity = event.get("operationId")
        sequence = event.get("sequence")
        event_id = event.get("eventId")
        if (
            not isinstance(identity, str)
            or not 1 <= len(identity) <= 200
            or not isinstance(event_id, str)
            or not 1 <= len(event_id) <= 240
            or isinstance(sequence, bool)
            or not isinstance(sequence, int)
            or sequence < 1
        ):
            raise ValueError("Invalid operation event identity or sequence")
        phase = event.get("phase")
        if phase not in {"started", "output", "state", "finished"}:
            raise ValueError("Invalid operation event phase")
        encoded = json.dumps(event)
        if len(encoded.encode()) > 65536:
            raise ValueError("Operation event exceeds 64 KB")
        with self.lock, self.db:
            previous = self.db.execute(
                "SELECT session_id,value FROM operations WHERE id=?", (identity,)
            ).fetchone()
            if previous and previous[0] != session_id:
                raise ValueError("Operation belongs to another conversation")
            if previous:
                prior = json.loads(previous[1])
                if (
                    prior["runtimeSessionId"] != runtime_session_id
                    or prior.get("ownerId") != event.get("ownerId")
                    or prior["producer"] != event["source"]
                    or prior["kind"] != event["kind"]
                ):
                    raise ValueError("Operation observation owner or producer changed")
            duplicate = self.db.execute(
                "SELECT value FROM operation_events WHERE operation_id=? AND (sequence=? OR event_id=?)",
                (identity, sequence, event_id),
            ).fetchone()
            if duplicate:
                if duplicate[0] not in {
                    encoded,
                    "sha256:" + hashlib.sha256(encoded.encode()).hexdigest(),
                }:
                    raise ValueError("Conflicting duplicate operation event")
                return self._record(identity, session_id), False
            value = (
                json.loads(previous[1])
                if previous
                else {
                    "id": identity,
                    "sessionId": session_id,
                    "runtimeSessionId": runtime_session_id,
                    "source": event["kind"],
                    "producer": event["source"],
                    "sourceId": identity,
                    "kind": event["kind"],
                    "state": "running",
                    "createdAt": time.time(),
                    "revision": 0,
                    "sequence": 0,
                    "outputComplete": False,
                    "captureComplete": True,
                    "controlAvailable": True,
                    "earliestCursor": 0,
                    "latestCursor": 0,
                    "droppedOutputBytes": 0,
                    "totalOutputBytes": 0,
                    "retainedOutputBytes": 0,
                    "retainedChunks": 0,
                    "ownerId": event.get("ownerId"),
                    "returncode": None,
                }
            )
            if value["runtimeSessionId"] != runtime_session_id or value.get(
                "ownerId"
            ) != event.get("ownerId"):
                raise ValueError("Operation observation owner changed")
            if sequence <= value["sequence"]:
                raise ValueError("Out-of-order operation observation")
            if sequence != value["sequence"] + 1 or event.get(
                "observerDroppedEvents", 0
            ):
                value["captureComplete"] = False
            # Only real final evidence from the same bound mount can settle a
            # previous unknown outcome. Observation never restores execution.
            if value["state"] in TERMINAL and (
                phase != "finished" or value["state"] != "outcome_unknown"
            ):
                raise ValueError("Operation already has terminal evidence")
            if phase == "output":
                chunk = event.get("chunk")
                if (
                    not isinstance(chunk, dict)
                    or chunk.get("stream") not in {"stdout", "stderr"}
                    or not isinstance(chunk.get("text"), str)
                ):
                    raise ValueError("Invalid output chunk")
                cursor = chunk.get("cursor")
                size = chunk.get("source_bytes")
                if (
                    isinstance(cursor, bool)
                    or not isinstance(cursor, int)
                    or cursor < value["latestCursor"]
                    or isinstance(size, bool)
                    or not isinstance(size, int)
                    or not 0 <= size <= 4096
                    or chunk.get("next_cursor") != cursor + 1
                ):
                    raise ValueError("Invalid output cursor or size")
                if (
                    cursor != value["latestCursor"]
                    or chunk.get("binary_output_withheld")
                    or chunk.get("encoding_loss")
                ):
                    value["captureComplete"] = False
                storage_bytes = max(size, len(chunk["text"].encode("utf-8")))
                self.db.execute(
                    "INSERT INTO operation_output VALUES (?,?,?,?)",
                    (identity, cursor, storage_bytes, json.dumps(chunk)),
                )
                value["latestCursor"] = cursor + 1
                value["totalOutputBytes"] += size
                value["retainedOutputBytes"] += storage_bytes
                # Budget by bytes and row count so one-byte writes stay bounded.
                value["retainedChunks"] = value.get("retainedChunks", 0) + 1
                while (
                    value["retainedOutputBytes"] > self.max_output_bytes
                    or value["retainedChunks"] > 10000
                ):
                    old_cursor, old_size, old_json = self.db.execute(
                        "SELECT cursor,bytes,value FROM operation_output WHERE operation_id=? ORDER BY cursor LIMIT 1",
                        (identity,),
                    ).fetchone()
                    self.db.execute(
                        "DELETE FROM operation_output WHERE operation_id=? AND cursor=?",
                        (identity, old_cursor),
                    )
                    value["retainedOutputBytes"] -= old_size
                    value["retainedChunks"] -= 1
                    value["droppedOutputBytes"] += json.loads(old_json)["source_bytes"]
                oldest = self.db.execute(
                    "SELECT MIN(cursor) FROM operation_output WHERE operation_id=?",
                    (identity,),
                ).fetchone()[0]
                value["earliestCursor"] = (
                    oldest if oldest is not None else value["latestCursor"]
                )
            else:
                status = event.get("status", {})
                state = status.get("state", "running")
                if state not in ACTIVE | TERMINAL:
                    raise ValueError("Invalid observed operation state")
                if phase == "finished" and state in ACTIVE:
                    raise ValueError("Final event has no terminal evidence")
                if (
                    phase == "finished"
                    and value["kind"] == "process"
                    and state in {"completed", "failed", "cancelled"}
                    and (
                        isinstance(status.get("returncode"), bool)
                        or not isinstance(status.get("returncode"), int)
                    )
                ):
                    raise ValueError(
                        "Terminal process result requires observed exit code"
                    )
                if (
                    value["kind"] == "process"
                    and state == "completed"
                    and status.get("returncode") != 0
                ):
                    raise ValueError("Completed process requires zero exit code")
                if status.get("capture_complete") is False:
                    value["captureComplete"] = False
                value.update(
                    state=state,
                    returncode=status.get("returncode"),
                    cancellationRequested=bool(status.get("cancellation_requested")),
                    terminationReason=status.get("termination_reason"),
                    streamComplete=bool(status.get("output_complete")),
                    outputComplete=bool(status.get("output_complete"))
                    and value["captureComplete"]
                    and value["droppedOutputBytes"] == 0,
                    controlAvailable=state in ACTIVE,
                    pid=status.get("pid"),
                    timeout=status.get("timeout"),
                    pty=status.get("pty") is True,
                    stdinAllowed=status.get("stdin_allowed") is True,
                    stdinClosed=status.get("stdin_closed") is True,
                    outputStreams=status.get("output_streams"),
                    eofSemantics=status.get("eof_semantics"),
                    questionIds=status.get("question_ids", []),
                )
                # A source's local ring can truncate while this journal still
                # captured everything. Only observer gaps affect journal capture.
                if phase == "finished":
                    value["endedAt"] = status.get("ended_at") or time.time()
                    if status.get("total_output_bytes", 0) != value["totalOutputBytes"]:
                        value["captureComplete"] = value["outputComplete"] = False
            if "metadata" in event:
                if not isinstance(event["metadata"], dict):
                    raise ValueError("Operation metadata must be an object")
                value["metadata"] = event["metadata"]
            if phase == "finished":
                value["evidence"] = {
                    key: status[key]
                    for key in ("result", "error", "resultTruncated")
                    if key in status
                }
            value.update(
                sequence=sequence, revision=value["revision"] + 1, updatedAt=time.time()
            )
            self._save(value)
            # Event identities remain durable for dedup, but output text lives
            # in one bounded output table, not a second unlimited event copy.
            self.db.execute(
                "INSERT INTO operation_events VALUES (?,?,?,?)",
                (identity, sequence, event_id, encoded),
            )
            # Hash output event bodies so dedup evidence does not duplicate
            # the output archive. Retain the latest 10000 source identities.
            if phase == "output":
                self.db.execute(
                    "UPDATE operation_events SET value=? WHERE operation_id=? AND sequence=?",
                    (
                        "sha256:" + hashlib.sha256(encoded.encode()).hexdigest(),
                        identity,
                        sequence,
                    ),
                )
            self.db.execute(
                "DELETE FROM operation_events WHERE operation_id=? AND sequence<?",
                (identity, sequence - 10000),
            )
            return value, True

    def status(self, session_id, identity):
        with self.lock:
            return self._record(identity, session_id)

    def list(self, session_id, limit=50, before=None):
        if type(limit) is not int or not 1 <= limit <= 101:raise ValueError('Operation list limit must be1..101')
        with self.lock:
            cursor=None
            if before:
                record=self._record(before,session_id);cursor=(record['createdAt'],record['id'])
            return self.page(session_id,limit=limit,cursor=cursor)

    def page(self, session_id, *, limit=50, cursor=None):
        if type(limit) is not int or not 1 <= limit <= 101:raise ValueError('Operation page limit must be1..101')
        where='session_id=?';args=[session_id]
        if cursor is not None:
            if not isinstance(cursor,(tuple,list)) or len(cursor)!=2 or not isinstance(cursor[0],(int,float)) or not isinstance(cursor[1],str):raise ValueError('Invalid operation cursor')
            where+=" AND (json_extract(value,'$.createdAt'),id)<(?,?)";args.extend(cursor)
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute('SELECT value FROM operations WHERE '+where+" ORDER BY json_extract(value,'$.createdAt') DESC,id DESC LIMIT ?",(*args,limit))]

    def read(self, session_id, identity, cursor=0, max_bytes=16384):
        if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
            raise ValueError("Output cursor must be a nonnegative integer")
        if not isinstance(max_bytes, int) or not 4096 <= max_bytes <= 100000:
            raise ValueError("Output page budget must be 4096–100000 source bytes")
        with self.lock:
            value = self._record(identity, session_id)
            if cursor > value["latestCursor"]:
                raise ValueError("Output cursor is ahead of observed output")
            chunks, size, next_cursor = [], 0, max(cursor, value["earliestCursor"])
            rows = self.db.execute(
                "SELECT value FROM operation_output WHERE operation_id=? AND cursor>=? ORDER BY cursor LIMIT 128",
                (identity, next_cursor),
            )
            gap = cursor < value["earliestCursor"]
            for row in rows:
                chunk = json.loads(row[0])
                if size + chunk["source_bytes"] > max_bytes:
                    break
                gap = gap or chunk["cursor"] != next_cursor
                chunks.append(chunk)
                size += chunk["source_bytes"]
                next_cursor = chunk["next_cursor"]
            return {
                **value,
                "chunks": chunks,
                "cursor": cursor,
                "nextCursor": next_cursor,
                "cursorGap": gap,
                "hasMore": next_cursor < value["latestCursor"],
            }

    def close(self):
        with self.lock:
            self.db.close()
