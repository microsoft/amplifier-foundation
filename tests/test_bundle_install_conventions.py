"""Execution tests for behavior hygiene and README installation conventions.

Every assertion runs the exact Python heredoc embedded in
``validate-bundle-repo.yaml``. Fixtures only supply files; they never duplicate
the recipe's classifiers or shell-command parsing.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent
RECIPE = REPO_ROOT / "recipes" / "validate-bundle-repo.yaml"
REPO_PATH_ENV = "VALIDATE_BUNDLE_REPO_PATH"

FOUNDATION = "git+https://github.com/microsoft/amplifier-foundation@main"
ANCHORS = f"{FOUNDATION}#subdirectory=bundles/anchors/bundle.md"
REDACTION = f"{FOUNDATION}#subdirectory=behaviors/redaction.yaml"


def _step_body(step_id: str) -> str:
    """Extract one bash step's Python heredoc verbatim and de-indent it."""
    lines = RECIPE.read_text(encoding="utf-8").splitlines()
    step = next(index for index, line in enumerate(lines) if line.strip() == f'- id: "{step_id}"')
    start = next(
        index
        for index in range(step, len(lines))
        if lines[index].rstrip().endswith("<< 'EOF'")
    )
    end = next(index for index in range(start + 1, len(lines)) if lines[index].strip() == "EOF")
    indent = len(lines[start]) - len(lines[start].lstrip())
    return "\n".join(line[indent:] for line in lines[start + 1 : end])


def _run_step(step_id: str, repo: Path, *, root_bundle_repo: bool = False) -> dict:
    """Run an actual recipe step against an on-disk fixture repository."""
    script = _step_body(step_id).replace(
        '"{{root_bundle_repo}}"',
        repr("true" if root_bundle_repo else "false"),
    )
    env = dict(os.environ)
    env[REPO_PATH_ENV] = str(repo)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )
    assert completed.returncode == 0, (
        f"{step_id} exited {completed.returncode}\nSTDERR:\n{completed.stderr}"
    )
    return json.loads(completed.stdout)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _behavior_with(include: str) -> str:
    return f"""\
bundle:
  name: behavior
  description: Test behavior
includes:
  - bundle: {include}
"""


def _warning_types(payload: dict) -> set[str]:
    return {warning["type"] for warning in payload["warnings"]}


def _error_types(payload: dict) -> set[str]:
    return {error["type"] for error in payload["errors"]}


def test_behavior_hygiene_allows_foundation_behavior_partials_and_nested_behavior_bundle(tmp_path: Path) -> None:
    """Foundation partials are not roots merely because their repo name says foundation."""
    repo = tmp_path / "partial-behaviors"
    _write(repo / "behaviors" / "direct.yaml", _behavior_with(REDACTION))
    _write(
        repo / "behaviors" / "nested" / "bundle.md",
        "---\n" + _behavior_with(
            f"{FOUNDATION}#subdirectory=behaviors/nested/bundle.md"
        ) + "---\n",
    )

    payload = _run_step("behavior-hygiene-validation", repo)

    assert payload["passed"] is True
    assert payload["errors"] == []
    assert payload["behaviors_checked"] == 2


def test_behavior_hygiene_rejects_the_own_root_manifest_name(tmp_path: Path) -> None:
    """A behavior cannot include its own domain-named root via the namespace."""
    repo = tmp_path / "domain-root"
    _write(
        repo / "bundle.md",
        "---\nbundle:\n  name: domain-root\n  description: Complete host\n---\n",
    )
    _write(repo / "behaviors" / "invalid.yaml", _behavior_with("domain-root"))

    payload = _run_step("behavior-hygiene-validation", repo)

    assert payload["passed"] is False
    assert "includes_root_bundle" in _error_types(payload)


