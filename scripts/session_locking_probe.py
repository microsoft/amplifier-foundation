"""Observe unchanged shared-session ownership; a successful diagnostic is not support."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import traceback

REPO = Path(__file__).resolve().parents[1]
BASE = "ab87882027bc5cb3aa74a6f6de539560b2e4d264"
sys.path.insert(0, str(REPO))


def save(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def exception_record(error: Exception) -> dict:
    frame = traceback.extract_tb(error.__traceback__)[-1].name
    unsupported = (
        type(error) is RuntimeError
        and "POSIX" in str(error)
        and frame == "_ensure_supported"
    )
    result = {"status": "unsupported" if unsupported else "error",
              "exception_type": type(error).__name__, "innermost_function": frame}
    if unsupported:
        result["message"] = str(error)
    return result


def child(fixture: Path, output: Path) -> int:
    # This is an ordinary library import, never an Amplifier application launch.
    from amplifier_foundation.session import shared_state

    source = Path(shared_state.__file__).resolve()
    assert source.is_relative_to(REPO)
    evidence = {
        "os_name": os.name, "python": platform.python_version(),
        "module_relative": source.relative_to(REPO).as_posix(),
        "module_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "fcntl_available": shared_state.fcntl is not None,
    }
    held = None
    try:
        store = shared_state.SharedSessionStore(
            fixture / "workspace", "locking-probe", root=fixture / "state")
        try:
            held = store.acquire(app="session-locking-diagnostic")
        except shared_state.SessionBusyError:
            save(output, dict(evidence, status="busy"))
            return 0
        except Exception as error:
            record = exception_record(error)
            save(output, dict(evidence, **record))
            return 0 if record["status"] == "unsupported" else 1
        held.check()
        held.write([{"role": "user", "content": "isolated diagnostic"}],
                   bundle="diagnostic")
        assert held.read()["messages"][0]["content"] == "isolated diagnostic"
        save(output, dict(evidence, status="acquired", checkpoint_roundtrip=True))
        command = sys.stdin.readline().strip()
        if command == "crash":
            os._exit(23)  # Exercise OS cleanup, with no release/finally.
        if command != "release":
            raise RuntimeError("Unexpected diagnostic protocol command")
        held.release()
        try:
            held.check()
        except RuntimeError as error:
            assert str(error) == "HeldSession is no longer active"
        else:
            raise AssertionError("Released handle remained active")
        save(output, dict(evidence, status="released", released_handle_refused=True))
        return 0
    except Exception as error:
        save(output, dict(evidence, **exception_record(error)))
        return 1
    finally:
        if held is not None:
            held.release()


class Children:
    def __init__(self, fixture: Path):
        self.fixture = fixture
        self.processes: list[subprocess.Popen] = []
        self.observations: list[dict] = []

    def start(self, stage: str) -> tuple[subprocess.Popen, Path, dict]:
        path = self.fixture / (stage + ".json")
        env = dict(os.environ, AMPLIFIER_HOME=str(self.fixture / "native-home"))
        for key in ("AMPLIFIER_SOURCE_STORE", "UV_OVERRIDE", "AMPLIFIER_SESSION_STATE_HOME"):
            env.pop(key, None)
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--child",
             str(self.fixture), "--output", str(path)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            text=True, env=env, cwd=REPO)
        self.processes.append(process)
        deadline = time.monotonic() + 20
        while not path.exists():
            if process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("Child did not produce a bounded observation")
            time.sleep(0.05)
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["os_name"] == os.name
        assert record["python"] == platform.python_version()
        assert record["module_relative"] == "amplifier_foundation/session/shared_state.py"
        self.observations.append(dict(record, stage=stage))
        return process, path, record

    def finish(self, process: subprocess.Popen, path: Path, command: str) -> dict:
        assert process.poll() is None, "Owner exited before the protocol command"
        process.stdin.write(command + "\n")
        process.stdin.flush()
        process.stdin.close()
        assert process.wait(timeout=10) == (23 if command == "crash" else 0)
        record = json.loads(path.read_text(encoding="utf-8"))
        if command == "release":
            assert record["status"] == "released" and record["released_handle_refused"]
        self.observations.append(dict(record, stage=command))
        return record

    def cleanup(self) -> bool:
        clean = True
        for process in self.processes:
            try:
                if process.stdin and not process.stdin.closed:
                    process.stdin.close()
            except OSError:
                clean = False
            try:
                if process.poll() is None:
                    process.terminate()  # Only the exact Popen child, never a broad kill.
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                clean = False
        return clean and all(p.poll() is not None for p in self.processes)


def observe(children: Children) -> dict:
    owner, path, first = children.start("first-acquisition")
    if first["status"] == "unsupported":
        assert owner.wait(timeout=10) == 0
        second, _, repeated = children.start("independent-acquisition")
        assert second.wait(timeout=10) == 0 and repeated["status"] == "unsupported"
        return {
            "verdict": "SESSION_OWNERSHIP_UNSUPPORTED",
            "meaning": "Refused before any lock was attempted; locking was not exercised.",
            "independent_refusals": 2,
            "coordination_state_created": (children.fixture / "state").exists(),
            "native_home_created": (children.fixture / "native-home").exists(),
        }
    assert first["status"] == "acquired" and owner.poll() is None
    contender, _, record = children.start("contender-while-held")
    assert contender.wait(timeout=10) == 0 and record["status"] == "busy"
    assert owner.poll() is None
    children.finish(owner, path, "release")
    next_owner, next_path, record = children.start("reacquire-after-release")
    assert record["status"] == "acquired"
    children.finish(next_owner, next_path, "release")
    crash_owner, crash_path, record = children.start("crash-owner")
    assert record["status"] == "acquired" and crash_owner.poll() is None
    contender, _, record = children.start("contender-before-crash")
    assert contender.wait(timeout=10) == 0 and record["status"] == "busy"
    children.finish(crash_owner, crash_path, "crash")
    recovered, recovered_path, record = children.start("reacquire-after-crash")
    assert record["status"] == "acquired"
    children.finish(recovered, recovered_path, "release")
    return {
        "verdict": "EXCLUSIVE_LOCK_PROBE_PASS",
        "meaning": "Real-process contention, release and crash recovery passed.",
        "contention": True, "release_reacquisition": True, "crash_reacquisition": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expect", choices=("observe", "lock-pass"), default="observe")
    args = parser.parse_args()
    if args.child:
        return child(args.child, args.output)
    report = {"verdict": "DIAGNOSTIC_ERROR", "os_name": os.name,
              "platform": platform.system(), "platform_release": platform.release(),
              "python": platform.python_version(), "source_base": BASE,
              "scope": "SharedSessionStore ownership only; no handoff/transfer/host parity claim"}
    clean = False
    try:
        def git(*argv):
            return subprocess.check_output(["git", "-C", str(REPO), *argv], text=True).strip()
        report["checkout_commit"] = git("rev-parse", "HEAD")
        subprocess.run(["git", "-C", str(REPO), "diff", "--exit-code", BASE, "--",
                        "amplifier_foundation"], check=True, capture_output=True)
        relative = "amplifier_foundation/session/shared_state.py"
        report["shared_state_git_blob"] = git("rev-parse", "HEAD:" + relative)
        assert report["shared_state_git_blob"] == git("rev-parse", BASE + ":" + relative)
        report["core_version"] = importlib.metadata.version("amplifier-core")
        with tempfile.TemporaryDirectory(prefix="session-lock-probe-", dir=REPO) as directory:
            fixture = Path(directory)
            (fixture / "workspace").mkdir()
            children = Children(fixture)
            try:
                report.update(observe(children))
            finally:
                clean = children.cleanup()
                report["observations"] = children.observations
                report["owned_children_reaped"] = clean
        report["fixture_removed"] = not fixture.exists()
    except Exception as error:
        report.update(verdict="DIAGNOSTIC_ERROR", error_type=type(error).__name__)
    if not clean:
        report["verdict"] = "DIAGNOSTIC_ERROR"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    save(args.output, report)
    print(json.dumps(report, indent=2))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(f"## {report['verdict']}\n\n{report.get('meaning', 'Diagnostic did not complete.')}\n")
    return 0 if report["verdict"] == "EXCLUSIVE_LOCK_PROBE_PASS" or (
        args.expect == "observe" and report["verdict"] == "SESSION_OWNERSHIP_UNSUPPORTED") else 1


if __name__ == "__main__":
    sys.exit(main())