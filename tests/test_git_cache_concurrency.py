"""Concurrent hosts must not remove or consume each other's unfinished clones."""

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from amplifier_foundation.exceptions import BundleNotFoundError
from amplifier_foundation.paths.resolution import parse_uri
from amplifier_foundation.sources import git as git_module
from amplifier_foundation.sources.git import GitSourceHandler


def wait_for(path):
    deadline = time.monotonic() + 15
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(str(path))
        time.sleep(0.01)


def repository(path):
    path.mkdir()
    for args in (
        ["init", "-b", "main"],
        ["config", "user.name", "Fixture"],
        ["config", "user.email", "fixture@example.invalid"],
    ):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    (path / "bundle.md").write_text("---\nbundle:\n  name: fixture\n---\n")
    subprocess.run(
        ["git", "-C", str(path), "add", "."], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "fixture"],
        check=True,
        capture_output=True,
    )
    return "git+" + path.as_uri() + "@main"


@pytest.mark.parametrize("operation", ["resolve", "update"])
def test_processes_share_one_completed_clone(tmp_path, operation):
    uri = repository(tmp_path / "remote")
    other_uri = repository(tmp_path / "other")
    cache = tmp_path / "cache"
    signals = tmp_path / "signals"
    signals.mkdir()
    if operation == "update":
        asyncio.run(GitSourceHandler().resolve(parse_uri(uri), cache))
    worker = Path(__file__).parent / "fixtures/git_cache_worker.py"
    processes = []

    def start(source, name, action):
        process = subprocess.Popen(
            [
                sys.executable,
                str(worker),
                source,
                str(cache),
                str(signals),
                name,
                action,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        processes.append(process)
        return process

    try:
        owner = start(uri, "owner", operation)
        wait_for(signals / "cloning")
        follower = start(uri, "follower", "resolve")
        wait_for(signals / "follower-started")
        # A different repository must remain usable while this clone is held.
        unrelated = start(other_uri, "unrelated", "resolve")
        output, error = unrelated.communicate(timeout=10)
        assert unrelated.returncode == 0, error
        assert json.loads(output)["bundle"].startswith("---")
        assert follower.poll() is None, (
            "A follower consumed or replaced an unfinished clone"
        )
        (signals / "release").touch()
        results = []
        for process in (owner, follower):
            output, error = process.communicate(timeout=15)
            assert process.returncode == 0, error
            results.append(json.loads(output))
        assert results[0] == results[1]
        metadata = Path(results[0]["root"]) / ".amplifier_cache_meta.json"
        assert json.loads(metadata.read_text())["ref"] == "main"
    finally:
        (signals / "release").touch()
        for process in processes:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=10)


@pytest.mark.asyncio
async def test_waiting_for_cache_does_not_block_cancellation(tmp_path):
    from filelock import FileLock

    uri = parse_uri(repository(tmp_path / "remote"))
    cache = tmp_path / "cache"
    cache.mkdir()
    handler = GitSourceHandler()
    path = handler._get_cache_path(uri, cache)
    with FileLock(path.with_name(f".{path.name}.lock")):
        waiter = asyncio.create_task(handler.resolve(uri, cache))
        await asyncio.sleep(0.05)
        assert not waiter.done()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        assert not path.exists()
    result = await asyncio.wait_for(handler.resolve(uri, cache), 10)
    assert (result.active_path / "bundle.md").is_file()


@pytest.mark.asyncio
async def test_failed_clone_releases_cache_for_the_next_client(tmp_path, monkeypatch):
    uri = parse_uri(repository(tmp_path / "remote"))
    handler = GitSourceHandler()
    cache = tmp_path / "cache"

    def fail(args, **kwargs):
        raise subprocess.CalledProcessError(128, args, stderr="fixture failure")

    with monkeypatch.context() as patch:
        patch.setattr(git_module, "_run_git_network_op", fail)
        with pytest.raises(BundleNotFoundError, match="fixture failure"):
            await handler.resolve(uri, cache)
    result = await asyncio.wait_for(handler.resolve(uri, cache), 10)
    assert (result.active_path / "bundle.md").is_file()
