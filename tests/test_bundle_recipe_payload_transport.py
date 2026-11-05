"""Large validator results must travel as data, not as a bash -c argument."""

from __future__ import annotations

import json
import hashlib
import os
import re
import runpy
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


@POSIX_RECIPE_EXECUTION
@pytest.mark.parametrize("length", [0, 70_000, 110_000])
@pytest.mark.parametrize("use_scratch", [False, True])
def test_report_transport_scalar_and_complete_private_artifact(
    tmp_path: Path, length: int, use_scratch: bool
) -> None:
    helpers = runpy.run_path(
        str(REPO_ROOT / "tests/test_validate_bundle_repo_integration.py")
    )
    step = _steps()["synthesize-report"]
    assert step["parse_json"] is True
    envelope, doc = helpers["_report_fixture"]()
    content = (
        '雪 "quote" \\\\path\n```json\n{"embedded":"not the report"}\n```\n'
        + "x" * length
    )
    envelope["inputs"]["checks"]["bundle_head_cost_results"]["transport_fixture"] = (
        content
    )
    report_path, doc_path = tmp_path / "input.json", tmp_path / "doc.json"
    report_path.write_text(json.dumps(envelope, ensure_ascii=False), encoding="utf-8")
    doc_path.write_text(json.dumps(doc), encoding="utf-8")
    before = report_path.read_bytes(), doc_path.read_bytes()
    scratch = tmp_path / "scratch" if use_scratch else None
    if scratch is not None:
        scratch.mkdir(mode=0o700)
    run = helpers["_run_report_paths"]
    first = run(
        step, tmp_path, report_path, doc_path, decode_report=False, scratch=scratch
    )
    second = run(
        step, tmp_path, report_path, doc_path, decode_report=False, scratch=scratch
    )
    assert first.returncode == second.returncode == 0, first.stderr
    assert first.stdout == second.stdout
    report = json.loads(first.stdout)
    assert type(report) is str and report.startswith(
        "# Bundle Repository Validation Report"
    )
    assert first.stdout == json.dumps(report, ensure_ascii=False) + "\n"
    assert any(
        block.get("transport_fixture") == content
        for block in helpers["_fenced_json"](report)
        if isinstance(block, dict)
    )
    receipts = [
        Path(proc.stderr.split("Report artifact receipt: ", 1)[1].splitlines()[0])
        for proc in (first, second)
    ]
    assert receipts[0] != receipts[1]  # Unique files, deterministic report bytes.
    for receipt_path in receipts:
        receipt = json.loads(receipt_path.read_text())
        data = Path(receipt["artifact_path"]).read_bytes()
        assert data == report.encode("utf-8")
        assert receipt["sha256"] == hashlib.sha256(data).hexdigest()
        assert receipt["utf8_bytes"] == len(data) > length
        assert data.startswith(
            b"# Bundle Repository Validation Report"
        ) and data.endswith(b"\n")
        assert receipt_path.stat().st_mode & 0o777 == 0o600
    if length:
        assert len(report.encode("utf-8")) > length
        assert len(report.encode("utf-8")[-65536:]) < len(report.encode("utf-8"))
    assert before == (report_path.read_bytes(), doc_path.read_bytes())


