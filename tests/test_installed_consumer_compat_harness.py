"""Portable harness guards; never build, install, bind, or launch consumers.

Retire these guards with the explicit installed-consumer CI harness. These check
its bounded test plumbing, not Windows behavior or consumer compatibility.
"""

import ast
from asyncio import CancelledError
import copy
import importlib.util
import json
from pathlib import Path
import re
from types import SimpleNamespace, coroutine

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "installed_consumer_compat.py"
TEXT = HARNESS.read_text(encoding="utf-8")
TREE = ast.parse(TEXT)
FUNCTIONS = {
    node.name: node for node in TREE.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}


def source(name):
    return ast.get_source_segment(TEXT, FUNCTIONS[name])


def calls(name, attribute):
    return [
        node for node in ast.walk(FUNCTIONS[name])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == attribute
    ]


@pytest.fixture(scope="module")
def harness():
    spec = importlib.util.spec_from_file_location("_installed_compat_guards", HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _drive(coro):
    """Bounded coroutine driver: no OS event loop, self-pipe, or real socket."""
    try:
        for _ in range(100):
            try:
                coro.send(None)
            except StopIteration as done:
                return done.value
        raise AssertionError("Portable coroutine model did not finish")
    finally:
        coro.close()


@pytest.fixture
def cleanup_loop(harness, monkeypatch):
    state = SimpleNamespace(now=0.0, callbacks=[], yields=0)
    loop = SimpleNamespace(
        time=lambda: state.now,
        is_closed=lambda: False,
        call_soon_threadsafe=lambda callback: state.callbacks.append(callback),
    )

    @coroutine
    def sleep(delay):
        yield None
        state.yields += 1
        callbacks, state.callbacks = state.callbacks, []
        for callback in callbacks:
            callback()
        state.now += delay

    monkeypatch.setattr(harness.asyncio, "get_running_loop", lambda: loop)
    monkeypatch.setattr(harness.asyncio, "sleep", sleep)
    monkeypatch.setattr(harness, "IPC_CLEANUP_TIMEOUT", 0.03)
    return loop, state


def test_harness_is_not_normally_collected_or_importing_product_at_module_scope():
    assert not HARNESS.name.startswith("test_")
    assert not any(name.startswith("test_") for name in FUNCTIONS)
    for node in TREE.body:
        if isinstance(node, ast.Import):
            assert not any(alias.name.startswith("amplifier_") for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("amplifier_")
    assert not re.search(r"sys\.path\.(insert|append|extend)", TEXT)
    assert "spec_from_file_location" in source("scripted_factory")
    assert "tests/support/scripted_provider.py" in source("scripted_factory")


def test_original_six_job_matrix_and_named_summary_remain():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    original = workflow["jobs"]["tests"]
    assert original["strategy"]["matrix"] == {
        "os": ["ubuntu-latest", "windows-latest"],
        "python-version": ["3.11", "3.12", "3.13"],
    }
    assert original["strategy"]["fail-fast"] is False
    assert original["steps"][-1]["run"] == "uv run pytest tests/ -q --tb=short -rA"


def test_separate_native_and_ubuntu_jobs_keep_pins_and_failure_evidence():
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    job = workflow["jobs"]["installed-consumers"]
    assert job["strategy"]["matrix"]["os"] == ["windows-2025", "ubuntu-24.04"]
    assert job["strategy"]["fail-fast"] is False
    assert job["permissions"] == {"contents": "read"}
    assert job["steps"][0]["with"]["ref"] == "${{ github.event.pull_request.head.sha || github.sha }}"
    for step in job["steps"]:
        if "uses" in step:
            assert re.fullmatch(r"[\w/-]+@[0-9a-f]{40}", step["uses"])
    python = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/setup-python@"))
    assert python["with"] == {"python-version": "3.13", "architecture": "x64"}
    upload = job["steps"][-1]
    assert upload["if"] == "${{ always() && steps.public-evidence.outcome == 'success' }}"
    assert upload["with"]["path"].endswith("/evidence/*.json")
    assert upload["with"]["if-no-files-found"] == "error"
    gate = job["steps"][-2]
    assert gate["id"] == "public-evidence" and "always()" in gate["if"]
    assert "--check-public" in gate["run"]
    runtime = job["steps"][-3]
    assert "always()" in runtime["if"]
    assert "python -I -X utf8 tests/installed_consumer_compat.py --run" in runtime["run"]


def test_only_explicit_candidate_override_and_distinct_core_graphs():
    prepare = source("prepare")
    assert '"amplifier-core==2.0.2"' in prepare
    assert '"agent": [' in prepare and '"cli": [' in prepare
    assert "amplifier-foundation @ {wheel.as_uri()}" in prepare
    assert "--no-deps" not in TEXT and "--editable" not in TEXT
    assert "--only-binary" in prepare and '"amplifier-core"' in prepare
    assert '("2.0.1" if consumer == "agent" else "2.0.2")' in source("installed_audit")
    assert '"vcs_info"]["commit_id"] == revision' in source("installed_audit")
    assert 'direct["archive_info"]["hashes"]["sha256"] == digest(wheel)' in source("installed_audit")
    assert 'path.read_bytes() == archive.read(name)' in source("installed_audit")
    assert "source_shadowing" in source("run_consumer")
    assert "site-packages" in source("import_audit")


def test_mocks_are_only_permitted_seams_and_network_tripwire():
    patches = []
    for node in ast.walk(TREE):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "setattr":
            patches.append(ast.unparse(node.args[1] if len(node.args) == 3 else node.args[0]))
    assert patches == [
        "'_provider_factory'", "'_provider_factory'", "'_create_bundle_session'",
        "'amplifier_app_cli.commands.init.check_first_run'", "'listen'",
    ]
    assert "HeldSession.write" not in TEXT
    assert "register_release_handler.__code__" in TEXT
    assert 'frame.f_locals.get("registration")' in TEXT
    assert "skip" not in {node.func.attr for node in ast.walk(TREE)
                          if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}


def test_real_resume_runs_second_turn_and_physical_backup_checks():
    sdk = source("agent_roundtrip")
    assert len(calls("agent_roundtrip", "run")) == 2
    assert len(calls("agent_roundtrip", "resume_session")) == 3
    assert 'busy.value.code == "session_in_use"' in sdk
    cli = calls("cli_roundtrip", "execute_single")
    assert len(cli) == 2
    assert any(keyword.arg == "initial_transcript" for keyword in cli[1].keywords)
    assert 'providers[1].requests[0]["messages"]' in source("cli_roundtrip")
    assert "assert_conversation(transcript, [QUOTE, SECOND])" in source("cli_roundtrip")
    assert "history_files(directory, [QUOTE, SECOND], prior=prior)" in source("cli_roundtrip")
    assert "reopened.history == second_history" in source("agent_roundtrip")
    assert "second_history = copy.deepcopy(resumed.history)" in source("agent_roundtrip")
    assert "transcript.jsonl.backup" in source("history_files")
    assert "metadata.json.backup" in source("history_files")
    assert "incremental=True" in source("incremental_history")
    assert "(old.dev, old.ino) == (new.dev, new.ino)" in source("incremental_history")
    scopes = [
        node for node in ast.walk(FUNCTIONS["cli_roundtrip"])
        if isinstance(node, ast.AsyncWith)
        and any(isinstance(item.context_expr, ast.Call)
                and isinstance(item.context_expr.func, ast.Attribute)
                and item.context_expr.func.attr == "release_scope" for item in node.items)
    ]
    assert len(scopes) == 2
    parents = {child: parent for parent in ast.walk(TREE) for child in ast.iter_child_nodes(parent)}
    for scope in scopes:
        assert isinstance(parents[scope], ast.With)  # Sync console outside async scope.


def test_settings_overlap_retains_real_read_and_failure_with_release_control():
    overlap = source("settings_overlap")
    assert "with _open_binary(history.transcript_path) as stream" in overlap
    assert len(calls("settings_overlap", "set_active_bundle")) == 3
    assert "raise failure" in overlap
    assert overlap.index('report["released_settings_write"]') < overlap.index("raise failure")
    private = source("private_directory")
    assert "win._pinned(win._API(), path, root=path, create=True)" in private
    assert not re.search(r"chmod|SetNamedSecurityInfo|icacls", TEXT)


def test_runner_restriction_and_no_shell_or_secret_environment_dump():
    main = source("main")
    assert main.index("require_runner()") < main.index("work.mkdir(")
    env = source("clean_environment")
    assert '"GIT_CONFIG_GLOBAL": os.devnull' in env
    assert '"GIT_CONFIG_NOSYSTEM": "1"' in env
    assert "if key.upper() in allowed" in env
    assert "dict(os.environ)" not in env
    run = calls("command", "run")
    assert len(run) == 1
    keywords = {keyword.arg: keyword.value for keyword in run[0].keywords}
    assert "shell" not in keywords
    assert ast.unparse(keywords["timeout"]) == "timeout"
    assert ast.unparse(keywords["stdin"]) == "subprocess.DEVNULL"
    assert "finally:" in source("run_consumer")
    assert "save_public_json" in source("run_consumer")
    assert '"private/preparation.json"' in source("run_consumer")
    assert '"metadata": distribution.read_text("METADATA")' not in TEXT


def test_compact_ipc_path_budget_without_allocating_or_binding(harness):
    endpoint = harness.short_endpoint_path(Path("/tmp/ic-abcdefgh/tmp"), 1001)
    assert len(str(endpoint).encode()) <= 103
    with pytest.raises(harness.HarnessPolicyError):
        harness.short_endpoint_path(Path("/tmp") / ("x" * 80), 1001)
    assert 'tempfile.TemporaryDirectory(prefix="ic-", dir="/tmp")' in source("run")
    assert "short_endpoint_path(root" in source("run")


def test_socket_guard_allows_only_frozen_unix_endpoint(harness, tmp_path, monkeypatch):
    """Fake sockets only. Test policy decisions, not real IPC or consumer startup."""
    guard = harness.SocketGuard(tmp_path, "cli", {})
    guard.scope = True
    endpoint = tmp_path / "control.sock"
    seen = []
    monkeypatch.setattr(guard, "private_endpoint", lambda path, **kw: path == endpoint)
    monkeypatch.setattr(guard, "production_endpoint", lambda path: path == endpoint)
    unix = getattr(harness.socket, "AF_UNIX", -777)
    monkeypatch.setattr(harness.socket, "AF_UNIX", unix, raising=False)
    listener = SimpleNamespace(family=unix, type=harness.socket.SOCK_STREAM,
                               getsockname=lambda: str(endpoint))
    guard.audit("socket.bind", (listener, str(endpoint)))
    guard.listen(lambda sock, backlog: seen.append(backlog), listener, 2)
    guard.audit("socket.connect", (listener, str(endpoint)))
    assert seen == [2]
    for event, args in (
        ("socket.bind", (listener, str(tmp_path / "other.sock"))),
        ("socket.bind", (listener, str(endpoint))),  # No second bind, even exact.
        ("socket.connect", (listener, str(tmp_path / "other.sock"))),
        ("socket.connect", (listener, "\0abstract")),
        ("socket.connect", (SimpleNamespace(family=harness.socket.AF_INET,
                                           type=harness.socket.SOCK_STREAM), ("127.0.0.1", 7))),
        ("socket.bind", (SimpleNamespace(family=harness.socket.AF_INET6,
                                        type=harness.socket.SOCK_STREAM), ("::1", 7))),
        ("socket.getaddrinfo", ("example.invalid", 443)),
        ("socket.gethostbyname", ("example.invalid",)),
        ("socket.gethostbyaddr", ("192.0.2.1",)),
        ("socket.getnameinfo", (("192.0.2.1", 443), 0)),
        ("socket.sendto", (listener, b"fixture", str(endpoint))),
    ):
        with pytest.raises(harness.HarnessPolicyError):
            guard.audit(event, args)
    with pytest.raises(harness.HarnessPolicyError):
        guard.listen(lambda *args: None, SimpleNamespace(family=unix), 2)
    guard.scope = False
    with pytest.raises(harness.HarnessPolicyError):
        guard.audit("socket.connect", (listener, str(endpoint)))
    assert len(guard.attempts) == 13


def test_endpoint_validation_requires_private_task_owned_registration(harness, tmp_path, monkeypatch):
    guard = harness.SocketGuard(tmp_path, "cli", {})
    guard.runtime = Path("C:/ipc") if harness.os.name == "nt" else Path("/ipc")
    endpoint = guard.runtime / "h-abcdefgh" / "control.sock"
    # Metadata doubles only: no actual socket or directory creation.
    uid = 1001
    monkeypatch.setattr(harness.os, "getuid", lambda: uid, raising=False)
    monkeypatch.setattr(Path, "resolve", lambda path: path)
    info = SimpleNamespace(st_mode=harness.stat.S_IFDIR | 0o700, st_uid=uid)
    monkeypatch.setattr(Path, "lstat", lambda path: info)
    monkeypatch.setattr(Path, "exists", lambda path: False)
    assert guard.private_endpoint(endpoint, bound=False)
    info.st_mode = harness.stat.S_IFDIR | 0o755
    assert not guard.private_endpoint(endpoint, bound=False)
    info.st_mode, info.st_uid = harness.stat.S_IFDIR | 0o700, uid + 1
    assert not guard.private_endpoint(endpoint, bound=False)
    info.st_uid = uid
    assert not guard.private_endpoint(tmp_path / "unrelated" / "control.sock", bound=False)
    assert not guard.private_endpoint(endpoint.with_name("other.sock"), bound=False)
    info.st_mode = harness.stat.S_IFSOCK | 0o600
    # Directories cannot be sockets, even if the leaf's permissions are private.
    assert not guard.private_endpoint(endpoint, bound=True)


@pytest.mark.parametrize("leftover", ["endpoint", "directory", "listener"])
def test_ipc_cleanup_failure_is_not_hidden(harness, tmp_path, monkeypatch, cleanup_loop, leftover):
    guard = harness.SocketGuard(tmp_path, "cli", {})
    guard.runtime = tmp_path
    endpoint = tmp_path / "h-abcdefgh" / "control.sock"
    monkeypatch.setattr(Path, "exists", lambda path: (
        path == endpoint if leftover == "endpoint" else
        path == endpoint.parent if leftover == "directory" else False
    ))
    detail = {}
    async def check():
        async with guard.release_scope(detail):
            guard.endpoint = endpoint
            guard.listener = SimpleNamespace(fileno=lambda: 10 if leftover == "listener" else -1)

    with pytest.raises(harness.HarnessPolicyError):
        _drive(check())
    assert detail["release_ipc_cleaned"] is False
    assert "release_ipc_cleanup_error" in detail
    assert cleanup_loop[1].yields > 0
    assert cleanup_loop[1].now <= 0.03


def test_ipc_cleanup_is_observed_not_performed_by_guard(harness, tmp_path, monkeypatch, cleanup_loop):
    guard = harness.SocketGuard(tmp_path, "cli", {})
    guard.runtime = tmp_path
    endpoint = tmp_path / "h-abcdefgh" / "control.sock"
    monkeypatch.setattr(Path, "exists", lambda path: False)
    detail = {}
    async def check():
        async with guard.release_scope(detail):
            guard.endpoint = endpoint
            guard.listener = SimpleNamespace(fileno=lambda: -1)

    _drive(check())
    assert detail["release_ipc_cleaned"] is True
    assert guard.endpoint is None and guard.listener is None and not guard.scope
    guard_source = ast.get_source_segment(TEXT, next(n for n in TREE.body
                                                   if isinstance(n, ast.ClassDef) and n.name == "SocketGuard"))
    assert ".unlink(" not in guard_source and ".close(" not in guard_source
    assert 'owner.get("session_id") == "installed-cli-utf8"' in guard_source
    assert 'owner.get("workspace") == str(self.workspace.resolve())' in guard_source


@pytest.fixture
def production_deactivate(tmp_path, monkeypatch, cleanup_loop):
    """Execute only the actual AST-extracted method, with all resources fake."""
    path = ROOT / "amplifier_foundation/session/handoff.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    registration_class = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                              and n.name == "ReleaseRegistration")
    method = copy.deepcopy(next(n for n in registration_class.body
                               if isinstance(n, ast.FunctionDef) and n.name == "_deactivate"))
    namespace = {"os": SimpleNamespace(getpid=lambda: 1001), "_REGISTRATIONS": set()}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                 "production-deactivate-probe", "exec"), namespace)
    registration = type("RegistrationProbe", (), {})()
    state = SimpleNamespace(endpoint=True, directory=True, fd=7, closes=0)
    endpoint = tmp_path / "h-abcdefgh/control.sock"

    def close():
        state.closes += 1
        state.fd = -1

    registration.pid = 1001
    registration.active = True
    registration.path, registration.directory = endpoint, endpoint.parent
    registration.listener = SimpleNamespace(fileno=lambda: state.fd)
    registration.server = SimpleNamespace(close=close)
    registration.loop = cleanup_loop[0]
    namespace["_REGISTRATIONS"].add(registration)
    monkeypatch.setattr(Path, "exists", lambda path: (
        state.endpoint if path == endpoint else state.directory if path == endpoint.parent else False
    ))

    def unlink(path, *, missing_ok=False):
        assert path == endpoint and missing_ok
        state.endpoint = False

    def rmdir(path):
        assert path == endpoint.parent
        state.directory = False

    monkeypatch.setattr(Path, "unlink", unlink)
    monkeypatch.setattr(Path, "rmdir", rmdir)
    return namespace["_deactivate"], registration, state


