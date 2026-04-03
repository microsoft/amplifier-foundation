"""Tests for bundle_repo_dot() v3 API and package-level imports."""

from pathlib import Path

import pytest

from dot_docs.bundle_to_dot import (
    _get_repo_git_url,
    _is_same_repo_include,
    _normalize_git_url,
    _resolve_local_include,
    bundle_repo_dot,
)

REPO_ROOT = Path(__file__).parent.parent


# ── TestPackageImports ─────────────────────────────────────────────────────────


class TestPackageImports:
    """Verify the v3 dot_docs package __init__.py exports."""

    def test_import_bundle_repo_dot(self) -> None:
        from dot_docs import bundle_repo_dot as _fn  # noqa: F401

        assert callable(_fn)

    def test_import_estimate_tokens(self) -> None:
        from dot_docs import estimate_tokens as _fn  # noqa: F401

        assert callable(_fn)

    def test_import_parse_frontmatter(self) -> None:
        from dot_docs import parse_frontmatter as _fn  # noqa: F401

        assert callable(_fn)


# ── TestAllExportsCallable ────────────────────────────────────────────────────


class TestAllExportsCallable:
    """Every name listed in __all__ is importable and callable."""

    def test_all_exports_callable(self) -> None:
        import dot_docs

        assert hasattr(dot_docs, "__all__"), "dot_docs must define __all__"
        for name in dot_docs.__all__:
            obj = getattr(dot_docs, name, None)
            assert obj is not None, f"dot_docs.{name} missing after import"
            assert callable(obj), f"dot_docs.{name} is not callable"


# ── TestBundleRepoDot ─────────────────────────────────────────────────────────