@POSIX_RECIPE_EXECUTION
@pytest.mark.parametrize(
    "failure", ["artifact_create", "artifact_write", "receipt_create", "receipt_write"]
)
def test_report_transport_write_failures_have_no_stdout(
    tmp_path: Path, failure: str
) -> None:
    helpers = runpy.run_path(
        str(REPO_ROOT / "tests/test_validate_bundle_repo_integration.py")
    )
    step = dict(_steps()["synthesize-report"])
    envelope, doc = helpers["_report_fixture"]()
    p, d = tmp_path / "input.json", tmp_path / "doc.json"
    p.write_text(json.dumps(envelope))
    d.write_text(json.dumps(doc))
    before = p.read_bytes(), d.read_bytes()
    scratch = tmp_path / "scratch"
    if failure == "artifact_create":
        scratch.write_text("not a directory")
    else:
        scratch.mkdir(mode=0o700)
        # Execute the actual YAML body under bounded OS fault conditions.
        # RLIMIT_FSIZE makes the real file write fail; no fake writer/renderer.
        prelude = "import resource\nimport signal\nsignal.signal(signal.SIGXFSZ, signal.SIG_IGN)\n"
        if failure == "artifact_write":
            prelude += "resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))\n"
        else:
            prelude += (
                "import sys\n"
                "def receipt_fault(event, args):\n"
                "    if event == 'open' and str(args[0]).endswith('.receipt.json'):\n"
            )
            prelude += (
                "        raise PermissionError('controlled receipt create failure')\n"
                if failure == "receipt_create"
                else "        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))\n"
            )
            prelude += "sys.addaudithook(receipt_fault)\n"
        step["command"] = step["command"].replace(
            "<< 'EOF'\n", "<< 'EOF'\n" + prelude, 1
        )
    proc = helpers["_run_report_paths"](step, tmp_path, p, d, scratch=scratch)
    assert proc.returncode != 0 and proc.stdout == "" and proc.stderr
    assert before == (p.read_bytes(), d.read_bytes())
    if failure.startswith("receipt"):
        artifacts = list(scratch.glob("*.md"))
        assert len(artifacts) == 1
        assert (
            artifacts[0]
            .read_bytes()
            .startswith(b"# Bundle Repository Validation Report")
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
    return {
        step["id"]: step
        for step in yaml.safe_load(RECIPE.read_text(encoding="utf-8"))["steps"]
    }


def _run_bash(
    command: str, cwd: Path, env: dict, *, timeout: float = 60
) -> subprocess.CompletedProcess:
    """Bound execution; signal the owned group only while its leader is unreaped.

    Drained pipes/leader exit do not prove arbitrary descendant containment.
    Once the leader is reaped its group ID is not safe cleanup authority.
    """
    home = cwd / ".validator-home"
    home.mkdir(mode=0o700, exist_ok=True)
    env = dict(env, HOME=str(home), AMPLIFIER_HOME=str(home / ".amplifier"))
    args = ["bash", "-c", command]
    proc = subprocess.Popen(
        args,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
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
                failure.add_note(
                    f"Owned Bash group {proc.pid} cleanup failed: {cleanup}"
                )
            raise
        return subprocess.CompletedProcess(args, proc.returncode, stdout, stderr)
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        if proc.stderr is not None:
            proc.stderr.close()


def _run(step: dict, context: dict, cwd: Path, *, scratch: Path | None) -> dict:
    command = _render(step["command"], context)
    # This checks actual argument size, rather than increasing any runner limit.
    assert len(command.encode("utf-8")) < 98_000
    env = dict(os.environ, AMPLIFIER_PYTHON=sys.executable, PYTHONPATH=str(REPO_ROOT))
    env.pop("AMPLIFIER_RECIPE_SCRATCH_DIR", None)
    if scratch is not None:
        env["AMPLIFIER_RECIPE_SCRATCH_DIR"] = str(scratch)
    env.update(
        {name: _render(value, context) for name, value in step.get("env", {}).items()}
    )
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
                "passed": True,
                "skipped": True,
                "errors": [],
                "warnings": [],
            }
    context["packaging_check"] = {"passed": True}
    context["enhance_diagrams"] = "false"
    # Build now shares the private payload contract. Execute its real skipped
    # producer rather than inventing a pointer or copying its result shape.
    scratch = Path(context["repo_path"])
    context.update(
        validate_all="false", validate_agents="false", validate_recipes="false"
    )
    _run(_steps()["validate-all-flags"], context, scratch, scratch=scratch)
    _run(_steps()["set-default-build-check"], context, scratch, scratch=scratch)


@POSIX_RECIPE_EXECUTION
def test_report_envelope_producers_and_large_transport(tmp_path: Path) -> None:
    helpers = runpy.run_path(
        str(REPO_ROOT / "tests/test_validate_bundle_repo_integration.py")
    )
    steps = _steps()
    repo = tmp_path / "repo"
    repo.mkdir()
    context = {
        "repo_path": str(repo),
        "enhance_diagrams": "false",
        "env_check": {
            "validator_version": "3.17.3",
            "validation_mode": "full",
            "capabilities": {
                "foundation_available": True,
                "hatchling_available": True,
                "pip_wheel_available": True,
            },
            "limitations": [],
        },
    }
    discovery = _run(steps["repo-discovery"], context, tmp_path, scratch=tmp_path)
    # Actual classifier uses real file transport with a large raw findings object.
    individual = {"bundles": [], "summary": {"total": 0, "loaded": 0, "failed": 0}}
    tools = {
        "suggestions": [
            {
                "type": f"late-{n}",
                "message": "''' | ``` $(touch NEVER)\nC:\\x\\new",
                "fix": "x" * 1100,
                "severity": "INFO",
            }
            for n in range(150)
        ]
    }
    for name, value in (
        ("individual_validation", individual),
        ("tool_placement_results", tools),
    ):
        p = tmp_path / f"{name}.json"
        p.write_text(json.dumps(value), encoding="utf-8")
        context[name] = {"_payload_file": str(p)}
    _classification_defaults(context, steps["quality-classification"])
    context["module_dep_resolvability_check"] = {
        "passed": False,
        "errors": [
            {
                "type": "late-error",
                "severity": "ERROR",
                "message": "late diagnostic",
                "fix": "keep full fix",
            }
        ],
    }
    classified = _run(
        steps["quality-classification"], context, tmp_path, scratch=tmp_path
    )
    envelope_path = Path(classified["_report_payload_file"])
    assert envelope_path.stat().st_size > 128 * 1024
    assert stat.S_IMODE(envelope_path.stat().st_mode) == 0o600
    stored = json.loads(envelope_path.read_text())
    assert stored["inputs"]["checks"]["tool_placement_results"] == tools
    assert stored["quality_classification"] == {
        k: v for k, v in classified.items() if k != "_report_payload_file"
    }
    doc = _run(steps["bundle-doc-freshness-check"], context, tmp_path, scratch=tmp_path)
    doc_path = Path(doc["_payload_file"])
    assert stat.S_IMODE(doc_path.stat().st_mode) == 0o600
    before = (
        envelope_path.read_bytes(),
        doc_path.read_bytes(),
        Path(discovery["_payload_file"]).read_bytes(),
    )
    proc = helpers["_run_report_paths"](
        steps["synthesize-report"], tmp_path, envelope_path, doc_path
    )
    assert proc.returncode == 0, proc.stderr
    assert "**Overall Verdict**: FAIL" in proc.stdout
    assert "late diagnostic" in proc.stdout and "keep full fix" in proc.stdout
    assert tools in helpers["_fenced_json"](proc.stdout)
    assert before == (
        envelope_path.read_bytes(),
        doc_path.read_bytes(),
        Path(discovery["_payload_file"]).read_bytes(),
    )
    assert not (tmp_path / "NEVER").exists()


@POSIX_RECIPE_EXECUTION
def test_report_early_classification_and_no_dot_exports(tmp_path: Path) -> None:
    helpers = runpy.run_path(
        str(REPO_ROOT / "tests/test_validate_bundle_repo_integration.py")
    )
    steps = _steps()
    (tmp_path / "repo").mkdir()
    context = {
        "repo_path": str(tmp_path / "repo"),
        "enhance_diagrams": "false",
        "env_check": {
            "validator_version": "3.17.3",
            "validation_mode": "structural_only",
            "capabilities": {"foundation_available": False},
            "limitations": [],
        },
    }
    _run(steps["repo-discovery"], context, tmp_path, scratch=tmp_path)
    individual = tmp_path / "limited.json"
    individual.write_text(
        json.dumps(
            {
                "skipped": True,
                "environment_limitation": True,
                "validation_mode": "structural_only",
                "reason": "missing Foundation",
                "bundles": [],
            }
        )
    )
    tools = tmp_path / "tools.json"
    tools.write_text("{}")
    context.update(
        individual_validation={"_payload_file": str(individual)},
        tool_placement_results={"_payload_file": str(tools)},
    )
    _classification_defaults(context, steps["quality-classification"])
    classified = _run(
        steps["quality-classification"], context, tmp_path, scratch=tmp_path
    )
    assert classified["quality_level"] == "limited"
    no_dot = dict(steps["bundle-doc-freshness-check"])
    no_dot["command"] = no_dot["command"].replace(
        "from amplifier_foundation.bundle_docs import bundle_repo_dot",
        "raise ImportError('controlled no-DOT prerequisite')",
    )
    doc = _run(no_dot, context, tmp_path, scratch=tmp_path)
    assert doc["dot_available"] is False
    proc = helpers["_run_report_paths"](
        steps["synthesize-report"],
        tmp_path,
        Path(classified["_report_payload_file"]),
        Path(doc["_payload_file"]),
    )
    assert proc.returncode == 0, proc.stderr
    assert "**Overall Verdict**: LIMITED VALIDATION" in proc.stdout
    assert (
        json.loads(Path(doc["_payload_file"]).read_text())["findings"]
        == doc["findings"]
    )
    assert "DOT_TOOL_UNAVAILABLE" in proc.stdout


@pytest.mark.parametrize(
    "fault", ["absent", "truncated", "bad_json", "schema", "bad_doc", "missing_pointer"]
)
def test_report_payload_failure_stops_publication(tmp_path: Path, fault: str) -> None:
    helpers = runpy.run_path(
        str(REPO_ROOT / "tests/test_validate_bundle_repo_integration.py")
    )
    envelope, doc = helpers["_report_fixture"]()
    report_path, doc_path = tmp_path / "report.json", tmp_path / "doc.json"
    if fault == "schema":
        envelope["schema_version"] = "1"
    report_path.write_text(json.dumps(envelope), encoding="utf-8")
    doc_path.write_text(json.dumps(doc), encoding="utf-8")
    if fault == "absent":
        report_path.unlink()
    elif fault in ("truncated", "bad_json"):
        report_path.write_text(
            '{"schema_version":' if fault == "truncated" else "not-json"
        )
    elif fault == "bad_doc":
        doc_path.write_text("{}")
    elif fault == "missing_pointer":
        report_path = Path("")
    proc = helpers["_run_report_paths"](
        _steps()["synthesize-report"], tmp_path, report_path, doc_path
    )
    assert proc.returncode != 0
    assert proc.stdout == "" and proc.stderr


@POSIX_RECIPE_EXECUTION
def test_report_keeps_doc_warning_after_later_write(tmp_path: Path) -> None:
    helpers = runpy.run_path(
        str(REPO_ROOT / "tests/test_validate_bundle_repo_integration.py")
    )
    steps = _steps()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "bundle.yaml").write_text("bundle:\n  name: local\n  version: 1.0.0\n")
    (repo / "bundle.dot").write_text('digraph old { source_hash="000000"; }\n')
    context = {"repo_path": str(repo)}
    _run(steps["repo-discovery"], context, tmp_path, scratch=tmp_path)
    doc = _run(steps["bundle-doc-freshness-check"], context, tmp_path, scratch=tmp_path)
    assert doc["summary"]["bundle_dot_status"] == "stale"
    assert doc["findings"][0]["code"] == "BUNDLE_DOT_STALE"
    doc_path = Path(doc["_payload_file"])
    original_payload = doc_path.read_bytes()
    # Model the later structural DOT write; it must not erase the earlier check.
    (repo / "bundle.dot").write_text(doc["overview_structural_dot"])
    envelope, _ = helpers["_report_fixture"]()
    report_path = tmp_path / "envelope.json"
    report_path.write_text(json.dumps(envelope))
    proc = helpers["_run_report_paths"](
        steps["synthesize-report"], tmp_path, report_path, doc_path
    )
    assert proc.returncode == 0, proc.stderr
    assert "**Overall Verdict**: PASS WITH WARNINGS" in proc.stdout
    assert "BUNDLE_DOT_STALE" in proc.stdout
    assert json.loads(original_payload) in helpers["_fenced_json"](proc.stdout)
    assert doc_path.read_bytes() == original_payload


