"""Exercise the actual release guard without publishing or contacting PyPI."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


@pytest.fixture
def release_guard(tmp_path):
    if (
        sys.platform == "win32"
        or shutil.which("bash") is None
        or shutil.which("python3") is None
    ):
        pytest.skip("The Ubuntu publishing guard requires bash and python3")
    workflow = yaml.safe_load(
        (Path(__file__).parents[1] / ".github/workflows/publish.yml").read_text()
    )
    step = next(
        step
        for step in workflow["jobs"]["publish"]["steps"]
        if step["name"] == "Verify release tag and merged source"
    )
    assert "if" not in step, "Manual dispatch must run the same release guard"
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True
        )

    git("config", "user.email", "release-test@example.invalid")
    git("config", "user.name", "Release guard test")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n')
    git("add", "pyproject.toml")
    git("commit", "-qm", "Merged version")
    git("update-ref", "refs/remotes/origin/main", "HEAD")

    def run(ref_type="tag", ref_name="v1.2.3", unmerged=False):
        if unmerged:
            git("commit", "--allow-empty", "-qm", "Unmerged candidate")
        return subprocess.run(
            ["bash", "-c", step["run"]],
            cwd=tmp_path,
            env={
                **os.environ,
                "GITHUB_REF_TYPE": ref_type,
                "GITHUB_REF_NAME": ref_name,
            },
            capture_output=True,
            text=True,
            check=False,
        )

    return run


def test_merged_matching_tag_passes(release_guard):
    result = release_guard()
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "kwargs,reason",
    [
        ({"ref_type": "branch", "ref_name": "main"}, "requires an existing"),
        ({"ref_name": "release-1.2.3"}, "requires an existing"),
        ({"ref_name": "v9.9.9"}, "does not match"),
        ({"unmerged": True}, "already be merged"),
    ],
)
def test_invalid_release_stops_before_publication(release_guard, kwargs, reason):
    result = release_guard(**kwargs)
    assert result.returncode != 0
    assert reason in result.stdout