class TestBundleRepoDot:
    """Tests for the v3 bundle_repo_dot() with 7 cluster categories."""

    def test_returns_valid_dot(self, tmp_path: Path) -> None:
        """bundle_repo_dot() returns a valid DOT string with source_hash."""
        (tmp_path / "bundle.md").write_text(
            "---\nbundle:\n  name: myrepo\n  version: 1.0.0\n---\n# Test\n"
        )
        dot = bundle_repo_dot(tmp_path)
        assert dot.startswith("digraph ")
        assert dot.strip().endswith("}")
        assert 'source_hash="' in dot

    def test_has_behavior_cluster(self, tmp_path: Path) -> None:
        """bundle_repo_dot() generates a cluster_behaviors subgraph."""
        beh_dir = tmp_path / "behaviors"
        beh_dir.mkdir()
        (beh_dir / "mybeh.yaml").write_text(
            "bundle:\n  name: mybeh\n  version: 1.0.0\n  description: My behavior\n"
        )
        dot = bundle_repo_dot(tmp_path)
        assert "cluster_behaviors" in dot
        assert "mybeh" in dot

    def test_has_agents_cluster(self, tmp_path: Path) -> None:
        """bundle_repo_dot() generates a cluster_agents subgraph."""
        agt_dir = tmp_path / "agents"
        agt_dir.mkdir()
        (agt_dir / "myagent.md").write_text(
            "---\nmeta:\n  name: myagent\n  description: My agent description\n---\nBody\n"
        )
        dot = bundle_repo_dot(tmp_path)
        assert "cluster_agents" in dot
        assert "myagent" in dot

    def test_has_modules_cluster(self, tmp_path: Path) -> None:
        """bundle_repo_dot() generates a cluster_modules subgraph for local modules."""
        mod_dir = tmp_path / "modules" / "my-tool"
        pkg_dir = mod_dir / "amplifier_module_my_tool"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").write_text("# tool module\n")
        dot = bundle_repo_dot(tmp_path)
        assert "cluster_modules" in dot
        assert "my-tool" in dot

    def test_has_providers_cluster(self, tmp_path: Path) -> None:
        """bundle_repo_dot() generates a cluster_providers subgraph."""
        prov_dir = tmp_path / "providers"
        prov_dir.mkdir()
        (prov_dir / "my-provider.yaml").write_text("provider:\n  name: my-provider\n")
        dot = bundle_repo_dot(tmp_path)
        assert "cluster_providers" in dot
        assert "my-provider" in dot

    def test_has_experiments_cluster(self, tmp_path: Path) -> None:
        """bundle_repo_dot() generates a cluster_experiments subgraph."""
        exp_dir = tmp_path / "experiments"
        exp_dir.mkdir()
        (exp_dir / "exp-alpha.md").write_text(
            "---\nbundle:\n  name: exp-alpha\n  version: 0.1.0\n---\n# Exp\n"
        )
        dot = bundle_repo_dot(tmp_path)
        assert "cluster_experiments" in dot
        assert "exp-alpha" in dot

    def test_has_context_cluster_when_behaviors_declare_context(
        self, tmp_path: Path
    ) -> None:
        """bundle_repo_dot() generates cluster_context when behaviors include context."""
        beh_dir = tmp_path / "behaviors"
        beh_dir.mkdir()
        ctx_dir = tmp_path / "context"
        ctx_dir.mkdir()
        (ctx_dir / "instructions.md").write_text("# Instructions\nDo stuff.\n")
        (beh_dir / "mybeh.yaml").write_text(
            "bundle:\n  name: mybeh\n  version: 1.0.0\n"
            "context:\n  include:\n    - test:context/instructions.md\n"
        )
        dot = bundle_repo_dot(tmp_path)
        assert "cluster_context" in dot
        assert "instructions.md" in dot

    def test_root_to_behavior_composes_edge(self, tmp_path: Path) -> None:
        """Root to local behavior includes are labeled 'composes'."""
        (tmp_path / "bundle.md").write_text(
            "---\nbundle:\n  name: root\n  version: 1.0.0\n"
            "includes:\n  - bundle: test:behaviors/mybeh\n---\n"
        )
        beh_dir = tmp_path / "behaviors"
        beh_dir.mkdir()
        (beh_dir / "mybeh.yaml").write_text(
            "bundle:\n  name: mybeh\n  version: 1.0.0\n"
        )
        dot = bundle_repo_dot(tmp_path)
        assert "composes" in dot

    def test_external_behavior_includes_shown_dashed(self, tmp_path: Path) -> None:
        """External git+ behavior includes appear as dashed nodes."""
        (tmp_path / "bundle.md").write_text(
            "---\nbundle:\n  name: root\n  version: 1.0.0\n"
            "includes:\n"
            "  - bundle: git+https://github.com/example/ext@main#subdirectory=behaviors/ext.yaml\n"
            "---\n"
        )
        dot = bundle_repo_dot(tmp_path)
        assert "dashed" in dot

    def test_disclaimer_node_present(self, tmp_path: Path) -> None:
        """A disclaimer note node with token legend text appears."""
        dot = bundle_repo_dot(tmp_path)
        assert "Token estimates" in dot

    def test_source_hash_deterministic(self, tmp_path: Path) -> None:
        """Same input always produces the same source_hash."""
        import re

        (tmp_path / "bundle.md").write_text(
            "---\nbundle:\n  name: stable\n  version: 1.0.0\n---\n"
        )
        dot1 = bundle_repo_dot(tmp_path)
        dot2 = bundle_repo_dot(tmp_path)
        hashes1 = re.findall(r'source_hash="([a-f0-9]+)"', dot1)
        hashes2 = re.findall(r'source_hash="([a-f0-9]+)"', dot2)
        assert len(hashes1) == 1
        assert hashes1 == hashes2

    def test_rankdir_is_lr(self, tmp_path: Path) -> None:
        """bundle_repo_dot() generates rankdir=LR (left-to-right layout)."""
        (tmp_path / "bundle.md").write_text(
            "---\\nbundle:\\n  name: myrepo\\n  version: 1.0.0\\n---\\n# Test\\n"
        )
        dot = bundle_repo_dot(tmp_path)
        assert "rankdir=LR" in dot, "Expected rankdir=LR in DOT output"

    def test_real_repo_covers_all_behaviors(self) -> None:
        """bundle_repo_dot() on the real repo includes all behavior file stems."""
        dot = bundle_repo_dot(REPO_ROOT)
        behaviors_dir = REPO_ROOT / "behaviors"
        for f in sorted(behaviors_dir.glob("*.yaml")):
            stem = f.stem
            assert stem in dot, f"Behavior '{stem}' not found in repo DOT output"


# ── TestGetRepoGitUrl ─────────────────────────────────────────────────────────


