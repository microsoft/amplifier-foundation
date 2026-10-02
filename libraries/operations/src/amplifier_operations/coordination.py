"""Portable, storage-neutral delivery cursors for authoritative task snapshots.

The caller owns identities, receipts, persistence, authorization and notification.
This module neither schedules work nor stores a second execution registry.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def encode_cursor(identity, sequence, result_id, signal):
    return base64.urlsafe_b64encode(json.dumps(
        [1, identity, sequence, result_id, signal], separators=(",", ":")
    ).encode()).decode().rstrip("=")


def decode_cursor(value, identity):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Invalid delivery cursor")
    try:
        row = json.loads(base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True))
        version, target, sequence, result_id, signal = row
    except (ValueError, TypeError):
        raise ValueError("Invalid delivery cursor") from None
    if version != 1 or target != identity or type(sequence) is not int or sequence < 0 or not isinstance(result_id, str) or not isinstance(signal, str):
        raise ValueError("Delivery cursor belongs to another target or version")
    return sequence, result_id, signal


def delivery(snapshot, after_cursor=None, *, max_bytes=8192, max_results=16):
    """Page results and wake signals without acknowledging undisclosed receipts.

    A retried cursor gives repeatable delivery. Advancing nextCursor acknowledges
    only returned receipts; clients deduplicate by receipt ID before committing it.
    """
    if not 512 <= max_bytes <= 65536 or not 1 <= max_results <= 64:
        raise ValueError("Delivery bounds are invalid")
    identity = snapshot["identity"]
    rows = snapshot.get("results", [])
    last = snapshot.get("latestSequence", rows[-1]["sequence"] if rows else 0)
    earliest = rows[0]["sequence"] if rows else last + 1
    sequence, result_id, previous_signal = decode_cursor(after_cursor, identity) if after_cursor else (0, "", "")
    anchor = next((row for row in rows if row["sequence"] == sequence), None)
    gap = bool(sequence > last or sequence and anchor and anchor["id"] != result_id or sequence < earliest - 1)
    if sequence > last or anchor and anchor["id"] != result_id:
        # Rewritten histories must never silently reuse a previous receipt ID.
        sequence, result_id = 0, ""
    if gap and not rows:
        sequence, result_id = last, ""
    signal = fingerprint(snapshot.get("signal", {}))
    delivered = []
    remaining = max_bytes
    for row in rows:
        if row["sequence"] <= sequence:
            continue
        if len(delivered) >= max_results or remaining < 1:
            break
        raw = str(row.get("text", "")).encode()
        text = raw[:remaining].decode("utf-8", errors="ignore")
        delivered.append({**row, "text": text, "textTruncated": bool(row.get("sourceTruncated") or len(raw) > remaining), "availableBytes": len(raw)})
        remaining -= len(text.encode())
        sequence, result_id = row["sequence"], row["id"]
    changed = bool(not after_cursor or delivered or gap or (snapshot.get("wakeable", True) and signal != previous_signal))
    return {
        **{key: value for key, value in snapshot.items() if key not in {"results", "signal", "wakeable"}},
        "results": delivered,
        "nextCursor": encode_cursor(identity, sequence, result_id, signal),
        "cursorGap": gap,
        "hasMore": sequence < last,
        "changed": changed,
        "earliestSequence": earliest,
    }


class ChangeSignal:
    """Rotating edge notification; capture before reading to avoid lost wakeups."""
    def __init__(self):
        self.event = asyncio.Event()

    def notify(self):
        previous, self.event = self.event, asyncio.Event()
        previous.set()

    async def wait(self, read, *, wait_ms=0):
        if type(wait_ms) is not int or not 0 <= wait_ms <= 60000:
            raise ValueError("Wait must be between 0 and 60000 milliseconds")
        deadline = asyncio.get_running_loop().time() + wait_ms / 1000
        while True:
            event = self.event
            result = read()
            if result["changed"] or wait_ms == 0:
                return {**result, "timedOut": False}
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return {**result, "timedOut": True}
            try:
                await asyncio.wait_for(event.wait(), remaining)
            except TimeoutError:
                latest = read()
                return {**latest, "timedOut": not latest["changed"]}
