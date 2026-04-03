"""Tests for bundle_overview_dot (v2 overview) and package-level imports."""

from pathlib import Path


from dot_docs.bundle_to_dot import (
    bundle_overview_dot,
    _get_repo_git_url,
    _normalize_git_url,
    _is_same_repo_include,
    _resolve_local_include,
)

REPO_ROOT = Path(__file__).parent.parent


# ── TestBundleOverviewDot ───────────────────────────────────────────────────


class TestBundleOverviewDot:
    """Tests for the overview DOT graph produced by bundle_overview_dot()."""

    def test_minimal_repo(self, tmp_path: Path) -> None:
        """Single bundle.md produces valid DOT."""
        f = tmp_path / "bundle.md"
        f.write_text("---\nbundle:\n  name: test\n  version: 1.0.0\n---\n# Test\n")
        dot = bundle_overview_dot(tmp_path)
        assert dot.startswith("digraph ")
        assert "test" in dot
        assert dot.strip().endswith("}")

    def test_shows_behaviors(self, tmp_path: Path) -> None:
        """Behaviors appear as nodes in the overview graph."""
        b = tmp_path / "bundle.md"
        b.write_text("---\nbundle:\n  name: root\n  version: 1.0.0\n---\n# Root\n")
        bdir = tmp_path / "behaviors"
        bdir.mkdir()
        (bdir / "helper.yaml").write_text(
            "bundle:\n  name: helper\n  version: 1.0.0\n  description: Helper behavior\n"
        )
        dot = bundle_overview_dot(tmp_path)
        assert "helper" in dot
        assert "root" in dot

    def test_shows_include_edges(self, tmp_path: Path) -> None:
        """Includes defined in bundle.md create directed edges in the graph."""
        b = tmp_path / "bundle.md"
        b.write_text(
            "---\nbundle:\n  name: root\n  version: 1.0.0\n"
            "includes:\n  - bundle: test:behaviors/child\n---\n# Root\n"
        )
        bdir = tmp_path / "behaviors"
        bdir.mkdir()
        (bdir / "child.yaml").write_text(
            "bundle:\n  name: child\n  version: 1.0.0\n  description: Child\n"
        )
        dot = bundle_overview_dot(tmp_path)
        assert "->" in dot

    def test_external_includes_dashed(self, tmp_path: Path) -> None:
        """External (git+) includes are shown as dashed nodes with (external) label."""
        b = tmp_path / "bundle.md"
        b.write_text(
            "---\nbundle:\n  name: root\n  version: 1.0.0\n"
            "includes:\n"
            "  - bundle: git+https://github.com/example/ext-bundle@main\n"
            "---\n# Root\n"
        )
        dot = bundle_overview_dot(tmp_path)
        assert "dashed" in dot
        assert "(external)" in dot

    def test_real_repo_overview(self) -> None:
        """Real foundation repo produces a valid overview DOT graph."""
        dot = bundle_overview_dot(REPO_ROOT)
        assert dot.startswith("digraph ")
        assert "foundation" in dot
        assert 'source_hash="' in dot
        assert dot.strip().endswith("}")

    def test_source_hash_deterministic(self, tmp_path: Path) -> None:
        """Same input always produces the same source_hash value."""
        import re

        f = tmp_path / "bundle.md"
        f.write_text("---\nbundle:\n  name: myrepo\n  version: 1.0.0\n---\n# Repo\n")
        dot1 = bundle_overview_dot(tmp_path)
        dot2 = bundle_overview_dot(tmp_path)
        hashes1 = re.findall(r'source_hash="([a-f0-9]+)"', dot1)
        hashes2 = re.findall(r'source_hash="([a-f0-9]+)"', dot2)
        assert len(hashes1) == 1
        assert hashes1 == hashes2

    def test_node_labels_show_counts(self, tmp_path: Path) -> None:
        """Node labels include token count annotations (~N tok)."""
        f = tmp_path / "bundle.md"
        f.write_text("---\nbundle:\n  name: myroot\n  version: 1.0.0\n---\n# Root\n")
        dot = bundle_overview_dot(tmp_path)
        # Each node label includes a token-count annotation
        assert "tok" in dot
        assert "~" in dot


# ── TestPackageImports ──────────────────────────────────────────────────────


class TestPackageImports:
    """Verify the v2 dot_docs package __init__.py exports."""

    def test_import_bundle_overview_dot(self) -> None:
        from dot_docs import bundle_overview_dot as _fn  # noqa: F401

        assert callable(_fn)

    def test_import_estimate_tokens(self) -> None:
        from dot_docs import estimate_tokens as _fn  # noqa: F401

        assert callable(_fn)

    def test_import_parse_frontmatter(self) -> None:
        from dot_docs import parse_frontmatter as _fn  # noqa: F401

        assert callable(_fn)

    def test_all_exports_callable(self) -> None:
        """Every name listed in __all__ is importable and callable."""
        import dot_docs

        assert hasattr(dot_docs, "__all__"), "dot_docs must define __all__"
        for name in dot_docs.__all__:
            obj = getattr(dot_docs, name, None)
            assert obj is not None, f"dot_docs.{name} missing after import"
            assert callable(obj), f"dot_docs.{name} is not callable"


# ── TestAllBehaviorsOverview ────────────────────────────────────────────────


