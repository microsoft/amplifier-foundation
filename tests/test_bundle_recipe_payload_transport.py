"""Large validator results must travel as data, not as a bash -c argument."""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import stat
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
RECIPE = REPO_ROOT / "recipes/validate-bundle-repo.yaml"
SPOOLED = {
    "repo-discovery": "discovery_results",
    "validate-all-bundles": "individual_validation",
    "tool-placement-analysis": "tool_placement_results",
}
POSIX_RECIPE_EXECUTION = pytest.mark.skipif(
    os.name != "posix",
    reason="Executes Bash recipe steps and verifies POSIX file permissions; Windows ACLs are not qualified.",
)


def _render(template: str, context: dict) -> str:
    """Match recipe substitution for strings and JSON-valued placeholders."""
    def replace(match: re.Match) -> str:
        value = context
        for part in match.group(1).split("."):
            value = value[part]
        return value if isinstance(value, str) else json.dumps(value)

    return re.sub(r"\{\{([^{}]+)\}\}", replace, template)


def _steps() -> dict:
    return {step["id"]: step for step in yaml.safe_load(RECIPE.read_text(encoding="utf-8"))["steps"]}


def _run_bash(command: str, cwd: Path, env: dict, *, timeout: float = 60) -> subprocess.CompletedProcess:
    """Bound execution; signal the owned group only while its leader is unreaped.

    Drained pipes/leader exit do not prove arbitrary descendant containment.
    Once the leader is reaped its group ID is not safe cleanup authority.
    """
    home = cwd / ".validator-home"
    home.mkdir(mode=0o700, exist_ok=True)
    env = dict(env, HOME=str(home), AMPLIFIER_HOME=str(home / ".amplifier"))
    args = ["bash", "-c", command]
    proc = subprocess.Popen(
        args, cwd=cwd, env=env, text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, start_new_session=True,
    )
    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except BaseException as failure:
            # communicate can raise after wait() reaped the leader (e.g. output
            # decoding). Check before BOTH signals, not only before escalation.
            try:
                if proc.poll() is None:
                    with suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.communicate(timeout=1)
                    except subprocess.TimeoutExpired:
                        if proc.poll() is None:
                            with suppress(ProcessLookupError):
                                os.killpg(proc.pid, signal.SIGKILL)
                        proc.communicate(timeout=5)
            except BaseException as cleanup:
                failure.add_note(f"Owned Bash group {proc.pid} cleanup failed: {cleanup}")
            raise
        return subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)
    finally:
        proc.stdout.close()
        proc.stderr.close()


def _run(step: dict, context: dict, cwd: Path, *, scratch: Path | None) -> dict:
    command = _render(step["command"], context)
    # This checks actual argument size, rather than increasing any runner limit.
    assert len(command.encode("utf-8")) < 98_000
    env = dict(os.environ, AMPLIFIER_PYTHON=sys.executable, PYTHONPATH=str(REPO_ROOT))
    env.pop("AMPLIFIER_RECIPE_SCRATCH_DIR", None)
    if scratch is not None:
        env["AMPLIFIER_RECIPE_SCRATCH_DIR"] = str(scratch)
    env.update({name: _render(value, context) for name, value in step.get("env", {}).items()})
    proc = _run_bash(command, cwd, env)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    if step["id"] in SPOOLED:
        payload = Path(result["_payload_file"])
        assert stat.S_IMODE(payload.stat().st_mode) == 0o600
        assert json.loads(payload.read_text(encoding="utf-8")) == {
            key: value for key, value in result.items() if key != "_payload_file"
        }
        assert payload.is_relative_to(cwd)
    context[step["output"]] = result
    return result


def _classification_defaults(context: dict, step: dict) -> None:
    for name in re.findall(r"\{\{([^{}]+)\}\}", step["command"]):
        if name not in context:
            context[name] = {
                "passed": True, "skipped": True, "errors": [], "warnings": [],
            }
    context["packaging_check"] = {"passed": True}


def _assert_large_payload(payload: Path, repo: str) -> None:
    assert payload.stat().st_size > 128 * 1024
    # Real serialized data must remain large without pytest's long temp path.
    raw = payload.read_text(encoding="utf-8")
    assert len(raw.replace(repo, "/r").encode("utf-8")) > 128 * 1024


