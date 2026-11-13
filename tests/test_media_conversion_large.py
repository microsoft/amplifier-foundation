"""Measured huge-single-record proof using disk media, no payload-size allocation."""

import asyncio
import base64
import hashlib
import json
import os
import tracemalloc
from contextlib import contextmanager

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX-only candidate durability qualification")

from amplifier_foundation.session.media_conversion import (
    build_candidate, preview, verify_export,
)
from tests.test_media_conversion import fence, origin


class DiskMedia:
    def __init__(self, root):
        self.root = root
        root.mkdir()

    async def ingest(self, value, *, source_format, origin, policy):
        temporary = self.root / "ingest"
        digest, size = hashlib.sha256(), 0
        with temporary.open("wb") as output:
            while chunk := value["stream"].read(65536):
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        key = digest.hexdigest()
        temporary.rename(self.root / key)
        ref = {"v": 1, "session_id": "fixture", "sha256": key, "bytes": size,
               "width": 1, "height": 1, "mime": value["mime"], "provenance": origin}
        return {"value": ref, "staged_refs": [ref]}

    async def publish(self, refs, **kwargs):
        return {"refs": refs}

    @contextmanager
    def reader(self, ref):
        with (self.root / ref["sha256"]).open("rb") as source:
            yield source


def test_huge_individual_record_string_and_image_bounded_peak(tmp_path):
    # 64 MiB opaque string + 64 MiB base64 in ONE record. Constructed in
    # constant chunks before measuring, never via json.dumps/readline/json.loads.
    source = tmp_path / "huge.jsonl"
    opaque = b"x" * 65536
    pixels = b"original-encoded-image" * 3072  # multiple of three
    encoded = base64.b64encode(pixels)
    with source.open("wb") as output:
        output.write(b'{"role":"tool","tool_call_id":"large","metadata":{"opaque":"')
        for _ in range(1024):
            output.write(opaque)
        output.write(b'"},"content":[{"type":"text","text":"before"},'
                     b'{"type":"image","source":{"type":"base64","media_type":"image/png","data":"')
        for _ in range(768):
            output.write(encoded)
        output.write(b'"}},{"type":"text","text":"after"}]}\n')
    media = DiskMedia(tmp_path / "store")
    tracemalloc.start()
    plan = preview(source, owner="fixture-owner", session_id="fixture", fence=fence)
    result = asyncio.run(build_candidate(
        plan, tmp_path / "op", fence=fence, capability=media, origin_for=origin,
        asset_reader=media.reader, export_assets=True,
    ))
    verify_export(tmp_path / "op/portable")
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(json.dumps({"fixture_bytes": source.stat().st_size,
                      "decoded_image_bytes": result.decoded_bytes,
                      "python_peak_working_bytes": peak}))
    # Measured Python pipeline peak, not an invented process RSS bound.
    assert source.stat().st_size > 128 * 1024 * 1024
    assert peak < 6 * 1024 * 1024
    assert result.decoded_bytes == len(pixels) * 768