@pytest.mark.parametrize("never_closes", [False, True])
def test_actual_production_queued_deactivate_is_observed_after_yield(
    harness, tmp_path, cleanup_loop, production_deactivate, never_closes,
):
    deactivate, registration, state = production_deactivate
    if never_closes:
        registration.server = SimpleNamespace(close=lambda: None)
    guard = harness.SocketGuard(tmp_path, "cli", {})
    guard.runtime = tmp_path
    detail = {}

    async def check():
        async with guard.release_scope(detail):
            guard.endpoint, guard.listener = registration.path, registration.listener
            deactivate(registration)
            # Original production source has removed names but only QUEUED
            # close. This is the exact false-failure state from the review.
            assert not state.endpoint and not state.directory
            assert state.fd == 7 and state.closes == 0
            assert len(cleanup_loop[1].callbacks) == 1

    if never_closes:
        with pytest.raises(harness.HarnessPolicyError):
            _drive(check())
        assert detail["release_ipc_cleaned"] is False and state.fd == 7
        assert guard.ipc[-1]["endpoint_removed"] and guard.ipc[-1]["directory_removed"]
        assert not guard.ipc[-1]["listener_closed"]
    else:
        _drive(check())
        assert detail["release_ipc_cleaned"] is True
        assert state.fd == -1 and state.closes == 1
        assert guard.ipc[-1]["checks"] >= 2
    assert cleanup_loop[1].yields > 0