@pytest.mark.parametrize("kind", ["large", "small", "long-root-only"])
def test_payload_size_oracle_rejects_small_or_path_padded_data(tmp_path: Path, kind: str) -> None:
    repo = "/root-" + "x" * 1000
    payload = tmp_path / "payload.json"
    data = ([repo] * 200 if kind == "long-root-only" else
            {"payload": "x" * (128 * 1024 if kind == "large" else 1)})
    payload.write_text(json.dumps(data), encoding="utf-8")
    if kind == "large":
        _assert_large_payload(payload, repo)
    else:
        with pytest.raises(AssertionError):
            _assert_large_payload(payload, repo)


@POSIX_RECIPE_EXECUTION
def test_large_payloads_execute_producers_and_classifier_losslessly(tmp_path: Path) -> None:
    steps = _steps()
    repo = tmp_path / "repo"
    behaviors = repo / "behaviors"
    behaviors.mkdir(parents=True)
    scratch = tmp_path / "run-scratch"
    scratch.mkdir(mode=0o700)
    # All three payloads still exceed a Linux 128 KiB single-argument ceiling,
    # even with a short root. Length carries the bytes without the old 800x800
    # repeated containing-root YAML parse cost. Each filename remains <255 bytes.
    # No network: the individual validator loads local declarations only.
    for number in range(256):
        name = f"fixture-{number:04d}-" + "x" * 180
        (behaviors / f"{name}.yaml").write_text(
            f"bundle:\n  name: {name}\n  version: 1.0.0\n"
            f"  description: Fixture {number}\n"
            f"tools:\n  - module: tool-fixture-{number:04d}\n"
            f"  - module: tool-root-{number:04d}\n",
            encoding="utf-8",
        )
    root_tools = "".join(f"  - module: tool-root-{n:04d}\n" for n in range(256))
    (repo / "bundle.yaml").write_text(
        "bundle:\n  name: fixture\n  version: 1.0.0\n  description: Fixture\n"
        f"tools:\n{root_tools}", encoding="utf-8",
    )
    # An error at the end must survive classification, not disappear in a prefix.
    (behaviors / "zz-broken.yaml").write_text("bundle: [invalid\n", encoding="utf-8")
    context = {
        "repo_path": str(repo),
        "env_check": {"capabilities": {"foundation_available": True},
                      "validation_mode": "full", "summary": {}},
    }
    for step_id in SPOOLED:
        result = _run(steps[step_id], context, tmp_path, scratch=scratch)
        _assert_large_payload(Path(result["_payload_file"]), str(repo))

    discovery = context["discovery_results"]
    individual = context["individual_validation"]
    tools = context["tool_placement_results"]
    _classification_defaults(context, steps["quality-classification"])
    classified = _run(steps["quality-classification"], context, tmp_path, scratch=scratch)
    _assert_lossless_results(discovery, individual, tools, classified)

    # The renderer no longer puts any of the large results in a bash argument.
    legacy_size = len(steps["quality-classification"]["command"].encode()) + sum(
        len(json.dumps(context[name]).encode()) for name in SPOOLED.values()
    )
    assert legacy_size > 128 * 1024


def _assert_lossless_results(discovery: dict, individual: dict, tools: dict, classified: dict) -> None:
    """The integration test and sensitivity probes use the same lossless oracle."""
    assert discovery["total_count"] == 258
    assert individual["summary"]["total"] == 258
    assert individual["summary"]["failed"] >= 1
    assert len(tools["tool_inventory"]) == 512
    assert len(tools["suggestions"]) == 256
    assert classified["summary"]["total"] == 258
    assert classified["quality_level"] == "critical"
    assert any(bundle["path"].endswith("zz-broken.yaml")
               and bundle["quality"] == "critical" for bundle in classified["bundles"])
    assert len(classified["tool_placement_issues"]) == len(tools["suggestions"])
    assert classified["tool_placement_issues"][-1]["tool"] == tools["suggestions"][-1]["tool"]
    assert classified["tool_placement_issues"][-1]["message"] == tools["suggestions"][-1]["message"]


