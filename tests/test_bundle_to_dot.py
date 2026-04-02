"""Tests for bundle_overview_dot (v2 overview) and package-level imports."""

from pathlib import Path


from dot_docs.bundle_to_dot import bundle_overview_dot

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

    def test_import_bundle_detail_md(self) -> None:
        from dot_docs import bundle_detail_md as _fn  # noqa: F401

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
