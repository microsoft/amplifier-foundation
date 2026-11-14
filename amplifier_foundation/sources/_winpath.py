"""Address a tree this library owns without the Windows path-length limit.

Windows refuses an ordinary path of 260 characters or more (248 for a new
directory) unless the machine opted in to long paths. That opt-in is off by
default and needs an administrator. Git writes deeper checkouts anyway (see
``_with_longpaths``), so code that walks a checkout file by file must be able
to reach every file Git wrote.

The extended-length form (``\\\\?\\C:\\...``) is accepted by the Win32 wide file
functions regardless of that setting. Windows does not normalize such a path,
so it must be absolute and already normalized. Use it only for traversal of an
owned tree. Never hand it to Git, to a child process as its working directory,
to ``Path.as_uri()``, or to a caller: keep ordinary paths at every boundary and
never compare a prefixed path with an ordinary one.
"""

from __future__ import annotations

import ntpath
import os
from pathlib import Path

__all__ = ["extended", "extended_text"]

_PREFIX = "\\\\?\\"


def extended_text(text: str) -> str:
    """Return the extended-length spelling of an absolute Windows path.

    Pure string work, so every platform can test it. Already-extended input is
    returned unchanged. Relative, drive-relative and device paths are refused:
    Windows would not resolve them in this form.
    """
    if text.startswith(_PREFIX):
        return text
    normal = ntpath.normpath(text)
    drive, rest = ntpath.splitdrive(normal)
    if not drive or not rest.startswith("\\") or normal.startswith("\\\\.\\"):
        raise ValueError(f"An absolute local or UNC path is required: {text}")
    if drive.startswith("\\\\"):
        return _PREFIX + "UNC\\" + normal[2:]
    return _PREFIX + normal


def extended(path):
    """The same location for tree traversal. Off Windows: the argument itself."""
    if os.name != "nt":
        return path
    return Path(extended_text(os.path.abspath(os.fspath(path))))