@pytest.mark.parametrize("original_type", [RuntimeError, SystemExit, CancelledError])
def test_original_cli_failure_survives_cleanup_timeout_with_evidence(
    harness, tmp_path, monkeypatch, cleanup_loop, original_type,
):
    guard = harness.SocketGuard(tmp_path, "cli", {})
    guard.runtime = tmp_path
    monkeypatch.setattr(Path, "exists", lambda path: False)
    original = original_type("synthetic-private-failure")
    detail = {}

    async def check():
        async with guard.release_scope(detail):
            guard.endpoint = tmp_path / "h-abcdefgh/control.sock"
            guard.listener = SimpleNamespace(fileno=lambda: 7)
            raise original

    with pytest.raises(original_type) as returned:
        _drive(check())
    assert returned.value is original
    assert detail["release_ipc_cleaned"] is False
    assert detail["release_ipc_cleanup_error"]["failure_domain"] == "qualification-policy"
    assert "synthetic-private-failure" not in json.dumps(detail)
    assert any("cleanup also failed" in note for note in original.__notes__)
    assert not guard.scope and guard.endpoint is None and guard.listener is None


def test_synthetic_command_failure_never_serializes_output_argv_or_messages(harness, tmp_path, monkeypatch):
    sentinel = "synthetic-private-sentinel"
    stdout = f"Authorization: Bearer {sentinel}\nhttps://fixture:{sentinel}@example.invalid"
    stderr = f"/home/fixture/{sentinel}/secret.txt"
    monkeypatch.setattr(harness.subprocess, "run", lambda *args, **kw: SimpleNamespace(
        returncode=17, stdout=stdout, stderr=stderr,
    ))
    with pytest.raises(harness.CommandFailure) as failure:
        harness.command(["fixture", sentinel], cwd=tmp_path, env={})
    record = harness.error_record(failure.value)
    serialized = json.dumps(record)
    assert sentinel not in serialized
    assert "Authorization" not in serialized and "example.invalid" not in serialized
    assert record["diagnostics"][0]["returncode"] == 17
    assert record["diagnostics"][0]["streams"]["stdout"]["sha256"] == harness.hashlib.sha256(stdout.encode()).hexdigest()
    try:
        raise RuntimeError(f"password={sentinel}") from failure.value
    except RuntimeError as exc:
        record = harness.error_record(exc)
    assert sentinel not in json.dumps(record)
    assert len(record["diagnostics"]) == 2
    harness.save_public_json(tmp_path, "agent.json", record)
    assert harness.check_public(tmp_path) == 0