def _assert_large_payload(payload: Path, repo: str) -> None:
    assert payload.stat().st_size > 128 * 1024
    # Real serialized data must remain large without pytest's long temp path.
    raw = payload.read_text(encoding="utf-8")
    assert len(raw.replace(repo, "/r").encode("utf-8")) > 128 * 1024


@pytest.mark.parametrize("kind", ["large", "small", "long-root-only"])
def test_payload_size_oracle_rejects_small_or_path_padded_data(
    tmp_path: Path, kind: str
) -> None:
    repo = "/root-" + "x" * 1000
    payload = tmp_path / "payload.json"
    data = (
        [repo] * 200
        if kind == "long-root-only"
        else {"payload": "x" * (128 * 1024 if kind == "large" else 1)}
    )
    payload.write_text(json.dumps(data), encoding="utf-8")
    if kind == "large":
        _assert_large_payload(payload, repo)
    else:
        with pytest.raises(AssertionError):
            _assert_large_payload(payload, repo)


@POSIX_RECIPE_EXECUTION
def test_large_payloads_execute_producers_and_classifier_losslessly(
    tmp_path: Path,
) -> None:
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
        f"tools:\n{root_tools}",
        encoding="utf-8",
    )
    # An error at the end must survive classification, not disappear in a prefix.
    (behaviors / "zz-broken.yaml").write_text("bundle: [invalid\n", encoding="utf-8")
    context = {
        "repo_path": str(repo),
        "env_check": {
            "capabilities": {"foundation_available": True},
            "validation_mode": "full",
            "summary": {},
        },
    }
    for step_id in SPOOLED:
        result = _run(steps[step_id], context, tmp_path, scratch=scratch)
        _assert_large_payload(Path(result["_payload_file"]), str(repo))

    discovery = context["discovery_results"]
    individual = context["individual_validation"]
    tools = context["tool_placement_results"]
    _classification_defaults(context, steps["quality-classification"])
    classified = _run(
        steps["quality-classification"], context, tmp_path, scratch=scratch
    )
    _assert_lossless_results(discovery, individual, tools, classified)

    # The renderer no longer puts any of the large results in a bash argument.
    legacy_size = len(steps["quality-classification"]["command"].encode()) + sum(
        len(json.dumps(context[name]).encode()) for name in SPOOLED.values()
    )
    assert legacy_size > 128 * 1024


