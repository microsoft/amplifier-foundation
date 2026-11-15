"""Deterministic JSON bytes without a whole-string/whole-row serialization.

Accepted schema bounds: depth <=128, <=256 members/object, keys <=4096 UTF-8
bytes and primitive tokens <=128 bytes. The role is a nonempty <=4096 UTF-8
byte string. Other values/arrays/individual strings have no size cap. These
are explicit refusal bounds, not silent truncation.
"""

from __future__ import annotations

import codecs
import json
from collections.abc import Iterator
from typing import Any, BinaryIO

from ._media_conversion_parser import Scanner, Span, Unsupported

STRING_CHARS = 4096


def _string_parts(value: str) -> Iterator[bytes]:
    for start in range(0, len(value), STRING_CHARS):
        part = value[start:start + STRING_CHARS]
        # Match the strict migration scanner; refuse unpaired surrogates.
        part.encode("utf-8")
        yield json.dumps(part, ensure_ascii=True)[1:-1].encode("ascii")


def canonical_bytes(value: Any, depth: int = 0) -> Iterator[bytes]:
    """Equivalent to strict sorted compact ensure_ascii JSON on accepted input."""
    if depth > 128:
        raise Unsupported("JSON depth exceeds adapter bound")
    if type(value) is str:
        yield b'"'
        yield from _string_parts(value)
        yield b'"'
    elif type(value) is dict:
        if len(value) > 256:
            raise Unsupported("Excessive object members")
        for key in value:
            if (type(key) is not str or len(key) > 4096
                    or len(key.encode("utf-8")) > 4096):
                raise Unsupported("Unsupported object key")
        yield b"{"
        for index, key in enumerate(sorted(value)):
            if index:
                yield b","
            yield from canonical_bytes(key, depth + 1)
            yield b":"
            yield from canonical_bytes(value[key], depth + 1)
        yield b"}"
    elif type(value) in (list, tuple):
        yield b"["
        for index, child in enumerate(value):
            if index:
                yield b","
            yield from canonical_bytes(child, depth + 1)
        yield b"]"
    elif value is None or type(value) in (bool, int, float):
        if type(value) is int and value.bit_length() > 426:
            raise Unsupported("JSON primitive exceeds adapter bound")
        raw = json.dumps(value, allow_nan=False).encode("ascii")
        if len(raw) > 128:
            raise Unsupported("JSON primitive exceeds adapter bound")
        yield raw
    else:
        raise Unsupported("Not a canonical JSON value")


def transduce(scanner: Scanner, span: Span, depth: int = 0) -> Iterator[bytes]:
    """Canonicalize a validated source span, retaining no huge scalar value."""
    if depth > 128:
        raise Unsupported("JSON depth exceeds adapter bound")
    scanner.seek(span.start)
    if span.kind == 34:
        yield b'"'
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        for raw in scanner.string_chunks():
            yield from _string_parts(decoder.decode(raw, final=False))
        yield from _string_parts(decoder.decode(b"", final=True))
        yield b'"'
    elif span.kind == 123:
        fields = scanner.fields(span)
        yield b"{"
        for index, key in enumerate(sorted(fields)):
            if index:
                yield b","
            yield from canonical_bytes(key)
            yield b":"
            yield from transduce(scanner, fields[key], depth + 1)
        yield b"}"
    elif span.kind == 91:
        yield b"["
        for index, child in enumerate(scanner.array(span)):
            if index:
                yield b","
            yield from transduce(scanner, child, depth + 1)
        yield b"]"
    else:
        yield from canonical_bytes(scanner.small(span, 128), depth)


def canonical_records(source: BinaryIO) -> Iterator[bytes]:
    """Strict role-bearing canonical-v1 JSONL -> stable canonical JSONL."""
    scanner = Scanner(source)
    for row in scanner.records():
        fields = scanner.fields(row)
        role = fields.get("role")
        value = scanner.small(role, 32768) if role is not None and role.kind == 34 else None
        if type(value) is not str or not value or len(value.encode("utf-8")) > 4096:
            raise Unsupported("Canonical message requires a nonempty role")
        yield from transduce(scanner, row)
        yield b"\n"