def test_synthetic_timeout_cannot_leak_retained_output(harness):
    error = harness.subprocess.TimeoutExpired(
        ["fixture", "synthetic-argv-sentinel"], 2,
        output="synthetic-output-sentinel", stderr="synthetic-stderr-sentinel",
    )
    record = harness.error_record(error)
    assert "sentinel" not in json.dumps(record)
    assert record["diagnostics"][0]["timeout"] == 2


def test_public_boundary_removes_credentials_and_normalizes_paths(harness, tmp_path):
    sentinel = "synthetic-private-sentinel"
    work = str(tmp_path)
    raw = {
        "Authorization": f"Bearer {sentinel}",
        "api_key": sentinel,
        "url": f"https://fixture:{sentinel}@example.invalid/repo?token={sentinel}#secret={sentinel}",
        "failure": f"Bearer {sentinel} password='{sentinel}'",
        "runner": f"{work}/agent/lib/site-packages/fixture.py",
        "foreign_path": f"/home/private-fixture/{sentinel}/config",
        "foreign_windows_path": rf"C:\Users\private-fixture\{sentinel}\config",
        "foreign_file_url": f"file:///home/private-fixture/{sentinel}/config",
        "email": f"{sentinel}@example.invalid",
        "sha256": "a" * 64,
    }
    harness.save_public_json(tmp_path, "cli.json", raw)
    public = json.loads((tmp_path / "evidence/cli.json").read_text(encoding="utf-8"))
    serialized = json.dumps(public)
    assert sentinel not in serialized and work not in serialized
    assert "private-fixture" not in serialized
    assert public["runner"].startswith("<agent-env>/")
    assert public["url"] == "https://example.invalid/repo"
    assert public["sha256"] == "a" * 64
    assert harness.check_public(tmp_path) == 0


