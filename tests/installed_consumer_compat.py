"""Explicit CI-only installed-consumer qualification; NOT normal pytest collection.

Run with CPython 3.13 x64 in the isolated GitHub job, from the candidate checkout:
  python -I -X utf8 tests/installed_consumer_compat.py --prepare --work <runner-temp>/compat
  python -I -X utf8 tests/installed_consumer_compat.py --run --work <runner-temp>/compat

Only provider, bundle assembly and first-run setup are doubles. History, leases,
resume and settings writes are real installed code. A refused operation is a
failure, never a skip. This is not a live-provider, default-bundle, clean-install,
Unified, or checkpoint/transfer-fence qualification. No memory bridge is needed.
Windows fixtures create private directories BEFORE use through the production
adapter; this does not qualify consumers' default directory creation policy.
POSIX CLI startup may use its exact private same-user release endpoint; all
other sockets remain prohibited. Public evidence is normalized and rescanned
before upload; private operational paths are never read back from public JSON.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack, asynccontextmanager
import copy
import hashlib
import importlib
import importlib.metadata as metadata
import importlib.util
import inspect
import json
import math
import os
from pathlib import Path
import platform
import re
import socket
import stat
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit
import zipfile


AGENT_REV = "95877ef54cd8849f8f2066ef3e97ce4c8a1669af"
ENGINE_REV = "1da05aa81fa2239d9044b375f21f75ed0e46c89f"  # SDK's v0.25.0
CLI_REV = "631c4bdbcf3dcd2da20456ad6ccef386e778c101"
CONTEXT_REV = "c7ec55db96888200ba619adedc857278daf787c5"
LOOP_REV = "dd916499344671e0209d4951bc025777e3e891fd"
QUOTE = "Préférer le café à Zürich et conserver 日本語."
SECOND = "Conserver aussi les notes de Montréal."
ANSWERS = ["Réponse café 日本語.", "Réponse Montréal conservée."]
# Qualification dependencies only, not runtime requirements or product metadata.
TEST_DEPENDENCIES = ["pytest>=9.0.3", "pytest-asyncio>=1.3.0"]
SCRIPT = Path(__file__).resolve()
PUBLIC_FILES = {"preparation.json", "agent.json", "cli.json", "execution.json"}
MAX_PUBLIC_BYTES = 16 * 1024 * 1024
MAX_PUBLIC_TEXT = 8192
IPC_CLEANUP_TIMEOUT = 2.0
IPC_CLEANUP_POLL = 0.01
_URL = re.compile(r"(?:git\+https|https?|file)://[^\s<>\"']+", re.I)
_CREDENTIAL_KEY = re.compile(
    r"(?i)^(?:authorization|proxy.authorization|(?:access|refresh)[_-]?token|"
    r"token|api[_-]?key|password|passwd|secret|client[_-]?secret|credentials?)$"
)
_AUTH = re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_ASSIGNMENT = re.compile(
    r"(?i)\b(?:authorization|proxy-authorization|(?:access|refresh)[_-]?token|"
    r"token|api[_-]?key|password|passwd|client[_-]?secret|secret)\s*[:=]\s*"
    r"(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s,;]+)"
)
_TOKEN = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_ABSOLUTE = re.compile(
    # A slash inside ./ or ../ is not the start of an absolute path. In
    # particular, RECORD identifiers such as ../../../bin/pytest must survive
    # unchanged and remain distinct from ../../../bin/py.test. This same
    # boundary is used by the independent saved-evidence rescan below.
    r"(?<![A-Za-z0-9.:/>])/(?!/)[^\s\"'<>]+|(?<![A-Za-z0-9])[A-Za-z]:[\\/](?!/)[^\s\"'<>]+"
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path: Path, value) -> None:
    """Private operational state only. Uploads use save_public_json instead."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def path_aliases(work: Path) -> dict[str, str]:
    paths = {
        str(work / "agent"): "<agent-env>", str(work / "cli"): "<cli-env>",
        str(work): "<work>", str(SCRIPT.parent.parent): "<checkout>",
        sys.prefix: "<python-prefix>", sys.base_prefix: "<python-base>",
        str(Path(sys.executable).parent): "<python-bin>",
    }
    for key, label in (
        ("GITHUB_WORKSPACE", "<checkout>"), ("RUNNER_TEMP", "<runner-temp>"),
        ("HOME", "<home>"), ("USERPROFILE", "<home>"), ("TMPDIR", "<temp>"),
    ):
        if os.environ.get(key):
            paths.setdefault(os.environ[key], label)
    # The compact CI runtime root is parent of the synthetic HOME.
    if os.environ.get("AMPLIFIER_HOME"):
        paths.setdefault(str(Path(os.environ["AMPLIFIER_HOME"]).parent.parent), "<runtime>")
    return paths


def public_string(value: str, aliases: dict[str, str]) -> str:
    """Lossy display only: checks/hashes always use original bytes first."""
    def url_without_credentials(match):
        parts = urlsplit(match.group())
        # Strip userinfo and ALL query/fragment values (including signed URLs).
        return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, "", ""))

    value = _URL.sub(url_without_credentials, value)
    for path, label in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
        variants = {path, path.replace("\\", "/")}
        if Path(path).is_absolute():
            variants.add(Path(path).as_uri())
        for variant in sorted(variants, key=len, reverse=True):
            value = re.sub(re.escape(variant), lambda _: label, value, flags=re.I)
    value = _URL.sub(
        lambda match: "[private-file-url]" if urlsplit(match.group()).scheme == "file" else match.group(),
        value,
    )
    value = _AUTH.sub("[redacted-auth]", value)
    value = _ASSIGNMENT.sub("[redacted-credential]", value)
    value = _TOKEN.sub("[redacted-token]", value)
    value = _EMAIL.sub("[redacted-email]", value)
    value = _ABSOLUTE.sub("[private-path]", value)
    return value if len(value) <= MAX_PUBLIC_TEXT else "[omitted oversized text]"


