"""Byte-identity probe: main vs branch, factory output, fixed paths.

Run the SAME script against both revisions of ``_prepared.py`` and compare the
sha256 it prints.  Paths are fixed (not pytest tmp_path) so the output is
byte-stable across runs; the only variable is the source revision.

Case A: bundle with NO instruction @mentions  -> must be byte-identical.
Case B: bundle WITH an instruction @mention   -> must change (that is the fix).

Usage:
    uv run python docs/lanes/mikb-instruction-mentions-lead/evidence/byte_identity_probe.py
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import shutil
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from amplifier_foundation.bundle import Bundle

_prep = importlib.import_module("amplifier_foundation.bundle._prepared")
PreparedBundle: Any = _prep.PreparedBundle
BundleModuleResolver: Any = _prep.BundleModuleResolver

ROOT = Path(tempfile.gettempdir()) / "mikb-byte-identity-probe"


def _session() -> MagicMock:
    hooks = AsyncMock()
    hooks.list_handlers = MagicMock(return_value={})
    hooks.register = MagicMock(return_value=MagicMock())
    coordinator = MagicMock()
    coordinator.hooks = hooks
    s = MagicMock()
    s.coordinator = coordinator
    return s


def _write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


async def main() -> None:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    sysmd = _write(ROOT / "context" / "system.md", "You are Amplifier. PRINCIPLES.")
    a = _write(ROOT / "a.md", "AAA awareness body")
    b = _write(ROOT / "b.md", "BBB awareness body")

    cases = {
        "A_no_instruction_mentions": Bundle(
            name="ns",
            base_path=ROOT,
            instruction="Inline instruction, no mentions.",
            context={"a:ctx.md": a, "b:ctx.md": b},
        ),
        "B_with_instruction_mention": Bundle(
            name="ns",
            base_path=ROOT,
            instruction="@ns:context/system.md",
            context={"a:ctx.md": a, "b:ctx.md": b},
        ),
    }

    for label, bundle in cases.items():
        prepared = PreparedBundle(
            mount_plan={},
            bundle=bundle,
            resolver=BundleModuleResolver(module_paths={}),
        )
        out = await prepared._create_system_prompt_factory(bundle, _session())()
        digest = hashlib.sha256(out.encode()).hexdigest()
        marker = "You are Amplifier"
        offset = out.find(marker)
        print(f"{label}: sha256={digest} len={len(out)} '{marker}'@offset={offset}")


if __name__ == "__main__":
    asyncio.run(main())
