"""Native session history with optional, in-memory activity enrichment.

The transcript is the conversation authority. Context Intelligence events are
observations, never replacement messages. This module neither executes a session
nor installs a logger, and reading never repairs or rewrites source files.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..io.files import _write_atomic, write_with_backup
from .messages import is_real_user_message
from .shared_state import FileStamp, file_stamp
from .store import METADATA_FILENAME, TRANSCRIPT_FILENAME

CI_EVENTS_PATH = Path("context-intelligence") / "events.jsonl"


@dataclass(frozen=True)
class HistoryDiagnostic:
    """Payload-free explanation of an incomplete or recovered read."""

    code: str
    source: str
    line: int | None = None
    severity: str = "warning"


class SessionHistoryError(ValueError):
    """Canonical history exists but cannot be read without dropping data."""

    def __init__(self, source: str, diagnostics: list[HistoryDiagnostic]) -> None:
        self.source = source
        self.diagnostics = tuple(diagnostics)
        super().__init__(f"Cannot read valid session {source} or its backup")


@dataclass(frozen=True)
class EventAssociation:
    """Evidence linking an event to transcript positions (all zero-indexed).

    ``method=None`` explicitly means unassociated. No timestamps are used to
    manufacture associations. Auxiliary observations remain available separately.
    """

    event_index: int
    message_indices: tuple[int, ...] = ()
    turn_index: int | None = None
    method: str | None = None
    auxiliary: bool = False
    turn_message_index: int | None = None


@dataclass
class SessionHistory:
    """An ephemeral view over existing files, not another persistence format."""

    messages: list[dict[str, Any]]
    metadata: dict[str, Any]
    events: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: list[HistoryDiagnostic] = field(default_factory=list)
    revision: dict[str, FileStamp | None] = field(default_factory=dict)
    associations: list[EventAssociation] = field(default_factory=list)


def _invalid_constant(_: str) -> Any:
    raise ValueError("Non-finite JSON number")


def _decode(raw: str | bytes) -> Any:
    return json.loads(raw, parse_constant=_invalid_constant)


def _valid_message(message: Any) -> bool:
    return (
        isinstance(message, dict)
        and isinstance(message.get("role"), str)
        and bool(message["role"])
    )


class _InvalidFile(ValueError):
    def __init__(self, line: int | None = None) -> None:
        self.line = line


def _read_messages(path: Path) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    # Decode per line so failures have a useful location without leaking payloads.
    with path.open("rb") as stream:
        for line_number, raw in enumerate(stream, 1):
            if not raw.strip():
                continue
            try:
                message = _decode(raw)
            except (ValueError, UnicodeError):
                raise _InvalidFile(line_number) from None
            if not _valid_message(message):
                raise _InvalidFile(line_number)
            messages.append(message)
    return messages


def _read_metadata(path: Path) -> dict[str, Any]:
    try:
        value = _decode(path.read_bytes())
    except (ValueError, UnicodeError):
        raise _InvalidFile() from None
    if not isinstance(value, dict):
        raise _InvalidFile()
    return value


class SessionHistoryStore:
    """Read/save CLI-compatible transcript and metadata, optionally joining CI.

    Hosts choose directory roots, event-log relocation, sanitization, visibility,
    and repair policy. They must hold their session ownership lock when writing.
    ``save`` replaces metadata; callers merge unknown fields before calling it.
    Writes are atomic *per file*, with the existing CLI ``.backup`` convention;
    the two files are not a multi-file transaction or a power-loss guarantee.
    """

    def __init__(
        self,
        session_dir: str | Path,
        *,
        events_path: str | Path | None = None,
        session_id: str | None = None,
    ) -> None:
        self.session_dir = Path(session_dir)
        self.transcript_path = self.session_dir / TRANSCRIPT_FILENAME
        self.metadata_path = self.session_dir / METADATA_FILENAME
        self.events_path = (
            Path(events_path)
            if events_path is not None
            else self.session_dir / CI_EVENTS_PATH
        )
        self.session_id = (
            session_id if session_id is not None else self.session_dir.name
        )
        self.diagnostics: list[HistoryDiagnostic] = []

    def exists(self) -> bool:
        """Whether any canonical transcript/metadata or their backup exists."""
        return any(path.exists() for path in self._canonical_paths().values())

    def _canonical_paths(self) -> dict[str, Path]:
        return {
            "transcript": self.transcript_path,
            "transcript_backup": self.transcript_path.with_suffix(".jsonl.backup"),
            "metadata": self.metadata_path,
            "metadata_backup": self.metadata_path.with_suffix(".json.backup"),
        }

    def _recover(
        self, path: Path, source: str, reader: Callable[[Path], Any], empty: Any
    ) -> Any:
        present = False
        for candidate, label in (
            (path, source),
            (path.with_suffix(path.suffix + ".backup"), source + "_backup"),
        ):
            try:
                value = reader(candidate)
            except FileNotFoundError:
                continue
            except (_InvalidFile, OSError) as error:
                present = True
                self.diagnostics.append(
                    HistoryDiagnostic(
                        "invalid_file"
                        if isinstance(error, _InvalidFile)
                        else "unreadable_file",
                        label,
                        getattr(error, "line", None),
                    )
                )
                continue
            if candidate != path:
                self.diagnostics.append(HistoryDiagnostic("recovered_backup", source))
            return value
        if present:
            raise SessionHistoryError(source, self.diagnostics)
        return empty

    def load_messages(self) -> list[dict[str, Any]]:
        """Load every canonical row or recover a complete backup; never skip rows.

        Missing files mean an empty history. Corrupt files without a valid backup
        raise ``SessionHistoryError``. Recovery is read-only and is reported in
        ``diagnostics``. This method never reads the event log.
        """
        self.diagnostics = []
        return self._recover(self.transcript_path, "transcript", _read_messages, [])

    def load_metadata(self) -> dict[str, Any]:
        """Load metadata with the same strict, read-only recovery contract."""
        self.diagnostics = []
        return self._recover(self.metadata_path, "metadata", _read_metadata, {})

    def load(self, *, include_events: bool = True) -> SessionHistory:
        """Load an in-memory view, reporting files changed during the read.

        Use ``include_events=False`` for resume: no event data is opened or
        materialized. Use ``iter_events`` for streaming large activity logs.
        A changed-file diagnostic tells hosts to retry under their ownership lock
        if they require a consistent revision before continuing a conversation.
        """
        self.diagnostics = []
        paths = self._canonical_paths()
        if include_events:
            paths["events"] = self.events_path
        before = {key: file_stamp(path) for key, path in paths.items()}
        messages = self._recover(self.transcript_path, "transcript", _read_messages, [])
        metadata = self._recover(self.metadata_path, "metadata", _read_metadata, {})
        events = list(self._iter_events()) if include_events else []
        after = {key: file_stamp(path) for key, path in paths.items()}
        for source, stamp in before.items():
            if stamp != after[source]:
                self.diagnostics.append(
                    HistoryDiagnostic("changed_during_read", source)
                )
        return SessionHistory(
            messages=messages,
            metadata=metadata,
            events=events,
            diagnostics=list(self.diagnostics),
            revision=before,
            associations=associate_events(messages, events),
        )

    def iter_events(
        self, *, max_lines: int | None = None, max_bytes: int | None = None
    ) -> Iterator[dict[str, Any]]:
        """Stream scoped CI events, recording bad/truncated rows in diagnostics.

        An absent optional log is normal. Native ``data`` and extra envelope
        fields are retained. ``event``, ``timestamp``, ``session_id``, and ``line``
        are normalized; unidentified session rows are retained but diagnosed.
        This is tolerant activity reading, never a resume-message source.
        Optional ``max_lines`` bounds physical rows scanned, including blank,
        invalid, and other-session rows. If more input remains, ``scan_limit`` is
        diagnosed. The host chooses this display policy; it is unlimited by
        default. Optional ``max_bytes`` also bounds total raw input bytes; a row
        crossing that budget is not parsed or yielded and reports ``scan_limit``.
        At most one extra byte is consumed to establish that the budget was hit.
        """
        for name, limit in (("max_lines", max_lines), ("max_bytes", max_bytes)):
            if limit is not None and (type(limit) is not int or limit < 1):
                raise ValueError(f"{name} must be a positive integer or None")
        self.diagnostics = []
        yield from self._iter_events(max_lines=max_lines, max_bytes=max_bytes)

    def _iter_events(
        self, *, max_lines: int | None = None, max_bytes: int | None = None
    ) -> Iterator[dict[str, Any]]:
        try:
            stream = self.events_path.open("rb")
        except FileNotFoundError:
            return
        except OSError:
            self.diagnostics.append(HistoryDiagnostic("unreadable_file", "events"))
            return
        try:
            with stream:
                line_number = consumed_bytes = 0
                while max_lines is None or line_number < max_lines:
                    remaining = (
                        max_bytes - consumed_bytes if max_bytes is not None else None
                    )
                    if remaining == 0:
                        if stream.peek(1):
                            self.diagnostics.append(
                                HistoryDiagnostic(
                                    "scan_limit", "events", line_number + 1
                                )
                            )
                        return
                    raw = stream.readline(
                        remaining + 1 if remaining is not None else -1
                    )
                    if not raw:
                        return
                    line_number += 1
                    if remaining is not None and len(raw) > remaining:
                        self.diagnostics.append(
                            HistoryDiagnostic("scan_limit", "events", line_number)
                        )
                        return
                    consumed_bytes += len(raw)
                    if not raw.strip():
                        continue
                    try:
                        record = _decode(raw)
                    except (ValueError, UnicodeError):
                        code = (
                            "invalid_event"
                            if raw.endswith(b"\n")
                            else "incomplete_event"
                        )
                        self.diagnostics.append(
                            HistoryDiagnostic(code, "events", line_number)
                        )
                        continue
                    if (
                        not isinstance(record, dict)
                        or not isinstance(record.get("event"), str)
                        or not record["event"]
                    ):
                        self.diagnostics.append(
                            HistoryDiagnostic("invalid_event", "events", line_number)
                        )
                        continue
                    data = record.get("data", {})
                    if not isinstance(data, dict):
                        self.diagnostics.append(
                            HistoryDiagnostic(
                                "invalid_event_data", "events", line_number
                            )
                        )
                        continue
                    nested_id, outer_id = (
                        data.get("session_id"),
                        record.get("session_id"),
                    )
                    if nested_id and outer_id and nested_id != outer_id:
                        self.diagnostics.append(
                            HistoryDiagnostic(
                                "conflicting_session_ids", "events", line_number
                            )
                        )
                        continue
                    identity = nested_id or outer_id
                    if identity is not None and not isinstance(identity, str):
                        self.diagnostics.append(
                            HistoryDiagnostic(
                                "invalid_session_id", "events", line_number
                            )
                        )
                        continue
                    if identity and identity != self.session_id:
                        continue
                    if not identity:
                        self.diagnostics.append(
                            HistoryDiagnostic("unscoped_event", "events", line_number)
                        )
                    yield {
                        **record,
                        "event": record["event"],
                        "timestamp": record.get("timestamp") or record.get("ts"),
                        "data": data,
                        "session_id": identity,
                        "line": line_number,
                    }
                # Peek at buffered bytes rather than reading/parsing an extra
                # potentially large JSON row just to establish truncation.
                if stream.peek(1):
                    self.diagnostics.append(
                        HistoryDiagnostic("scan_limit", "events", line_number + 1)
                    )
        except OSError:
            self.diagnostics.append(HistoryDiagnostic("unreadable_file", "events"))

    @staticmethod
    def _messages_content(
        messages: list[Any],
        preserve_system: bool,
        sanitizer: Callable[[Any], dict[str, Any]] | None,
    ) -> str:
        if not isinstance(messages, list):
            raise ValueError("messages must be a list")  # noqa: TRY004 - validation API
        lines: list[str] = []
        for message in messages:
            original = (
                message
                if isinstance(message, dict)
                else message.model_dump()
                if hasattr(message, "model_dump")
                else None
            )
            if not _valid_message(original):
                raise ValueError("Each message must be an object with a nonempty role")
            if not preserve_system and original["role"] in ("system", "developer"):
                continue
            value = sanitizer(message) if sanitizer is not None else original
            if not _valid_message(value):
                raise ValueError(
                    "Message sanitizer must return an object with a nonempty role"
                )
            try:
                lines.append(json.dumps(value, ensure_ascii=False, allow_nan=False))
            except (TypeError, ValueError):
                raise ValueError(
                    "Message is not JSON serializable; provide a sanitizer"
                ) from None
        return "\n".join(lines) + ("\n" if lines else "")

    @staticmethod
    def _metadata_content(metadata: dict[str, Any]) -> str:
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")  # noqa: TRY004 - validation API
        try:
            return json.dumps(metadata, indent=2, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError("Metadata is not JSON serializable") from None

    def _prepare_write(self, path: Path, reader: Callable[[Path], Any]) -> bool:
        # Validate existing state before changing either file. Never rotate a
        # damaged primary over the only readable backup. The return value means
        # preserve that backup when replacing the damaged primary.
        try:
            reader(path)
        except FileNotFoundError:
            pass
        except _InvalidFile:
            backup_path = path.with_suffix(path.suffix + ".backup")
            try:
                reader(backup_path)
            except (FileNotFoundError, _InvalidFile):
                raise SessionHistoryError(
                    path.stem, [HistoryDiagnostic("invalid_file", path.name)]
                ) from None
            return True
        return False

    @staticmethod
    def _write_prepared(path: Path, content: str, preserve_backup: bool) -> None:
        if preserve_backup:
            _write_atomic(path, content)
        else:
            write_with_backup(path, content)

    def save(
        self,
        messages: list[Any],
        metadata: dict[str, Any],
        *,
        preserve_system: bool = False,
        sanitizer: Callable[[Any], dict[str, Any]] | None = None,
    ) -> None:
        """Validate both payloads, then replace native files with CLI backups.

        Default role filtering matches CLI. Other sanitization/redaction is host
        policy; JSON-serializable provider continuation fields survive untouched.
        No logger, event log, checkpoint, or additional session body is written.
        """
        transcript_content = self._messages_content(
            messages, preserve_system, sanitizer
        )
        metadata_content = self._metadata_content(metadata)
        preserve_transcript_backup = self._prepare_write(
            self.transcript_path, _read_messages
        )
        preserve_metadata_backup = self._prepare_write(
            self.metadata_path, _read_metadata
        )
        self._write_prepared(
            self.transcript_path, transcript_content, preserve_transcript_backup
        )
        self._write_prepared(
            self.metadata_path, metadata_content, preserve_metadata_backup
        )

    def save_messages(
        self,
        messages: list[Any],
        *,
        preserve_system: bool = False,
        sanitizer: Callable[[Any], dict[str, Any]] | None = None,
    ) -> None:
        """Replace only transcript.jsonl, for incremental context persistence."""
        content = self._messages_content(messages, preserve_system, sanitizer)
        preserve_backup = self._prepare_write(self.transcript_path, _read_messages)
        self._write_prepared(self.transcript_path, content, preserve_backup)

    def save_metadata(self, metadata: dict[str, Any]) -> None:
        """Replace only metadata.json, for naming and other host metadata changes."""
        content = self._metadata_content(metadata)
        preserve_backup = self._prepare_write(self.metadata_path, _read_metadata)
        self._write_prepared(self.metadata_path, content, preserve_backup)


def _tool_ids(message: dict[str, Any], *, results: bool) -> set[str]:
    found: set[str] = set()
    if results and isinstance(message.get("tool_call_id"), str):
        found.add(message["tool_call_id"])
    blocks = message.get("content", [])
    if isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            expected = (
                ("tool_result", "function_call_output")
                if results
                else ("tool_use", "tool_call", "function_call")
            )
            if block.get("type") in expected:
                identity = (
                    block.get("tool_use_id")
                    or block.get("tool_call_id")
                    or block.get("call_id")
                    or block.get("id")
                )
                if isinstance(identity, str):
                    found.add(identity)
    if not results and isinstance(message.get("tool_calls"), list):
        for call in message["tool_calls"]:
            if isinstance(call, dict) and isinstance(call.get("id"), str):
                found.add(call["id"])
    return found


def associate_events(
    messages: list[dict[str, Any]], events: list[dict[str, Any]]
) -> list[EventAssociation]:
    """Associate exact identifiers or verified full prompt sequences, never time.

    Repeated prompts only associate if the complete event prompt sequence equals
    the transcript's human prompt sequence. Otherwise only globally unique exact
    prompt matches qualify. No event produces or changes a message. Unidentified
    session events and auxiliary model calls deliberately remain unassociated.
    """
    declarations: dict[str, set[int]] = defaultdict(set)
    results: dict[str, set[int]] = defaultdict(set)
    message_ids: dict[str, set[int]] = defaultdict(set)
    turns: dict[int, int | None] = {}
    turn_anchors: dict[int, int] = {}
    human: list[tuple[int, str]] = []
    turn: int | None = None
    for index, message in enumerate(messages):
        metadata = (
            message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
        )
        if (
            is_real_user_message(message)
            and not metadata.get("ephemeral")
            and not _tool_ids(message, results=True)
        ):
            turn = 0 if turn is None else turn + 1
            turn_anchors[turn] = index
            if isinstance(message.get("content"), str):
                human.append((index, message["content"]))
        turns[index] = turn
        identity = message.get("message_id") or metadata.get("message_id")
        if isinstance(identity, str):
            message_ids[identity].add(index)
        for identity in _tool_ids(message, results=False):
            declarations[identity].add(index)
        for identity in _tool_ids(message, results=True):
            results[identity].add(index)
    prompts: list[tuple[int, str]] = []
    auxiliary: set[int] = set()
    for index, event in enumerate(events):
        data = event.get("data", {})
        purpose = str(data.get("purpose", "")).lower()
        origin = str(data.get("origin_module", "")).lower()
        if any(
            term in purpose or term in origin
            for term in ("naming", "summariz", "summary", "title")
        ):
            auxiliary.add(index)
        if (
            event.get("event") == "prompt:submit"
            and isinstance(data.get("prompt"), str)
            and event.get("session_id")
        ):
            prompts.append((index, data["prompt"]))
    prompt_matches: dict[int, int] = {}
    if [text for _, text in human] == [text for _, text in prompts]:
        prompt_matches = {
            event_index: message_index
            for (event_index, _), (message_index, _) in zip(prompts, human, strict=True)
        }
    else:
        human_by_text: dict[str, list[int]] = defaultdict(list)
        event_by_text: dict[str, list[int]] = defaultdict(list)
        for index, text in human:
            human_by_text[text].append(index)
        for index, text in prompts:
            event_by_text[text].append(index)
        for text, indices in event_by_text.items():
            if len(indices) == 1 and len(human_by_text.get(text, [])) == 1:
                prompt_matches[indices[0]] = human_by_text[text][0]
    associations: list[EventAssociation] = []
    for index, event in enumerate(events):
        data = event.get("data", {})
        positions: tuple[int, ...] = ()
        method = None
        if event.get("session_id") and index not in auxiliary:
            identity = data.get("message_id")
            if isinstance(identity, str) and len(message_ids.get(identity, ())) == 1:
                positions, method = tuple(message_ids[identity]), "message_id"
            identity = data.get("tool_call_id") or data.get("call_id")
            # Reused IDs are ambiguous even if only one result was persisted.
            if (
                not positions
                and isinstance(identity, str)
                and len(declarations.get(identity, ())) <= 1
                and len(results.get(identity, ())) <= 1
            ):
                matching = (
                    results
                    if event.get("event") in ("tool:post", "tool:error")
                    else declarations
                )
                if len(matching.get(identity, ())) == 1:
                    positions, method = tuple(matching[identity]), "tool_call_id"
            if not positions and index in prompt_matches:
                positions, method = (prompt_matches[index],), "prompt_match"
        matching_turns = {turns[position] for position in positions}
        associated_turn = (
            next(iter(matching_turns)) if len(matching_turns) == 1 else None
        )
        associations.append(
            EventAssociation(
                event_index=index,
                message_indices=positions,
                turn_index=associated_turn,
                method=method,
                auxiliary=index in auxiliary,
                turn_message_index=turn_anchors.get(associated_turn)
                if associated_turn is not None
                else None,
            )
        )
    return associations