def public_value(value, aliases: dict[str, str]):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("Public evidence requires string keys")
            normalized = public_string(key, aliases)
            if normalized in result:
                raise ValueError("Public evidence normalization collided")
            result[normalized] = (
                "[redacted]" if _CREDENTIAL_KEY.fullmatch(key) else public_value(item, aliases)
            )
        return result
    if isinstance(value, (list, tuple)):
        return [public_value(item, aliases) for item in value]
    if isinstance(value, str):
        return public_string(value, aliases)
    if value is None or type(value) in {int, float, bool}:
        return value
    raise TypeError("Unexpected public evidence value")


def assert_public(value) -> None:
    """Independent fail-closed rescan: never repair or silently omit evidence."""
    if isinstance(value, dict):
        for key, item in value.items():
            assert_public(key)
            if _CREDENTIAL_KEY.fullmatch(key) and item != "[redacted]":
                raise ValueError("Public evidence contains a credential field")
            assert_public(item)
    elif isinstance(value, list):
        for item in value:
            assert_public(item)
    elif isinstance(value, str):
        if (len(value) > MAX_PUBLIC_TEXT or _AUTH.search(value) or _ASSIGNMENT.search(value)
                or _TOKEN.search(value) or _EMAIL.search(value) or _ABSOLUTE.search(value)):
            raise ValueError("Public evidence failed the sensitive-data rescan")
        for match in _URL.finditer(value):
            parts = urlsplit(match.group())
            if parts.scheme == "file" or "@" in parts.netloc or parts.query or parts.fragment:
                raise ValueError("Public evidence contains URL credentials or parameters")
    elif value is not None and type(value) not in {int, float, bool}:
        raise ValueError("Unexpected public JSON value")
    elif type(value) is float and not math.isfinite(value):
        raise ValueError("Non-finite public JSON value")


def save_public_json(work: Path, name: str, value) -> None:
    if name not in PUBLIC_FILES:
        raise ValueError("Unexpected public evidence file")
    document = public_value(value, path_aliases(work))
    assert_public(document)
    text = json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2)
    if len(text.encode("utf-8")) > MAX_PUBLIC_BYTES:
        raise ValueError("Public evidence exceeds its size limit")
    destination = work / "evidence" / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")


def check_public(work: Path) -> int:
    """Final upload gate reads saved bytes, not a newly scrubbed substitute."""
    directory = work / "evidence"
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("Public evidence directory is absent or unsafe")
    paths = list(directory.iterdir())
    if not paths:
        raise ValueError("No public evidence was generated")
    for path in paths:
        if (path.name not in PUBLIC_FILES or path.is_symlink() or not path.is_file()
                or path.stat().st_size > MAX_PUBLIC_BYTES):
            raise ValueError("Unexpected or oversized public evidence")
        assert_public(json.loads(path.read_text(encoding="utf-8")))
    return 0


class CommandFailure(RuntimeError):
    def __init__(self, result):
        super().__init__("Qualification subprocess failed; raw output withheld")
        self.returncode = result.returncode
        self.streams = {
            name: {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                   "bytes": len(text.encode("utf-8"))}
            for name, text in (("stdout", result.stdout), ("stderr", result.stderr))
        }


def error_record(exc: BaseException) -> dict:
    # Exception messages, source lines, locals, argv and subprocess streams may
    # contain arbitrary secrets. Keep bounded frame locations and numeric codes,
    # not raw str(exc), repr(exc), traceback text or captured output.
    chain, seen, policy_failure = [], set(), False
    while exc is not None and id(exc) not in seen and len(chain) < 4:
        seen.add(id(exc))
        policy_failure = policy_failure or isinstance(exc, HarnessPolicyError)
        frames, tb = [], exc.__traceback__
        while tb is not None:
            frames.append({"file": tb.tb_frame.f_code.co_filename, "line": tb.tb_lineno,
                           "function": tb.tb_frame.f_code.co_name[:128]})
            tb = tb.tb_next
        detail = {"type": type(exc).__name__[:128], "frames": frames[-8:]}
        for name in ("errno", "winerror", "returncode", "timeout"):
            number = getattr(exc, name, None)
            if type(number) in {int, float}:
                detail[name] = number
        if isinstance(exc, CommandFailure):
            detail["streams"] = exc.streams
        chain.append(detail)
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    return {
        "status": "failed", "diagnostics": chain, "raw_diagnostics": "withheld",
        "failure_domain": "qualification-policy" if policy_failure else "check-or-prerequisite",
    }


def require_runner() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("Build/install/consumer execution is restricted to the isolated GitHub job")
    if sys.version_info[:2] != (3, 13) or struct.calcsize("P") != 8:
        raise RuntimeError("Qualification requires ordinary CPython 3.13 x64")
    if platform.python_implementation() != "CPython" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise RuntimeError("Qualification requires ordinary CPython on x64")
    if sys.platform not in {"win32", "linux"}:
        raise RuntimeError("Only the declared Windows and Ubuntu runners are qualified")
    if not sys.flags.isolated or not sys.flags.utf8_mode:
        raise RuntimeError("Invoke the harness with -I -X utf8; do not expose checkout imports")


