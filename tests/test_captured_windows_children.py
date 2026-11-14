"""Captured resolver/install children must not allocate a user's terminal."""

import ast
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "relative,function",
    [
        ("sources/git.py", "_get_local_commit"),
        ("sources/git.py", "_get_remote_commit"),
        ("sources/git.py", "_clone_at_commit"),
        ("sources/shared.py", "clean_checkout"),
        ("sources/shared.py", "_index_refresh_owned"),
        ("sources/shared.py", "verify"),
        ("sources/shared.py", "ensure"),
        ("modules/activator.py", "_install_dependencies_uncached"),
        ("modules/batch.py", "_install"),
    ],
)
def test_captured_resolver_and_installer_flags_are_explicit(relative, function):
    root = Path(__file__).resolve().parents[1] / "amplifier_foundation"
    tree = ast.parse((root / relative).read_text(encoding="utf-8"))
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function
    ]
    assert len(functions) == 1
    calls = [
        node
        for node in ast.walk(functions[0])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
        and node.func.attr in {"run", "check_output", "Popen"}
    ]
    assert calls
    for call in calls:
        flags = next(
            (item.value for item in call.keywords if item.arg == "creationflags"), None
        )
        assert flags is not None, (relative, function, call.lineno)
        assert "CREATE_NO_WINDOW" in ast.unparse(flags)


def test_git_runner_retains_process_group_and_adds_no_window(monkeypatch):
    from unittest.mock import Mock

    from amplifier_foundation.sources import git

    process = Mock(returncode=0)
    process.communicate.return_value = ("ok", "")
    spawn = Mock(return_value=process)
    monkeypatch.setattr(git.platform, "system", lambda: "Windows")
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, "Popen", spawn)
    result = git._run_git_subprocess(["git", "--version"], None, 5)
    assert result.stdout == "ok" and result.returncode == 0
    assert spawn.call_args.kwargs["creationflags"] == 0x08000200
    assert spawn.call_args.kwargs["start_new_session"] is False


@pytest.mark.skipif(os.name != "nt", reason="native Windows console observation")
def test_real_captured_git_owners_do_not_allocate_console():
    from amplifier_foundation.sources import _windows_git, git

    script = (
        "import ctypes as c,json; k=c.WinDLL('kernel32');"
        "k.GetConsoleWindow.restype=c.c_void_p;"
        "print(json.dumps({'console':int(k.GetConsoleWindow() or 0)}))"
    )
    # The actual captured child owners, without network, clone or installation.
    args = [sys.executable, "-I", "-S", "-c", script]
    ordinary = git._run_git_subprocess(args, None, 10)
    assert json.loads(ordinary.stdout) == {"console": 0}
    assert ordinary.returncode == 0
    shared = asyncio.run(_windows_git.check_output(args, timeout=10))
    assert json.loads(shared) == {"console": 0}
