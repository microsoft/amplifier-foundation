"""A disposable host may delegate Git to a longer-lived process owner."""
import asyncio
import subprocess

import pytest

from amplifier_foundation.sources.git import _run_git_subprocess, git_subprocess_runner


def test_scoped_runner_preserves_exact_inputs_result_and_original_error(tmp_path):
    args = ['git', 'clone', 'file:///synthetic/repository']
    result = subprocess.CompletedProcess(args, 0, 'out', 'err')
    calls = []
    def run(command, cwd, timeout):
        calls.append((command, cwd, timeout))
        return result
    with git_subprocess_runner(run):
        assert _run_git_subprocess(args, tmp_path, 17) is result
    assert calls == [(args, tmp_path, 17)]
    failure = OSError('unconfirmed writer cleanup')
    def fail(*args):
        raise failure
    with git_subprocess_runner(fail):
        with pytest.raises(OSError) as caught:
            _run_git_subprocess(args, tmp_path, 17)
    assert caught.value is failure


@pytest.mark.asyncio
async def test_runner_is_task_scoped_and_follows_to_thread_without_cross_talk():
    async def own(label):
        result = subprocess.CompletedProcess(['git'], 0, label, '')
        with git_subprocess_runner(lambda *args: result):
            await asyncio.sleep(0)
            assert await asyncio.to_thread(_run_git_subprocess, ['git'], None, 5) is result
    await asyncio.gather(own('one'), own('two'))


def test_nested_runner_restores_previous_owner_and_default(monkeypatch):
    from amplifier_foundation.sources import git
    first = subprocess.CompletedProcess(['git'], 0, 'one', '')
    second = subprocess.CompletedProcess(['git'], 0, 'two', '')
    with git_subprocess_runner(lambda *args: first):
        with git_subprocess_runner(lambda *args: second):
            assert _run_git_subprocess(['git'], None, 5) is second
        assert _run_git_subprocess(['git'], None, 5) is first
    assert git._git_subprocess_runner.get() is None