@pytest.mark.parametrize("filenames", [
    ("../../../bin/py.test", "../../../bin/pytest"),
    ("../../Scripts/py.test.exe", "../../Scripts/pytest.exe"),
    (r"..\..\Scripts\py.test.exe", r"..\..\Scripts\pytest.exe"),
])
def test_relative_record_script_identifiers_remain_distinct_through_upload_gate(
    harness, tmp_path, filenames,
):
    files = {
        name: {"sha256": str(index) * 64, "path": str(tmp_path / "fixture" / str(index))}
        for index, name in enumerate(filenames, 1)
    }
    raw = {"provenance": {"distributions": {"fixture": {"files": files}}}}
    harness.save_public_json(tmp_path, "agent.json", raw)
    saved = json.loads((tmp_path / "evidence/agent.json").read_text(encoding="utf-8"))
    actual = saved["provenance"]["distributions"]["fixture"]["files"]
    assert list(actual) == list(filenames)
    assert [actual[name]["sha256"] for name in filenames] == ["1" * 64, "2" * 64]
    assert all(harness.public_string(name, {}) == name for name in filenames)
    assert harness.check_public(tmp_path) == 0
    # The final scanner also accepts original relative RECORD identifiers,
    # rather than relying on some redactor-only exemption.
    for name in filenames:
        harness.assert_public(name)


