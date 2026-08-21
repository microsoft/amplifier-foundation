"""Relative bundle `includes:` must resolve against the DECLARING bundle.

Regression oracle for: BundleRegistry resolved relative includes
(``includes: - bundle: ./base.md``) against a single ``Path.cwd()``
snapshot taken once at registry-construction time (the CLI process's
invocation directory), instead of against the INCLUDING bundle file's own
directory.

Confirmed repro (see registry.py:_source_resolver construction and
sources/file.py:FileSourceHandler.resolve):

    $ cd <repo>/bundles && load child.md   -> works (cwd happens to match)
    $ cd /tmp/anywhere  && load child.md   -> "Include Failed (skipping):
                                              File not found: /tmp/anywhere/base.md"
                                              composed bundle is EMPTY

The failure does not raise. It produces a bundle that "loads successfully"
but is empty, which detonates much later and elsewhere as an unrelated
``ValueError: Configuration must specify session.orchestrator``.

Fix: anchor literal relative include sources ("./x", "../x") to the
DECLARING bundle's own ``base_path`` (registry.py:
``_anchor_relative_include_source`` / ``_compose_includes``) before they
reach ``_load_single``. Mirrors the sibling fix in ``bundle/_dataclass.py``
(commit b667815), which anchored relative ``session``/``providers``/
``tools``/``hooks`` ``source:`` fields to the declaring bundle's
``base_path`` instead of the app's.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pytest
from amplifier_foundation.registry import (
    BundleRegistry,
    _anchor_relative_include_source,
)


def _write_child_and_base(bundles_dir: Path) -> tuple[Path, Path]:
    """Create bundles_dir/child.md (includes ./base.md) and bundles_dir/base.md."""
    bundles_dir.mkdir(parents=True, exist_ok=True)

    child = bundles_dir / "child.md"
    child.write_text(
        "---\nbundle:\n  name: child\nincludes:\n  - bundle: ./base.md\n---\n# Child\n",
        encoding="utf-8",
    )

    base_file = bundles_dir / "base.md"
    base_file.write_text(
        "---\n"
        "bundle:\n"
        "  name: base\n"
        "session:\n"
        "  orchestrator: loop-basic\n"
        "tools:\n"
        "  - module: tool-example\n"
        "hooks:\n"
        "  - module: hook-example\n"
        "---\n"
        "# Base\n",
        encoding="utf-8",
    )
    return child, base_file


class TestIncludeResolvesAgainstDeclaringBundle:
    """BundleRegistry._compose_includes anchors relative includes correctly."""

    @pytest.mark.asyncio
    async def test_include_resolves_when_cwd_matches_bundle_dir(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Guard existing behavior: cwd == bundle's own directory still works."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir).resolve()
            bundles_dir = base / "bundles"
            child, _base_file = _write_child_and_base(bundles_dir)

            monkeypatch.chdir(bundles_dir)
            registry = BundleRegistry(home=base / "home")

            bundle = await registry._load_single(f"file://{child}")

            assert bundle.name == "child"
            assert bundle.session.get("orchestrator") == "loop-basic"
            assert any(t.get("module") == "tool-example" for t in bundle.tools)
            assert any(h.get("module") == "hook-example" for h in bundle.hooks)

    @pytest.mark.asyncio
    async def test_include_resolves_when_cwd_is_unrelated(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """THE BUG: cwd elsewhere at registry-construction time must not matter.

        Before the fix, the include ('./base.md') was resolved against the
        registry's Path.cwd() snapshot (captured once in __init__), not
        against child.md's own directory -- so this raised "File not found"
        and the composed bundle silently ended up with an empty session/
        tools/hooks instead of failing loudly.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir).resolve()
            bundles_dir = base / "bundles"
            child, _base_file = _write_child_and_base(bundles_dir)

            unrelated_cwd = base / "somewhere" / "else"
            unrelated_cwd.mkdir(parents=True)
            monkeypatch.chdir(unrelated_cwd)

            registry = BundleRegistry(home=base / "home")

            bundle = await registry._load_single(f"file://{child}")

            assert bundle.name == "child"
            assert bundle.session.get("orchestrator") == "loop-basic", (
                "base.md's session was not composed in -- include failed to "
                "resolve relative to the declaring bundle's own directory"
            )
            assert any(t.get("module") == "tool-example" for t in bundle.tools)
            assert any(h.get("module") == "hook-example" for h in bundle.hooks)

    @pytest.mark.asyncio
    async def test_nested_includes_each_anchor_to_their_own_directory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """C includes B includes A, each in a different directory.

        Loaded from a FOURTH, unrelated directory. Each bundle's relative
        include must resolve against its OWN directory, independent of the
        others and independent of cwd.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir).resolve()

            dir_a = base / "dir-a"
            dir_a.mkdir()
            (dir_a / "a.md").write_text(
                "---\n"
                "bundle:\n"
                "  name: bundle-a\n"
                "tools:\n"
                "  - module: tool-a\n"
                "---\n"
                "# A\n",
                encoding="utf-8",
            )

            # b.md includes a.md via a RELATIVE sibling-directory path -- this
            # must resolve against dir_b (b's own directory), not dir_c, not
            # the fourth_dir cwd, and not dir_a directly.
            dir_b = base / "dir-b"
            dir_b.mkdir()
            (dir_b / "b.md").write_text(
                "---\n"
                "bundle:\n"
                "  name: bundle-b\n"
                "includes:\n"
                "  - bundle: ../dir-a/a.md\n"
                "tools:\n"
                "  - module: tool-b\n"
                "---\n"
                "# B\n",
                encoding="utf-8",
            )

            # c.md includes b.md via a RELATIVE sibling-directory path -- this
            # must resolve against dir_c (c's own directory).
            dir_c = base / "dir-c"
            dir_c.mkdir()
            (dir_c / "c.md").write_text(
                "---\n"
                "bundle:\n"
                "  name: bundle-c\n"
                "includes:\n"
                "  - bundle: ../dir-b/b.md\n"
                "tools:\n"
                "  - module: tool-c\n"
                "---\n"
                "# C\n",
                encoding="utf-8",
            )

            fourth_dir = base / "fourth"
            fourth_dir.mkdir()
            monkeypatch.chdir(fourth_dir)

            registry = BundleRegistry(home=base / "home")
            bundle = await registry._load_single(f"file://{dir_c}/c.md")

            assert bundle.name == "bundle-c"
            module_names = {t.get("module") for t in bundle.tools}
            assert module_names == {"tool-a", "tool-b", "tool-c"}, module_names

    @pytest.mark.asyncio
    async def test_dotdot_include_resolves_against_declaring_bundle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """'../' includes work: bundle in bundles/ includes ../shared/x.md."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir).resolve()

            shared_dir = base / "shared"
            shared_dir.mkdir()
            (shared_dir / "x.md").write_text(
                "---\n"
                "bundle:\n"
                "  name: shared-x\n"
                "tools:\n"
                "  - module: tool-shared\n"
                "---\n"
                "# Shared\n",
                encoding="utf-8",
            )

            bundles_dir = base / "bundles"
            bundles_dir.mkdir()
            (bundles_dir / "main.md").write_text(
                "---\n"
                "bundle:\n"
                "  name: main\n"
                "includes:\n"
                "  - bundle: ../shared/x.md\n"
                "---\n"
                "# Main\n",
                encoding="utf-8",
            )

            unrelated_cwd = base / "unrelated"
            unrelated_cwd.mkdir()
            monkeypatch.chdir(unrelated_cwd)

            registry = BundleRegistry(home=base / "home")
            bundle = await registry._load_single(f"file://{bundles_dir}/main.md")

            assert bundle.name == "main"
            assert any(t.get("module") == "tool-shared" for t in bundle.tools)

    @pytest.mark.asyncio
    async def test_missing_include_message_names_bundle_and_base_path(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A genuinely missing include still fails (non-strict: warns, doesn't raise).

        The warning must name the INCLUDING bundle and the base_path used
        for resolution -- not just the missing target -- so the failure is
        actionable instead of a bare 'File not found: <path>'.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir).resolve()
            bundles_dir = base / "bundles"
            bundles_dir.mkdir()
            (bundles_dir / "child.md").write_text(
                "---\n"
                "bundle:\n"
                "  name: child\n"
                "includes:\n"
                "  - bundle: ./does-not-exist.md\n"
                "---\n"
                "# Child\n",
                encoding="utf-8",
            )

            monkeypatch.chdir(bundles_dir)
            registry = BundleRegistry(home=base / "home")

            with caplog.at_level(logging.WARNING):
                bundle = await registry._load_single(f"file://{bundles_dir}/child.md")

            # Non-strict mode: doesn't raise, but doesn't silently succeed either.
            assert bundle.name == "child"
            assert not bundle.session
            assert not bundle.tools

            warning_text = "\n".join(caplog.messages)
            assert "child" in warning_text, warning_text
            assert str(bundles_dir) in warning_text, warning_text


class TestNonRelativeIncludesUnaffected:
    """Namespace refs, git+, and file:// includes must never be rewritten."""

    def test_git_uri_untouched(self) -> None:
        source = "git+https://github.com/microsoft/x@main"
        assert _anchor_relative_include_source(source, Path("/some/base")) == source

    def test_absolute_file_uri_untouched(self) -> None:
        source = "file:///abs/path/to/bundle.yaml"
        assert _anchor_relative_include_source(source, Path("/some/base")) == source

    def test_namespace_ref_untouched(self) -> None:
        source = "foundation:behaviors/logging"
        assert _anchor_relative_include_source(source, Path("/some/base")) == source

    def test_no_base_path_falls_back_untouched(self) -> None:
        """When the declaring bundle has no base_path, leave source as-is."""
        source = "./base.md"
        assert _anchor_relative_include_source(source, None) == source

    def test_relative_source_is_anchored(self) -> None:
        base_path = Path("/some/base")
        assert _anchor_relative_include_source("./base.md", base_path) == str(
            (base_path / "./base.md").resolve()
        )
        assert _anchor_relative_include_source("../shared/x.md", base_path) == str(
            (base_path / "../shared/x.md").resolve()
        )
