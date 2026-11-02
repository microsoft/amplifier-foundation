"""Explicit CI-only installed-consumer qualification; NOT normal pytest collection.

Run with CPython 3.13 x64 in the isolated GitHub job, from the candidate checkout:
  python -I -X utf8 tests/installed_consumer_compat.py --prepare --work <runner-temp>/compat
  python -I -X utf8 tests/installed_consumer_compat.py --run --work <runner-temp>/compat

Only provider, bundle assembly, first-run setup and the optional CLI commit-date
display lookup are doubles. History, leases,
resume and settings writes are real installed code. An unexplained refusal is a
failure, never a skip. This is not a live-provider, default-bundle, clean-install,
Unified, or checkpoint/transfer-fence qualification. No memory bridge is needed.
Windows fixtures create private directories BEFORE use through the production
adapter in the prepared cells. Additional Windows-only default-creation cells
leave consumer descendants absent under a safe HOME, comparing the candidate
with an exact pre-PR456 wheel in separate environments.
POSIX CLI startup may use its exact private same-user release endpoint; all
other sockets remain prohibited. One exact urllib3 import-time capability bind
may be accounted as expected only AFTER it was blocked and cleanup verified.
Public evidence is normalized and rescanned
before upload; private operational paths are never read back from public JSON.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack, asynccontextmanager, contextmanager
import copy
import hashlib
import importlib
import importlib.metadata as metadata
import importlib.util
import inspect
import ipaddress
import json
import math
import os
from pathlib import Path, PureWindowsPath
import platform
import re
import socket
import stat
import struct
import subprocess
import sys
import sysconfig
import tempfile
import threading
from types import CodeType, SimpleNamespace
from urllib.parse import urlsplit, urlunsplit
import zipfile


AGENT_REV = "95877ef54cd8849f8f2066ef3e97ce4c8a1669af"
ENGINE_REV = "1da05aa81fa2239d9044b375f21f75ed0e46c89f"  # SDK's v0.25.0
BASELINE_REV = "99fdb524037fb998b9c36355315e66a3052b662f"
CLI_REV = "631c4bdbcf3dcd2da20456ad6ccef386e778c101"
CONTEXT_REV = "c7ec55db96888200ba619adedc857278daf787c5"
LOOP_REV = "dd916499344671e0209d4951bc025777e3e891fd"
IPV6_SOURCE_SHA256 = "2633bbdb69731e5ccb5cf4e4afd65605d86c7979cc5633126f50c92d5ad74a74"
IPV6_PROBE_CODE_SHA256 = "16f32caf75b2fb5bb67d19a82fb3240074695863f4d1baa41f7812a8b55b5829"
IPV6_MODULE_CODE_SHA256 = "fc158648bd87c9bf71909fc523043351fcba012708865dbb216869755cfb9ff7"
CLI_VERSION_SOURCE_SHA256 = "6eda6dd35a4bb1e694da4f1e7c4786f4ac9e8086a59512e57c8cee1d1fe71543"
CLI_REPO_URL = "https://github.com/microsoft/amplifier-app-cli"
QUOTE = "Préférer le café à Zürich et conserver 日本語."
SECOND = "Conserver aussi les notes de Montréal."
ANSWERS = ["Réponse café 日本語.", "Réponse Montréal conservée."]
# Qualification dependencies only, not runtime requirements or product metadata.
TEST_DEPENDENCIES = ["pytest>=9.0.3", "pytest-asyncio>=1.3.0"]
SCRIPT = Path(__file__).resolve()
PUBLIC_FILES = {"preparation.json", "agent.json", "cli.json", "execution.json"}
PUBLIC_FILES |= {f"default-{variant}-{consumer}.json"
                 for variant in ("candidate", "baseline") for consumer in ("agent", "cli")}
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
        str(work / "b" / "agent"): "<baseline-agent-env>",
        str(work / "b" / "cli"): "<baseline-cli-env>",
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
            # Path.as_uri, pathlib and subprocesses can mix slash styles on
            # Windows. Match separators, not a partially normalized prefix.
            pattern = r"[\\/]".join(re.escape(part) for part in re.split(r"[\\/]", variant))
            value = re.sub(pattern, lambda _: label, value, flags=re.I)
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


def setup_diagnostics(stderr: str) -> dict:
    """Allow-listed uv stderr markers, NOT arbitrary redacted prose.

    An unknown line stays withheld, even if it looks harmless. Recognized
    templates and package identities are useful for the next setup failure
    without exporting paths, URLs, credentials, compiler output or tracebacks.
    Markers describe the reported failure, not an independently proven cause.
    """
    sample = stderr[-65536:].lower()
    markers = [
        label for label, needles in (
            ("dependency-resolution", ("no solution found", "unsatisfiable", "conflicting dependencies")),
            ("compatible-wheel-unavailable", ("no matching distribution", "no wheels", "matching platform tag",
                                              "does not have a wheel", "doesn't have a wheel")),
            ("python-requirement", ("requires-python", "requires python")),
            ("git-fetch", ("failed to fetch", "failed to clone", "git fetch", "git clone")),
            ("connection", ("connection refused", "connection reset", "timed out", "failed to connect",
                            "dns error", "name resolution")),
            ("certificate", ("certificate verify failed", "invalid peer certificate", "unknown issuer")),
            ("access-denied", ("permission denied", "access is denied")),
            ("path-too-long", ("filename or extension is too long", "filename too long", "path too long")),
            ("sharing-violation", ("being used by another process", "sharing violation")),
            ("missing-file", ("no such file or directory", "cannot find the file", "cannot find the path")),
            ("disk-full", ("no space left", "disk is full")),
            ("build-backend", ("build backend", "failed to build", "failed to prepare metadata")),
            ("compiler", ("microsoft visual c++", "microsoft c++ build tools", "compiler not found")),
        )
        if any(needle in sample for needle in needles)
    ]
    known_packages = (
        "amplifier-agent", "amplifier-agent-engine", "amplifier-foundation", "amplifier-core",
        "amplifier-app-cli", "amplifier-module-loop-streaming", "amplifier-module-context-simple",
        "amplifier-module-tool-delegate", "amplifier-module-tool-skills", "amplifier-module-tool-mcp",
        "amplifier-module-tool-filesystem", "amplifier-module-tool-apply-patch", "amplifier-module-tool-bash",
        "amplifier-module-tool-web", "amplifier-module-tool-search", "amplifier-module-hooks-routing",
        "amplifier-module-hook-context-intelligence", "amplifier-module-hook-redaction",
        "pytest", "pytest-asyncio", "hatchling", "jsonschema", "pyyaml",
    )
    return {
        "interpretation": "recognized stderr markers, not a proven root cause",
        "categories": markers or ["unclassified"],
        "packages": [name for name in known_packages
                     if re.search(r"(?<![\w-])" + re.escape(name) + r"(?![\w-])", sample)][:8],
        "windows_codes": sorted({int(code) for code in re.findall(
            r"(?:winerror|os error)\s*[:\[]?\s*(\d{1,4})\b", sample,
        )})[:8],
        "http_status": sorted({int(code) for code in re.findall(
            r"\b(401|403|404|408|429|500|502|503|504)\s+(?:unauthorized|forbidden|not found|"
            r"request timeout|too many requests|internal server error|bad gateway|"
            r"service unavailable|gateway timeout)\b", sample,
        )})[:8],
        "raw_stderr": "withheld",
    }


class CommandFailure(RuntimeError):
    def __init__(self, result, *, diagnose_setup: bool = False):
        super().__init__("Qualification subprocess failed; raw output withheld")
        self.returncode = result.returncode
        self.streams = {
            name: {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                   "bytes": len(text.encode("utf-8"))}
            for name, text in (("stdout", result.stdout), ("stderr", result.stderr))
        }
        self.setup = setup_diagnostics(result.stderr) if diagnose_setup else None


def error_record(exc: BaseException) -> dict:
    # Traceback filenames, function names and custom exception class names are
    # untrusted identifiers too. Unlike socket caller details, exception frames
    # need no named-source diagnostics: keep numeric locations and bounded
    # fingerprints only, without reading any co_filename.
    chain, seen, policy_failure = [], set(), False
    while exc is not None and id(exc) not in seen and len(chain) < 4:
        seen.add(id(exc))
        policy_failure = policy_failure or isinstance(exc, HarnessPolicyError)
        frames, tb = [], exc.__traceback__
        while tb is not None:
            code = tb.tb_frame.f_code
            frames.append({
                "line": tb.tb_lineno,
                "code_sha256": hashlib.sha256(code.co_code[:65536]).hexdigest(),
                "code_bytes": len(code.co_code),
                "code_fingerprint_scope": "bounded-prefix" if len(code.co_code) > 65536 else "complete",
                "filename_sha256": hashlib.sha256(code.co_filename[:4096].encode(
                    "utf-8", errors="replace",
                )).hexdigest(),
            })
            if len(frames) > 8:
                frames.pop(0)
            tb = tb.tb_next
        # Exact class identity, not subclass __name__ or a caller's module claim.
        kind = "withheld"
        for known, label in (
            (HarnessPolicyError, "HarnessPolicyError"), (CommandFailure, "CommandFailure"),
            (AssertionError, "AssertionError"), (KeyError, "KeyError"), (ValueError, "ValueError"),
            (TypeError, "TypeError"), (RuntimeError, "RuntimeError"), (OSError, "OSError"),
            (PermissionError, "PermissionError"), (FileNotFoundError, "FileNotFoundError"),
            (TimeoutError, "TimeoutError"), (subprocess.TimeoutExpired, "TimeoutExpired"),
            (SystemExit, "SystemExit"),
        ):
            if type(exc) is known:
                kind = label
                break
        detail = {"type": kind, "frames": frames}
        for name in ("errno", "winerror", "returncode", "timeout"):
            number = getattr(exc, name, None)
            if type(number) in {int, float}:
                detail[name] = number
        if isinstance(exc, CommandFailure):
            detail["streams"] = exc.streams
            if exc.setup is not None:
                detail["setup"] = exc.setup
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


def clean_environment(root: Path, *, runtime: bool = False) -> dict[str, str]:
    """Allow-list OS prerequisites; runtime HOME must be created by native helper.

    uv preparation has only public build inputs and may use an ordinary HOME.
    The consumer HOME is different and initially absent, so elevated Windows
    runners cannot pre-create it with an administrator-owned inherited ACL.
    """
    allowed = {
        "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT",
        "SYSTEMDRIVE", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "RUNNER_TEMP",
    }
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    names = ["t", "config", "c", "appdata", "localappdata"]
    if not runtime:
        names.append("home")
    if not (runtime and os.name == "nt"):
        for name in names:
            (root / name).mkdir(parents=True, exist_ok=True)
    env.update({
        "HOME": str(root / "home"), "USERPROFILE": str(root / "home"),
        "APPDATA": str(root / "appdata"), "LOCALAPPDATA": str(root / "localappdata"),
        "XDG_CONFIG_HOME": str(root / "config"), "XDG_CACHE_HOME": str(root / "cache"),
        "TMP": str(root / "t"), "TEMP": str(root / "t"), "TMPDIR": str(root / "t"),
        "AMPLIFIER_HOME": str(root / "home" / ".amplifier"),
        "AMPLIFIER_MEMORY_HOME": str(root / "memory"),
        "AMPLIFIER_SESSION_STATE_HOME": str(root / "authority"),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "",
        "UV_NO_CONFIG": "1", "UV_KEYRING_PROVIDER": "disabled",
        "UV_CACHE_DIR": str(root / "c"), "UV_NO_PROGRESS": "1",
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "GITHUB_ACTIONS": "true",
    })
    return env


def windows_runtime_root(original_temp: str, *, evidence=None) -> Path:
    """One native-pytest-style location, NOT a drive search or a trust decision.

    Capture the job's original TEMP before clean_environment replaces it. Build
    caches stay compact on RUNNER_TEMP's drive; storage fixtures must not inherit
    that drive choice. Like pytest's temproot, canonicalize the existing TEMP
    before adding a new leaf. Unlike resolve alone, reject original/canonical
    reparse components and require the same directory identity and local drive.
    This is spelling normalization, NOT ACL approval: the installed child still
    validates every canonical ancestor before privately creating anything.
    Do not call gettempdir/TemporaryDirectory here: both can write before native
    ancestor validation. No fallback to another drive if production refuses.
    """
    base = PureWindowsPath(original_temp)
    if (not base.is_absolute() or not re.fullmatch(r"[A-Za-z]:", base.drive)
            or ".." in base.parts or len(base.parts) > 129):
        raise HarnessPolicyError("Original TEMP is not an absolute local fixture location")
    detail = evidence if evidence is not None else {}
    detail.update({
        "basis": "original TEMP; strict existing-directory canonical spelling only",
        "status": "inspecting-existing-temp",
        "short_name_shaped_components": sum(bool(re.search(r"~[0-9]", part)) for part in base.parts),
    })
    original = Path(str(base))
    canonical = None
    identity = None
    # lstat each component in order, before resolving: do not silently turn an
    # original junction/symlink into an acceptable fixture at its target.
    for label in ("original", "canonical"):
        path = original if label == "original" else canonical
        for component in reversed([path, *path.parents]):
            info = component.lstat()
            if (info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
                    or not stat.S_ISDIR(info.st_mode)):
                raise HarnessPolicyError("TEMP chain contains a reparse point or non-directory")
        current = (info.st_dev, info.st_ino)
        if (any(type(value) is not int for value in current) or info.st_ino <= 0):
            raise HarnessPolicyError("TEMP directory identity is unavailable")
        if label == "original":
            identity = current
            canonical = original.resolve(strict=True)
            resolved = PureWindowsPath(canonical)
            if (not resolved.is_absolute() or resolved.drive.casefold() != base.drive.casefold()
                    or not re.fullmatch(r"[A-Za-z]:", resolved.drive)
                    or ".." in resolved.parts or len(resolved.parts) > 129):
                raise HarnessPolicyError("Canonical TEMP changed local volume or namespace")
        elif current != identity:
            raise HarnessPolicyError("Canonical TEMP does not identify the original directory")
    detail.update({
        "status": "canonical-spelling-selected; production preflight still required",
        "spelling_changed": str(canonical).casefold() != str(original).casefold(),
        "same_directory_identity": True, "reparse_components": False,
    })
    return canonical / ("ic-" + os.urandom(8).hex())


def setup_path_budget(work: Path | PureWindowsPath, env: dict) -> dict:
    """Fixture preflight, not a promise that every dependency supports Windows.

    Reserve 160 characters below the selected roots for uv's nested checkouts/
    unpack/build names. Keep the resulting planning budget below classic
    MAX_PATH (260 incl. terminator). Record measured maxima separately on
    failure; dependency-internal suffixes can still exceed this reserve.
    """
    roots = {
        "work": work, "temp": env["TEMP"], "cache": env["UV_CACHE_DIR"],
        "agent_env": work / "agent", "cli_env": work / "cli",
    }
    lengths = {role: len(str(path)) for role, path in roots.items()}
    reserve = 160
    within = all(length + reserve < 260 for length in lengths.values())
    return {
        "root_characters": lengths, "nested_suffix_reserve": reserve,
        "planning_budget_characters": 259, "within_planning_budget": within,
        "qualification": "bounded fixture path plan; not a measured dependency maximum",
    }


def validate_windows_work_drive(work, runner_temp: str) -> None:
    """Reject a compact root on a different or nonlocal runner-temp volume."""
    runner_drive = PureWindowsPath(runner_temp).drive
    if (not re.fullmatch(r"[A-Za-z]:", runner_drive)
            or PureWindowsPath(work).drive.lower() != runner_drive.lower()):
        raise HarnessPolicyError("Work root must use the runner temporary drive")


def setup_path_observation(work: Path, env: dict) -> dict:
    """Bounded filename-length measurement only. Never export names or contents."""
    result = {}
    remaining = 20000
    for role, root in (("cache", Path(env["UV_CACHE_DIR"])), ("temp", Path(env["TEMP"])),
                       ("agent_env", work / "agent"), ("cli_env", work / "cli")):
        detail = result[role] = {"entries": 0, "max_characters": 0, "scan_errors": []}

        def scan_error(exc):
            if len(detail["scan_errors"]) < 8:
                detail["scan_errors"].append({
                    name: getattr(exc, name) for name in ("errno", "winerror")
                    if type(getattr(exc, name, None)) is int
                })

        for directory, dirs, files in os.walk(root, followlinks=False, onerror=scan_error):
            for name in dirs + files:
                path = str(Path(directory) / name)
                detail["entries"] += 1
                if len(path) > detail["max_characters"]:
                    detail["max_characters"] = len(path)
                    detail["longest_path_sha256"] = hashlib.sha256(os.fsencode(path)).hexdigest()
                remaining -= 1
                if remaining <= 0:
                    break
            if remaining <= 0:
                break
        if remaining <= 0:
            break
    result["capped"] = remaining <= 0
    return result


def command(argv, *, cwd: Path, env: dict, timeout: int = 600, diagnose_setup: bool = False) -> str:
    """No shell, credentials, inherited stdin, or unbounded setup/child lifetime."""
    result = subprocess.run(
        [str(item) for item in argv], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    if result.returncode:
        raise CommandFailure(result, diagnose_setup=diagnose_setup)
    return result.stdout


def env_python(work: Path, consumer: str) -> Path:
    return work / consumer / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def prepare(work: Path) -> int:
    """Resolve normal dependency metadata with ONE explicit candidate override."""
    report = {"status": "failed", "consumers": {}, "test_dependencies": TEST_DEPENDENCIES}
    env = clean_environment(work / "p")
    source = SCRIPT.parent.parent
    try:
        require_runner()
        report["setup_path_budget"] = setup_path_budget(work, env)
        if os.name == "nt" and not report["setup_path_budget"]["within_planning_budget"]:
            raise HarnessPolicyError("Windows qualification requires a shorter owned setup root")
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
        graphs = {
            "agent": [
                f"amplifier-agent @ git+https://github.com/microsoft/amplifier-agent@{AGENT_REV}#subdirectory=packages/python",
            ],
            "cli": [
                f"amplifier-app-cli @ git+https://github.com/microsoft/amplifier-app-cli@{CLI_REV}",
                "amplifier-core==2.0.2",
                f"amplifier-module-context-simple @ git+https://github.com/microsoft/amplifier-module-context-simple@{CONTEXT_REV}",
                f"amplifier-module-loop-streaming @ git+https://github.com/microsoft/amplifier-module-loop-streaming@{LOOP_REV}",
            ],
        }
        for consumer, requirements in graphs.items():
            phase = "create-venv"
            try:
                python = env_python(work, consumer)
                command(["uv", "venv", "--python", sys.executable, python.parent.parent], cwd=work, env=env)
                phase = "install"
                command(["uv", "pip", "install", "--python", python, "--no-sources",
                         "--only-binary", "amplifier-core", "--overrides", override,
                         *requirements, *TEST_DEPENDENCIES], cwd=work, env=env, diagnose_setup=True)
                phase = "dependency-check"
                command(["uv", "pip", "check", "--python", python], cwd=work, env=env)
                report["consumers"][consumer] = {
                    "status": "passed", "requirements": requirements, "dependency_check": "passed",
                }
            except Exception as exc:
                report["consumers"][consumer] = error_record(exc)
                report["consumers"][consumer]["phase"] = phase
                report["consumers"][consumer]["paths_at_failure"] = setup_path_observation(work, env)
        if os.name == "nt":
            # Comparison is Windows-only. Existing candidate installs are never
            # replaced/reinstalled; each baseline Core graph gets its own venv.
            report["baseline"] = prepare_baseline(work, env, graphs)
        if all(item["status"] == "passed" for item in report["consumers"].values()):
            report["status"] = "passed"
        if os.name == "nt" and report["baseline"]["status"] != "passed":
            report["status"] = "failed"
    except Exception as exc:
        report["error"] = error_record(exc)
    finally:
        # Raw paths needed by subsequent checks stay private and are NOT uploads.
        save_json(work / "private" / "preparation.json", report)
        save_public_json(work, "preparation.json", report)
    return int(report["status"] != "passed")


def prepare_baseline(work: Path, env: dict, graphs: dict) -> dict:
    """One exact pre-PR456 wheel, two isolated graphs constrained to candidate deps."""
    report = {"status": "failed", "revision": BASELINE_REV, "consumers": {}}
    try:
        source = work / "f"
        command(["git", "init", source], cwd=work, env=env)
        command(["git", "-C", source, "-c", "credential.helper=", "fetch", "--depth=1",
                 "https://github.com/microsoft/amplifier-foundation", BASELINE_REV], cwd=work, env=env)
        command(["git", "-C", source, "-c", "core.autocrlf=false", "checkout", "--detach", "FETCH_HEAD"],
                cwd=work, env=env)
        assert command(["git", "-C", source, "rev-parse", "HEAD"], cwd=work, env=env).strip() == BASELINE_REV
        assert not command(["git", "-C", source, "status", "--porcelain"], cwd=work, env=env).strip()
        report["tree"] = command(["git", "-C", source, "rev-parse", "HEAD^{tree}"], cwd=work, env=env).strip()
        command(["uv", "build", "--wheel", "--no-sources", "--out-dir", work / "bw", source],
                cwd=work, env=env)
        wheels = list((work / "bw").glob("amplifier_foundation-*.whl"))
        assert len(wheels) == 1
        wheel = wheels[0]
        report.update({"wheel": str(wheel), "wheel_sha256": digest(wheel), "files": {}})
        with zipfile.ZipFile(wheel) as archive:
            for path in (source / "amplifier_foundation").rglob("*.py"):
                name = path.relative_to(source).as_posix()
                assert archive.read(name) == path.read_bytes()
                report["files"][name] = digest(path)
        override = work / "baseline-override.txt"
        override.write_text(f"amplifier-foundation @ {wheel.as_uri()}\n", encoding="utf-8")
        for consumer, requirements in graphs.items():
            phase = "freeze-candidate-graph"
            try:
                frozen = command(["uv", "pip", "freeze", "--python", env_python(work, consumer)],
                                 cwd=work, env=env)
                listed = json.loads(command(["uv", "pip", "list", "--python", env_python(work, consumer),
                                             "--format", "json"], cwd=work, env=env))
                versions = {item["name"].lower().replace("_", "-"): item["version"] for item in listed}
                constraints = baseline_constraints(frozen, versions)
                constraint = work / "private" / f"baseline-{consumer}-constraints.txt"
                constraint.parent.mkdir(parents=True, exist_ok=True)
                constraint.write_text(constraints, encoding="utf-8")
                python = env_python(work / "b", consumer)
                phase = "create-venv"
                command(["uv", "venv", "--python", sys.executable, python.parent.parent], cwd=work, env=env)
                phase = "install"
                command(["uv", "pip", "install", "--python", python, "--no-sources",
                         "--only-binary", "amplifier-core", "--overrides", override,
                         "--constraints", constraint, *requirements, *TEST_DEPENDENCIES],
                        cwd=work, env=env, diagnose_setup=True)
                phase = "dependency-check"
                command(["uv", "pip", "check", "--python", python], cwd=work, env=env)
                report["consumers"][consumer] = {
                    "status": "passed", "requirements": requirements,
                    "candidate_constraints_sha256": hashlib.sha256(constraints.encode("utf-8")).hexdigest(),
                }
            except Exception as exc:
                report["consumers"][consumer] = {**error_record(exc), "phase": phase}
        assert set(report["consumers"]) == {"agent", "cli"}
        if all(item["status"] == "passed" for item in report["consumers"].values()):
            report["status"] = "passed"
    except Exception as exc:
        report["error"] = error_record(exc)
    return report


def baseline_constraints(frozen: str, versions: dict) -> str:
    """Keep exact other graph pins; replace Foundation alone with full dependency resolution."""
    lines = frozen.splitlines()
    foundation = [line for line in lines
                  if re.match(r"(?i)^amplifier[-_]foundation(?:\s|=|@|\[)", line)]
    assert len(foundation) == 1, "Candidate freeze must identify Foundation exactly once"
    result = []
    for line in lines:
        if line in foundation:
            continue
        direct = re.fullmatch(r"([A-Za-z0-9_.-]+)\s+@\s+\S+", line)
        if direct:
            # A freeze emits the resolved VCS commit; the consumer can declare
            # the SAME source as a tag (engine v0.25.0). Do not inject conflicting
            # source spellings via constraints or override a declared VCS pin.
            # Keep its version, resolve its original requirement normally, and
            # require exact ORIGINAL direct_url identities at consumer entry.
            name = direct[1]
            version = versions[name.lower().replace("_", "-")]
            assert re.fullmatch(r"[A-Za-z0-9.!+_-]+", version)
            result.append(f"{name}=={version}")
        else:
            result.append(line)
    return "\n".join(result) + "\n"


def candidate_install_proof(wheel: Path, expected_sha256: str, foundation) -> dict:
    """Prove the candidate independently of optional installer hash metadata."""
    actual_sha256 = digest(wheel)
    assert actual_sha256 == expected_sha256, "Candidate wheel changed after preparation"
    direct = json.loads(foundation.read_text("direct_url.json"))
    assert direct["url"] == wheel.as_uri(), "Foundation was not installed from this candidate wheel"
    assert not direct.get("dir_info", {}).get("editable"), "Editable candidate install"
    info = direct["archive_info"]
    assert isinstance(info, dict), "Invalid archive_info"
    hashes = info.get("hashes", {})
    assert isinstance(hashes, dict), "Invalid archive hash metadata"
    declared = dict(hashes)
    if "hash" in info:  # Legacy PEP 610 spelling, if the installer supplies it.
        algorithm, value = info["hash"].split("=", 1)
        assert algorithm not in declared or declared[algorithm] == value, "Conflicting archive hashes"
        declared[algorithm] = value
    for algorithm, value in declared.items():
        hasher = hashlib.new(algorithm)  # Unknown algorithms fail closed.
        with wheel.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
        assert isinstance(value, str) and hasher.hexdigest() == value, "Installer archive hash mismatch"
    compared = {}
    with zipfile.ZipFile(wheel) as archive:
        assert len(archive.namelist()) == len(set(archive.namelist())), "Duplicate wheel members"
        for name in archive.namelist():
            if name.endswith("/"):
                continue
            # No unqualified wheel .data relocation is silently exempted.
            assert ".data/" not in name, "Candidate wheel relocation is not qualified"
            if name.endswith(".dist-info/RECORD"):
                continue  # Installer rewrites this; installed hash is retained by installed_audit.
            path = Path(foundation.locate_file(name)).resolve()
            assert path.is_relative_to(Path(sys.prefix).resolve()), "Installed candidate escaped its environment"
            assert "site-packages" in path.parts, "Candidate is not in site-packages"
            expected = archive.read(name)
            assert path.read_bytes() == expected, f"Installed candidate byte mismatch: {name}"
            compared[name] = hashlib.sha256(expected).hexdigest()
    assert any(name.startswith("amplifier_foundation/") and name.endswith(".py") for name in compared)
    return {
        "candidate_wheel_sha256": actual_sha256, "preparation_digest_verified": True,
        "direct_url_verified": True,
        "installer_hash_metadata": "verified" if declared else "absent",
        "installer_hashes": declared, "installed_wheel_members": compared,
        "record_exception": "installer-rewritten RECORD only; its installed hash retained separately",
    }


def installed_audit(wheel: Path, expected_sha256: str, consumer: str, result: dict) -> None:
    """Full selected-distribution hashes/metadata, exact VCS IDs, no editable imports."""
    names = ["amplifier-foundation", "amplifier-core"]
    names += ["amplifier-agent", "amplifier-agent-engine", "urllib3"] if consumer == "agent" else [
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
    result["candidate_install_proof"] = candidate_install_proof(wheel, expected_sha256, foundation)


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


@contextmanager
def cli_display_offline(detail: dict, provenance: dict):
    """Preload the REAL installed version module, then double ONE helper.

    Importing amplifier_app_cli (even project_utils) eagerly initializes main,
    before a normal dotted monkeypatch could reach this helper. This stdlib-only
    installed file can be loaded under its canonical name without importing its
    parent. Subsequent ordinary package/main imports use these same unmodified
    module bytes. No doctor route, initializer/version replacement or HTTP mock.
    """
    import pytest

    proof = detail["commit_date_display"] = {
        "seam": "optional commit-date display lookup offline double",
        "calls": 0, "status": "armed", "helper_restored": False,
    }
    distribution = provenance["distributions"]["amplifier-app-cli"]
    direct = distribution["direct_url"]
    assert direct["vcs_info"]["commit_id"] == CLI_REV
    assert direct["url"] == CLI_REPO_URL
    name = "amplifier_app_cli.utils.version"
    assert not any(key == "amplifier_app_cli" or key.startswith("amplifier_app_cli.")
                   for key in sys.modules), "CLI imported before optional-display fixture was armed"
    evidence = distribution["files"]["amplifier_app_cli/utils/version.py"]
    path = Path(evidence["path"])
    assert path.is_relative_to(Path(sys.prefix)) and "site-packages" in path.parts
    assert digest(path) == evidence["sha256"] == CLI_VERSION_SOURCE_SHA256
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclass requires the real canonical module.
    try:
        spec.loader.exec_module(module)  # No CLI parent, HTTP or consumer launch here.
    except BaseException:
        del sys.modules[name]
        raise
    original = module._get_commit_date_from_api

    def offline_date(git_url, commit_sha):
        proof["calls"] += 1
        if proof["calls"] != 1 or git_url != CLI_REPO_URL or commit_sha != CLI_REV:
            raise HarnessPolicyError("Unexpected optional commit-date display lookup")
        proof["arguments_verified"] = True
        return None  # Real get_version_info supplies its normal fallback date.

    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(module, "_get_commit_date_from_api", offline_date)
            yield
            assert proof["calls"] == 1 and proof["arguments_verified"]
            # Verify cached real version output; do not spoof SHA/date/display.
            info = module.get_version_info()
            assert info.sha == CLI_REV[:7] and info.is_local is False and info.has_changes is False
            assert info.date and info.display == f"{info.date}-{CLI_REV[:7]}"
            assert module.get_version() == info.display
            assert json.loads(metadata.distribution("amplifier-app-cli").read_text("direct_url.json")) == direct
            proof.update({"status": "verified", "version_sha": info.sha,
                          "metadata_unchanged": True, "fallback_date_retained": True})
    finally:
        # Keep normal loaded modules/cache; restore only the approved helper.
        # Removing cached modules would replay startup.
        proof["helper_restored"] = module._get_commit_date_from_api is original


def private_directory(path: Path) -> None:
    """Never repair an existing ACL. Create missing private directories natively."""
    if os.name == "nt":
        from amplifier_foundation.session import _windows_ownership as win

        with win._pinned(win._API(), path, root=path, create=True):
            pass
    else:
        path.mkdir(parents=True, mode=0o700, exist_ok=True)


def windows_acl_evidence(api, handle, win) -> dict:
    """Read-only bounded ACL summary; no SID, path, or ACL prose leaves this function."""
    owner, dacl, descriptor = win.PVOID(), win.PVOID(), win.PVOID()
    error = api.GetSecurityInfo(handle, 1, 5, win.C.byref(owner), None,
                                win.C.byref(dacl), None, win.C.byref(descriptor))
    if error:
        return {"read_error": int(error)}
    try:
        def principal(sid):
            return {
                "class": "current-user" if sid == api.sid else
                "os-authority" if sid in win._OS_AUTHORITIES else
                "owner-rights" if sid == "S-1-3-4" else "other",
                "sid_sha256": hashlib.sha256(sid.encode("ascii")).hexdigest(),
            }

        result = {"owner": principal(api.sid_text(owner)), "dacl_present": bool(dacl), "aces": []}
        control, revision = win.WORD(), win.DWORD()
        api.ok(api.GetSecurityDescriptorControl(descriptor, win.C.byref(control), win.C.byref(revision)))
        result["dacl_protected"] = bool(control.value & 0x1000)
        if dacl:
            info = win.ACL_INFO()
            api.ok(api.GetAclInformation(dacl, win.C.byref(info), win.C.sizeof(info), 2))
            result["ace_count"] = info.count
            result["aces_capped"] = info.count > 32
            for index in range(min(info.count, 32)):
                pointer = win.PVOID()
                api.ok(api.GetAce(dacl, index, win.C.byref(pointer)))
                ace = win.C.cast(pointer, win.C.POINTER(win.ACE)).contents
                entry = {"kind": int(ace.kind), "flags": int(ace.flags)}
                if ace.kind in {0, 1} and ace.size >= win.C.sizeof(win.ACE):
                    entry["mask"] = int(ace.mask)
                    entry["principal"] = principal(api.sid_text(win.PVOID(pointer.value + win.ACE.sid.offset)))
                else:
                    entry["principal"] = {"class": "unsupported-layout"}
                result["aces"].append(entry)
        return result
    finally:
        api.LocalFree(descriptor)


def windows_ancestor_evidence(path: Path, win) -> dict:
    """Locate an existing invalid component with production validation, no mutation.

    This diagnostic runs only after the normal production preflight refused.
    It never makes a failed path acceptable. All opened ancestors remain held
    until inspection ends, using the same production handle/share policy.
    """
    path = win._absolute(path)
    api = win._API()
    report = {"validation": "diagnostic only; original refusal retained", "components": []}
    chain = list(reversed([path, *path.parents]))
    if len(chain) > 129:
        return {"validation": "component limit exceeded"}
    with ExitStack() as pins:
        for depth, component in enumerate(chain):
            role = "drive" if depth == 0 else "owned-leaf" if component == path else "ancestor"
            detail = {"depth": depth, "role": role}
            report["components"].append(detail)
            try:
                handle = api.open(component, directory=True)
            except FileNotFoundError:
                detail["status"] = "missing"
                break
            except Exception as exc:
                detail["status"] = "open-refused"
                detail["error"] = error_record(exc)
                break
            pins.callback(api.close, handle)
            try:
                owned = component == path
                api.validate_acl(handle, private=owned, traversal=not owned)
                detail["status"] = "validated"
            except Exception as exc:
                detail["status"] = "refused"
                detail["error"] = error_record(exc)
                try:
                    detail["acl"] = windows_acl_evidence(api, handle, win)
                except Exception:
                    detail["acl"] = {"diagnostic": "unavailable"}
                break
    return report


def private_ancestor_preflight(path: Path, *, failure_evidence=None) -> dict:
    """Read-only production validation before any directory creation."""
    if os.name == "nt":
        from amplifier_foundation.session import _windows_ownership as win

        try:
            with win._pinned(win._API(), path, root=path, create=False) as pins:
                return {"existing_chain_validated": True, "owned_leaf_missing": pins.missing}
        except Exception:
            if failure_evidence is not None:
                try:
                    failure_evidence.update(windows_ancestor_evidence(path, win))
                except Exception:
                    failure_evidence["validation"] = "diagnostic unavailable; original refusal retained"
            raise
    return {"native_acl_preflight": "not applicable on POSIX"}


def prepare_runtime_home(report: dict, *, history_root=True) -> None:
    """Create safe HOME, optionally its history root, before any payload.

    Refuse a reused fixture instead of adopting or repairing an inherited ACL.
    Production's helper remains the sole creator/validator on Windows. Failure
    categories are exact known messages, never raw SIDs, paths or ACL contents.
    """
    home = Path(os.environ["HOME"])
    amplifier_home = Path(os.environ["AMPLIFIER_HOME"])
    if amplifier_home != home / ".amplifier":
        raise HarnessPolicyError("Fixture history root is not under synthetic HOME")
    detail = report["private_fixture"] = {
        "creation_policy": ("production adapter before payload; no ACL repair" if history_root
                            else "safe HOME boundary only; consumer descendants must remain absent"),
        "preexisting_home": home.exists(), "preexisting_history_root": amplifier_home.exists(),
    }
    if os.name == "nt":
        detail["runtime_location_basis"] = "original process TEMP before environment sanitization"
        detail["preexisting_runtime_root"] = home.parent.exists()
        if detail["preexisting_runtime_root"]:
            raise HarnessPolicyError("Windows runtime fixture root must be absent before private creation")
    if detail["preexisting_home"] or detail["preexisting_history_root"]:
        raise HarnessPolicyError("Runtime HOME must be absent before private creation")
    categories = {
        "Windows state requires trusted ownership and a non-null DACL": "owner-or-null-dacl",
        "Windows private state requires a protected DACL": "unprotected-dacl",
        "Windows state DACL grants foreign access": "foreign-ace",
        "unsupported Windows state ACE": "unsupported-ace",
        "Windows state path has an alternate namespace alias": "namespace-alias",
    }
    for role, path in ((("home", home), ("history-root", amplifier_home)) if history_root
                       else (("home", home),)):
        detail["stage"] = role
        detail["operation"] = "read-only-ancestor-preflight"
        try:
            evidence = detail.setdefault("ancestor_failure_evidence", {}).setdefault(role, {})
            detail.setdefault("ancestor_preflights", {})[role] = private_ancestor_preflight(
                path, failure_evidence=evidence,
            )
            detail["operation"] = "private-create"
            private_directory(path)
        except Exception as exc:
            chain, found = exc, set()
            for _ in range(4):
                if chain is None:
                    break
                for argument in chain.args:
                    if isinstance(argument, str):
                        for message, category in categories.items():
                            if message in argument:
                                found.add(category)
                chain = chain.__cause__ or chain.__context__
            detail["security_categories"] = sorted(found) or ["unclassified"]
            detail["error"] = error_record(exc)
            raise
    detail["stage"] = "ready-before-payload"
    detail["operation"] = "complete"


def default_tree_snapshot(home: Path) -> dict:
    """Bounded read-only facts/bytes for synthetic consumer roots, not validation.

    No raw filename, SID, contents or error prose is published. The candidate
    validator is NOT used to accept ordinary mkdir: inspect actual ownership and
    DACL flags even when private validation would refuse, without repairing them.
    """
    from amplifier_foundation.session import _windows_ownership as win

    api = win._API()
    result = {"roots": {}, "entries": []}
    remaining = 16 * 1024 * 1024
    for label in (".amplifier", ".amplifier-agent"):
        root = home / label
        try:
            root.lstat()  # A dangling reparse point is not an absent root.
        except FileNotFoundError:
            result["roots"][label] = False
            continue
        result["roots"][label] = True
        pending = [root]
        while pending:
            path = pending.pop()
            assert len(result["entries"]) < 256, "Synthetic creation evidence exceeded entry bound"
            relative = path.relative_to(home)
            assert len(relative.parts) <= 32
            info = path.lstat()
            detail = {
                "root": label, "depth": len(relative.parts) - 1,
                "relative_sha256": hashlib.sha256(str(relative).encode("utf-8")).hexdigest(),
            }
            result["entries"].append(detail)
            assert not info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT, "Fixture reparse point"
            directory = stat.S_ISDIR(info.st_mode)
            assert directory or stat.S_ISREG(info.st_mode)
            detail["kind"] = "directory" if directory else "file"
            handle = api.open(path, directory=directory)
            try:
                detail["security"] = windows_acl_evidence(api, handle, win)
            finally:
                api.close(handle)
            if directory:
                pending.extend(sorted(path.iterdir()))
            else:
                assert info.st_size <= remaining, "Synthetic creation evidence exceeded byte bound"
                raw = path.read_bytes()
                remaining -= len(raw)
                assert remaining >= 0
                detail.update({"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    return result


def default_failure_categories(exc) -> list[str]:
    """Known native validation reasons only; never publish consumer error prose."""
    known = {
        "Windows state requires trusted ownership and a non-null DACL": "owner-or-null-dacl",
        "Windows private state requires a protected DACL": "unprotected-dacl",
        "Windows state DACL grants foreign access": "foreign-ace",
        "unsupported Windows state ACE": "unsupported-ace",
        "Windows state path has an alternate namespace alias": "namespace-alias",
    }
    found, seen = set(), set()
    for _ in range(4):
        if exc is None or id(exc) in seen:
            break
        seen.add(id(exc))
        for argument in exc.args:
            if type(argument) is str:
                found.update(category for message, category in known.items() if message in argument)
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    return sorted(found) or ["unclassified"]


async def qualify_default(work: Path, consumer: str, report: dict, caller_sources) -> None:
    """Windows default mkdir discriminator; no session paths/precreated history."""
    import pytest

    assert os.name == "nt", "Default-directory comparison is Windows-only"
    home = Path(os.environ["HOME"])
    root = home.parent / "cases"
    guard = SocketGuard(root, consumer, report, caller_sources)
    sys.addaudithook(guard.audit)
    prepare_runtime_home(report, history_root=False)
    for name in ("t", "config", "c", "appdata", "localappdata", "cache", "cases"):
        private_directory(home.parent / name)  # Siblings of HOME, NOT consumer descendants.
    os.chdir(root)
    config = home.parent / "config" / "unrelated.txt"
    config.write_bytes(b"synthetic unrelated configuration\n")
    before = config.read_bytes()
    detail = report["checks"][f"{consumer}_roundtrip"] = {"status": "running", "default_creation": True}
    detail["before_creation"] = default_tree_snapshot(home)
    assert not any(detail["before_creation"]["roots"].values()), "Consumer roots must start absent"
    assert not detail["before_creation"]["entries"]
    failure = None
    guard.phase = f"{consumer}_roundtrip"  # Same exact blocked capability import policy.
    try:
        # listen() has no CPython audit event. Keep the same explicit guard as
        # the prepared cell, even for an otherwise unbound Windows socket.
        with pytest.MonkeyPatch.context() as patch, ExitStack() as fixtures:
            original_listen = socket.socket.listen

            def guarded_listen(sock, *args, **kwargs):
                return guard.listen(original_listen, sock, *args, **kwargs)

            patch.setattr(socket.socket, "listen", guarded_listen)
            if consumer == "cli":
                fixtures.enter_context(cli_display_offline(detail, report["provenance"]))
            check = (agent_roundtrip(work, root, detail, default_creation=True) if consumer == "agent"
                     else cli_roundtrip(work, root, detail, guard, default_creation=True))
            await asyncio.wait_for(check, timeout=90)
            if consumer == "agent":
                guard.verify_ipv6_probe()
        guard.verify_policy()
        assert config.read_bytes() == before
        detail["status"] = "passed"
    except (Exception, SystemExit) as exc:
        failure = exc
        detail.update(error_record(exc))
        detail["security_categories"] = default_failure_categories(exc)
        raise
    finally:
        # Retain the primary failure if a read-only evidence operation also
        # fails (e.g. consumer cleanup removed a partial session).
        detail["unrelated_config_sha256_before"] = hashlib.sha256(before).hexdigest()
        try:
            after = config.read_bytes()
            detail["unrelated_config_sha256_after"] = hashlib.sha256(after).hexdigest()
            report["synthetic_config_preserved"] = after == before
            detail["after_creation"] = default_tree_snapshot(home)
            assert report["synthetic_config_preserved"], "Default consumer changed unrelated config"
        except Exception as exc:
            detail["after_creation_error"] = error_record(exc)
            detail["status"] = "failed"
            if failure is None:
                raise


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


async def agent_roundtrip(work: Path, root: Path, report: dict, *, default_creation=False) -> None:
    import pytest
    import amplifier_agent as sdk
    from amplifier_agent_engine._engine import assembly

    factory = scripted_factory(work)
    session_id = "installed-sdk-utf8"
    if default_creation:
        from amplifier_agent_engine._engine.configuration import slug

        sessions = Path.home() / ".amplifier-agent" / "projects" / slug(Path.cwd().resolve())
    else:
        sessions = root / "sdk"
    directory = sessions / "sessions" / session_id
    if not default_creation:
        private_directory(directory)
    options = sdk.AgentOptions(provider="anthropic", model="claude-sonnet-5", approvals="allow",
                               **({} if default_creation else {"sessions_directory": sessions}))
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


async def cli_roundtrip(work: Path, root: Path, report: dict, guard, *, default_creation=False) -> None:
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
    if not default_creation:
        private_directory(directory)  # BEFORE SessionStore's ordinary mkdir in main cell only.
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


def address_fingerprint(value) -> dict:
    """Typed primitive tree, bounded before encoding; never invoke custom hooks.

    A capped/unknown tree has only a prefix fingerprint, explicitly labelled.
    This is diagnostic correlation, not an identity/permission decision.
    """
    nodes, payload_bytes, truncated, unsupported = 0, 0, False, False

    def normalize(item, depth=0):
        nonlocal nodes, payload_bytes, truncated, unsupported
        nodes += 1
        if nodes > 64 or depth > 4:
            truncated = True
            return ["limit"]
        kind = type(item)
        if kind in (str, bytes):
            # Slice BEFORE encoding/copying; never materialize an unbounded
            # caller value. bytes remain distinguishable from text in the hash.
            limit = min(1024, max(0, 4096 - payload_bytes))
            prefix = item[:limit]
            raw = prefix.encode("utf-8", errors="replace") if kind is str else prefix
            payload_bytes += len(raw)
            truncated = truncated or len(prefix) != len(item)
            return ["text" if kind is str else "bytes", len(item), raw.hex()]
        if item is None or kind is bool:
            return [kind.__name__, item]
        if kind is int:
            if item.bit_length() <= 64:
                return ["int", item]
            truncated = True
            return ["large-int", item.bit_length()]
        if kind in (tuple, list):
            if len(item) > 16:
                truncated = True
            return [kind.__name__, [normalize(child, depth + 1) for child in item[:16]]]
        unsupported = True
        return ["unsupported"]  # No repr, properties, iteration or custom JSON.

    encoded = json.dumps(normalize(value), ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return {
        "address_sha256": hashlib.sha256(encoded).hexdigest(), "address_bytes": len(encoded),
        "fingerprint_scope": "bounded-prefix" if truncated else "supported-tree",
        "unsupported_value": unsupported,
    }


def socket_address_evidence(event, args) -> dict:
    """Non-resolving shape/class/hash only; never return a hostname or endpoint."""
    sock = args[0] if args and event in {"socket.bind", "socket.connect", "socket.listen",
                                        "socket.sendto", "socket.sendmsg"} else None
    family = getattr(sock, "family", None)
    kind = {socket.AF_INET: "ipv4", socket.AF_INET6: "ipv6"}
    if hasattr(socket, "AF_UNIX"):
        kind[socket.AF_UNIX] = "unix"
    result = {"family": kind.get(family, "unknown")}
    address = None
    if event in {"socket.bind", "socket.connect"} and len(args) > 1:
        address = args[1]
    elif event in {"socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr", "socket.getnameinfo"}:
        address = args[:2]
    elif event == "socket.listen" and sock is not None:
        # The bound local name can be inspected without creating/contacting one.
        try:
            address = sock.getsockname()
        except (OSError, AttributeError):
            pass
    result.update(address_fingerprint(address))
    if family == getattr(socket, "AF_UNIX", object()):
        raw = os.fsdecode(address[:1024]) if type(address) in (str, bytes) else ""
        result["classification"] = "unix-abstract" if raw.startswith("\0") else "unix-filesystem"
        return result
    host = address[0] if type(address) in (tuple, list) and address else address
    if type(host) in (tuple, list) and host:
        host = host[0]  # getnameinfo's sockaddr.
    if type(host) not in (str, bytes, type(None)):
        result["classification"] = "unclassified"
        return result
    if host is not None and len(host) > 1024:
        result["classification"] = "oversized-host"
        return result
    if type(host) is bytes:
        host = host.decode("ascii", errors="replace")
    if host is None or host == "":
        classification = "no-host"
    elif isinstance(host, str) and host.lower() == "localhost":
        classification = "loopback-name"
    else:
        try:
            ip = ipaddress.ip_address(host)
            classification = ("ip-loopback" if ip.is_loopback else "ip-unspecified" if ip.is_unspecified
                              else "ip-private" if ip.is_private else "ip-public")
        except ValueError:
            classification = "named-host"
    result["classification"] = classification
    return result


class CallerSources:
    """Small source allow-list; frame filenames never authorize file reads.

    Installed paths/digests come from the completed installation audit. Stdlib
    paths are fixed beneath this interpreter's stdlib, not module.__file__.
    A name is published only if the actual code equals code compiled from
    those verified bytes. A claimed module, filename or site-packages path is
    insufficient. Compilation is read-only: no source is executed/imported.
    """

    def __init__(self, provenance=None):
        self.sources, self.compiled = {}, {}
        self.ipv6_source = None
        self.add(SCRIPT, "installed_consumer_compat", "harness", digest(SCRIPT))
        stdlib = Path(sysconfig.get_path("stdlib"))
        for module in ("socket", "threading", "platform", "asyncio.base_events", "asyncio.events",
                       "asyncio.runners", "asyncio.selector_events", "asyncio.unix_events",
                       "asyncio.proactor_events", "concurrent.futures.thread"):
            path = stdlib.joinpath(*module.split(".")).with_suffix(".py")
            if path.is_file():
                self.add(path, module, "stdlib", digest(path))
        for package, distribution in (provenance or {}).get("distributions", {}).items():
            if package not in {
                "amplifier-foundation", "amplifier-core", "amplifier-agent", "amplifier-agent-engine",
                "amplifier-app-cli", "amplifier-module-context-simple", "amplifier-module-loop-streaming",
            }:
                continue
            namespace = package.replace("-", "_")
            for relative, evidence in distribution["files"].items():
                parts = relative.replace("\\", "/").split("/")
                if not parts or parts[0] != namespace or not parts[-1].endswith(".py"):
                    continue
                parts[-1] = parts[-1][:-3]
                if parts[-1] == "__init__":
                    parts.pop()
                if not all(part.isidentifier() for part in parts):
                    continue
                path = Path(evidence["path"])
                if path.is_relative_to(Path(sys.prefix)) and "site-packages" in path.parts:
                    self.add(path, ".".join(parts), "installed", evidence["sha256"])
        urllib = (provenance or {}).get("distributions", {}).get("urllib3", {})
        evidence = urllib.get("files", {}).get("urllib3/util/connection.py", {})
        if urllib.get("version") == "2.8.0" and evidence.get("sha256") == IPV6_SOURCE_SHA256:
            path = Path(evidence["path"])
            if path.is_relative_to(Path(sys.prefix)) and "site-packages" in path.parts:
                self.ipv6_source = path
                self.add(path, "urllib3.util.connection", "installed", IPV6_SOURCE_SHA256)

    def add(self, path, module, kind, sha256):
        # Only explicit registration code above supplies paths, never frames.
        key = os.path.normcase(os.path.normpath(str(path)))
        self.sources[key] = (Path(path), module, kind, sha256)

    def identify(self, code) -> dict | None:
        key = os.path.normcase(os.path.normpath(code.co_filename))
        entry = self.sources.get(key)
        if entry is None:
            return None  # Do not stat/open/resolve a frame's unknown filename.
        path, module, kind, expected_digest = entry
        cache_key = (key, code.co_filename)
        if cache_key not in self.compiled:
            if path.stat().st_size > 2 * 1024 * 1024:
                return None
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected_digest:
                return None
            compiled = compile(raw, code.co_filename, "exec", dont_inherit=True, optimize=sys.flags.optimize)
            codes, pending = set(), [compiled]
            while pending:
                current = pending.pop()
                codes.add(current)
                pending.extend(value for value in current.co_consts if type(value) is CodeType)
            self.compiled[cache_key] = codes
        if code not in self.compiled[cache_key]:
            return None  # Dynamic code spoofing this filename is not its source.
        return {
            "module": module, "function": code.co_name, "source_kind": kind,
            "source_sha256": expected_digest,
        }


def socket_caller_evidence(sources=None) -> list[dict]:
    """Unknown frames expose fingerprints only, never claimed names or paths."""
    frames = []
    frame = sys._getframe(1)
    try:
        while frame is not None and len(frames) < 10:
            code = frame.f_code
            source = sources.identify(code) if sources is not None else None
            if source is None:
                source = {
                    "source_kind": "unverified", "module": "withheld", "function": "withheld",
                    "code_sha256": hashlib.sha256(code.co_code[:65536]).hexdigest(),
                    "code_bytes": len(code.co_code),
                    "code_fingerprint_scope": "bounded-prefix" if len(code.co_code) > 65536 else "complete",
                    "filename_sha256": hashlib.sha256(code.co_filename[:4096].encode(
                        "utf-8", errors="replace",
                    )).hexdigest(),
                }
            source["line"] = frame.f_lineno
            frames.append(source)
            frame = frame.f_back
    finally:
        del frame
    return frames


def short_endpoint_path(temp: Path, uid: int) -> Path:
    # Production tempfile.mkdtemp uses h- plus eight random characters.
    endpoint = temp / f"amplifier-handoff-{uid}" / ("h-" + "x" * 8) / "control.sock"
    if len(os.fsencode(endpoint)) > 103:
        raise HarnessPolicyError("Synthetic temporary root exceeds production IPC path budget")
    return endpoint


class SocketGuard:
    """One exact production release socket per CLI call; no other network."""

    def __init__(self, workspace: Path, consumer: str, report: dict, caller_sources=None):
        self.workspace, self.consumer = workspace, consumer
        self.scope = False
        self.endpoint = self.listener = None
        self.attempts = report["blocked_socket_operations"] = []
        self.blocked_details = report["blocked_socket_details"] = []
        self.phase = "fixture-setup"
        self.caller_sources = caller_sources
        self.capability_probe = None
        self.expected_blocked = report["expected_blocked_capability_probes"] = []
        self.ipc = report["release_ipc"] = []
        self.runtime = (
            Path(tempfile.gettempdir()) / f"amplifier-handoff-{os.getuid()}"
            if os.name != "nt" else None
        )

    def blocked(self, event, args=()):
        self.attempts.append(event)  # Never retain addresses or protocol payloads.
        detail = None
        if len(self.blocked_details) < 8:
            detail = {
                "event": event, "phase": self.phase, "consumer": self.consumer,
                "address": {"classification": "diagnostic-unavailable"}, "frames": [],
                "interpretation": "blocked request; caller cause and product compatibility not established",
            }
            self.blocked_details.append(detail)  # Retain refusal before diagnostics.
            for field, read in (
                ("address", lambda: socket_address_evidence(event, args)),
                ("frames", lambda: socket_caller_evidence(self.caller_sources)),
                ("thread", lambda: "main" if threading.current_thread() is threading.main_thread()
                 else "background"),
            ):
                try:
                    detail[field] = read()
                except BaseException:
                    # Diagnostics cannot replace policy refusal, even when a
                    # property, encoder, file or frame inspection fails.
                    detail.setdefault("diagnostic_failures", []).append(field)
        try:
            if self.attest_ipv6_probe(event, args):
                if detail is not None:
                    detail["policy_classification"] = "expected-blocked-capability-probe-pending-verification"
        except BaseException:
            if detail is not None:
                detail["capability_attestation"] = "unavailable; refusal remains unexplained"
        raise HarnessPolicyError("Socket operation outside the exact CLI release endpoint")

    def attest_ipv6_probe(self, event, args) -> bool:
        """Recognize ONE blocked import probe; never call/allow bind ourselves."""
        sources = self.caller_sources
        if (self.consumer != "agent" or self.phase != "agent_roundtrip"
                or event != "socket.bind" or self.capability_probe is not None
                or sources is None or sources.ipv6_source is None or len(args) != 2):
            return False
        sock, address = args
        if (sock.family != socket.AF_INET6 or sock.type != socket.SOCK_STREAM
                or type(address) is not tuple or len(address) != 2
                or type(address[0]) is not str or address[0] != "::1"
                or type(address[1]) is not int or address[1] != 0):
            return False
        if digest(sources.ipv6_source) != IPV6_SOURCE_SHA256:
            return False
        frame = sys._getframe(1)
        try:
            # Only the immediate caller of our actual audit callback qualifies.
            # A helper lower in an arbitrary stack is not an import-time probe.
            for _ in range(8):
                if frame is None:
                    return False
                if frame.f_code is SocketGuard.audit.__code__:
                    break
                frame = frame.f_back
            else:
                return False
            probe = frame.f_back
            module_frame = probe.f_back if probe is not None else None
            if probe is None or module_frame is None:
                return False
            expected = (
                (probe, "_has_ipv6", IPV6_PROBE_CODE_SHA256),
                (module_frame, "<module>", IPV6_MODULE_CODE_SHA256),
            )
            for caller, function, fingerprint in expected:
                evidence = sources.identify(caller.f_code)
                if (evidence is None or evidence["module"] != "urllib3.util.connection"
                        or evidence["source_sha256"] != IPV6_SOURCE_SHA256
                        or evidence["function"] != function
                        or hashlib.sha256(caller.f_code.co_code).hexdigest() != fingerprint):
                    return False
            name = "urllib3.util.connection"
            module = sys.modules.get(name)
            if (module is None or module.__dict__ is not module_frame.f_globals
                    or probe.f_globals is not module_frame.f_globals
                    or probe.f_locals.get("sock") is not sock or probe.f_locals.get("host") != "::1"
                    or "HAS_IPV6" in module_frame.f_globals):
                return False
            receipt = {
                "dependency": "urllib3", "version": "2.8.0", "source_sha256": IPV6_SOURCE_SHA256,
                "attempt_index": len(self.attempts) - 1, "status": "pending-post-import-verification",
                "bind_allowed": False, "socket_closed": False, "has_ipv6_false": False,
            }
            self.expected_blocked.append(receipt)
            self.capability_probe = (sock, module, probe.f_code, module_frame.f_code, receipt)
            return True
        finally:
            del frame

    def verify_ipv6_probe(self) -> None:
        """Observe the original socket and real module after normal import/run."""
        if self.capability_probe is None:
            return
        sock, module, probe_code, module_code, receipt = self.capability_probe
        receipt["socket_closed"] = sock.fileno() == -1
        receipt["has_ipv6_false"] = module.__dict__.get("HAS_IPV6") is False
        intact = (
            sys.modules.get("urllib3.util.connection") is module
            and digest(self.caller_sources.ipv6_source) == IPV6_SOURCE_SHA256
            and self.caller_sources.identify(probe_code) is not None
            and self.caller_sources.identify(module_code) is not None
            and getattr(module.__dict__.get("_has_ipv6"), "__code__", None) is probe_code
        )
        if not intact or not receipt["socket_closed"] or not receipt["has_ipv6_false"]:
            receipt["status"] = "failed-post-import-verification"
            raise HarnessPolicyError("Expected blocked IPv6 probe did not settle safely")
        receipt["status"] = "verified-blocked"
        index = receipt["attempt_index"]
        if index < len(self.blocked_details):
            self.blocked_details[index]["policy_classification"] = "verified-expected-blocked-capability-probe"

    def verify_policy(self) -> None:
        self.verify_ipv6_probe()
        expected = {receipt["attempt_index"] for receipt in self.expected_blocked
                    if receipt["status"] == "verified-blocked"}
        if (len(expected) > 1 or any(index not in expected for index in range(len(self.attempts)))):
            raise HarnessPolicyError("Unexplained blocked socket operation; qualification remains failed")

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
        self.blocked(event, args)

    def listen(self, original, sock, *args, **kwargs):
        if (self.scope and sock is self.listener and self.endpoint is not None
                and sock.family == getattr(socket, "AF_UNIX", None)
                and sock.getsockname() == str(self.endpoint)
                and self.private_endpoint(self.endpoint, bound=True)):
            return original(sock, *args, **kwargs)
        self.blocked("socket.listen", (sock,))

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


async def qualify(work: Path, consumer: str, report: dict, caller_sources=None) -> None:
    import pytest

    # asyncio's own wakeup pair is already initialized. The only exception is
    # normal POSIX CLI registration, scoped around each actual execute_single.
    root = (Path(os.environ["HOME"]).parent / "cases" if os.name == "nt"
            else work / "cases" / consumer)
    if os.name != "nt":
        root.mkdir(parents=True, exist_ok=True)
    guard = SocketGuard(root, consumer, report, caller_sources)
    sys.addaudithook(guard.audit)
    prepare_runtime_home(report)
    if os.name == "nt":
        # HOME creation above also creates the missing runtime parent with a
        # protected TokenUser DACL. Keep every writable fixture on THAT validated
        # chain, not the build volume. Nothing is adopted or ACL-repaired.
        for name in ("t", "config", "c", "appdata", "localappdata", "cache", "cases"):
            private_directory(Path(os.environ["HOME"]).parent / name)
    home = Path(os.environ["AMPLIFIER_HOME"])
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
            guard.phase = name
            detail = report["checks"][name] = {"status": "running"}
            try:
                with ExitStack() as fixtures:
                    if name == "cli_roundtrip":
                        fixtures.enter_context(cli_display_offline(detail, report["provenance"]))
                    result = check(detail)
                    if inspect.isawaitable(result):
                        await asyncio.wait_for(result, timeout=90)
                    if name == "agent_roundtrip":
                        guard.verify_ipv6_probe()
                assert config.read_bytes() == before, "Consumer changed unrelated synthetic config"
                detail["status"] = "passed"
            except (Exception, SystemExit) as exc:
                detail.update(error_record(exc))
            guard.phase = "between-checks"
            save_public_json(work, f"{consumer}.json", report)
    report["synthetic_config_preserved"] = config.read_bytes() == before
    guard.verify_policy()
    assert report["synthetic_config_preserved"]


def graph_identity(provenance: dict) -> list[dict]:
    """Original metadata only, before public path/URL normalization."""
    return [item for item in provenance["all_distributions"]
            if item["name"].lower().replace("_", "-") != "amplifier-foundation"]


def default_report_name(consumer: str, variant: str) -> str:
    assert consumer in {"agent", "cli"} and variant in {"candidate", "baseline"}
    return f"default-{variant}-{consumer}.json"


def run_consumer(work: Path, consumer: str, *, default_creation=False, variant="candidate") -> int:
    report = {
        "status": "failed", "consumer": consumer, "checks": {},
        "python": sys.version, "executable": sys.executable, "os": platform.platform(),
        "machine": platform.machine(), "pointer_bits": struct.calcsize("P") * 8,
        "isolated": sys.flags.isolated, "utf8_mode": sys.flags.utf8_mode,
        "mocks": ["provider factory"] + ([
            "CLI bundle assembly", "CLI first-run setup predicate",
            "optional commit-date display lookup offline double",
        ] if consumer == "cli" else []),
        "limits": [
            "No live provider, default bundle, memory integration or Unified runtime qualification",
            "Windows private history directories prepared by production adapter BEFORE consumer use",
            "Windows runtime fixtures use original TEMP only; unsafe ancestors fail without fallback",
            "Consumer saves replace files; logical extension is distinct from Foundation opt-in append",
            "No checkpoint/fence writes, takeover requests, installer, service, or clean-install claim",
            "Python socket guard is a regression tripwire, not an adversarial OS sandbox",
            "Normal POSIX CLI release registration uses only its exact private AF_UNIX endpoint",
            "Exact urllib3 capability bind remains BLOCKED; accounted only after source/close/HAS_IPV6 proof",
            "Optional CLI commit-date helper returns None; actual version identity and initializer stay real",
        ],
    }
    name = default_report_name(consumer, variant) if default_creation else f"{consumer}.json"
    report["cell"] = "default-creation" if default_creation else "privately-prepared"
    report["foundation_variant"] = variant
    if default_creation:
        report["limits"][1] = "Only safe synthetic HOME is precreated; actual consumers create descendants"
    try:
        require_runner()
        assert variant == "candidate" or default_creation, "Baseline is only the default-creation comparison"
        assert not default_creation or os.name == "nt"
        preparation = json.loads((work / "private/preparation.json").read_text(encoding="utf-8"))
        selected = preparation[variant]
        if variant == "baseline":
            assert selected["revision"] == BASELINE_REV
            assert selected["consumers"][consumer]["status"] == "passed"
        report["foundation_source"] = {key: selected[key] for key in
                                       (("revision", "tree") if variant == "baseline" else ("head", "tree"))}
        wheel = Path(selected["wheel"])
        assert digest(wheel) == selected["wheel_sha256"]
        report["provenance"] = {}
        installed_audit(wheel, selected["wheel_sha256"], consumer, report["provenance"])
        identity = graph_identity(report["provenance"])
        identity_path = work / "private" / f"candidate-graph-{consumer}.json"
        if default_creation:
            assert identity == json.loads(identity_path.read_text(encoding="utf-8")), "Other graph identities changed"
            report["other_graph_identity_matches_candidate"] = True
        else:
            save_json(identity_path, identity)
        check = qualify_default if default_creation else qualify
        asyncio.run(check(work, consumer, report, CallerSources(report["provenance"])))
        assert report["checks"], "No consumer check executed"
        assert all(item["status"] == "passed" for item in report["checks"].values())
        report["status"] = "passed"
    except (Exception, SystemExit) as exc:
        report["error"] = error_record(exc)
    finally:
        report["imports"] = import_audit()
        if report["imports"]["source_shadowing"]:
            report["status"] = "failed"
        save_public_json(work, name, report)
    return int(report["status"] != "passed")


def run(work: Path) -> int:
    require_runner()
    report = {"status": "failed", "consumers": {}}
    for consumer in ("agent", "cli"):
        compact = None
        location = {}
        try:
            # Preparation failures do not suppress the other graph's evidence.
            with ExitStack() as resources:
                root = work / "runtime" / consumer
                if os.name == "nt":
                    root = windows_runtime_root(os.environ.get("TEMP", ""), evidence=location)
                    # No mkdir here. The installed child must validate every
                    # existing ancestor and privately create the absent root.
                else:
                    # Private, unique, compact CI-only synthetic root. Runner
                    # temp defaults can exceed the handler's 103-byte bound.
                    root = Path(resources.enter_context(
                        tempfile.TemporaryDirectory(prefix="ic-", dir="/tmp"),
                    ))
                    compact = root
                    short_endpoint_path(root / "t", os.getuid())
                env = clean_environment(root, runtime=True)
                # -I ignores PYTHONUTF8: set the fixture encoding explicitly.
                command([env_python(work, consumer), "-I", "-X", "utf8", SCRIPT,
                         "--consumer", consumer, "--work", work],
                        cwd=work, env=env, timeout=300)
            report["consumers"][consumer] = {"status": "passed"}
        except Exception as exc:
            report["consumers"][consumer] = error_record(exc)
        finally:
            if os.name == "nt":
                report["consumers"][consumer]["runtime_location"] = location
            if compact is not None:
                removed = not compact.exists()
                report["consumers"][consumer]["compact_root_removed"] = removed
                if not removed:
                    report["consumers"][consumer]["status"] = "failed"
    if all(item["status"] == "passed" for item in report["consumers"].values()):
        report["status"] = "passed"
    if os.name == "nt":
        report["default_creation"] = run_defaults(work)
        if report["default_creation"]["status"] != "passed":
            report["status"] = "failed"
    else:
        report["default_creation"] = {"scope": "Windows-only; no additional Linux cell"}
    save_public_json(work, "execution.json", report)
    return int(report["status"] != "passed")


def default_comparison(candidate: dict, baseline: dict) -> dict:
    """A classification is not acceptance: both actual cells must pass."""
    states = [candidate.get("status"), baseline.get("status")]
    return {
        "candidate": states[0], "baseline": states[1],
        "status": "passed" if states == ["passed", "passed"] else "failed",
        "interpretation": ("both default paths qualified" if states == ["passed", "passed"] else
                           "candidate default cell failed while baseline passed; inspect failure domain"
                           if states == ["failed", "passed"] else
                           "baseline also failed; no regression verdict from this comparison"),
    }


def run_defaults(work: Path) -> dict:
    """Four fresh Windows children; reuse candidate venvs, never reinstall them."""
    assert os.name == "nt"
    report = {"status": "failed", "cells": {}, "comparisons": {}}
    for consumer in ("agent", "cli"):
        states = {}
        for variant in ("candidate", "baseline"):
            name = default_report_name(consumer, variant)
            detail = report["cells"][name] = {"status": "failed"}
            location = detail["runtime_location"] = {}
            try:
                # Same qualified canonical-temp/private-HOME setup, new absent
                # runtime parent for EACH process/cell. No guessed drive fallback.
                root = windows_runtime_root(os.environ.get("TEMP", ""), evidence=location)
                env = clean_environment(root, runtime=True)
                base = work if variant == "candidate" else work / "b"
                command([env_python(base, consumer), "-I", "-X", "utf8", SCRIPT,
                         "--consumer", consumer, "--work", work, "--default-creation", "--variant", variant],
                        cwd=work, env=env, timeout=300)
                # Read a bounded generated receipt, not operational paths from it.
                saved = work / "evidence" / name
                assert saved.stat().st_size <= MAX_PUBLIC_BYTES
                body = json.loads(saved.read_text(encoding="utf-8"))
                assert body["status"] == "passed" and body["checks"]
                assert body["cell"] == "default-creation" and body["foundation_variant"] == variant
                assert body["other_graph_identity_matches_candidate"]
                assert all(item["status"] == "passed" for item in body["checks"].values())
                detail["status"] = "passed"
            except Exception as exc:
                detail["error"] = error_record(exc)  # Never reinterpret a refusal as expected success.
            states[variant] = detail
        report["comparisons"][consumer] = default_comparison(states["candidate"], states["baseline"])
    if all(item["status"] == "passed" for item in report["comparisons"].values()):
        report["status"] = "passed"
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--run", action="store_true")
    mode.add_argument("--check-public", action="store_true")
    mode.add_argument("--consumer", choices=("agent", "cli"))
    parser.add_argument("--default-creation", action="store_true")
    parser.add_argument("--variant", choices=("candidate", "baseline"), default="candidate")
    parser.add_argument("--work", required=True, type=Path)
    args = parser.parse_args()
    work = args.work.resolve()
    # Refuse before any allocation/install/consumer side effects outside CI.
    require_runner()
    if args.default_creation or args.variant != "candidate":
        assert args.consumer is not None and args.default_creation and os.name == "nt"
    if os.name == "nt":
        # The workflow computes the short root from RUNNER_TEMP. Refuse a
        # drive mismatch rather than trusting C: or repairing an unsafe volume.
        validate_windows_work_drive(work, os.environ.get("RUNNER_TEMP", ""))
    if args.check_public:
        return check_public(work)
    work.mkdir(parents=True, exist_ok=True)
    if args.prepare:
        return prepare(work)
    if args.run:
        return run(work)
    return run_consumer(work, args.consumer, default_creation=args.default_creation, variant=args.variant)


if __name__ == "__main__":
    try:
        exit_status = main()
    except Exception:
        # Do not leak raw terminal tracebacks if setup or the public gate fails.
        print("Installed-consumer qualification failed; raw diagnostics withheld.", file=sys.stderr)
        exit_status = 1
    raise SystemExit(exit_status)