def test_behavior_hygiene_rejects_a_foreign_conventional_nested_root(tmp_path: Path) -> None:
    """A `bundles/**/bundle.*` partial is a known complete-root layout."""
    repo = tmp_path / "foreign-conventional-root"
    _write(
        repo / "behaviors" / "invalid.yaml",
        _behavior_with(
            "git+https://github.com/example/foreign@main"
            "#subdirectory=bundles/complete-host/bundle.yml"
        ),
    )

    payload = _run_step("behavior-hygiene-validation", repo)

    assert payload["passed"] is False
    assert "includes_root_bundle" in _error_types(payload)


def test_behavior_hygiene_rejects_a_foundation_conventional_nested_root(tmp_path: Path) -> None:
    """Known Foundation nested roots are no exception to the `bundles/**` rule."""
    repo = tmp_path / "foundation-conventional-root"
    _write(
        repo / "behaviors" / "invalid.yaml",
        _behavior_with(
            f"{FOUNDATION}#subdirectory=bundles/anchors-amp-dev/bundle.md"
        ),
    )

    payload = _run_step("behavior-hygiene-validation", repo)

    assert payload["passed"] is False
    assert "includes_root_bundle" in _error_types(payload)


@pytest.mark.parametrize(
    "include",
    [
        f"{FOUNDATION}#subdirectory=bundles/anchors-amp-dev",
        "git+https://github.com/example/foreign@main#subdirectory=bundles/complete-host",
        f"{FOUNDATION}#subdirectory=bundles/with-anthropic.yaml",
    ],
)
def test_behavior_hygiene_rejects_all_conventional_bundles_surfaces(
    tmp_path: Path, include: str
) -> None:
    """Every documented `bundles/` full-host surface is forbidden inside a behavior."""
    repo = tmp_path / "conventional-bundle-surface"
    _write(repo / "behaviors" / "invalid.yaml", _behavior_with(include))

    payload = _run_step("behavior-hygiene-validation", repo)

    assert payload["passed"] is False
    assert "includes_root_bundle" in _error_types(payload)


@pytest.mark.parametrize(
    "include",
    [
        "foundation",
        "anchors",
        FOUNDATION,
        ANCHORS,
    ],
)
def test_behavior_hygiene_rejects_known_complete_roots(tmp_path: Path, include: str) -> None:
    """Bare Foundation/Anchors and their complete root forms remain prohibited in a behavior."""
    repo = tmp_path / "root-in-behavior"
    _write(repo / "behaviors" / "invalid.yaml", _behavior_with(include))

    payload = _run_step("behavior-hygiene-validation", repo)

    assert payload["passed"] is False
    assert "includes_root_bundle" in _error_types(payload)


def test_foreign_bare_root_reference_remains_visible_to_reference_hygiene(tmp_path: Path) -> None:
    """Narrowing the root classifier does not remove the foreign-root warning guard."""
    repo = tmp_path / "foreign-root"
    _write(
        repo / "behaviors" / "invalid.yaml",
        _behavior_with("git+https://github.com/example/foreign-root@main"),
    )

    payload = _run_step("behavior-reference-hygiene", repo)

    assert payload["errors"] == []
    assert "cross_repo_root_bundle_ref" in _warning_types(payload)


def test_nested_yml_behavior_is_discovered_and_hygiene_checks_all_prohibitions(tmp_path: Path) -> None:
    """The `.yml` conventional entry must not bypass discovery or hygiene."""
    repo = tmp_path / "nested-yml"
    nested = repo / "behaviors" / "nested" / "bundle.yml"
    _write(
        nested,
        """\
bundle:
  name: nested
  description: Invalid behavior fixture
includes:
  - bundle: foundation
session:
  orchestrator:
    module: loop-streaming
providers:
  - module: provider-example
""",
    )

    discovery = _run_step("repo-discovery", repo)
    hygiene = _run_step("behavior-hygiene-validation", repo)
    references = _run_step("behavior-reference-hygiene", repo)

    assert discovery["bundles_found"]["behaviors"] == [str(nested)]
    assert hygiene["behaviors_checked"] == 1
    assert hygiene["behavior_details"][0]["name"] == "nested"
    assert {
        "includes_root_bundle",
        "has_session_orchestrator",
        "has_providers",
    } <= _error_types(hygiene)
    assert references["behaviors_checked"] == 1
    assert references["behavior_details"][0]["name"] == "nested"