def _assert_lossless_results(
    discovery: dict, individual: dict, tools: dict, classified: dict
) -> None:
    """The integration test and sensitivity probes use the same lossless oracle."""
    assert discovery["total_count"] == 258
    assert individual["summary"]["total"] == 258
    assert individual["summary"]["failed"] >= 1
    assert len(tools["tool_inventory"]) == 512
    assert len(tools["suggestions"]) == 256
    assert classified["summary"]["total"] == 258
    assert classified["quality_level"] == "critical"
    assert any(
        bundle["path"].endswith("zz-broken.yaml") and bundle["quality"] == "critical"
        for bundle in classified["bundles"]
    )
    assert len(classified["tool_placement_issues"]) == len(tools["suggestions"])
    assert (
        classified["tool_placement_issues"][-1]["tool"]
        == tools["suggestions"][-1]["tool"]
    )
    assert (
        classified["tool_placement_issues"][-1]["message"]
        == tools["suggestions"][-1]["message"]
    )


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "discovery-prefix",
        "validation-prefix",
        "lost-error",
        "inventory-prefix",
        "suggestion-prefix",
        "classification-prefix",
        "lost-late-error",
        "issue-prefix",
        "last-tool",
        "last-message",
    ],
)
def test_lossless_oracle_rejects_truncation_and_missing_late_results(
    fault: str,
) -> None:
    """Models exercise assertions only; the integration above uses real payloads."""
    import copy

    discovery = {"total_count": 258}
    individual = {"summary": {"total": 258, "failed": 1}}
    tools = {
        "tool_inventory": [{} for _ in range(512)],
        "suggestions": [
            {"tool": f"tool-{n}", "message": f"message-{n}"} for n in range(256)
        ],
    }
    classified = {
        "summary": {"total": 258},
        "quality_level": "critical",
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
        '"${AMPLIFIER_PYTHON}" -c ' + shlex.quote(code),
        tmp_path,
        dict(
            os.environ,
            AMPLIFIER_PYTHON=sys.executable,
            HOME=str(foreign),
            AMPLIFIER_HOME=str(foreign),
        ),
    )
    home = tmp_path / ".validator-home"
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [str(home), str(home / ".amplifier")]
    assert stat.S_IMODE(home.stat().st_mode) == 0o700
    assert marker.read_bytes() == b"unrelated synthetic registry"