class TestGetRepoGitUrl:
    """Tests for _get_repo_git_url() reading remote origin from .git config."""

    def test_reads_url_from_git_directory(self, tmp_path: Path) -> None:
        """Reads remote origin URL from a standard .git directory."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text(
            "[core]\n\trepositoryformatversion = 0\n"
            '[remote "origin"]\n\turl = https://github.com/example/myrepo.git\n'
        )
        result = _get_repo_git_url(tmp_path)
        assert result == "https://github.com/example/myrepo.git"

    def test_reads_url_from_gitfile_submodule(self, tmp_path: Path) -> None:
        """Reads remote origin URL when .git is a file pointing to a gitdir (submodule)."""
        gitdir = tmp_path / ".git_modules" / "myrepo"
        gitdir.mkdir(parents=True)
        (gitdir / "config").write_text(
            "[core]\n\trepositoryformatversion = 0\n"
            '[remote "origin"]\n\turl = https://github.com/example/myrepo.git\n'
        )
        (tmp_path / ".git").write_text("gitdir: .git_modules/myrepo\n")
        result = _get_repo_git_url(tmp_path)
        assert result == "https://github.com/example/myrepo.git"

    def test_returns_none_when_no_git(self, tmp_path: Path) -> None:
        """Returns None when there is no .git file or directory."""
        result = _get_repo_git_url(tmp_path)
        assert result is None

    def test_returns_none_when_no_remote(self, tmp_path: Path) -> None:
        """Returns None when .git/config has no remote section."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text("[core]\n\trepositoryformatversion = 0\n")
        result = _get_repo_git_url(tmp_path)
        assert result is None


# ── TestNormalizeGitUrl ───────────────────────────────────────────────────────


class TestNormalizeGitUrl:
    """Tests for _normalize_git_url() stripping git+, @ref, #fragment, .git."""

    def test_strips_git_plus_prefix(self) -> None:
        url = "git+https://github.com/example/repo.git"
        assert _normalize_git_url(url) == "https://github.com/example/repo"

    def test_strips_ref_suffix(self) -> None:
        url = "git+https://github.com/example/repo@main"
        assert _normalize_git_url(url) == "https://github.com/example/repo"

    def test_strips_fragment(self) -> None:
        url = "git+https://github.com/example/repo@main#subdirectory=behaviors/foo.yaml"
        assert _normalize_git_url(url) == "https://github.com/example/repo"

    def test_strips_trailing_dot_git(self) -> None:
        url = "https://github.com/example/repo.git"
        assert _normalize_git_url(url) == "https://github.com/example/repo"

    def test_normalizes_same_repo_urls_to_equal(self) -> None:
        """Include URL and clone URL should normalize to the same value."""
        include_url = "git+https://github.com/microsoft/amplifier-foundation@main#subdirectory=behaviors/foo.yaml"
        clone_url = "https://github.com/microsoft/amplifier-foundation.git"
        assert _normalize_git_url(include_url) == _normalize_git_url(clone_url)


# ── TestIsSameRepoInclude ────────────────────────────────────────────────────


class TestIsSameRepoInclude:
    """Tests for _is_same_repo_include() detecting and resolving same-repo git URLs."""

    def _make_repo(self, tmp_path: Path, origin_url: str) -> None:
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text(
            "[core]\n\trepositoryformatversion = 0\n"
            f'[remote "origin"]\n\turl = {origin_url}\n'
        )

    def test_same_repo_url_resolves_to_local_path(self, tmp_path: Path) -> None:
        """A git+ URL pointing to the same repo resolves to the local file."""
        self._make_repo(tmp_path, "https://github.com/example/myrepo.git")
        behavior = tmp_path / "behaviors" / "my-behavior.yaml"
        behavior.parent.mkdir()
        behavior.write_text("bundle:\n  name: my-behavior\n  version: 1.0\n")

        result = _is_same_repo_include(
            "git+https://github.com/example/myrepo@main#subdirectory=behaviors/my-behavior.yaml",
            tmp_path,
        )
        assert result == behavior.resolve()

    def test_different_repo_url_returns_none(self, tmp_path: Path) -> None:
        """A git+ URL pointing to a different repo returns None."""
        self._make_repo(tmp_path, "https://github.com/example/myrepo.git")
        behavior = tmp_path / "behaviors" / "my-behavior.yaml"
        behavior.parent.mkdir()
        behavior.write_text("bundle:\n  name: my-behavior\n  version: 1.0\n")

        result = _is_same_repo_include(
            "git+https://github.com/other-org/other-repo@main#subdirectory=behaviors/my-behavior.yaml",
            tmp_path,
        )
        assert result is None

    def test_non_git_ref_returns_none(self, tmp_path: Path) -> None:
        """A non-git+ reference (bare name, namespace:path) returns None."""
        self._make_repo(tmp_path, "https://github.com/example/myrepo.git")
        result = _is_same_repo_include("foundation:behaviors/my-behavior", tmp_path)
        assert result is None

    def test_no_subdirectory_returns_none(self, tmp_path: Path) -> None:
        """A same-repo URL without #subdirectory= fragment returns None."""
        self._make_repo(tmp_path, "https://github.com/example/myrepo.git")
        result = _is_same_repo_include(
            "git+https://github.com/example/myrepo@main",
            tmp_path,
        )
        assert result is None

    def test_nonexistent_subdirectory_returns_none(self, tmp_path: Path) -> None:
        """A same-repo URL whose subdirectory path doesn't exist returns None."""
        self._make_repo(tmp_path, "https://github.com/example/myrepo.git")
        result = _is_same_repo_include(
            "git+https://github.com/example/myrepo@main#subdirectory=behaviors/missing.yaml",
            tmp_path,
        )
        assert result is None