def test_readme_accepts_a_quoted_multiline_canonical_behavior_install(tmp_path: Path) -> None:
    """A canonical URI and --app survive shell continuations and quotes."""
    repo = tmp_path / "correct-behavior-install"
    _write(
        repo / "README.md",
        f"""\
```bash
amplifier bundle add \\
  "{FOUNDATION}#subdirectory=behaviors/capability.yaml" \\
  --app
```
""",
    )

    payload = _run_step("readme-install-convention-check", repo)

    assert payload["warnings"] == []
    assert payload["summary"]["behavior_install_commands_found"] == 1


def test_readme_accepts_options_before_direct_behavior_uri(tmp_path: Path) -> None:
    """`--app`, `--name`, `-n`, and `--name=` may all appear before the URI."""
    repo = tmp_path / "options-before-uri"
    _write(
        repo / "README.md",
        f"""\
amplifier bundle add --app --name=one "{FOUNDATION}#subdirectory=behaviors/one.yaml"
amplifier bundle add -n two --app "{FOUNDATION}#subdirectory=behaviors/two.yml"
amplifier bundle add --name three --app "{FOUNDATION}#subdirectory=behaviors/three.md"
""",
    )

    payload = _run_step("readme-install-convention-check", repo)

    assert payload["warnings"] == []
    assert payload["summary"]["behavior_install_commands_found"] == 3


def test_readme_accepts_an_unquoted_behavior_fragment_as_the_uri(tmp_path: Path) -> None:
    """A fragment is part of an unquoted URI, not a shell comment."""
    repo = tmp_path / "unquoted-fragment"
    _write(
        repo / "README.md",
        f"amplifier bundle add --app {FOUNDATION}#subdirectory=behaviors/capability.yaml\n",
    )

    payload = _run_step("readme-install-convention-check", repo)

    assert payload["warnings"] == []
    assert payload["summary"]["behavior_install_commands_found"] == 1


def test_readme_bounds_inline_code_before_following_prose(tmp_path: Path) -> None:
    """A valid inline command retains its in-code --app without parsing later prose."""
    repo = tmp_path / "inline-code"
    _write(
        repo / "README.md",
        f'Run `amplifier bundle add "{FOUNDATION}#subdirectory=behaviors/capability.yaml" --app` first.\n',
    )

    payload = _run_step("readme-install-convention-check", repo)

    assert payload["warnings"] == []
    assert payload["summary"]["behavior_install_commands_found"] == 1


def test_readme_flags_behavior_install_missing_app(tmp_path: Path) -> None:
    """A behavior installation is incomplete without --app."""
    repo = tmp_path / "missing-app"
    _write(
        repo / "README.md",
        f'amplifier bundle add "{FOUNDATION}#subdirectory=behaviors/capability.yaml"\n',
    )

    payload = _run_step("readme-install-convention-check", repo)

    assert _warning_types(payload) == {"readme_missing_app_flag"}


def test_readme_does_not_accept_prose_app_flag_after_inline_command(tmp_path: Path) -> None:
    """Only --app inside the command span satisfies a behavior installation."""
    repo = tmp_path / "inline-code-missing-app"
    _write(
        repo / "README.md",
        f'Run `amplifier bundle add "{FOUNDATION}#subdirectory=behaviors/capability.yaml"` with --app later.\n',
    )

    payload = _run_step("readme-install-convention-check", repo)

    assert _warning_types(payload) == {"readme_missing_app_flag"}