@POSIX_RECIPE_EXECUTION
def test_bash_timeout_terminates_owned_child_without_signalling_other_group(
    tmp_path: Path, monkeypatch
) -> None:
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
                    assert time.monotonic() < deadline, (
                        "timeout fixture did not become ready"
                    )
                    time.sleep(0.01)
            return super().communicate(*args, **kwargs)

    def killpg(group, signum):
        assert group == owned[0].pid
        assert group != os.getpgrp() and group != unrelated.pid
        signals.append((group, signum))
        return real_killpg(group, signum)

    with real_popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    ) as unrelated:
        try:
            monkeypatch.setattr(subprocess, "Popen", ReadyPopen)
            monkeypatch.setattr(os, "killpg", killpg)
            with pytest.raises(subprocess.TimeoutExpired) as caught:
                _run_bash(
                    'exec "${AMPLIFIER_PYTHON}" -c ' + shlex.quote(code),
                    tmp_path,
                    dict(os.environ, AMPLIFIER_PYTHON=sys.executable),
                    timeout=0.05,
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
def test_bash_decode_failure_after_exit_never_signals_reaped_group(
    tmp_path: Path, monkeypatch
) -> None:
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
    monkeypatch.setattr(
        os, "killpg", lambda group, signum: signals.append((group, signum))
    )
    code = "import os; os.write(1, bytes([255]))"
    with pytest.raises(UnicodeDecodeError) as caught:
        _run_bash(
            'exec "${AMPLIFIER_PYTHON}" -c ' + shlex.quote(code),
            tmp_path,
            dict(os.environ, AMPLIFIER_PYTHON=sys.executable),
        )
    assert caught.value is failures[0]
    assert len(owned) == 1 and owned[0].returncode == 0
    assert owned[0].stdout.closed and owned[0].stderr.closed
    assert signals == [], "must not signal an already-reaped leader's group ID"


@POSIX_RECIPE_EXECUTION
@pytest.mark.parametrize("interrupted", [False, True])
def test_bash_cleanup_escalation_is_bounded_and_only_targets_its_owned_group(
    tmp_path, monkeypatch, interrupted
):
    import io

    failure = (
        KeyboardInterrupt()
        if interrupted
        else subprocess.TimeoutExpired("owned command", 60)
    )
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
    monkeypatch.setattr(
        os, "killpg", lambda group, signum: calls.append(("signal", group, signum))
    )
    with pytest.raises(type(failure)) as caught:
        _run_bash("synthetic", tmp_path, {})
    assert caught.value is failure
    assert calls == [
        ("communicate", 60),
        ("signal", proc.pid, signal.SIGTERM),
        ("communicate", 1),
        ("signal", proc.pid, signal.SIGKILL),
        ("communicate", 5),
    ]
    assert proc.stdout.closed and proc.stderr.closed


@POSIX_RECIPE_EXECUTION
@pytest.mark.parametrize("phase", ["reaped-during-grace", "signal-failed"])
def test_bash_cleanup_preserves_failure_and_rechecks_leader_before_kill(
    tmp_path, monkeypatch, phase
):
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
            ("communicate", 60),
            ("signal", proc.pid, signal.SIGTERM),
            ("communicate", 1),
            ("communicate", 5),
        ]
        assert not getattr(failure, "__notes__", [])
    else:
        assert calls == [("communicate", 60), ("signal", proc.pid, signal.SIGTERM)]
        assert any(
            "cleanup failed" in note and "synthetic group signal refusal" in note
            for note in failure.__notes__
        )
    assert proc.stdout.closed and proc.stderr.closed


@POSIX_RECIPE_EXECUTION
def test_payload_files_are_unique_in_workspace_without_runtime_scratch(
    tmp_path: Path,
) -> None:
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
def test_classifier_refuses_missing_or_corrupt_payloads(
    tmp_path: Path, contents: str | None
) -> None:
    context: dict[str, object] = {"repo_path": str(tmp_path)}
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