def clean_environment(root: Path) -> dict[str, str]:
    """Allow-list OS prerequisites; never forward provider/Git/cloud credentials."""
    allowed = {
        "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
        "SYSTEMDRIVE", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    for name in ("home", "tmp", "config", "cache", "appdata", "localappdata"):
        (root / name).mkdir(parents=True, exist_ok=True)
    env.update({
        "HOME": str(root / "home"), "USERPROFILE": str(root / "home"),
        "APPDATA": str(root / "appdata"), "LOCALAPPDATA": str(root / "localappdata"),
        "XDG_CONFIG_HOME": str(root / "config"), "XDG_CACHE_HOME": str(root / "cache"),
        "TMP": str(root / "tmp"), "TEMP": str(root / "tmp"), "TMPDIR": str(root / "tmp"),
        "AMPLIFIER_HOME": str(root / "home" / ".amplifier"),
        "AMPLIFIER_MEMORY_HOME": str(root / "memory"),
        "AMPLIFIER_SESSION_STATE_HOME": str(root / "authority"),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "",
        "UV_NO_CONFIG": "1", "UV_KEYRING_PROVIDER": "disabled",
        "UV_CACHE_DIR": str(root / "cache" / "uv"), "UV_NO_PROGRESS": "1",
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "GITHUB_ACTIONS": "true",
    })
    return env


def command(argv, *, cwd: Path, env: dict, timeout: int = 600) -> str:
    """No shell, credentials, inherited stdin, or unbounded setup/child lifetime."""
    result = subprocess.run(
        [str(item) for item in argv], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    if result.returncode:
        raise CommandFailure(result)
    return result.stdout


def env_python(work: Path, consumer: str) -> Path:
    return work / consumer / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def prepare(work: Path) -> int:
    """Resolve normal dependency metadata with ONE explicit candidate override."""
    report = {"status": "failed", "consumers": {}, "test_dependencies": TEST_DEPENDENCIES}
    env = clean_environment(work / "preparation")
    source = SCRIPT.parent.parent
    try:
        require_runner()
        command(["uv", "build", "--wheel", "--no-sources", "--out-dir", work / "wheels", source],
                cwd=work, env=env)
        wheels = list((work / "wheels").glob("amplifier_foundation-*.whl"))
        assert len(wheels) == 1, "A fresh work directory must contain exactly one candidate wheel"
        wheel = wheels[0]
        tracked = command(["git", "-C", source, "ls-files", "-z"], cwd=work, env=env).split("\0")
        report["candidate"] = {
            "head": command(["git", "-C", source, "rev-parse", "HEAD"], cwd=work, env=env).strip(),
            "tree": command(["git", "-C", source, "rev-parse", "HEAD^{tree}"], cwd=work, env=env).strip(),
            "status": command(["git", "-C", source, "status", "--porcelain"], cwd=work, env=env),
            "files": {name: digest(source / name) for name in tracked if name and (source / name).is_file()},
            "wheel": str(wheel), "wheel_sha256": digest(wheel),
        }
        # Verify the packaged Python bytes before any consumer is installed.
        with zipfile.ZipFile(wheel) as archive:
            for path in (source / "amplifier_foundation").rglob("*.py"):
                assert archive.read(path.relative_to(source).as_posix()) == path.read_bytes()
        override = work / "candidate-override.txt"
        override.write_text(f"amplifier-foundation @ {wheel.as_uri()}\n", encoding="utf-8")
        # This checkout supplies only the explicit-file-loaded test provider.
        helper = work / "agent-source"
        command(["git", "init", helper], cwd=work, env=env)
        command(["git", "-C", helper, "-c", "credential.helper=", "fetch", "--depth=1",
                 "https://github.com/microsoft/amplifier-agent", AGENT_REV], cwd=work, env=env)
        command(["git", "-C", helper, "-c", "core.autocrlf=false", "checkout", "--detach", "FETCH_HEAD"],
                cwd=work, env=env)
        assert command(["git", "-C", helper, "rev-parse", "HEAD"], cwd=work, env=env).strip() == AGENT_REV
        report["scripted_provider"] = {
            "revision": AGENT_REV,
            "sha256": digest(helper / "tests/support/scripted_provider.py"),
            "scenarios_sha256": digest(helper / "tests/support/scenarios/turns.json"),
        }
        for consumer, requirements in {
            "agent": [
                f"amplifier-agent @ git+https://github.com/microsoft/amplifier-agent@{AGENT_REV}#subdirectory=packages/python",
            ],
            "cli": [
                f"amplifier-app-cli @ git+https://github.com/microsoft/amplifier-app-cli@{CLI_REV}",
                "amplifier-core==2.0.2",
                f"amplifier-module-context-simple @ git+https://github.com/microsoft/amplifier-module-context-simple@{CONTEXT_REV}",
                f"amplifier-module-loop-streaming @ git+https://github.com/microsoft/amplifier-module-loop-streaming@{LOOP_REV}",
            ],
        }.items():
            try:
                python = env_python(work, consumer)
                command(["uv", "venv", "--python", sys.executable, python.parent.parent], cwd=work, env=env)
                command(["uv", "pip", "install", "--python", python, "--no-sources",
                         "--only-binary", "amplifier-core", "--overrides", override,
                         *requirements, *TEST_DEPENDENCIES], cwd=work, env=env)
                command(["uv", "pip", "check", "--python", python], cwd=work, env=env)
                report["consumers"][consumer] = {
                    "status": "passed", "requirements": requirements, "dependency_check": "passed",
                }
            except Exception as exc:
                report["consumers"][consumer] = error_record(exc)
        if all(item["status"] == "passed" for item in report["consumers"].values()):
            report["status"] = "passed"
    except Exception as exc:
        report["error"] = error_record(exc)
    finally:
        # Raw paths needed by subsequent checks stay private and are NOT uploads.
        save_json(work / "private" / "preparation.json", report)
        save_public_json(work, "preparation.json", report)
    return int(report["status"] != "passed")


def installed_audit(wheel: Path, consumer: str, result: dict) -> None:
    """Full selected-distribution hashes/metadata, exact VCS IDs, no editable imports."""
    names = ["amplifier-foundation", "amplifier-core"]
    names += ["amplifier-agent", "amplifier-agent-engine"] if consumer == "agent" else [
        "amplifier-app-cli", "amplifier-module-context-simple", "amplifier-module-loop-streaming",
    ]
    result.update({"distributions": {}, "all_distributions": []})
    for distribution in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"].lower()):
        direct = json.loads(distribution.read_text("direct_url.json") or "null")
        assert not (direct or {}).get("dir_info", {}).get("editable"), distribution.metadata["Name"]
        result["all_distributions"].append({
            "name": distribution.metadata["Name"], "version": distribution.version,
            "direct_url": direct, "requires": distribution.requires,
        })
    for name in names:
        distribution = metadata.distribution(name)
        files = {}
        for relative in distribution.files or ():
            path = Path(distribution.locate_file(relative)).resolve()
            if path.is_file() and path.suffix != ".pyc":
                files[str(relative)] = {"path": str(path), "sha256": digest(path)}
        assert files, name
        result["distributions"][name] = {
            # Exact METADATA bytes are hashed in files. Do not publish authors,
            # email, README bodies or other irrelevant identifying metadata.
            "name": distribution.metadata["Name"], "version": distribution.version,
            "requires_python": distribution.metadata.get("Requires-Python"),
            "requires": distribution.requires,
            "direct_url": json.loads(distribution.read_text("direct_url.json") or "null"),
            "files": files,
        }
    assert metadata.version("amplifier-core") == ("2.0.1" if consumer == "agent" else "2.0.2")
    expected = {"amplifier-agent": AGENT_REV, "amplifier-agent-engine": ENGINE_REV} if consumer == "agent" else {
        "amplifier-app-cli": CLI_REV, "amplifier-module-context-simple": CONTEXT_REV,
        "amplifier-module-loop-streaming": LOOP_REV,
    }
    for name, revision in expected.items():
        assert result["distributions"][name]["direct_url"]["vcs_info"]["commit_id"] == revision, name
    foundation = metadata.distribution("amplifier-foundation")
    direct = json.loads(foundation.read_text("direct_url.json"))
    assert direct["url"] == wheel.as_uri()
    assert direct["archive_info"]["hashes"]["sha256"] == digest(wheel)
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            # Installers legitimately rewrite RECORD to include direct_url,
            # INSTALLER and bytecode. Its installed hash is retained above.
            if name.endswith(("/", ".dist-info/RECORD")) or ".data/" in name:
                continue
            path = Path(foundation.locate_file(name)).resolve()
            assert path.is_relative_to(Path(sys.prefix).resolve()), str(path)
            assert path.read_bytes() == archive.read(name), name
    result["candidate_wheel_sha256"] = digest(wheel)


def import_audit() -> dict:
    modules, shadowing = {}, []
    for name, module in sorted(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if name.startswith(("amplifier_",)) and path and Path(path).is_file():
            path = Path(path).resolve()
            modules[name] = {"path": str(path), "sha256": digest(path)}
            if not path.is_relative_to(Path(sys.prefix).resolve()) or "site-packages" not in path.parts:
                shadowing.append(name)
    return {"modules": modules, "source_shadowing": shadowing, "sys_path": list(sys.path)}


def scripted_factory(work: Path):
    path = work / "agent-source/tests/support/scripted_provider.py"
    spec = importlib.util.spec_from_file_location("_compat_scripted_provider", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ScriptedFactory


def private_directory(path: Path) -> None:
    """Never repair an existing ACL. Create missing private directories natively."""
    if os.name == "nt":
        from amplifier_foundation.session import _windows_ownership as win

        with win._pinned(win._API(), path, root=path, create=True):
            pass
    else:
        path.mkdir(parents=True, mode=0o700, exist_ok=True)


def conversation(messages: list[dict]) -> list[tuple[str, str]]:
    """Compare fixture role/content, independent of string vs text-block form."""
    result = []
    for message in messages:
        role, content = message["role"], message.get("content")
        if role in {"system", "developer"}:
            continue
        assert role in {"user", "assistant"}, "Unexpected role in text-only fixture"
        if isinstance(content, list):
            assert all(isinstance(block, dict) and block.get("type") == "text"
                       and isinstance(block.get("text"), str) for block in content)
            content = "".join(block["text"] for block in content)
        assert isinstance(content, str), "Fixture content must be text"
        result.append((role, content))
    return result


def assert_conversation(messages: list[dict], prompts: list[str]) -> None:
    expected = [
        pair for prompt, answer in zip(prompts, ANSWERS)
        for pair in (("user", prompt), ("assistant", answer))
    ]
    assert len(prompts) in {1, 2} and conversation(messages) == expected, "Conversation differs"


def saved_state(directory: Path) -> tuple[list[dict], dict]:
    return (
        [json.loads(row) for row in (directory / "transcript.jsonl").read_text(encoding="utf-8").splitlines()],
        json.loads((directory / "metadata.json").read_text(encoding="utf-8")),
    )


def history_files(directory: Path, prompts: list[str], *, prior=None) -> dict:
    """Read physical bytes independently of the consumer's history facade."""
    records = {}
    names = ["transcript.jsonl", "metadata.json"]
    if prior is not None:
        names += ["transcript.jsonl.backup", "metadata.json.backup"]
        assert_conversation(prior[0], [QUOTE])
        assert prior[1]["turn_count"] == 1
    for name in names:
        path = directory / name
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        value = [json.loads(row) for row in text.splitlines()] if "jsonl" in name else json.loads(text)
        assert isinstance(value, list if "jsonl" in name else dict), name
        records[name] = {"sha256": digest(path), "bytes": len(raw)}
        if name == "transcript.jsonl":
            assert_conversation(value, prompts)
        if name == "metadata.json":
            assert value["turn_count"] == len(prompts), value
        if name == "transcript.jsonl.backup":
            # Save/cleanup cadence may rotate a later checkpoint. It must still
            # retain the complete prior first-turn conversation, in order.
            previous = conversation(prior[0])
            assert conversation(value)[:len(previous)] == previous, "Backup lost prior conversation"
        if name == "metadata.json.backup":
            assert type(value.get("turn_count")) is int
            assert prior[1]["turn_count"] <= value["turn_count"] <= len(prompts)
            for field in ("session_id", "bundle", "persistence"):
                if field in prior[1]:
                    assert value.get(field) == prior[1][field], "Backup lost prior metadata"
    assert not (directory / "transcript.jsonl.append-pending").exists()
    return records


async def agent_roundtrip(work: Path, root: Path, report: dict) -> None:
    import pytest
    import amplifier_agent as sdk
    from amplifier_agent_engine._engine import assembly

    factory = scripted_factory(work)
    session_id = "installed-sdk-utf8"
    sessions = root / "sdk"
    directory = sessions / "sessions" / session_id
    private_directory(directory)
    options = sdk.AgentOptions(provider="anthropic", model="claude-sonnet-5", approvals="allow",
                               sessions_directory=sessions)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(assembly, "_provider_factory", factory([{"text": ANSWERS[0]}]))
        async with await sdk.create_agent(options) as first:
            session = await first.create_session(sdk.SessionOptions(session_id=session_id))
            outcome = await session.run(sdk.TurnInput([sdk.TextPart(QUOTE)]))
            assert outcome.state == "success", outcome.error
            assert outcome.content == [sdk.TextPart(ANSWERS[0])]
            history = session.history
            report["first_turn"] = history_files(directory, [QUOTE])
            prior = saved_state(directory)
            before = (directory / "transcript.jsonl").read_bytes()
            async with await sdk.create_agent(options) as contender:
                with pytest.raises(sdk.AgentError) as busy:
                    await contender.resume_session(session_id)
                assert busy.value.code == "session_in_use"
            report["busy_lease_refused"] = True
            await session.close()
        patch.setattr(assembly, "_provider_factory", factory([{"text": ANSWERS[1]}]))
        async with await sdk.create_agent(options) as successor:
            resumed = await successor.resume_session(session_id)
            assert resumed.history == history
            assert resumed.history[0].input.content == [sdk.TextPart(QUOTE)]
            outcome = await resumed.run(sdk.TurnInput([sdk.TextPart(SECOND)]))
            assert outcome.state == "success", outcome.error
            assert outcome.content == [sdk.TextPart(ANSWERS[1])]
            assert len(resumed.history) == 2
            second_history = copy.deepcopy(resumed.history)
            await resumed.close()
        report["resumed_second_turn"] = history_files(directory, [QUOTE, SECOND], prior=prior)
        assert (directory / "transcript.jsonl").read_bytes().startswith(before)
        # Re-open a third time: the second turn must be DURABLE, not only in RAM.
        async with await sdk.create_agent(options) as reader:
            reopened = await reader.resume_session(session_id)
            assert reopened.history == second_history, "Reopen changed complete second-turn history"
            await reopened.close()
    report["logical_append_not_in_place_append"] = True


async def cli_roundtrip(work: Path, root: Path, report: dict, guard) -> None:
    import pytest
    import amplifier_core
    import amplifier_module_context_simple as context_module
    import amplifier_module_loop_streaming as loop_module
    from amplifier_foundation import Bundle
    from amplifier_foundation.bundle._prepared import PreparedBundle, BundleModuleResolver
    from amplifier_app_cli.project_utils import get_project_slug
    from amplifier_app_cli.session_store import SessionStore

    # Ordinary import/initializer; no doctor argv or handoff/ownership patch.
    main = importlib.import_module("amplifier_app_cli.main")
    runner = importlib.import_module("amplifier_app_cli.session_runner")
    factory = scripted_factory(work)
    session_id = "installed-cli-utf8"
    home = Path(os.environ["AMPLIFIER_HOME"])
    directory = home / "projects" / get_project_slug() / "sessions" / session_id
    private_directory(directory)  # BEFORE SessionStore's ordinary mkdir.
    prepared = PreparedBundle(
        mount_plan={"session": {"orchestrator": {"module": "loop-streaming"},
                                "context": {"module": "context-simple"}}},
        resolver=BundleModuleResolver({}), bundle=Bundle(name="offline-compat", base_path=root),
    )
    providers = [factory([{"text": answer}]) for answer in ANSWERS]
    constructed = []

    async def create_bundle_session(**kwargs):
        provider = providers[len(constructed)]

        async def fixtures(session):
            await context_module.mount(session.coordinator, {})
            await loop_module.mount(session.coordinator, {"stream": False})
            scripted = await provider(SimpleNamespace(model="claude-sonnet-5"), session.coordinator)
            await session.coordinator.mount("providers", scripted, name="anthropic")

        session = await prepared.create_session(
            session_id=kwargs["session_id"], session_cwd=root, before_initialize=fixtures,
        )
        assert isinstance(session, amplifier_core.AmplifierSession)
        constructed.append(session)
        return session

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(runner, "_create_bundle_session", create_bundle_session)
        patch.setattr("amplifier_app_cli.commands.init.check_first_run", lambda: False)
        # Capture output without replacing the real console or startup.
        with main.console.capture():
            async with guard.release_scope(report):
                await main.execute_single(
                    prompt=QUOTE, config={}, search_paths=[root], verbose=False,
                    session_id=session_id, bundle_name="offline-compat", prepared_bundle=prepared,
                )
        assert len(constructed) == 1
        transcript, facts = SessionStore().load(session_id)
        assert facts["session_id"] == session_id
        assert_conversation(transcript, [QUOTE])
        # Initial CLI save has no prior files; backups are required after resume.
        before = (directory / "transcript.jsonl").read_bytes()
        prior = saved_state(directory)
        report["first_turn"] = history_files(directory, [QUOTE])
        with main.console.capture():
            async with guard.release_scope(report):
                await main.execute_single(
                    prompt=SECOND, config={}, search_paths=[root], verbose=False,
                    session_id=session_id, bundle_name="offline-compat", prepared_bundle=prepared,
                    initial_transcript=transcript,
                )
        assert len(constructed) == 2
        # Provider saw the restored first turn, not just the new prompt.
        request = json.dumps(providers[1].requests[0]["messages"], ensure_ascii=False)
        assert all(text in request for text in (QUOTE, ANSWERS[0], SECOND))
        transcript, facts = SessionStore().load(session_id)
        assert_conversation(transcript, [QUOTE, SECOND])
        assert facts["turn_count"] == 2
        assert (directory / "transcript.jsonl").read_bytes().startswith(before)
        report["resumed_second_turn"] = history_files(directory, [QUOTE, SECOND], prior=prior)
        report["logical_append_not_in_place_append"] = True


def foundation_ownership(root: Path, report: dict) -> None:
    from amplifier_foundation.session.shared_state import SharedSessionStore

    workspace = root / "workspace"
    workspace.mkdir()
    state = root / "state"
    store = SharedSessionStore(workspace, "installed-owner", root=state)
    held = store.acquire(app="installed-compat")
    # A real separate process must see busy, not just a same-process registry.
    probe = (
        "import sys; from amplifier_foundation.session.shared_state import "
        "SharedSessionStore, SessionBusyError\n"
        "try:\n"
        " h=SharedSessionStore(sys.argv[1], 'installed-owner', root=sys.argv[2]).acquire(app='contender')\n"
        "except SessionBusyError:\n"
        " print('busy')\n"
        "else:\n"
        " h.release(); raise AssertionError('contender acquired live owner')\n"
    )
    try:
        output = command([sys.executable, "-I", "-X", "utf8", "-c", probe, workspace, state],
                         cwd=root, env=dict(os.environ), timeout=30)
        assert output.strip() == "busy"
    finally:
        held.release()
    successor = store.acquire(app="successor")
    try:
        assert successor.owner["acquisition_id"] != held.owner["acquisition_id"]
        report["real_process_busy_release_successor"] = True
    finally:
        successor.release()
    report["checkpoint_or_transfer_mutations"] = "not attempted (unsupported on Windows)"


def incremental_history(root: Path, report: dict) -> None:
    from amplifier_foundation.session.history import SessionHistoryStore
    from amplifier_foundation.session.jsonl import file_stamp

    directory = root / "incremental"
    private_directory(directory)
    history = SessionHistoryStore(directory)
    first = [{"role": "user", "content": QUOTE}, {"role": "assistant", "content": ANSWERS[0]}]
    history.save(first, {"turn_count": 1}, incremental=True)
    prior = saved_state(directory)
    old = file_stamp(history.transcript_path)
    prefix = history.transcript_path.read_bytes()
    second = first + [{"role": "user", "content": SECOND}, {"role": "assistant", "content": ANSWERS[1]}]
    history.save(second, {"turn_count": 2}, incremental=True)
    new = file_stamp(history.transcript_path)
    assert old and new and (old.dev, old.ino) == (new.dev, new.ino)
    assert history.transcript_path.read_bytes().startswith(prefix)
    assert history.load_messages() == second
    assert (directory / "transcript.jsonl.backup").read_bytes() == prefix
    report["files"] = history_files(directory, [QUOTE, SECOND], prior=prior)
    report["real_in_place_append"] = True
    report["scope"] = "Foundation opt-in interface, NOT the pinned consumers' default save mode"


def settings_overlap(root: Path, report: dict) -> None:
    import pytest
    from amplifier_app_cli.lib.settings import AppSettings
    from amplifier_foundation.session.history import SessionHistoryStore
    from amplifier_foundation.session.jsonl import _open_binary

    home = root / ".amplifier"
    directory = home / "projects" / "fixture" / "sessions" / "history"
    private_directory(directory)
    history = SessionHistoryStore(directory)
    history.save([{"role": "user", "content": QUOTE}], {"turn_count": 1})
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("AMPLIFIER_HOME", str(home))
        settings = AppSettings()
        settings.set_active_bundle("baseline-café")
        assert settings.get_active_bundle() == "baseline-café"
        report["baseline_settings_write"] = True
        # Actual production read context retains the native ancestor pins. Do
        # not substitute builtin open, weaken sharing, or expect an ACL refusal.
        failure = None
        try:
            with _open_binary(history.transcript_path) as stream:
                assert stream.read()
                settings.set_active_bundle("overlap-Montréal")
                assert settings.get_active_bundle() == "overlap-Montréal"
                report["settings_replace_during_retained_history_read"] = True
        except Exception as exc:
            failure = exc
            report["overlap_error"] = error_record(exc)
        settings.set_active_bundle("released-日本語")
        assert settings.get_active_bundle() == "released-日本語"
        report["released_settings_write"] = True
        if failure is not None:
            raise failure  # Preserve counterevidence even when release fixed contention.


class HarnessPolicyError(RuntimeError):
    """Harness admission/isolation failure, NOT evidence of a product regression."""


def short_endpoint_path(temp: Path, uid: int) -> Path:
    # Production tempfile.mkdtemp uses h- plus eight random characters.
    endpoint = temp / f"amplifier-handoff-{uid}" / ("h-" + "x" * 8) / "control.sock"
    if len(os.fsencode(endpoint)) > 103:
        raise HarnessPolicyError("Synthetic temporary root exceeds production IPC path budget")
    return endpoint


class SocketGuard:
    """One exact production release socket per CLI call; no other network."""

    def __init__(self, workspace: Path, consumer: str, report: dict):
        self.workspace, self.consumer = workspace, consumer
        self.scope = False
        self.endpoint = self.listener = None
        self.attempts = report["blocked_socket_operations"] = []
        self.ipc = report["release_ipc"] = []
        self.runtime = (
            Path(tempfile.gettempdir()) / f"amplifier-handoff-{os.getuid()}"
            if os.name != "nt" else None
        )

    def blocked(self, event):
        self.attempts.append(event)  # Never retain addresses or protocol payloads.
        raise HarnessPolicyError("Socket operation outside the exact CLI release endpoint")

    def production_endpoint(self, endpoint: Path) -> bool:
        """Read-only attestation from the actual installed handler's bind frame.

        The handler generates a random registration directory. Do not allow a
        wildcard AF_UNIX tree or patch handoff to choose a known endpoint. Learn
        and freeze the exact path only from that handler and this fixture's
        active ownership capability, before its first bind.
        """
        from amplifier_foundation.session.handoff import register_release_handler

        frame = sys._getframe(1)
        try:
            while frame is not None:
                if frame.f_code is register_release_handler.__code__:
                    registration = frame.f_locals.get("registration")
                    if registration is None or registration.path != endpoint:
                        return False
                    held = registration.held
                    owner = held.owner
                    return (
                        held.active and owner.get("session_id") == "installed-cli-utf8"
                        and owner.get("workspace") == str(self.workspace.resolve())
                        and owner.get("pid") == os.getpid()
                    )
                frame = frame.f_back
            return False
        finally:
            del frame

    def private_endpoint(self, endpoint: Path, *, bound: bool) -> bool:
        if (self.runtime is None or not endpoint.is_absolute()
                or endpoint.name != "control.sock" or endpoint.parent.parent != self.runtime
                or not re.fullmatch(r"h-[a-z0-9_]{8}", endpoint.parent.name)
                or endpoint != endpoint.resolve() or len(os.fsencode(endpoint)) > 103):
            return False
        try:
            for directory in (self.runtime, endpoint.parent):
                info = directory.lstat()
                if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) & 0o077):
                    return False
            if bound:
                info = endpoint.lstat()
                return (stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid()
                        and not stat.S_IMODE(info.st_mode) & 0o077)
            return not endpoint.exists()
        except OSError:
            return False

    def audit(self, event, args):
        if event not in {"socket.bind", "socket.connect", "socket.getaddrinfo",
                         "socket.gethostbyname", "socket.gethostbyaddr", "socket.getnameinfo",
                         "socket.sendto", "socket.sendmsg"}:
            return
        if event in {"socket.bind", "socket.connect"} and self.scope:
            sock, address = args[:2]
            if (sock.family == getattr(socket, "AF_UNIX", None)
                    and sock.type == socket.SOCK_STREAM and isinstance(address, (str, bytes))):
                endpoint = Path(os.fsdecode(address))
                if event == "socket.bind" and self.endpoint is None:
                    if (self.private_endpoint(endpoint, bound=False)
                            and self.production_endpoint(endpoint)):
                        self.endpoint, self.listener = endpoint, sock
                        return
                elif event == "socket.connect" and endpoint == self.endpoint:
                    if self.private_endpoint(endpoint, bound=True):
                        return
        self.blocked(event)

    def listen(self, original, sock, *args, **kwargs):
        if (self.scope and sock is self.listener and self.endpoint is not None
                and sock.family == getattr(socket, "AF_UNIX", None)
                and sock.getsockname() == str(self.endpoint)
                and self.private_endpoint(self.endpoint, bound=True)):
            return original(sock, *args, **kwargs)
        self.blocked("socket.listen")

    async def observe_release_cleanup(self, detail: dict) -> None:
        """Yield to scheduled production cleanup; never perform it ourselves."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + IPC_CLEANUP_TIMEOUT
        observation = {
            "path_bytes": len(os.fsencode(self.endpoint)), "checks": 0,
            "endpoint_removed": False, "directory_removed": False, "listener_closed": False,
        }
        self.ipc.append(observation)
        detail["release_ipc_cleaned"] = False
        while True:
            observation.update({
                "endpoint_removed": not self.endpoint.exists(),
                "directory_removed": not self.endpoint.parent.exists(),
                "listener_closed": self.listener.fileno() == -1,
            })
            observation["checks"] += 1
            if all(observation[key] for key in (
                "endpoint_removed", "directory_removed", "listener_closed",
            )):
                detail["release_ipc_cleaned"] = True
                return
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise HarnessPolicyError("Production release IPC cleanup was incomplete")
            # _deactivate queues server.close with call_soon_threadsafe. A
            # synchronous finally cannot observe that callback completing.
            await asyncio.sleep(min(IPC_CLEANUP_POLL, remaining))

    @asynccontextmanager
    async def release_scope(self, detail: dict):
        if self.scope or self.endpoint is not None:
            raise HarnessPolicyError("Nested CLI release IPC scope")
        self.scope = self.consumer == "cli" and self.runtime is not None
        succeeded = False
        original_failure = None
        try:
            yield
            succeeded = True
        except BaseException as exc:
            original_failure = exc
            raise
        finally:
            self.scope = False
            if self.endpoint is not None:
                try:
                    await self.observe_release_cleanup(detail)
                except BaseException as cleanup_failure:
                    detail["release_ipc_cleanup_error"] = error_record(cleanup_failure)
                    if original_failure is None:
                        raise
                    # Keep the CLI failure/cancellation primary; retain the
                    # independent cleanup failure in safe public evidence.
                    original_failure.add_note("Release IPC cleanup also failed; see cleanup evidence.")
                finally:
                    self.endpoint = self.listener = None
            elif succeeded and self.consumer == "cli" and self.runtime is not None:
                raise HarnessPolicyError("Normal POSIX CLI did not register its release endpoint")


async def qualify(work: Path, consumer: str, report: dict) -> None:
    import pytest

    # asyncio's own wakeup pair is already initialized. The only exception is
    # normal POSIX CLI registration, scoped around each actual execute_single.
    root = work / "cases" / consumer
    root.mkdir(parents=True, exist_ok=True)
    guard = SocketGuard(root, consumer, report)
    sys.addaudithook(guard.audit)
    home = Path(os.environ["AMPLIFIER_HOME"])
    private_directory(home)
    config = home / "settings.yaml"
    config.write_text("# synthetic offline configuration\nfixture: café-日本語\n", encoding="utf-8")
    before = config.read_bytes()
    os.chdir(root)
    cases = [
        ("foundation_ownership", lambda detail: foundation_ownership(root, detail)),
        ("foundation_incremental", lambda detail: incremental_history(root, detail)),
        (f"{consumer}_roundtrip", lambda detail: (
            agent_roundtrip(work, root, detail) if consumer == "agent"
            else cli_roundtrip(work, root, detail, guard))),
    ]
    if consumer == "cli":
        cases.append(("settings_overlap", lambda detail: settings_overlap(root, detail)))
    with pytest.MonkeyPatch.context() as patch:
        original_listen = socket.socket.listen

        def guarded_listen(sock, *args, **kwargs):
            return guard.listen(original_listen, sock, *args, **kwargs)

        patch.setattr(socket.socket, "listen", guarded_listen)
        for name, check in cases:
            detail = report["checks"][name] = {"status": "running"}
            try:
                result = check(detail)
                if inspect.isawaitable(result):
                    await asyncio.wait_for(result, timeout=90)
                assert config.read_bytes() == before, "Consumer changed unrelated synthetic config"
                detail["status"] = "passed"
            except (Exception, SystemExit) as exc:
                detail.update(error_record(exc))
            save_public_json(work, f"{consumer}.json", report)
    report["synthetic_config_preserved"] = config.read_bytes() == before
    if guard.attempts:
        raise HarnessPolicyError("Qualification blocked a socket operation; not a product-regression verdict")
    assert report["synthetic_config_preserved"]


def run_consumer(work: Path, consumer: str) -> int:
    report = {
        "status": "failed", "consumer": consumer, "checks": {},
        "python": sys.version, "executable": sys.executable, "os": platform.platform(),
        "machine": platform.machine(), "pointer_bits": struct.calcsize("P") * 8,
        "isolated": sys.flags.isolated, "utf8_mode": sys.flags.utf8_mode,
        "mocks": ["provider factory", "CLI bundle assembly", "CLI first-run setup predicate"],
        "limits": [
            "No live provider, default bundle, memory integration or Unified runtime qualification",
            "Windows private history directories prepared by production adapter BEFORE consumer use",
            "Consumer saves replace files; logical extension is distinct from Foundation opt-in append",
            "No checkpoint/fence writes, takeover requests, installer, service, or clean-install claim",
            "Python socket guard is a regression tripwire, not an adversarial OS sandbox",
            "Normal POSIX CLI release registration uses only its exact private AF_UNIX endpoint",
        ],
    }
    try:
        require_runner()
        preparation = json.loads((work / "private/preparation.json").read_text(encoding="utf-8"))
        wheel = Path(preparation["candidate"]["wheel"])
        assert digest(wheel) == preparation["candidate"]["wheel_sha256"]
        report["provenance"] = {}
        installed_audit(wheel, consumer, report["provenance"])
        asyncio.run(qualify(work, consumer, report))
        assert all(item["status"] == "passed" for item in report["checks"].values())
        report["status"] = "passed"
    except (Exception, SystemExit) as exc:
        report["error"] = error_record(exc)
    finally:
        report["imports"] = import_audit()
        if report["imports"]["source_shadowing"]:
            report["status"] = "failed"
        save_public_json(work, f"{consumer}.json", report)
    return int(report["status"] != "passed")


def run(work: Path) -> int:
    require_runner()
    report = {"status": "failed", "consumers": {}}
    for consumer in ("agent", "cli"):
        compact = None
        try:
            # Preparation failures do not suppress the other graph's evidence.
            with ExitStack() as resources:
                root = work / "runtime" / consumer
                if os.name != "nt":
                    # Private, unique, compact CI-only synthetic root. Runner
                    # temp defaults can exceed the handler's 103-byte bound.
                    root = Path(resources.enter_context(
                        tempfile.TemporaryDirectory(prefix="ic-", dir="/tmp"),
                    ))
                    compact = root
                    short_endpoint_path(root / "tmp", os.getuid())
                env = clean_environment(root)
                # -I ignores PYTHONUTF8: set the fixture encoding explicitly.
                command([env_python(work, consumer), "-I", "-X", "utf8", SCRIPT,
                         "--consumer", consumer, "--work", work],
                        cwd=work, env=env, timeout=300)
            report["consumers"][consumer] = {"status": "passed"}
        except Exception as exc:
            report["consumers"][consumer] = error_record(exc)
        finally:
            if compact is not None:
                removed = not compact.exists()
                report["consumers"][consumer]["compact_root_removed"] = removed
                if not removed:
                    report["consumers"][consumer]["status"] = "failed"
    if all(item["status"] == "passed" for item in report["consumers"].values()):
        report["status"] = "passed"
    save_public_json(work, "execution.json", report)
    return int(report["status"] != "passed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--check-public", action="store_true")
    mode.add_argument("--consumer", choices=("agent", "cli"))
    parser.add_argument("--work", required=True, type=Path)
    args = parser.parse_args()
    work = args.work.resolve()
    # Refuse before any allocation/install/consumer side effects outside CI.
    require_runner()
    if args.check_public:
        return check_public(work)
    work.mkdir(parents=True, exist_ok=True)
    if args.prepare:
        return prepare(work)
    if args.run:
        return run(work)
    return run_consumer(work, args.consumer)


if __name__ == "__main__":
    try:
        exit_status = main()
    except Exception:
        # Do not leak raw terminal tracebacks if setup or the public gate fails.
        print("Installed-consumer qualification failed; raw diagnostics withheld.", file=sys.stderr)
        exit_status = 1
    raise SystemExit(exit_status)