@pytest.mark.parametrize("fault", [
    "none", "discovery-prefix", "validation-prefix", "lost-error", "inventory-prefix",
    "suggestion-prefix", "classification-prefix", "lost-late-error",
    "issue-prefix", "last-tool", "last-message",
])
def test_lossless_oracle_rejects_truncation_and_missing_late_results(fault: str) -> None:
    """Models exercise assertions only; the integration above uses real payloads."""
    import copy

    discovery = {"total_count": 258}
    individual = {"summary": {"total": 258, "failed": 1}}
    tools = {
        "tool_inventory": [{} for _ in range(512)],
        "suggestions": [{"tool": f"tool-{n}", "message": f"message-{n}"} for n in range(256)],
    }
    classified = {
        "summary": {"total": 258}, "quality_level": "critical",
        "bundles": [{"path": "/r/behaviors/zz-broken.yaml", "quality": "critical"}],
        "tool_placement_issues": copy.deepcopy(tools["suggestions"]),
    }
    if fault == "discovery-prefix":
        discovery["total_count"] -= 1
    elif fault == "validation-prefix":
        individual["summary"]["total"] -= 1
    elif fault == "lost-error":
        individual["summary"]["failed"] = 0
    elif fault == "inventory-prefix":
        tools["tool_inventory"].pop()
    elif fault == "suggestion-prefix":
        tools["suggestions"].pop()
    elif fault == "classification-prefix":
        classified["summary"]["total"] -= 1
    elif fault == "lost-late-error":
        classified["bundles"].clear()
    elif fault == "issue-prefix":
        classified["tool_placement_issues"].pop()
    elif fault == "last-tool":
        classified["tool_placement_issues"][-1]["tool"] = "wrong"
    elif fault == "last-message":
        classified["tool_placement_issues"][-1]["message"] = "truncated"
    if fault == "none":
        _assert_lossless_results(discovery, individual, tools, classified)
    else:
        with pytest.raises(AssertionError):
            _assert_lossless_results(discovery, individual, tools, classified)


@POSIX_RECIPE_EXECUTION
def test_bash_subprocess_home_is_isolated(tmp_path: Path) -> None:
    foreign = tmp_path / "unrelated"
    foreign.mkdir()
    marker = foreign / "registry.json"
    marker.write_bytes(b"unrelated synthetic registry")
    code = (
        "import json,os; from pathlib import Path; "
        "print(json.dumps([str(Path.home()),os.environ['AMPLIFIER_HOME']]))"
    )
    result = _run_bash(
        '"${AMPLIFIER_PYTHON}" -c ' + shlex.quote(code), tmp_path,
        dict(os.environ, AMPLIFIER_PYTHON=sys.executable, HOME=str(foreign), AMPLIFIER_HOME=str(foreign)),
    )
    home = tmp_path / ".validator-home"
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [str(home), str(home / ".amplifier")]
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    assert marker.read_bytes() == b"unrelated synthetic registry"


@POSIX_RECIPE_EXECUTION
def test_bash_timeout_terminates_owned_child_without_signalling_other_group(tmp_path: Path, monkeypatch) -> None:
    real_popen, real_killpg = subprocess.Popen, os.killpg
    ready, reaped = tmp_path / "ready.json", tmp_path / "reaped.json"
    # Parent receives TERM with its child, reaps it, then exits. This distinguishes
    # completed child teardown from merely killing a shell and orphaning Python.
    code = (
        "import json,os,signal,subprocess,sys,time\n"
        "from pathlib import Path\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
        "def stop(signum,frame):\n"
        " child.wait(timeout=3)\n"
        f" Path({str(reaped)!r}).write_text(json.dumps(child.returncode))\n"
        " raise SystemExit(0)\n"
        "signal.signal(signal.SIGTERM,stop)\n"
        f"Path({str(ready)!r}).write_text(json.dumps([os.getpid(),child.pid,os.getpgrp()]))\n"
        "time.sleep(60)\n"
    )
    owned, signals = [], []

    class ReadyPopen(real_popen):
        def communicate(self, *args, **kwargs):
            if not owned:
                owned.append(self)
                deadline = time.monotonic() + 5
                while not ready.exists():
                    assert time.monotonic() < deadline, "timeout fixture did not become ready"
                    time.sleep(0.01)
            return super().communicate(*args, **kwargs)

    def killpg(group, signum):
        assert group == owned[0].pid
        assert group != os.getpgrp() and group != unrelated.pid
        signals.append((group, signum))
        return real_killpg(group, signum)

    with real_popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True,
    ) as unrelated:
        try:
            monkeypatch.setattr(subprocess, "Popen", ReadyPopen)
            monkeypatch.setattr(os, "killpg", killpg)
            with pytest.raises(subprocess.TimeoutExpired) as caught:
                _run_bash(
                    'exec "${AMPLIFIER_PYTHON}" -c ' + shlex.quote(code), tmp_path,
                    dict(os.environ, AMPLIFIER_PYTHON=sys.executable), timeout=0.05,
                )
            assert caught.value.timeout == 0.05  # Original timeout remains failure.
            parent, child, group = json.loads(ready.read_text())
            assert parent == group == owned[0].pid
            assert json.loads(reaped.read_text()) == -signal.SIGTERM
            assert owned[0].returncode == 0 and unrelated.poll() is None
            with pytest.raises(ProcessLookupError):
                os.kill(child, 0)  # Child was reaped, not merely left as a zombie.
            assert signals == [(group, signal.SIGTERM)]
        finally:
            unrelated.terminate()  # Separate, explicitly owned control teardown.
            unrelated.wait(timeout=5)