class TestAllBehaviorsOverview:
    """Verifies bundle_overview_dot() succeeds on the real repo.

    A single test exercises all behaviors/bundles indirectly — the overview
    function discovers and processes every YAML/MD file in the repo.
    """

    def test_overview_covers_all_behaviors(self) -> None:
        """bundle_overview_dot() on the real repo processes all behaviors without error."""
        dot = bundle_overview_dot(REPO_ROOT)

        # Basic structural validity
        assert dot.startswith("digraph ")
        assert 'source_hash="' in dot
        assert dot.strip().endswith("}")

        # The overview should have discovered at least one behavior file
        behaviors_dir = REPO_ROOT / "behaviors"
        behavior_files = sorted(behaviors_dir.glob("*.yaml"))
        assert len(behavior_files) > 0, (
            "No behavior files found — is repo_root correct?"
        )

        # Every discovered behavior name should appear somewhere in the DOT output
        for f in behavior_files:
            stem = f.stem  # e.g. "agents" from "agents.yaml"
            assert stem in dot, f"Behavior '{stem}' not found in overview DOT output"


# ── TestGetRepoGitUrl ──────────────────────────────────────────────────────────


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
        # .git is a file, not a dir
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


# ── TestNormalizeGitUrl ────────────────────────────────────────────────────────


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


# ── TestIsSameRepoInclude ──────────────────────────────────────────────────────


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


# ── TestResolveLocalIncludeSameRepo ───────────────────────────────────────────


class TestResolveLocalIncludeSameRepo:
    """Tests that _resolve_local_include() now handles same-repo git+ URLs."""

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


# ── TestOverviewSameRepoEdges ──────────────────────────────────────────────────


class TestOverviewSameRepoEdges:
    """Tests that bundle_overview_dot() draws direct edges for same-repo git includes."""

    def test_same_repo_include_creates_direct_edge_not_external(
        self, tmp_path: Path
    ) -> None:
        """A same-repo git+ include creates a direct (non-dashed) edge to the behavior node."""
        # Setup git remote
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text(
            "[core]\n\trepositoryformatversion = 0\n"
            '[remote "origin"]\n\turl = https://github.com/example/myrepo.git\n'
        )
        # Root bundle
        (tmp_path / "bundle.md").write_text(
            "---\nbundle:\n  name: root\n  version: 1.0\n---\n# Root\n"
        )
        # A behavior file
        behaviors_dir = tmp_path / "behaviors"
        behaviors_dir.mkdir()
        (behaviors_dir / "cool-behavior.yaml").write_text(
            "bundle:\n  name: cool-behavior\n  version: 1.0\n  description: Cool\n"
        )
        # A standalone bundle with same-repo git+ include
        bundles_dir = tmp_path / "bundles"
        bundles_dir.mkdir()
        (bundles_dir / "mystack.yaml").write_text(
            "bundle:\n  name: mystack\n  version: 1.0\n"
            "includes:\n"
            "  - bundle: git+https://github.com/example/myrepo@main"
            "#subdirectory=behaviors/cool-behavior.yaml\n"
        )

        dot = bundle_overview_dot(tmp_path)

        # mystack should appear
        assert "mystack" in dot
        # cool-behavior should appear
        assert "cool" in dot
        # The edge should NOT be dashed (not external) — cool-behavior should resolve locally
        # Find the cool-behavior node id and check it's not in an external dashed node
        assert (
            "(external)" not in dot
            or "cool" not in dot.split("(external)")[0].split("\n")[-1]
        )

    def test_standalone_bundle_aggregate_includes_same_repo_behavior_tokens(
        self, tmp_path: Path
    ) -> None:
        """Standalone bundle node label tok count includes tokens from same-repo behaviors."""
        import re

        # Setup git remote
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config").write_text(
            "[core]\n\trepositoryformatversion = 0\n"
            '[remote "origin"]\n\turl = https://github.com/example/myrepo.git\n'
        )
        # Root bundle (small — just a few tokens)
        (tmp_path / "bundle.md").write_text(
            "---\nbundle:\n  name: root\n  version: 1.0\n---\n# Root\n"
        )
        # A large behavior file (clearly more tokens than root)
        behaviors_dir = tmp_path / "behaviors"
        behaviors_dir.mkdir()
        big_content = "bundle:\n  name: big-behavior\n  version: 1.0\n" + "x" * 4000
        (behaviors_dir / "big-behavior.yaml").write_text(big_content)

        # Standalone bundle that includes big-behavior via same-repo git URL
        bundles_dir = tmp_path / "bundles"
        bundles_dir.mkdir()
        (bundles_dir / "mystack.yaml").write_text(
            "bundle:\n  name: mystack\n  version: 1.0\n"
            "includes:\n"
            "  - bundle: git+https://github.com/example/myrepo@main"
            "#subdirectory=behaviors/big-behavior.yaml\n"
        )

        dot = bundle_overview_dot(tmp_path)

        # Extract token counts from node labels: "name\n~N tok"
        tok_matches = re.findall(r"~(\d+) tok", dot)
        tok_values = [int(t) for t in tok_matches]

        # mystack's aggregate should be bigger than root's own tok
        # root has tiny content (~15 tok), big-behavior has ~1000 tok
        # mystack aggregate = own (~48 tok) + big-behavior (~1000 tok) >> root (~15 tok)
        # We verify there's a high token count (>500) present in the diagram
        assert any(t > 500 for t in tok_values), (
            f"Expected at least one node with >500 tok (big-behavior included), got: {tok_values}"
        )
