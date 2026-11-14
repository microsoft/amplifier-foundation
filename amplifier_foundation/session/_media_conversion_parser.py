"""Private bounded scanner for canonical media conversion, not JSON-in-text."""

from __future__ import annotations

import codecs
import json
import re
from dataclasses import dataclass
from typing import BinaryIO, Iterator

CHUNK = 64 * 1024
_SPECIAL = re.compile(rb'["\\\x00-\x1f]')
_NUMBER = re.compile(rb"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z")


class Unsupported(ValueError):
    """Malformed or unqualified input; preserve the source."""


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    kind: int


class Scanner:
    def __init__(self, stream: BinaryIO):
        self.stream = stream
        self.buffer = b""
        self.index = 0
        self.base = stream.tell()

    @property
    def offset(self) -> int:
        return self.base + self.index

    def seek(self, offset: int) -> None:
        self.stream.seek(offset)
        self.base = offset
        self.buffer = b""
        self.index = 0

    def peek(self) -> int:
        if self.index == len(self.buffer):
            self.base += self.index
            self.index = 0
            self.buffer = self.stream.read(CHUNK)
        return self.buffer[self.index] if self.buffer else -1

    def byte(self) -> int:
        value = self.peek()
        if value < 0:
            raise Unsupported("Unexpected end of JSON")
        self.index += 1
        return value

    def expect(self, value: int) -> None:
        if self.byte() != value:
            raise Unsupported("Unexpected JSON delimiter")

    def whitespace(self) -> None:
        # Newlines delimit canonical JSONL records, never interior whitespace.
        while self.peek() in (9, 13, 32):
            self.index += 1

    def string_chunks(self) -> Iterator[bytes]:
        """Decode one JSON string into bounded UTF-8 chunks, strictly."""
        self.expect(34)
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        try:
            while True:
                if self.peek() < 0:
                    raise Unsupported("Unterminated JSON string")
                match = _SPECIAL.search(self.buffer, self.index)
                end = match.start() if match else len(self.buffer)
                if end > self.index:
                    chunk = self.buffer[self.index:end]
                    self.index = end
                    decoder.decode(chunk, final=False)
                    yield chunk
                if match is None:
                    continue
                value = self.byte()
                decoder.decode(b"", final=True)
                decoder.reset()
                if value == 34:
                    return
                if value != 92:
                    raise Unsupported("Control character in JSON string")
                escape = self.byte()
                short = {
                    34: b'"', 92: b"\\", 47: b"/", 98: b"\b",
                    102: b"\f", 110: b"\n", 114: b"\r", 116: b"\t",
                }
                if escape in short:
                    yield short[escape]
                    continue
                if escape != 117:
                    raise Unsupported("Invalid JSON escape")
                raw = bytes(self.byte() for _ in range(4))
                if not re.fullmatch(rb"[0-9a-fA-F]{4}", raw):
                    raise Unsupported("Invalid Unicode escape")
                point = int(raw, 16)
                if 0xD800 <= point <= 0xDBFF:
                    self.expect(92)
                    self.expect(117)
                    low = bytes(self.byte() for _ in range(4))
                    if not re.fullmatch(rb"[0-9a-fA-F]{4}", low):
                        raise Unsupported("Invalid Unicode surrogate")
                    low_point = int(low, 16)
                    if not 0xDC00 <= low_point <= 0xDFFF:
                        raise Unsupported("Invalid Unicode surrogate pair")
                    point = 0x10000 + ((point - 0xD800) << 10) + low_point - 0xDC00
                elif 0xDC00 <= point <= 0xDFFF:
                    raise Unsupported("Unpaired Unicode surrogate")
                yield chr(point).encode("utf-8")
        except UnicodeError as exc:
            raise Unsupported("Invalid UTF-8 in JSON string") from exc

    def small_string(self, limit: int = 4096) -> str:
        output = bytearray()
        for chunk in self.string_chunks():
            if len(output) + len(chunk) > limit:
                raise Unsupported("Inspected JSON string exceeds adapter bound")
            output.extend(chunk)
        return output.decode("utf-8")

    def value(self, depth: int = 0) -> Span:
        if depth > 128:
            raise Unsupported("JSON depth exceeds adapter bound")
        self.whitespace()
        start, kind = self.offset, self.peek()
        if kind == 34:
            for _ in self.string_chunks():
                pass
        elif kind in (123, 91):
            self.byte()
            self.whitespace()
            closing = 125 if kind == 123 else 93
            if self.peek() != closing:
                while True:
                    if kind == 123:
                        for _ in self.string_chunks():
                            pass
                        self.whitespace()
                        self.expect(58)
                    self.value(depth + 1)
                    self.whitespace()
                    if self.peek() == closing:
                        break
                    self.expect(44)
                    self.whitespace()
            self.expect(closing)
        else:
            token = bytearray()
            while self.peek() not in (-1, 9, 10, 13, 32, 44, 93, 125):
                if len(token) == 128:
                    raise Unsupported("JSON primitive exceeds adapter bound")
                token.append(self.byte())
            raw = bytes(token)
            if raw not in (b"true", b"false", b"null") and not _NUMBER.fullmatch(raw):
                raise Unsupported("Invalid JSON primitive")
        return Span(start, self.offset, kind)

    def fields(self, span: Span) -> dict[str, Span]:
        """Index a known bounded-schema object; opaque values stay spans."""
        if span.kind != 123:
            raise Unsupported("Expected canonical object")
        self.seek(span.start)
        self.expect(123)
        self.whitespace()
        output: dict[str, Span] = {}
        if self.peek() != 125:
            while True:
                key = self.small_string()
                if key in output or len(output) >= 256:
                    raise Unsupported("Duplicate or excessive inspected object members")
                self.whitespace()
                self.expect(58)
                output[key] = self.value()
                self.whitespace()
                if self.peek() == 125:
                    break
                self.expect(44)
                self.whitespace()
        self.expect(125)
        return output

    def array(self, span: Span) -> Iterator[Span]:
        if span.kind != 91:
            raise Unsupported("Expected canonical array")
        self.seek(span.start)
        self.expect(91)
        self.whitespace()
        if self.peek() != 93:
            while True:
                child = self.value()
                next_offset = self.offset
                yield child
                self.seek(next_offset)
                self.whitespace()
                if self.peek() == 93:
                    break
                self.expect(44)
                self.whitespace()
        self.expect(93)

    def small(self, span: Span, limit: int = 16384):
        if span.end - span.start > limit:
            raise Unsupported("Inspected canonical value exceeds adapter bound")
        self.seek(span.start)
        raw = self.stream.read(span.end - span.start)
        return json.loads(raw)

    def records(self) -> Iterator[Span]:
        """Canonical v1 JSONL only. Blank lines/mixed formats are refused."""
        self.seek(0)
        while self.peek() >= 0:
            start = self.offset
            record = self.value()
            if record.kind != 123:
                raise Unsupported("Canonical record must be an object")
            while self.peek() in (9, 13, 32):
                self.index += 1
            if self.peek() not in (-1, 10):
                raise Unsupported("Canonical records must be newline separated")
            if self.peek() == 10:
                self.byte()
            next_offset = self.offset
            if record.start != start:
                raise Unsupported("Blank/indented canonical record is unsupported")
            yield record
            self.seek(next_offset)


def copy_span(stream: BinaryIO, output: BinaryIO, start: int, end: int) -> None:
    stream.seek(start)
    while start < end:
        chunk = stream.read(min(CHUNK, end - start))
        if not chunk:
            raise Unsupported("Source shortened while copying")
        output.write(chunk)
        start += len(chunk)