@POSIX_RECIPE_EXECUTION
def test_bash_decode_failure_after_exit_never_signals_reaped_group(tmp_path: Path, monkeypatch) -> None:
    """communicate may reap successfully before raising while decoding output."""
    real_popen = subprocess.Popen
    owned, failures, signals = [], [], []

    class InvalidOutputPopen(real_popen):
        def __init__(self, *args, **kwargs):
            kwargs["encoding"] = "utf-8"  # Independent of the calling locale.
            super().__init__(*args, **kwargs)
            owned.append(self)

        def communicate(self, *args, **kwargs):
            try:
                return super().communicate(*args, **kwargs)
            except UnicodeDecodeError as error:
                assert self.returncode == 0
                failures.append(error)
                raise

    monkeypatch.setattr(subprocess, "Popen", InvalidOutputPopen)
    # Observe an unsafe request without sending any signal, including pre-fix.
    monkeypatch.setattr(os, "killpg", lambda group, signum: signals.append((group, signum)))
    code = "import os; os.write(1, bytes([255]))"
    with pytest.raises(UnicodeDecodeError) as caught:
        _run_bash(
            'exec "${AMPLIFIER_PYTHON}" -c ' + shlex.quote(code), tmp_path,
            dict(os.environ, AMPLIFIER_PYTHON=sys.executable),
        )
    assert caught.value is failures[0]
    assert len(owned) == 1 and owned[0].returncode == 0
    assert owned[0].stdout.closed and owned[0].stderr.closed
    assert signals == [], "must not signal an already-reaped leader's group ID"


@POSIX_RECIPE_EXECUTION
@pytest.mark.parametrize("interrupted", [False, True])
def test_bash_cleanup_escalation_is_bounded_and_only_targets_its_owned_group(tmp_path, monkeypatch, interrupted):
    import io

    failure = KeyboardInterrupt() if interrupted else subprocess.TimeoutExpired("owned command", 60)
    calls = []

    class Process:
        pid = 123456  # Fake identifier: no real signal is sent by this model.
        returncode = None
        stdout, stderr = io.StringIO(), io.StringIO()

        def poll(self):
            return self.returncode

        def communicate(self, *, timeout):
            calls.append(("communicate", timeout))
            if len(calls) == 1:
                raise failure
            if timeout == 1:
                raise subprocess.TimeoutExpired("owned command", 1)
            self.returncode = -signal.SIGKILL
            return "", ""

    proc = Process()

    def popen(args, **kwargs):
        assert args == ["bash", "-c", "synthetic"]
        assert kwargs["start_new_session"] is True and kwargs["cwd"] == tmp_path
        return proc

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(os, "killpg", lambda group, signum: calls.append(("signal", group, signum)))
    with pytest.raises(type(failure)) as caught:
        _run_bash("synthetic", tmp_path, {})
    assert caught.value is failure
    assert calls == [
        ("communicate", 60), ("signal", proc.pid, signal.SIGTERM),
        ("communicate", 1), ("signal", proc.pid, signal.SIGKILL), ("communicate", 5),
    ]
    assert proc.stdout.closed and proc.stderr.closed