def test_readme_flags_nested_behavior_install_missing_app(tmp_path: Path) -> None:
    """Conventional nested behavior entries require --app just like direct files."""
    repo = tmp_path / "nested-behavior-missing-app"
    _write(
        repo / "README.md",
        f'amplifier bundle add "{FOUNDATION}#subdirectory=behaviors/capability/bundle.yml"\n',
    )

    payload = _run_step("readme-install-convention-check", repo)

    assert _warning_types(payload) == {"readme_missing_app_flag"}


def test_readme_does_not_count_comment_or_trailing_prose_as_a_behavior_uri(tmp_path: Path) -> None:
    """A comment token is never accepted as the add command's URI operand."""
    repo = tmp_path / "comment-evasion"
    _write(
        repo / "README.md",
        f"""\
# amplifier bundle add "{REDACTION}" --app
amplifier bundle add --app # "{REDACTION}"
""",
    )

    payload = _run_step("readme-install-convention-check", repo)

    assert payload["summary"]["bundle_add_commands_found"] == 1
    assert payload["summary"]["behavior_install_commands_found"] == 0
    assert "readme_recommends_root_bundle" in _warning_types(payload)


def test_readme_first_named_anchors_root_before_behavior_is_still_a_warning(tmp_path: Path) -> None:
    """A named Anchors root cannot displace the behavior-first primary command."""
    repo = tmp_path / "anchors-first"
    _write(
        repo / "README.md",
        f"""\
amplifier bundle add --name=anchors-host "{ANCHORS}"
amplifier bundle add --app "{FOUNDATION}#subdirectory=behaviors/capability.yaml"
""",
    )

    payload = _run_step("readme-install-convention-check", repo)

    assert "readme_recommends_root_bundle" in _warning_types(payload)
    assert "readme_missing_app_flag" not in _warning_types(payload)


def test_readme_later_unrelated_use_cannot_hide_a_bad_first_root(tmp_path: Path) -> None:
    """A later `bundle use` is unrelated to whether the first add teaches a capability."""
    repo = tmp_path / "unrelated-use"
    _write(
        repo / "README.md",
        f"""\
amplifier bundle add "{ANCHORS}"
amplifier bundle use unrelated-host
amplifier bundle add --app "{FOUNDATION}#subdirectory=behaviors/capability.yaml"
""",
    )

    payload = _run_step("readme-install-convention-check", repo)

    assert "readme_recommends_root_bundle" in _warning_types(payload)


def test_readme_allows_explicit_supporting_root_registration_and_selection(tmp_path: Path) -> None:
    """Supporting roots remain valid without --app and are not restricted to Anchors."""
    repo = tmp_path / "supporting-roots"
    _write(
        repo / "README.md",
        f"""\
amplifier bundle add "{FOUNDATION}#subdirectory=behaviors/capability.yaml" --app
amplifier bundle add "{ANCHORS}" --name anchors-host
amplifier bundle use anchors-host
amplifier bundle add "git+https://github.com/example/other-supporting-root@main" --name other-host
""",
    )

    payload = _run_step("readme-install-convention-check", repo)

    assert payload["warnings"] == []
    assert payload["summary"]["behavior_install_commands_found"] == 1


def test_readme_allows_a_genuine_root_product_when_explicitly_declared(tmp_path: Path) -> None:
    """root_bundle_repo preserves the explicit full-host product exception."""
    repo = tmp_path / "root-product"
    _write(
        repo / "README.md",
        f'amplifier bundle add "{FOUNDATION}"\n',
    )

    payload = _run_step("readme-install-convention-check", repo, root_bundle_repo=True)

    assert payload["root_bundle_repo"] is True
    assert payload["warnings"] == []


def test_readme_without_bundle_commands_keeps_the_existing_info_only_outcome(tmp_path: Path) -> None:
    """No install command is informational, not silently treated as a valid behavior install."""
    repo = tmp_path / "no-commands"
    _write(repo / "README.md", "# Package\nUse the documented installer.\n")

    payload = _run_step("readme-install-convention-check", repo)

    assert payload["warnings"] == []
    assert [info["type"] for info in payload["info"]] == ["no_bundle_add_commands"]