@pytest.mark.parametrize("keys", [
    ("/home/private-one/fixture", "/home/private-two/fixture"),
    ("https://one:synthetic-secret@example.invalid/repo",
     "https://two:synthetic-secret@example.invalid/repo"),
])
def test_genuinely_ambiguous_public_key_normalization_still_fails_closed(harness, tmp_path, keys):
    with pytest.raises(ValueError, match="collided"):
        harness.save_public_json(tmp_path, "agent.json", {keys[0]: 1, keys[1]: 2})
    assert not (tmp_path / "evidence/agent.json").exists()


def test_relative_record_preservation_does_not_exempt_absolute_paths_or_auth(harness):
    value = ("../../../bin/pytest /home/private-fixture/config "
             r"C:\Users\private-fixture\config "
             "Authorization: Bearer synthetic-private-sentinel")
    normalized = harness.public_string(value, {})
    assert normalized.startswith("../../../bin/pytest ")
    assert "private-fixture" not in normalized and "synthetic-private-sentinel" not in normalized
    harness.assert_public(normalized)
    with pytest.raises(ValueError):
        harness.assert_public(value)


@pytest.mark.parametrize("unsafe", [
    {"failure": "Bearer synthetic-private-sentinel"},
    {"failure": "password=synthetic-private-sentinel"},
    {"failure": "https://fixture:synthetic-private-sentinel@example.invalid/repo"},
    {"failure": "https://example.invalid/repo?token=synthetic-private-sentinel"},
    {"failure": "/home/private-fixture/synthetic-private-sentinel"},
    {"failure": r"C:\Users\private-fixture\synthetic-private-sentinel"},
    {"failure": "file:///home/private-fixture/synthetic-private-sentinel"},
    {"api_key": "synthetic-private-sentinel"},
])
def test_final_rescan_fails_closed_on_unscrubbed_saved_evidence(harness, tmp_path, unsafe):
    directory = tmp_path / "evidence"
    directory.mkdir()
    path = directory / "cli.json"
    raw = json.dumps(unsafe)
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError):
        harness.check_public(tmp_path)
    assert path.read_text(encoding="utf-8") == raw  # Gate did not repair and upload.