# ── TestResolveLocalInclude ──────────────────────────────────────────────────


class TestResolveLocalInclude:
    """Tests that _resolve_local_include() handles same-repo git+ URLs."""

    def test_same_repo_git_url_resolves_to_local_path(self, tmp_path: Path) -> None:
        """A same-repo git+ URL in _resolve_local_include() returns the local path."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text(
            "[core]\n\trepositoryformatversion = 0\n"
            '[remote "origin"]\n\turl = https://github.com/example/myrepo.git\n'
        )
        behavior = tmp_path / "behaviors" / "my-behavior.yaml"
        behavior.parent.mkdir()
        behavior.write_text("bundle:\n  name: my-behavior\n  version: 1.0\n")

        result = _resolve_local_include(
            "git+https://github.com/example/myrepo@main#subdirectory=behaviors/my-behavior.yaml",
            tmp_path,
        )
        assert result == behavior.resolve()

    def test_external_git_url_still_returns_none(self, tmp_path: Path) -> None:
        """A git+ URL to a different repo still returns None."""
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text(
            "[core]\n\trepositoryformatversion = 0\n"
            '[remote "origin"]\n\turl = https://github.com/example/myrepo.git\n'
        )
        result = _resolve_local_include(
            "git+https://github.com/other/other-repo@main#subdirectory=behaviors/foo.yaml",
            tmp_path,
        )
        assert result is None


# ── Parametrized real-repo coverage ──────────────────────────────────────────

import yaml as _yaml  # noqa: E402  (used in module-level setup below)

_BEHAVIORS_DIR = REPO_ROOT / "behaviors"
_BUNDLES_DIR = REPO_ROOT / "bundles"

_BEHAVIOR_STEMS = [f.stem for f in sorted(_BEHAVIORS_DIR.glob("*.yaml"))]


def _bundle_name_from_yaml(path: Path) -> str:
    """Extract bundle.name from a YAML file, falling back to the file stem."""
    try:
        data = _yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data.get("bundle", {}).get("name", path.stem)
    except Exception:
        return path.stem


_BUNDLE_NAMES: list[str] = (
    [_bundle_name_from_yaml(f) for f in sorted(_BUNDLES_DIR.glob("*.yaml"))]
    if _BUNDLES_DIR.exists()
    else []
)


@pytest.fixture(scope="module")
def real_repo_dot_output() -> str:
    """Generate bundle_repo_dot() once for the real repo (module-scoped for speed)."""
    return bundle_repo_dot(REPO_ROOT)


@pytest.mark.parametrize("stem", _BEHAVIOR_STEMS, ids=_BEHAVIOR_STEMS)
def test_each_behavior_in_repo_dot(stem: str, real_repo_dot_output: str) -> None:
    """Every behavior file stem appears in bundle_repo_dot() output for the real repo."""
    assert stem in real_repo_dot_output, (
        f"Behavior '{stem}' not found in repo DOT output"
    )


@pytest.mark.skipif(not _BUNDLES_DIR.exists(), reason="No bundles/ directory in repo")
@pytest.mark.parametrize("name", _BUNDLE_NAMES, ids=_BUNDLE_NAMES or ["(none)"])
def test_each_bundle_in_repo_dot(name: str, real_repo_dot_output: str) -> None:
    """Every standalone bundle name appears in bundle_repo_dot() output for the real repo."""
    assert name in real_repo_dot_output, f"Bundle '{name}' not found in repo DOT output"