@POSIX_RECIPE_EXECUTION
@pytest.mark.parametrize("phase", ["reaped-during-grace", "signal-failed"])
def test_bash_cleanup_preserves_failure_and_rechecks_leader_before_kill(tmp_path, monkeypatch, phase):
    import io

    failure = subprocess.TimeoutExpired("owned command", 60)
    calls = []

    class Process:
        pid = 123456  # Model only; intercepted signals never reach the OS.
        returncode = None
        stdout, stderr = io.StringIO(), io.StringIO()

        def poll(self):
            return self.returncode

        def communicate(self, *, timeout):
            calls.append(("communicate", timeout))
            if timeout == 60:
                raise failure
            if timeout == 1:
                # Pipes could remain held by a descendant after leader exit.
                # A known reaped ID is not authority to signal that descendant.
                self.returncode = 0
                raise subprocess.TimeoutExpired("owned command", 1)
            assert timeout == 5
            return "", ""

    proc = Process()
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: proc)

    def signal_group(group, signum):
        assert group == proc.pid
        calls.append(("signal", group, signum))
        if phase == "signal-failed":
            raise PermissionError("synthetic group signal refusal")

    monkeypatch.setattr(os, "killpg", signal_group)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        _run_bash("synthetic", tmp_path, {})
    assert caught.value is failure
    assert not any(call[-1] == signal.SIGKILL for call in calls if call[0] == "signal")
    if phase == "reaped-during-grace":
        assert calls == [
            ("communicate", 60), ("signal", proc.pid, signal.SIGTERM),
            ("communicate", 1), ("communicate", 5),
        ]
        assert not getattr(failure, "__notes__", [])
    else:
        assert calls == [("communicate", 60), ("signal", proc.pid, signal.SIGTERM)]
        assert any("cleanup failed" in note and "synthetic group signal refusal" in note
                   for note in failure.__notes__)
    assert proc.stdout.closed and proc.stderr.closed


@POSIX_RECIPE_EXECUTION
def test_payload_files_are_unique_in_workspace_without_runtime_scratch(tmp_path: Path) -> None:
    context = {"repo_path": str(tmp_path)}
    step = _steps()["repo-discovery"]
    first = _run(step, context, tmp_path, scratch=None)
    second = _run(step, context, tmp_path, scratch=None)
    assert first["_payload_file"] != second["_payload_file"]
    for result in (first, second):
        parent = Path(result["_payload_file"]).parent
        assert parent.parent == tmp_path
        assert stat.S_IMODE(parent.stat().st_mode) == 0o700


@pytest.mark.parametrize("contents", [None, "{broken json"])
@POSIX_RECIPE_EXECUTION
def test_classifier_refuses_missing_or_corrupt_payloads(tmp_path: Path, contents: str | None) -> None:
    context = {"repo_path": str(tmp_path)}
    steps = _steps()
    discovered = _run(steps["repo-discovery"], context, tmp_path, scratch=tmp_path)
    context["individual_validation"] = {
        "_payload_file": discovered["_payload_file"],
    }
    payload = tmp_path / "unavailable.json"
    if contents is not None:
        payload.write_text(contents)
    context["tool_placement_results"] = {"_payload_file": str(payload)}
    step = steps["quality-classification"]
    _classification_defaults(context, step)
    env = dict(os.environ, AMPLIFIER_PYTHON=sys.executable)
    env.update({name: _render(value, context) for name, value in step["env"].items()})
    proc = _run_bash(_render(step["command"], context), tmp_path, env, timeout=30)
    assert proc.returncode != 0
    assert not proc.stdout


def test_every_bash_consumer_of_spooled_outputs_receives_paths_only() -> None:
    for step in _steps().values():
        if step.get("type") != "bash":
            continue
        for variable in SPOOLED.values():
            assert "{{" + variable + "}}" not in step["command"], step["id"]


def test_recipe_static_check_with_legacy_default_encoding(monkeypatch) -> None:
    """Exercise the real UTF-8 recipe under a simulated legacy Windows default."""
    read_text = Path.read_text

    def legacy_read_text(path, encoding=None, errors=None, **kwargs):
        if path == RECIPE and encoding is None:
            encoding = "cp1252"
        return read_text(path, encoding=encoding, errors=errors, **kwargs)

    monkeypatch.setattr(Path, "read_text", legacy_read_text)
    assert set(SPOOLED) <= _steps().keys()
    test_every_bash_consumer_of_spooled_outputs_receives_paths_only()