@pytest.mark.parametrize("kind", ["unknown", "oversized", "empty", "nonfinite"])
def test_upload_gate_rejects_extra_or_unbounded_evidence(harness, tmp_path, monkeypatch, kind):
    directory = tmp_path / "evidence"
    directory.mkdir()
    if kind != "empty":
        (directory / ("raw.txt" if kind == "unknown" else "cli.json")).write_text('{"safe":true}')
    if kind == "oversized":
        monkeypatch.setattr(harness, "MAX_PUBLIC_BYTES", 4)
    if kind == "nonfinite":
        (directory / "cli.json").write_text('{"unsafe":NaN}')
    with pytest.raises(ValueError):
        harness.check_public(tmp_path)


def _fixture_messages(harness):
    return [
        {"role": role, "content": content}
        for prompt, answer in zip([harness.QUOTE, harness.SECOND], harness.ANSWERS)
        for role, content in (("user", prompt), ("assistant", answer))
    ]


@pytest.mark.parametrize("mutation", ["missing", "swapped", "wrong_role", "wrong_answer"])
def test_returned_conversation_rejects_missing_reordered_or_wrong_content(harness, mutation):
    rows = _fixture_messages(harness)
    if mutation == "missing":
        rows.pop(1)
    elif mutation == "swapped":
        rows[0], rows[1] = rows[1], rows[0]
    elif mutation == "wrong_role":
        rows[3]["role"] = "user"
    else:
        rows[3]["content"] = "wrong answer"
    with pytest.raises(AssertionError):
        harness.assert_conversation(rows, [harness.QUOTE, harness.SECOND])


