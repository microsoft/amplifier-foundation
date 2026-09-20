"""A separate cache client, optionally paused inside its first clone."""

import asyncio
import json
import shutil
import sys
import time
from pathlib import Path

from amplifier_foundation.paths.resolution import parse_uri
from amplifier_foundation.sources import git


def wait_for(path):
    deadline = time.monotonic() + 20
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(str(path))
        time.sleep(0.01)


uri, cache, signals, name, operation = sys.argv[1:]
signals = Path(signals)
if name == "owner":
    original = git._run_git_network_op

    def paused_clone(args, **kwargs):
        destination = Path(args[-1])
        destination.mkdir()
        marker = destination / "clone-in-progress"
        marker.write_text("owned by the first process")
        (signals / "cloning").touch()
        wait_for(signals / "release")
        if not marker.exists():
            raise RuntimeError("Another cache client removed the in-progress clone")
        shutil.rmtree(destination)
        return original(args, **kwargs)

    git._run_git_network_op = paused_clone

(signals / f"{name}-started").touch()
result = asyncio.run(
    getattr(git.GitSourceHandler(), operation)(parse_uri(uri), Path(cache))
)
print(
    json.dumps(
        {
            "root": str(result.source_root),
            "bundle": (result.active_path / "bundle.md").read_text(),
        }
    )
)
