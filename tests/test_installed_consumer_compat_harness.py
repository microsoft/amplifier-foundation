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
    work_expression = (
        "${{ matrix.os == 'windows-2025' && format('C:/ic-{0}-{1}', github.run_id, github.run_attempt)"
        " || format('{0}/installed-consumers', runner.temp) }}"
    )
    for step in job["steps"]:
        if "installed_consumer_compat.py" in step.get("run", ""):
            assert f'--work "{work_expression}"' in step["run"]
    assert upload["with"]["path"] == work_expression + "/evidence/*.json"


def test_only_explicit_candidate_override_and_distinct_core_graphs():
    prepare = source("prepare")
    assert '"amplifier-core==2.0.2"' in prepare
    assert '"agent": [' in prepare and '"cli": [' in prepare
    assert "amplifier-foundation @ {wheel.as_uri()}" in prepare
    assert "--no-deps" not in TEXT and "--editable" not in TEXT
    assert "--only-binary" in prepare and '"amplifier-core"' in prepare
    assert '("2.0.1" if consumer == "agent" else "2.0.2")' in source("installed_audit")
    assert '"vcs_info"]["commit_id"] == revision' in source("installed_audit")
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


@pytest.fixture
def installed_model(harness, tmp_path, monkeypatch):
    """Synthetic ZIP and installed bytes only: no build, install, import or launch."""
    prefix = tmp_path / "environment"
    site = prefix / "lib/python3.13/site-packages"
    site.mkdir(parents=True)
    monkeypatch.setattr(harness.sys, "prefix", str(prefix))
    wheel = tmp_path / "candidate.whl"
    members = {
        "amplifier_foundation/__init__.py": b"# synthetic candidate\n",
        "amplifier_foundation/session/history.py": b"# synthetic history\n",
        "amplifier_foundation-1.0.0.dist-info/METADATA": b"Name: amplifier-foundation\nVersion: 1.0.0\n",
        "amplifier_foundation-1.0.0.dist-info/RECORD": b"synthetic pre-install RECORD\n",
    }
    with harness.zipfile.ZipFile(wheel, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
            target = site / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    # Legitimate installer modification is confined to RECORD.
    (site / "amplifier_foundation-1.0.0.dist-info/RECORD").write_bytes(b"installed RECORD\n")
    expected = harness.digest(wheel)
    distributions = {}
    for name, revision in {
        "amplifier-foundation": None, "amplifier-core": None,
        "amplifier-agent": harness.AGENT_REV, "amplifier-agent-engine": harness.ENGINE_REV,
        "amplifier-app-cli": harness.CLI_REV, "amplifier-module-context-simple": harness.CONTEXT_REV,
        "amplifier-module-loop-streaming": harness.LOOP_REV,
    }.items():
        direct = None if name == "amplifier-core" else (
            {"url": wheel.as_uri(), "archive_info": {}} if name == "amplifier-foundation" else {
                "url": f"https://github.com/microsoft/{name}",
                "vcs_info": {"commit_id": revision},
            }
        )
        if name == "amplifier-foundation":
            files = list(members)
        else:
            files = [f"{name.replace('-', '_')}/__init__.py"]
            target = site / files[0]
            target.parent.mkdir()
            target.write_bytes(b"# synthetic dependency\n")
        distribution = SimpleNamespace(
            metadata={"Name": name, "Version": "1.0.0"}, version="1.0.0",
            requires=["synthetic>=1"], files=files, direct=direct,
            locate_file=lambda name: site / name,
        )
        distribution.read_text = lambda filename, d=distribution: (
            json.dumps(d.direct) if filename == "direct_url.json" and d.direct is not None else None
        )
        distributions[name] = distribution
    monkeypatch.setattr(harness.metadata, "distributions", lambda: list(distributions.values()))
    monkeypatch.setattr(harness.metadata, "distribution", lambda name: distributions[name])
    core = {"version": "2.0.1"}
    monkeypatch.setattr(harness.metadata, "version", lambda name: core["version"])
    return SimpleNamespace(wheel=wheel, expected=expected, members=members, site=site,
                           distributions=distributions, core=core)


@pytest.mark.parametrize("consumer", ["agent", "cli"])
def test_actual_installed_audit_accepts_empty_uv_archive_info_only_with_full_proof(
    harness, installed_model, tmp_path, consumer,
):
    model = installed_model
    model.core["version"] = "2.0.1" if consumer == "agent" else "2.0.2"
    result = {}
    harness.installed_audit(model.wheel, model.expected, consumer, result)
    proof = result["candidate_install_proof"]
    assert proof["candidate_wheel_sha256"] == model.expected
    assert proof["preparation_digest_verified"] and proof["direct_url_verified"]
    assert proof["installer_hash_metadata"] == "absent" and proof["installer_hashes"] == {}
    expected_files = {name: harness.hashlib.sha256(data).hexdigest() for name, data in model.members.items()
                      if not name.endswith(".dist-info/RECORD")}
    assert proof["installed_wheel_members"] == expected_files
    assert result["distributions"]["amplifier-foundation"]["direct_url"]["archive_info"] == {}
    harness.save_public_json(tmp_path, f"{consumer}.json", result)
    assert harness.check_public(tmp_path) == 0


@pytest.mark.parametrize("metadata_hash", ["sha256", "sha512", "legacy"])
def test_candidate_hash_metadata_is_verified_when_present(harness, installed_model, metadata_hash):
    model = installed_model
    info = model.distributions["amplifier-foundation"].direct["archive_info"]
    algorithm = "sha256" if metadata_hash == "legacy" else metadata_hash
    value = harness.hashlib.new(algorithm, model.wheel.read_bytes()).hexdigest()
    if metadata_hash == "legacy":
        info["hash"] = f"sha256={value}"
    else:
        info["hashes"] = {algorithm: value}
    result = {}
    harness.installed_audit(model.wheel, model.expected, "agent", result)
    proof = result["candidate_install_proof"]
    assert proof["installer_hash_metadata"] == "verified"
    assert proof["installer_hashes"] == {algorithm: value}


@pytest.mark.parametrize("fault", [
    "wrong-wheel-url", "mutated-wheel", "mutated-installed-python", "mutated-installed-metadata",
    "missing-installed-file", "hash-mismatch", "legacy-hash-mismatch", "conflicting-hashes",
    "invalid-hashes", "invalid-archive-info", "editable", "core-pin", "sdk-vcs", "engine-vcs",
])
def test_actual_installed_audit_rejects_failed_candidate_identity_oracles(
    harness, installed_model, fault,
):
    model = installed_model
    direct = model.distributions["amplifier-foundation"].direct
    if fault == "wrong-wheel-url":
        direct["url"] = (model.wheel.parent / "different.whl").as_uri()
    elif fault == "mutated-wheel":
        model.wheel.write_bytes(model.wheel.read_bytes() + b"changed since preparation")
    elif fault.startswith("mutated-installed"):
        name = ("amplifier_foundation/session/history.py" if fault.endswith("python") else
                "amplifier_foundation-1.0.0.dist-info/METADATA")
        (model.site / name).write_bytes(b"wrong installed bytes")
    elif fault == "missing-installed-file":
        # Simulate a missing installed member through locate_file, not file deletion.
        foundation = model.distributions["amplifier-foundation"]
        foundation.locate_file = lambda name: model.site / "missing" / name
    elif fault == "hash-mismatch":
        direct["archive_info"]["hashes"] = {"sha256": "0" * 64}
    elif fault == "legacy-hash-mismatch":
        direct["archive_info"]["hash"] = "sha256=" + "0" * 64
    elif fault == "conflicting-hashes":
        direct["archive_info"] = {"hashes": {"sha256": model.expected}, "hash": "sha256=" + "0" * 64}
    elif fault == "invalid-hashes":
        direct["archive_info"]["hashes"] = None
    elif fault == "invalid-archive-info":
        direct["archive_info"] = []
    elif fault == "editable":
        direct["dir_info"] = {"editable": True}
    elif fault == "core-pin":
        model.core["version"] = "2.0.2"
    else:
        package = "amplifier-agent" if fault == "sdk-vcs" else "amplifier-agent-engine"
        model.distributions[package].direct["vcs_info"]["commit_id"] = "0" * 40
    result = {}
    with pytest.raises((AssertionError, FileNotFoundError)):
        harness.installed_audit(model.wheel, model.expected, "agent", result)
    assert "candidate_install_proof" not in result


@pytest.mark.parametrize("consumer", ["agent", "cli"])
def test_cli_graph_cannot_borrow_agent_core_or_consumer_revision(harness, installed_model, consumer):
    model = installed_model
    model.core["version"] = "2.0.1" if consumer == "cli" else "2.0.2"
    with pytest.raises(AssertionError):
        harness.installed_audit(model.wheel, model.expected, consumer, {})
    model.core["version"] = "2.0.2" if consumer == "cli" else "2.0.1"
    package = "amplifier-app-cli" if consumer == "cli" else "amplifier-agent"
    model.distributions[package].direct["vcs_info"]["commit_id"] = "0" * 40
    with pytest.raises(AssertionError):
        harness.installed_audit(model.wheel, model.expected, consumer, {})


@pytest.mark.parametrize("stderr,category,code", [
    ("error: No solution found: amplifier-core has no wheels with a matching platform tag", "dependency-resolution", None),
    ("error: Failed to fetch amplifier-agent-engine\nHTTP 403 Forbidden", "git-fetch", 403),
    ("error: The filename or extension is too long (os error 206)", "path-too-long", 206),
    ("error: Access is denied [WinError 5]", "access-denied", 5),
    ("error: Build backend failed: Microsoft Visual C++ is required", "build-backend", None),
    ("error: certificate verify failed", "certificate", None),
])
def test_setup_diagnostics_are_useful_bounded_templates_not_raw_stderr(
    harness, tmp_path, monkeypatch, stderr, category, code,
):
    sentinel = "synthetic-private-sentinel"
    raw = (
        stderr + f"\nAuthorization: Bearer {sentinel}\npassword={sentinel}\n"
        f"https://fixture:{sentinel}@example.invalid/private?token={sentinel}\n"
        f"/home/{sentinel}/config\nC:\\Users\\{sentinel}\\config\n"
        f"arbitrary unrecognized private prose {sentinel}\n"
    )
    stdout = f"arbitrary stdout {sentinel}"
    monkeypatch.setattr(harness.subprocess, "run", lambda *args, **kw: SimpleNamespace(
        returncode=1, stdout=stdout, stderr=raw,
    ))
    with pytest.raises(harness.CommandFailure) as raised:
        harness.command(["uv", sentinel], cwd=tmp_path, env={}, diagnose_setup=True)
    record = harness.error_record(raised.value)
    detail = record["diagnostics"][0]
    setup = detail["setup"]
    assert category in setup["categories"]
    if code:
        assert code in setup["windows_codes"] + setup["http_status"]
    assert detail["streams"]["stderr"]["sha256"] == harness.hashlib.sha256(raw.encode()).hexdigest()
    text = json.dumps(setup)
    assert len(text) < 2048
    assert all(value not in text for value in (
        sentinel, "Authorization", "example.invalid", "C:\\Users", "/home/", "arbitrary", "password=",
    ))
    harness.save_public_json(tmp_path, "preparation.json", record)
    assert sentinel not in (tmp_path / "evidence/preparation.json").read_text()
    assert harness.check_public(tmp_path) == 0


def test_unknown_setup_stderr_stays_withheld_and_default_command_remains_private(
    harness, tmp_path, monkeypatch,
):
    raw = "unknown synthetic-private-sentinel /home/private-fixture\n"
    monkeypatch.setattr(harness.subprocess, "run", lambda *args, **kw: SimpleNamespace(
        returncode=1, stdout=raw, stderr=raw,
    ))
    with pytest.raises(harness.CommandFailure) as raised:
        harness.command(["fixture"], cwd=tmp_path, env={})
    assert "setup" not in harness.error_record(raised.value)["diagnostics"][0]
    with pytest.raises(harness.CommandFailure) as raised:
        harness.command(["fixture"], cwd=tmp_path, env={}, diagnose_setup=True)
    setup = harness.error_record(raised.value)["diagnostics"][0]["setup"]
    assert setup["categories"] == ["unclassified"]
    assert setup["packages"] == [] and setup["raw_stderr"] == "withheld"
    assert "sentinel" not in json.dumps(setup)


@pytest.mark.parametrize("path", [
    r"C:\runner\work\agent/lib/site-packages/fixture.py",
    r"C:/runner/work\agent\Lib\site-packages\fixture.py",
    r"C:\runner/work/agent\Lib\site-packages\fixture.py",
])
def test_public_windows_mixed_separators_preserve_specific_environment_alias(harness, path):
    # Pure strings: validates Windows normalization without a Windows host or install.
    normalized = harness.public_string(path, {
        r"C:\runner\work\agent": "<agent-env>", r"C:\runner\work": "<work>",
    })
    assert normalized.startswith("<agent-env>")
    assert "<work>" not in normalized and "runner" not in normalized
    harness.assert_public(normalized)


def test_short_windows_setup_path_budget_keeps_cache_temp_and_both_envs_compact(harness):
    work = harness.PureWindowsPath("C:/ic-41358043348-1")
    env = {"TEMP": str(work / "p/t"), "UV_CACHE_DIR": str(work / "p/c")}
    budget = harness.setup_path_budget(work, env)
    assert budget["within_planning_budget"]
    assert budget["root_characters"] == {
        "work": len(str(work)), "temp": len(str(work / "p/t")), "cache": len(str(work / "p/c")),
        "agent_env": len(str(work / "agent")), "cli_env": len(str(work / "cli")),
    }
    assert max(budget["root_characters"].values()) + budget["nested_suffix_reserve"] < 260
    assert harness.env_python(Path(str(work)), "agent") != harness.env_python(Path(str(work)), "cli")
    long = harness.PureWindowsPath("C:/") / ("long" * 40)
    assert not harness.setup_path_budget(long, {
        "TEMP": str(long / "p/t"), "UV_CACHE_DIR": str(long / "p/c"),
    })["within_planning_budget"]


def test_path_failure_observation_is_bounded_and_has_no_filename_payload(harness, tmp_path):
    sentinel = "synthetic-private-sentinel"
    cache, temp = tmp_path / "c", tmp_path / "t"
    cache.mkdir()
    temp.mkdir()
    fixture = cache / sentinel
    fixture.write_bytes(b"unread payload")
    observation = harness.setup_path_observation(tmp_path, {
        "TEMP": str(temp), "UV_CACHE_DIR": str(cache),
    })
    assert observation["cache"]["entries"] == 1
    assert observation["cache"]["max_characters"] == len(str(fixture))
    assert observation["cache"]["longest_path_sha256"] == harness.hashlib.sha256(
        harness.os.fsencode(str(fixture)),
    ).hexdigest()
    assert not observation["capped"]
    assert sentinel not in json.dumps(observation) and str(tmp_path) not in json.dumps(observation)
    harness.save_public_json(tmp_path, "preparation.json", observation)
    assert harness.check_public(tmp_path) == 0


def test_path_observation_records_unreadable_subtree_without_claiming_complete_scan(
    harness, tmp_path, monkeypatch,
):
    sentinel = "synthetic-private-sentinel"
    def walk(root, *, followlinks, onerror):
        assert followlinks is False
        onerror(OSError(13, sentinel, str(root / sentinel)))
        return iter(())
    monkeypatch.setattr(harness.os, "walk", walk)
    observation = harness.setup_path_observation(tmp_path, {
        "TEMP": str(tmp_path / "t"), "UV_CACHE_DIR": str(tmp_path / "c"),
    })
    assert observation["cache"]["scan_errors"] == [{"errno": 13}]
    assert observation["cache"]["entries"] == 0
    assert sentinel not in json.dumps(observation)


def test_runtime_environment_never_precreates_home_or_copies_credentials(harness, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "synthetic-private-sentinel")
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-private-sentinel")
    root = tmp_path / "runtime"
    env = harness.clean_environment(root, runtime=True)
    assert not (root / "home").exists()
    assert not Path(env["AMPLIFIER_HOME"]).exists()
    assert env["HOME"] == env["USERPROFILE"] == str(root / "home")
    assert env["AMPLIFIER_HOME"] == str(root / "home/.amplifier")
    assert Path(env["TEMP"]).is_dir() and Path(env["UV_CACHE_DIR"]).is_dir()
    assert all("synthetic-private-sentinel" not in value for value in env.values())
    prep = harness.clean_environment(tmp_path / "prep")
    assert Path(prep["HOME"]).is_dir()  # Public-only builder HOME is deliberately separate.


def test_runtime_home_uses_adapter_for_missing_home_before_config_payload(harness, tmp_path, monkeypatch):
    env = harness.clean_environment(tmp_path / "runtime", runtime=True)
    monkeypatch.setenv("HOME", env["HOME"])
    monkeypatch.setenv("AMPLIFIER_HOME", env["AMPLIFIER_HOME"])
    home, history = Path(env["HOME"]), Path(env["AMPLIFIER_HOME"])
    created = []
    preflighted = []

    def preflight(path):
        assert not path.exists()
        preflighted.append(path)
        return {"existing_chain_validated": True, "owned_leaf_missing": True}

    def adapter(path):
        assert preflighted[-1] == path
        assert not path.exists(), "Do not adopt or repair an ordinary-mkdir fixture"
        assert not (history / "settings.yaml").exists()
        if path == history:
            assert created == [home]
        path.mkdir(mode=0o700)
        created.append(path)

    monkeypatch.setattr(harness, "private_directory", adapter)
    monkeypatch.setattr(harness, "private_ancestor_preflight", preflight)
    report = {}
    harness.prepare_runtime_home(report)
    assert created == [home, history]
    assert preflighted == [home, history]
    assert report["private_fixture"]["stage"] == "ready-before-payload"
    assert report["private_fixture"]["preexisting_home"] is False
    # Real adapter is unchanged; neither function contains an ACL repair seam.
    assert "win._pinned(win._API(), path, root=path, create=True)" in source("private_directory")
    assert "prepare_runtime_home(report)" in source("qualify")
    assert "root=path, create=False" in source("private_ancestor_preflight")
    assert source("qualify").index("prepare_runtime_home(report)") < source("qualify").index("config.write_text")
    assert "runtime=True" in source("run")


def test_runtime_home_preexisting_directory_is_refused_without_repair(harness, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    sentinel = home / "untouched"
    sentinel.write_bytes(b"retained")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AMPLIFIER_HOME", str(home / ".amplifier"))
    calls = []
    monkeypatch.setattr(harness, "private_directory", lambda path: calls.append(path))
    report = {}
    with pytest.raises(harness.HarnessPolicyError):
        harness.prepare_runtime_home(report)
    assert report["private_fixture"]["preexisting_home"] is True
    assert calls == [] and sentinel.read_bytes() == b"retained"
    assert not (home / ".amplifier").exists()


@pytest.mark.parametrize("failing_role", ["home", "history-root"])
@pytest.mark.parametrize("message,category", [
    ("Windows state requires trusted ownership and a non-null DACL", "owner-or-null-dacl"),
    ("Windows private state requires a protected DACL", "unprotected-dacl"),
    ("Windows state DACL grants foreign access", "foreign-ace"),
])
def test_private_creation_failure_reports_role_category_not_sid_or_path(
    harness, tmp_path, monkeypatch, failing_role, message, category,
):
    home = tmp_path / "home"
    history = home / ".amplifier"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AMPLIFIER_HOME", str(history))
    def adapter(path):
        if path == (home if failing_role == "home" else history):
            try:
                raise ValueError(f"{message}: S-1-5-21-synthetic-private-sentinel")
            except ValueError as cause:
                raise RuntimeError("unsafe traversal /home/synthetic-private-sentinel") from cause
        path.mkdir(mode=0o700)
    monkeypatch.setattr(harness, "private_directory", adapter)
    monkeypatch.setattr(harness, "private_ancestor_preflight", lambda path: {
        "existing_chain_validated": True, "owned_leaf_missing": True,
    })
    report = {}
    with pytest.raises(RuntimeError):
        harness.prepare_runtime_home(report)
    detail = report["private_fixture"]
    assert detail["stage"] == failing_role and detail["security_categories"] == [category]
    assert detail["operation"] == "private-create"
    assert not (history / "settings.yaml").exists()
    harness.save_public_json(tmp_path, "cli.json", report)
    saved = (tmp_path / "evidence/cli.json").read_text()
    assert "synthetic-private-sentinel" not in saved and "S-1-5" not in saved
    assert harness.check_public(tmp_path) == 0


def test_unsafe_existing_ancestor_is_refused_before_private_creation(harness, tmp_path, monkeypatch):
    home = tmp_path / "missing-home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AMPLIFIER_HOME", str(home / ".amplifier"))
    calls = []
    def refuse(path):
        raise RuntimeError("Windows state requires trusted ownership and a non-null DACL")
    monkeypatch.setattr(harness, "private_ancestor_preflight", refuse)
    monkeypatch.setattr(harness, "private_directory", lambda path: calls.append(path))
    report = {}
    with pytest.raises(RuntimeError):
        harness.prepare_runtime_home(report)
    assert calls == [] and not home.exists()
    detail = report["private_fixture"]
    assert detail["stage"] == "home" and detail["operation"] == "read-only-ancestor-preflight"
    assert detail["security_categories"] == ["owner-or-null-dacl"]


@pytest.mark.parametrize("event,args,classification", [
    ("socket.getaddrinfo", ("synthetic-private-sentinel.invalid", 443, 0, 0, 0), "named-host"),
    ("socket.getaddrinfo", ("localhost", 443), "loopback-name"),
    ("socket.getaddrinfo", ("127.0.0.1", 443), "ip-loopback"),
    ("socket.getaddrinfo", ("0.0.0.0", 0), "ip-unspecified"),
    ("socket.getaddrinfo", ("10.0.0.1", 443), "ip-private"),
    ("socket.getaddrinfo", ("8.8.8.8", 443), "ip-public"),
    ("socket.getaddrinfo", (None, 0), "no-host"),
])
def test_socket_address_evidence_never_resolves_or_exports_raw_address(
    harness, monkeypatch, event, args, classification,
):
    def forbidden(*args, **kwargs):
        raise AssertionError("A diagnostic must not perform DNS")
    monkeypatch.setattr(harness.socket, "getaddrinfo", forbidden)
    detail = harness.socket_address_evidence(event, args)
    assert detail["classification"] == classification
    assert len(detail["address_sha256"]) == 64
    assert detail["address_bytes"] > 0
    assert all(str(value) not in json.dumps(detail) for value in args[:1] if isinstance(value, str))


def test_blocked_socket_retains_failure_counter_and_safe_phase_source_evidence(harness, tmp_path):
    guard = harness.SocketGuard(tmp_path, "agent", {}, harness.CallerSources())
    guard.phase = "agent_roundtrip"
    sentinel = "synthetic-private-sentinel.invalid"
    for _ in range(10):
        with pytest.raises(harness.HarnessPolicyError):
            guard.audit("socket.getaddrinfo", (sentinel, 443, 0, 0, 0))
    assert guard.attempts == ["socket.getaddrinfo"] * 10
    assert len(guard.blocked_details) == 8  # Diagnostics bounded, no failed counter dropped.
    detail = guard.blocked_details[0]
    assert detail["consumer"] == "agent" and detail["phase"] == "agent_roundtrip"
    assert detail["thread"] == "main"
    assert detail["address"]["classification"] == "named-host"
    assert not any(frame["function"] == "test_blocked_socket_retains_failure_counter_and_safe_phase_source_evidence"
                   for frame in detail["frames"])
    assert any(frame["source_kind"] == "harness" and frame["function"] == "blocked"
               for frame in detail["frames"])
    assert any(frame.get("source_sha256") == harness.digest(HARNESS) for frame in detail["frames"])
    harness.save_public_json(tmp_path, "agent.json", {"failures": guard.blocked_details})
    public = (tmp_path / "evidence/agent.json").read_text()
    assert sentinel not in public and str(ROOT) not in public
    assert harness.check_public(tmp_path) == 0


def test_extra_local_bind_is_still_refused_and_classified_not_added_to_allowlist(harness, tmp_path):
    guard = harness.SocketGuard(tmp_path, "agent", {})
    sock = SimpleNamespace(family=harness.socket.AF_INET, type=harness.socket.SOCK_STREAM)
    with pytest.raises(harness.HarnessPolicyError):
        guard.audit("socket.bind", (sock, ("127.0.0.1", 0)))
    assert guard.attempts == ["socket.bind"]
    assert guard.endpoint is None
    assert guard.blocked_details[0]["address"]["classification"] == "ip-loopback"


@pytest.mark.parametrize("consumer,event", [("agent", "socket.bind"), ("cli", "socket.getaddrinfo")])
def test_swallowed_socket_attempt_keeps_overall_qualification_failed_after_passed_checks(
    harness, tmp_path, monkeypatch, consumer, event,
):
    """Model the measured all-checks-pass/overall-policy-fail case without IPC."""
    audits = []
    monkeypatch.setattr(harness.sys, "addaudithook", lambda callback: audits.append(callback))
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "runtime-home"
    monkeypatch.setenv("AMPLIFIER_HOME", str(home))
    monkeypatch.setattr(harness, "prepare_runtime_home", lambda report: home.mkdir())
    monkeypatch.setattr(harness, "foundation_ownership", lambda *args: None)
    monkeypatch.setattr(harness, "incremental_history", lambda *args: None)
    monkeypatch.setattr(harness, "settings_overlap", lambda *args: None)

    async def roundtrip(*args):
        try:
            if event == "socket.bind":
                audits[0](event, (SimpleNamespace(family=harness.socket.AF_INET,
                                                  type=harness.socket.SOCK_STREAM), ("127.0.0.1", 0)))
            else:
                audits[0](event, ("synthetic-private-sentinel.invalid", 443))
        except harness.HarnessPolicyError:
            pass  # Mimic a dependency catching the refusal; it is still a failure.

    async def wait_for(coro, *, timeout):
        return await coro  # No OS loop or real timeout needed by this finite model.

    monkeypatch.setattr(harness, "agent_roundtrip", roundtrip)
    monkeypatch.setattr(harness, "cli_roundtrip", roundtrip)
    monkeypatch.setattr(harness.asyncio, "wait_for", wait_for)
    report = {"checks": {}}
    with pytest.raises(harness.HarnessPolicyError):
        _drive(harness.qualify(tmp_path, consumer, report))
    assert all(value["status"] == "passed" for value in report["checks"].values())
    assert report["blocked_socket_operations"] == [event]
    assert report["blocked_socket_details"][0]["phase"] == f"{consumer}_roundtrip"
    assert report["blocked_socket_details"][0]["interpretation"].startswith("blocked request")
    assert report["release_ipc"] == []


def _dynamic_caller(harness, sources, filename, *, symbol="private_person_project_sentinel",
                    module="private_person_project_sentinel"):
    # Deliberately plausible module/symbol/file claims; capture must not trust
    # them merely because they match an identifier regex or package location.
    namespace = {"__name__": module, "capture": harness.socket_caller_evidence, "sources": sources}
    exec(compile(f"def {symbol}():\n    return capture(sources)\n", filename, "exec"), namespace)
    return namespace[symbol]()


@pytest.mark.parametrize("filename_kind", ["private", "claimed-installed", "harness", "stdlib"])
def test_unknown_spoofed_frame_names_are_withheld_without_reading_claimed_files(
    harness, tmp_path, monkeypatch, filename_kind,
):
    sources = harness.CallerSources()
    sentinel = "private_person_project_sentinel"
    filename = {
        "private": str(tmp_path / sentinel / "secret.py"),
        "claimed-installed": str(tmp_path / "site-packages/amplifier_agent" / (sentinel + ".py")),
        "harness": str(HARNESS),  # Existing trusted file, but different code.
        "stdlib": str(Path(harness.sysconfig.get_path("stdlib")) / "socket.py"),
    }[filename_kind]
    allowed = {str(entry[0]) for entry in sources.sources.values()}
    old_read, old_stat = Path.read_bytes, Path.stat
    reads = []

    def read(path):
        assert str(path) in allowed, "Unverified co_filename caused a file read"
        reads.append(str(path))
        return old_read(path)

    def stat(path, *args, **kwargs):
        assert str(path) in allowed, "Unverified co_filename caused a stat"
        return old_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", read)
    monkeypatch.setattr(Path, "stat", stat)
    frames = _dynamic_caller(harness, sources, filename, module="amplifier_agent." + sentinel)
    assert frames[0]["source_kind"] == "unverified"
    assert frames[0]["module"] == frames[0]["function"] == "withheld"
    assert frames[0]["line"] == 2
    assert len(frames[0]["code_sha256"]) == len(frames[0]["filename_sha256"]) == 64
    text = json.dumps(harness.public_value(frames, {}))
    assert sentinel not in text
    harness.assert_public(harness.public_value(frames, {}))
    assert all(path in allowed for path in reads)
    if filename_kind in {"private", "claimed-installed"}:
        assert filename not in reads


def test_verified_harness_frames_keep_useful_names_without_trusting_globals(harness, tmp_path):
    sources = harness.CallerSources()
    guard = harness.SocketGuard(tmp_path, "cli", {}, sources)
    with pytest.raises(harness.HarnessPolicyError):
        guard.audit("socket.getaddrinfo", (b"synthetic-private-sentinel.invalid", 443))
    frames = guard.blocked_details[0]["frames"]
    assert any(frame["module"] == "installed_consumer_compat" and frame["function"] == "blocked"
               and frame["source_kind"] == "harness" and frame["source_sha256"] == harness.digest(HARNESS)
               for frame in frames)
    harness.save_public_json(tmp_path, "cli.json", {"frames": frames})
    assert harness.check_public(tmp_path) == 0


def test_selected_installed_bytes_verify_code_not_filename_or_module_claim(harness, tmp_path, monkeypatch):
    prefix = tmp_path / "environment"
    path = prefix / "lib/python3.13/site-packages/amplifier_agent/diagnostic_fixture.py"
    path.parent.mkdir(parents=True)
    raw = b"def known_caller():\n    return capture(sources)\n"
    path.write_bytes(raw)
    monkeypatch.setattr(harness.sys, "prefix", str(prefix))
    sha = harness.digest(path)
    evidence = {"distributions": {"amplifier-agent": {"files": {
        "amplifier_agent/diagnostic_fixture.py": {"path": str(path), "sha256": sha},
    }}}}
    sources = harness.CallerSources(evidence)
    namespace = {
        "__name__": "private_person_project_sentinel",  # Do not publish frame globals.
        "capture": harness.socket_caller_evidence, "sources": sources,
    }
    exec(compile(raw, str(path), "exec", dont_inherit=True), namespace)
    frames = namespace["known_caller"]()
    assert frames[0] == {
        "source_kind": "installed", "module": "amplifier_agent.diagnostic_fixture",
        "function": "known_caller", "source_sha256": sha, "line": 2,
    }
    # Same file/module claim is not enough; sentinel code differs from audited bytes.
    spoofed = _dynamic_caller(harness, sources, str(path), module="amplifier_agent.diagnostic_fixture")
    assert spoofed[0]["source_kind"] == "unverified"
    assert "private_person_project_sentinel" not in json.dumps(spoofed)
    # A digest mismatch cannot authorize publishing symbols either.
    changed = copy.deepcopy(evidence)
    changed["distributions"]["amplifier-agent"]["files"]["amplifier_agent/diagnostic_fixture.py"]["sha256"] = "0" * 64
    namespace["sources"] = harness.CallerSources(changed)
    assert namespace["known_caller"]()[0]["source_kind"] == "unverified"


def test_known_stdlib_source_verifies_real_code_and_rejects_dynamic_spoof(harness):
    sources = harness.CallerSources()
    code = harness.socket.getaddrinfo.__code__
    detail = sources.identify(code)  # Read/compile only: no getaddrinfo invocation.
    assert detail["source_kind"] == "stdlib" and detail["module"] == "socket"
    assert detail["function"] == "getaddrinfo" and len(detail["source_sha256"]) == 64
    frames = _dynamic_caller(harness, sources, code.co_filename,
                             symbol="private_person_project_sentinel", module="socket")
    assert frames[0]["source_kind"] == "unverified"
    assert "private_person_project_sentinel" not in json.dumps(frames)


class _NoDiagnosticHooks:
    def __repr__(self):
        raise AssertionError("No custom repr")

    def __str__(self):
        raise AssertionError("No custom string conversion")

    def __iter__(self):
        raise AssertionError("No custom iteration")

    def __eq__(self, other):
        raise AssertionError("No custom comparison")


@pytest.mark.parametrize("event,args,classification", [
    ("socket.getaddrinfo", (b"synthetic-private-sentinel.invalid", 443), "named-host"),
    ("socket.getaddrinfo", (b"127.0.0.1", b"443"), "ip-loopback"),
    ("socket.getnameinfo", ((b"127.0.0.1", 443, 0, 0), 0), "ip-loopback"),
    ("socket.getnameinfo", (("::1", 443, 0, 0), 0), "ip-loopback"),
    ("socket.getaddrinfo", (_NoDiagnosticHooks(), 443), "unclassified"),
    ("socket.getnameinfo", ((_NoDiagnosticHooks(), 443), 0), "unclassified"),
])
def test_nested_bytes_address_arguments_keep_policy_error_and_safe_details(
    harness, tmp_path, event, args, classification,
):
    guard = harness.SocketGuard(tmp_path, "cli", {})
    with pytest.raises(harness.HarnessPolicyError):
        guard.audit(event, args)
    assert guard.attempts == [event]
    detail = guard.blocked_details[0]
    assert detail["address"]["classification"] == classification
    assert len(detail["address"]["address_sha256"]) == 64
    assert "diagnostic_failures" not in detail
    text = json.dumps(detail)
    assert "synthetic-private-sentinel" not in text
    harness.save_public_json(tmp_path, "cli.json", detail)
    assert harness.check_public(tmp_path) == 0


@pytest.mark.parametrize("value", [
    b"x" * 100000, "x" * 100000,
    tuple(b"x" * 10000 for _ in range(1000)),
    [[[[[[[b"nested"]]]]]]],
    1 << 10000,
], ids=["large-bytes", "large-text", "large-container", "deep-container", "large-integer"])
def test_oversized_address_trees_are_capped_before_encoding(harness, value):
    first = harness.address_fingerprint(value)
    assert first == harness.address_fingerprint(value)
    assert first["fingerprint_scope"] == "bounded-prefix"
    assert first["address_bytes"] < 12000
    assert len(json.dumps(first)) < 256
    cyclic = []
    cyclic.append(cyclic)
    assert harness.address_fingerprint(cyclic)["fingerprint_scope"] == "bounded-prefix"


def test_address_fingerprint_distinguishes_bytes_text_and_nested_types(harness):
    values = [(b"localhost", 443), ("localhost", 443), [b"localhost", 443],
              (b"localhost", b"443"), (b"localhost", 444)]
    hashes = {harness.address_fingerprint(value)["address_sha256"] for value in values}
    assert len(hashes) == len(values)
    detail = harness.address_fingerprint((_NoDiagnosticHooks(), b"fixture"))
    assert detail["unsupported_value"] is True


@pytest.mark.parametrize("shape", ["large-text", "large-bytes", "deep", "cycle", "custom-socket"])
def test_large_or_unknown_audit_arguments_still_fail_with_policy_error(harness, tmp_path, shape):
    guard = harness.SocketGuard(tmp_path, "agent", {})
    event = "socket.getaddrinfo"
    if shape == "large-text":
        args = ("private_person_project_sentinel" * 10000, 443)
    elif shape == "large-bytes":
        args = (b"private_person_project_sentinel" * 10000, 443)
    elif shape == "deep":
        args = (((((((b"private_person_project_sentinel",),),),),),), 443)
    elif shape == "cycle":
        cyclic = []
        cyclic.append(cyclic)
        args = (cyclic, 443)
    else:
        class FaultySocket:
            @property
            def family(self):
                raise TypeError("private_person_project_sentinel")
        event, args = "socket.bind", (FaultySocket(), ("127.0.0.1", 0))
    with pytest.raises(harness.HarnessPolicyError):
        guard.audit(event, args)
    assert guard.attempts == [event] and len(guard.blocked_details) == 1
    text = json.dumps(guard.blocked_details)
    assert "private_person_project_sentinel" not in text
    assert len(text) < 12000
    if shape == "custom-socket":
        assert guard.blocked_details[0]["diagnostic_failures"] == ["address"]
    else:
        assert "diagnostic_failures" not in guard.blocked_details[0]
    harness.save_public_json(tmp_path, "agent.json", {"details": guard.blocked_details})
    assert harness.check_public(tmp_path) == 0


@pytest.mark.parametrize("fault", ["address", "frames", "thread"])
def test_diagnostic_exceptions_never_replace_policy_error_or_erase_attempts(
    harness, tmp_path, monkeypatch, fault,
):
    def fail(*args, **kwargs):
        raise TypeError("private_person_project_sentinel")
    if fault == "address":
        monkeypatch.setattr(harness, "socket_address_evidence", fail)
    elif fault == "frames":
        monkeypatch.setattr(harness, "socket_caller_evidence", fail)
    else:
        monkeypatch.setattr(harness.threading, "current_thread", fail)
    guard = harness.SocketGuard(tmp_path, "agent", {})
    for _ in range(10):
        with pytest.raises(harness.HarnessPolicyError):
            guard.audit("socket.getaddrinfo", (b"synthetic-private-sentinel.invalid", 443))
    assert guard.attempts == ["socket.getaddrinfo"] * 10
    assert len(guard.blocked_details) == 8
    assert guard.blocked_details[0]["diagnostic_failures"] == [fault]
    harness.save_public_json(tmp_path, "agent.json", {"details": guard.blocked_details})
    public = (tmp_path / "evidence/agent.json").read_text()
    assert "private_person_project_sentinel" not in public and "synthetic-private-sentinel" not in public
    assert harness.check_public(tmp_path) == 0


@pytest.mark.parametrize("custom_exception", [None, "os-error", "policy-subclass", "command-subclass"])
def test_unknown_caller_policy_exception_error_record_public_rescan_never_leaks_symbols(
    harness, tmp_path, monkeypatch, custom_exception,
):
    """Exact named-check catch path, including plausible private identifiers."""
    sentinel = "SYNTHETIC_PRIVATE_PERSON_PROJECT_a91f"
    filename = str(tmp_path / sentinel / "private_source.py")
    guard = harness.SocketGuard(tmp_path, "cli", {})
    guard.phase = "cli_roundtrip"
    namespace = {"__name__": sentinel, "guard": guard}
    exec(compile(
        f"def {sentinel}():\n"
        "    guard.audit('socket.getaddrinfo', (b'synthetic-private-host.invalid', 443))\n",
        filename, "exec",
    ), namespace)

    def forbidden(*args, **kwargs):
        raise AssertionError("Exception diagnostics must not inspect a traceback filename")

    # No CallerSources map is needed for this probe: unknown frame details and
    # exception serialization must perform NO filesystem inspection at all.
    with monkeypatch.context() as patch:
        for name in ("read_bytes", "read_text", "stat", "is_file", "resolve"):
            patch.setattr(Path, name, forbidden)
        try:
            try:
                namespace[sentinel]()
            except harness.HarnessPolicyError as primary:
                if custom_exception == "os-error":
                    raise type(sentinel, (OSError,), {})(13, sentinel) from primary
                if custom_exception == "policy-subclass":
                    raise type(sentinel, (harness.HarnessPolicyError,), {})(sentinel) from primary
                if custom_exception == "command-subclass":
                    raise type(sentinel, (harness.CommandFailure,), {})(SimpleNamespace(
                        returncode=19, stdout=sentinel, stderr=sentinel,
                    )) from primary
                raise
        except (Exception, SystemExit) as caught:
            record = harness.error_record(caught)  # Actual named-check catch seam.
        public = harness.public_value({"check": record, "blocked": guard.blocked_details}, {})
        harness.assert_public(public)

    assert guard.attempts == ["socket.getaddrinfo"]
    assert record["failure_domain"] == "qualification-policy"
    assert record["diagnostics"][-1]["type"] == "HarnessPolicyError"
    if custom_exception:
        assert record["diagnostics"][0]["type"] == "withheld"
        assert len(record["diagnostics"]) == 2
    if custom_exception == "os-error":
        assert record["diagnostics"][0]["errno"] == 13
    if custom_exception == "command-subclass":
        assert record["diagnostics"][0]["returncode"] == 19
        assert record["diagnostics"][0]["streams"]["stderr"]["sha256"] == harness.hashlib.sha256(
            sentinel.encode(),
        ).hexdigest()
    frames = [frame for detail in record["diagnostics"] for frame in detail["frames"]]
    assert frames and all(type(frame["line"]) is int for frame in frames)
    assert all("function" not in frame and "file" not in frame for frame in frames)
    assert any(frame["line"] == 2 and frame["filename_sha256"] == harness.hashlib.sha256(
        filename.encode(),
    ).hexdigest() for frame in frames)
    assert all(len(frame["code_sha256"]) == len(frame["filename_sha256"]) == 64 for frame in frames)
    assert sentinel not in json.dumps(public)
    assert "synthetic-private-host.invalid" not in json.dumps(public)
    harness.save_public_json(tmp_path, "cli.json", {"check": record, "blocked": guard.blocked_details})
    saved = (tmp_path / "evidence/cli.json").read_text(encoding="utf-8")
    assert sentinel not in saved and "private_source.py" not in saved
    assert "synthetic-private-host.invalid" not in saved
    assert harness.check_public(tmp_path) == 0


def test_error_record_bounds_exception_chain_and_frame_evidence_without_raw_symbols(harness):
    def recurse(depth):
        if depth:
            return recurse(depth - 1)
        raise PermissionError(13, "SYNTHETIC_PRIVATE_PERSON_PROJECT_a91f")

    try:
        recurse(20)
    except PermissionError as exc:
        record = harness.error_record(exc)
    detail = record["diagnostics"][0]
    assert detail["type"] == "PermissionError" and detail["errno"] == 13
    assert len(detail["frames"]) == 8
    assert "recurse" not in json.dumps(record)
    assert "SYNTHETIC_PRIVATE_PERSON_PROJECT_a91f" not in json.dumps(record)
    harness.assert_public(harness.public_value(record, {}))