"""Regression test for the GAP-007 pattern missed in ZipSourceHandler.

GAP-007 fixed exactly one instance of "a foreign-OS absolute path silently
produces a misleading error" -- in FileSourceHandler.resolve(). An independent
review found the identical defect shape still live in ZipSourceHandler: a
POSIX-authored `zip+file:///home/x/a.zip` resolved on Windows became
`C:\\home\\x\\a.zip` and failed with a bare "Zip file not found", sending the
user hunting for a file that was never expected to exist on that machine.

This is the "if algorithm rejects X, trace what depends on X loading
successfully" miss: the fix was right, but only one of two call sites with the
same vulnerability shape was updated.
"""

from __future__ import annotations

import asyncio
import platform
import tempfile
from pathlib import Path

import pytest
from amplifier_foundation.exceptions import BundleNotFoundError
from amplifier_foundation.paths.resolution import ParsedURI
from amplifier_foundation.sources.zip import ZipSourceHandler


def _foreign_absolute_path() -> str:
    """An absolute path shaped for the OS we are NOT running on."""
    if platform.system() == "Windows":
        return "/home/someone/archive.zip"
    return "C:\\Users\\someone\\archive.zip"


def test_zip_file_foreign_os_path_names_the_real_problem() -> None:
    handler = ZipSourceHandler()
    foreign = _foreign_absolute_path()
    parsed = ParsedURI(
        scheme="zip+file", host="", path=foreign, ref="", subpath=""
    )

    with pytest.raises(BundleNotFoundError) as exc_info:
        asyncio.run(handler.resolve(parsed, Path(tempfile.mkdtemp())))

    message = str(exc_info.value)
    assert "cannot exist on" in message, (
        f"error did not identify this as a cross-OS path problem: {message!r}. "
        "A bare 'Zip file not found' sends the user hunting for a file that "
        "was never expected to exist on this machine."
    )
    assert foreign in message, (
        "the error must echo the path the user actually wrote, not a coerced one"
    )