def test_returned_conversation_accepts_both_real_text_representations(harness):
    rows = _fixture_messages(harness)
    rows[1]["content"] = [{"type": "text", "text": rows[1]["content"]}]
    harness.assert_conversation(rows, [harness.QUOTE, harness.SECOND])


@pytest.mark.parametrize("invalid", ["empty_transcript", "empty_metadata", "wrong_turn", "lost_session"])
def test_backup_oracles_reject_empty_or_unrelated_previous_state(harness, tmp_path, invalid):
    rows = _fixture_messages(harness)
    previous = (rows[:2], {"turn_count": 1, "session_id": "fixture"})
    transcript = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    (tmp_path / "transcript.jsonl").write_text(transcript, encoding="utf-8")
    (tmp_path / "metadata.json").write_text('{"turn_count":2,"session_id":"fixture"}')
    (tmp_path / "transcript.jsonl.backup").write_text(
        "" if invalid == "empty_transcript" else
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows[:2]), encoding="utf-8",
    )
    backup = {} if invalid == "empty_metadata" else {
        "turn_count": 0 if invalid == "wrong_turn" else 1,
        "session_id": "other" if invalid == "lost_session" else "fixture",
    }
    (tmp_path / "metadata.json.backup").write_text(json.dumps(backup))
    with pytest.raises(AssertionError):
        harness.history_files(tmp_path, [harness.QUOTE, harness.SECOND], prior=previous)


@pytest.mark.parametrize("backup_turns", [1, 2])
def test_backup_oracles_allow_real_checkpoint_cadence_without_timestamp_equality(harness, tmp_path, backup_turns):
    rows = _fixture_messages(harness)
    previous = (rows[:2], {"turn_count": 1, "session_id": "fixture", "last_updated": "earlier"})
    for name, values in (
        ("transcript.jsonl", rows), ("transcript.jsonl.backup", rows[:backup_turns * 2]),
    ):
        (tmp_path / name).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in values),
                                     encoding="utf-8")
    for name, count in (("metadata.json", 2), ("metadata.json.backup", backup_turns)):
        (tmp_path / name).write_text(json.dumps({
            "turn_count": count, "session_id": "fixture", "last_updated": "later",
        }))
    assert len(harness.history_files(tmp_path, [harness.QUOTE, harness.SECOND], prior=previous)) == 4


def test_first_turn_does_not_require_a_nonexistent_prior_backup(harness, tmp_path):
    rows = _fixture_messages(harness)[:2]
    (tmp_path / "transcript.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8",
    )
    (tmp_path / "metadata.json").write_text('{"turn_count":1}')
    assert len(harness.history_files(tmp_path, [harness.QUOTE])) == 2