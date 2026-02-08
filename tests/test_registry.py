"""Tests for BundleRegistry."""

import tempfile
from pathlib import Path

import pytest
from amplifier_foundation.registry import BundleRegistry


class TestFindNearestBundleFile:
    """Tests for _find_nearest_bundle_file method."""

    def test_finds_bundle_md_in_start_directory(self) -> None:
        """Finds bundle.md in the starting directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "bundle.md").write_text("---\nname: root\n---\n# Root")

            registry = BundleRegistry(home=base / "home")
            result = registry._find_nearest_bundle_file(start=base, stop=base)

            assert result == base / "bundle.md"

    def test_finds_bundle_yaml_in_start_directory(self) -> None:
        """Finds bundle.yaml in the starting directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "bundle.yaml").write_text("name: root")

            registry = BundleRegistry(home=base / "home")
            result = registry._find_nearest_bundle_file(start=base, stop=base)

            assert result == base / "bundle.yaml"

    def test_prefers_bundle_md_over_bundle_yaml(self) -> None:
        """When both exist, prefers bundle.md."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "bundle.md").write_text("---\nname: root\n---\n# Root")
            (base / "bundle.yaml").write_text("name: root")

            registry = BundleRegistry(home=base / "home")
            result = registry._find_nearest_bundle_file(start=base, stop=base)

            assert result == base / "bundle.md"

    def test_walks_up_to_find_bundle(self) -> None:
        """Walks up directories to find bundle file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            subdir = base / "behaviors" / "recipes"
            subdir.mkdir(parents=True)

            # Root has bundle.md
            (base / "bundle.md").write_text("---\nname: root\n---\n# Root")

            # Subdir has its own bundle.yaml
            (subdir / "bundle.yaml").write_text("name: recipes")

            registry = BundleRegistry(home=base / "home")

            # Start from subdir parent (behaviors), stop at root (base)
            result = registry._find_nearest_bundle_file(
                start=subdir.parent,  # behaviors
                stop=base,
            )

            # Should find root's bundle.md
            assert result == base / "bundle.md"

    def test_returns_none_when_not_found(self) -> None:
        """Returns None when no bundle file found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            subdir = base / "behaviors" / "recipes"
            subdir.mkdir(parents=True)

            # No bundle files anywhere

            registry = BundleRegistry(home=base / "home")
            result = registry._find_nearest_bundle_file(start=subdir, stop=base)

            assert result is None

    def test_stops_at_stop_directory(self) -> None:
        """Does not search above stop directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # Create nested structure
            repo_root = base / "repo"
            repo_root.mkdir()
            behaviors = repo_root / "behaviors"
            behaviors.mkdir()
            recipes = behaviors / "recipes"
            recipes.mkdir()

            # Put bundle.md at repo_root (outside stop boundary)
            (repo_root / "bundle.md").write_text("---\nname: root\n---")

            registry = BundleRegistry(home=base / "home")

            # Search from recipes to behaviors (stop before repo_root)
            result = registry._find_nearest_bundle_file(
                start=recipes,
                stop=behaviors,
            )

            # Should NOT find repo_root/bundle.md because we stopped at behaviors
            assert result is None


class TestUnregister:
    """Tests for unregister method."""

    def test_unregister_existing_bundle_returns_true(self) -> None:
        """Unregistering an existing bundle returns True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            registry = BundleRegistry(home=base / "home")

            # Register a bundle
            registry.register(
                {"test-bundle": "git+https://github.com/example/test@main"}
            )

            # Unregister should return True
            assert registry.unregister("test-bundle") is True

    def test_unregister_nonexistent_bundle_returns_false(self) -> None:
        """Unregistering a non-existent bundle returns False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            registry = BundleRegistry(home=base / "home")

            # Unregister non-existent bundle should return False
            assert registry.unregister("nonexistent") is False

    def test_unregister_removes_from_list_registered(self) -> None:
        """Unregistered bundles don't appear in list_registered."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            registry = BundleRegistry(home=base / "home")

            # Register bundles
            registry.register(
                {
                    "bundle-a": "git+https://github.com/example/a@main",
                    "bundle-b": "git+https://github.com/example/b@main",
                    "bundle-c": "git+https://github.com/example/c@main",
                }
            )

            # Verify all are registered
            assert sorted(registry.list_registered()) == [
                "bundle-a",
                "bundle-b",
                "bundle-c",
            ]

            # Unregister bundle-b
            registry.unregister("bundle-b")

            # Verify bundle-b is gone
            assert sorted(registry.list_registered()) == ["bundle-a", "bundle-c"]

    def test_unregister_does_not_auto_persist(self) -> None:
        """Unregister does not automatically call save()."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            registry = BundleRegistry(home=base / "home")

            # Register and save
            registry.register(
                {"test-bundle": "git+https://github.com/example/test@main"}
            )
            registry.save()

            # Unregister (without calling save)
            registry.unregister("test-bundle")

            # Create new registry instance - should still have the bundle
            registry2 = BundleRegistry(home=base / "home")
            assert "test-bundle" in registry2.list_registered()

    def test_unregister_cleans_up_includes_relationships(self) -> None:
        """Unregister cleans up includes references in child bundles."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            registry = BundleRegistry(home=base / "home")

            # Register bundles
            registry.register(
                {
                    "parent": "git+https://github.com/example/parent@main",
                    "child-a": "git+https://github.com/example/child-a@main",
                    "child-b": "git+https://github.com/example/child-b@main",
                }
            )

            # Manually set up relationships (simulating what happens after loading)
            parent_state = registry.get_state("parent")
            child_a_state = registry.get_state("child-a")
            child_b_state = registry.get_state("child-b")

            parent_state.includes = ["child-a", "child-b"]
            child_a_state.included_by = ["parent"]
            child_b_state.included_by = ["parent"]

            # Unregister parent
            registry.unregister("parent")

            # Verify parent is gone
            assert "parent" not in registry.list_registered()

            # Verify children no longer reference parent
            assert child_a_state.included_by == []
            assert child_b_state.included_by == []

    def test_unregister_cleans_up_included_by_relationships(self) -> None:
        """Unregister cleans up included_by references in parent bundles."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            registry = BundleRegistry(home=base / "home")

            # Register bundles
            registry.register(
                {
                    "parent-a": "git+https://github.com/example/parent-a@main",
                    "parent-b": "git+https://github.com/example/parent-b@main",
                    "child": "git+https://github.com/example/child@main",
                }
            )

            # Manually set up relationships
            parent_a_state = registry.get_state("parent-a")
            parent_b_state = registry.get_state("parent-b")
            child_state = registry.get_state("child")

            parent_a_state.includes = ["child"]
            parent_b_state.includes = ["child"]
            child_state.included_by = ["parent-a", "parent-b"]

            # Unregister child
            registry.unregister("child")

            # Verify child is gone
            assert "child" not in registry.list_registered()

            # Verify parents no longer reference child
            assert parent_a_state.includes == []
            assert parent_b_state.includes == []

    def test_unregister_handles_partial_relationships(self) -> None:
        """Unregister handles bundles with only some relationships."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            registry = BundleRegistry(home=base / "home")

            # Register bundles
            registry.register(
                {
                    "bundle-a": "git+https://github.com/example/a@main",
                    "bundle-b": "git+https://github.com/example/b@main",
                }
            )

            # Set up partial relationships
            bundle_a_state = registry.get_state("bundle-a")
            bundle_a_state.includes = ["bundle-b"]
            # Note: bundle-b has no included_by set

            # Unregister should not crash
            assert registry.unregister("bundle-a") is True
            assert "bundle-a" not in registry.list_registered()


class TestSubdirectoryBundleLoading:
    """Tests for loading bundles from subdirectories with root access."""

    @pytest.mark.asyncio
    async def test_subdirectory_bundle_gets_source_base_paths(self) -> None:
        """Subdirectory bundle gets source_base_paths populated for root access."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # Create root bundle (bundle.md with frontmatter)
            (base / "bundle.md").write_text(
                "---\nbundle:\n  name: root-bundle\n  version: 1.0.0\n---\n# Root Bundle"
            )

            # Create shared context
            context_dir = base / "context"
            context_dir.mkdir()
            (context_dir / "shared.md").write_text("# Shared Context")

            # Create subdirectory bundle (YAML needs nested bundle: key)
            behaviors = base / "behaviors"
            behaviors.mkdir()
            recipes = behaviors / "recipes"
            recipes.mkdir()
            (recipes / "bundle.yaml").write_text(
                "bundle:\n  name: recipes\n  version: 1.0.0"
            )

            # Create registry and load subdirectory bundle via file source
            registry = BundleRegistry(home=base / "home")

            # Load the subdirectory bundle with a subpath
            # This simulates loading via git+https://...#subdirectory=behaviors/recipes
            bundle = await registry._load_single(
                f"file://{base}#subdirectory=behaviors/recipes"
            )

            # The bundle should have source_base_paths set up
            assert bundle.name == "recipes"
            assert bundle.source_base_paths.get("recipes") == base.resolve()

    @pytest.mark.asyncio
    async def test_root_bundle_no_extra_source_base_paths(self) -> None:
        """Loading root bundle directly doesn't add extra source_base_paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # Create root bundle (bundle.md with frontmatter)
            (base / "bundle.md").write_text(
                "---\nbundle:\n  name: root-bundle\n  version: 1.0.0\n---\n# Root Bundle"
            )

            registry = BundleRegistry(home=base / "home")
            bundle = await registry._load_single(f"file://{base}")

            # When loading root directly (not subdirectory), no extra source_base_paths
            # because active_path == source_root
            assert bundle.name == "root-bundle"
            # source_base_paths should be empty or not contain extra entries
            assert "root-bundle" not in bundle.source_base_paths

    @pytest.mark.asyncio
    async def test_subdirectory_without_root_bundle_no_source_base_paths(self) -> None:
        """Subdirectory without discoverable root bundle doesn't add source_base_paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # No root bundle.md or bundle.yaml

            # Create subdirectory bundle (YAML needs nested bundle: key)
            subdir = base / "components" / "auth"
            subdir.mkdir(parents=True)
            (subdir / "bundle.yaml").write_text(
                "bundle:\n  name: auth\n  version: 1.0.0"
            )

            registry = BundleRegistry(home=base / "home")
            bundle = await registry._load_single(
                f"file://{base}#subdirectory=components/auth"
            )

            # Without a root bundle, source_base_paths won't be populated
            assert bundle.name == "auth"
            assert "auth" not in bundle.source_base_paths


class TestDiamondAndCircularDependencies:
    """Tests for diamond dependency handling and circular dependency detection."""

    @pytest.mark.asyncio
    async def test_diamond_dependency_loads_successfully(self) -> None:
        """Diamond dependencies (A->B->C, A->C) should NOT be flagged as circular.

        Structure:
            A includes [B, C]
            B includes [C]
        This creates a diamond: A->B->C and A->C both reach C.
        C should be loaded only once without errors.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # Create bundle A (includes B and C)
            bundle_a = base / "bundle-a"
            bundle_a.mkdir()
            (bundle_a / "bundle.yaml").write_text(
                "bundle:\n"
                "  name: bundle-a\n"
                "  version: 1.0.0\n"
                "includes:\n"
                f"  - file://{base}/bundle-b\n"
                f"  - file://{base}/bundle-c\n"
            )

            # Create bundle B (includes C)
            bundle_b = base / "bundle-b"
            bundle_b.mkdir()
            (bundle_b / "bundle.yaml").write_text(
                "bundle:\n"
                "  name: bundle-b\n"
                "  version: 1.0.0\n"
                "includes:\n"
                f"  - file://{base}/bundle-c\n"
            )

            # Create bundle C (no includes - leaf node)
            bundle_c = base / "bundle-c"
            bundle_c.mkdir()
            (bundle_c / "bundle.yaml").write_text(
                "bundle:\n  name: bundle-c\n  version: 1.0.0\n"
            )

            # Create registry and load bundle A
            registry = BundleRegistry(home=base / "home")
            bundle = await registry._load_single(f"file://{bundle_a}")

            # Should load successfully without circular dependency error
            assert bundle is not None
            # The composed bundle should have content from all three bundles
            # Bundle A's name wins because it's composed last
            assert bundle.name == "bundle-a"

    @pytest.mark.asyncio
    async def test_circular_dependency_handled_gracefully(self) -> None:
        """True circular (A->B->A) should be detected but handled gracefully.

        Structure:
            A includes [B]
            B includes [A]
        The circular include (B's include of A) is skipped, but loading succeeds.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # Create bundle A (includes B)
            bundle_a = base / "bundle-a"
            bundle_a.mkdir()
            (bundle_a / "bundle.yaml").write_text(
                "bundle:\n"
                "  name: bundle-a\n"
                "  version: 1.0.0\n"
                "includes:\n"
                f"  - file://{base}/bundle-b\n"
            )

            # Create bundle B (includes A - creates circular dependency)
            bundle_b = base / "bundle-b"
            bundle_b.mkdir()
            (bundle_b / "bundle.yaml").write_text(
                "bundle:\n"
                "  name: bundle-b\n"
                "  version: 1.0.0\n"
                "includes:\n"
                f"  - file://{base}/bundle-a\n"
            )

            # Create registry and load bundle A
            registry = BundleRegistry(home=base / "home")

            # Should succeed - circular include is skipped with warning
            bundle = await registry._load_single(f"file://{bundle_a}")

            # Bundle A should load successfully (composed with B)
            assert bundle is not None
            assert bundle.name == "bundle-a"

    @pytest.mark.asyncio
    async def test_bundle_cached_after_first_load(self) -> None:
        """Bundle should be cached and returned from cache on subsequent loads."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # Create a simple bundle
            bundle_dir = base / "test-bundle"
            bundle_dir.mkdir()
            (bundle_dir / "bundle.yaml").write_text(
                "bundle:\n  name: test-bundle\n  version: 1.0.0\n"
            )

            registry = BundleRegistry(home=base / "home")
            uri = f"file://{bundle_dir}"

            # First load
            bundle1 = await registry._load_single(uri)
            assert bundle1.name == "test-bundle"

            # Second load should return cached version
            bundle2 = await registry._load_single(uri)

            # Should be the exact same object (from cache)
            assert bundle1 is bundle2

    @pytest.mark.asyncio
    async def test_three_level_circular_dependency_handled_gracefully(self) -> None:
        """Three-level circular (A->B->C->A) should be detected but handled gracefully.

        Structure:
            A includes [B]
            B includes [C]
            C includes [A]
        The circular include (C's include of A) is skipped, but loading succeeds.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # Create bundle A (includes B)
            bundle_a = base / "bundle-a"
            bundle_a.mkdir()
            (bundle_a / "bundle.yaml").write_text(
                "bundle:\n"
                "  name: bundle-a\n"
                "  version: 1.0.0\n"
                "includes:\n"
                f"  - file://{base}/bundle-b\n"
            )

            # Create bundle B (includes C)
            bundle_b = base / "bundle-b"
            bundle_b.mkdir()
            (bundle_b / "bundle.yaml").write_text(
                "bundle:\n"
                "  name: bundle-b\n"
                "  version: 1.0.0\n"
                "includes:\n"
                f"  - file://{base}/bundle-c\n"
            )

            # Create bundle C (includes A - creates circular dependency)
            bundle_c = base / "bundle-c"
            bundle_c.mkdir()
            (bundle_c / "bundle.yaml").write_text(
                "bundle:\n"
                "  name: bundle-c\n"
                "  version: 1.0.0\n"
                "includes:\n"
                f"  - file://{base}/bundle-a\n"
            )

            registry = BundleRegistry(home=base / "home")

            # Should succeed - circular include is skipped with warning
            bundle = await registry._load_single(f"file://{bundle_a}")

            # Bundle A should load successfully (composed with B and C)
            assert bundle is not None
            assert bundle.name == "bundle-a"

    @pytest.mark.asyncio
    async def test_circular_dependency_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Circular dependency should log a helpful warning message.

        Structure:
            A includes [B]
            B includes [A]
        The warning should include the chain and guidance.
        """
        import logging

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # Create bundle A (includes B)
            bundle_a = base / "bundle-a"
            bundle_a.mkdir()
            (bundle_a / "bundle.yaml").write_text(
                "bundle:\n"
                "  name: bundle-a\n"
                "  version: 1.0.0\n"
                "includes:\n"
                f"  - file://{base}/bundle-b\n"
            )

            # Create bundle B (includes A - creates circular dependency)
            bundle_b = base / "bundle-b"
            bundle_b.mkdir()
            (bundle_b / "bundle.yaml").write_text(
                "bundle:\n"
                "  name: bundle-b\n"
                "  version: 1.0.0\n"
                "includes:\n"
                f"  - file://{base}/bundle-a\n"
            )

            registry = BundleRegistry(home=base / "home")

            # Capture warning logs
            with caplog.at_level(logging.WARNING):
                bundle = await registry._load_single(f"file://{bundle_a}")

            # Should succeed
            assert bundle is not None

            # Should have logged a warning about circular dependency
            warning_messages = [
                r.message for r in caplog.records if r.levelno == logging.WARNING
            ]
            assert any("Circular Include Skipped" in msg for msg in warning_messages)


class TestStrictMode:
    """Tests for strict mode in BundleRegistry."""

    @pytest.mark.asyncio
    async def test_default_non_strict_skips_missing_includes(self) -> None:
        """Default (non-strict) mode logs warnings and succeeds when includes are missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # Create bundle that includes a non-existent bundle
            bundle_dir = base / "test-bundle"
            bundle_dir.mkdir()
            (bundle_dir / "bundle.yaml").write_text(
                "bundle:\n"
                "  name: test-bundle\n"
                "  version: 1.0.0\n"
                "includes:\n"
                "  - nonexistent-namespace:some/path\n"
            )

            registry = BundleRegistry(home=base / "home")
            bundle = await registry._load_single(f"file://{bundle_dir}")

            # Should succeed - missing include is skipped
            assert bundle is not None
            assert bundle.name == "test-bundle"

    @pytest.mark.asyncio
    async def test_strict_mode_raises_on_unresolvable_include(self) -> None:
        """Strict mode raises BundleDependencyError when an include cannot be resolved."""
        from amplifier_foundation.exceptions import BundleDependencyError

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # Create bundle that includes a non-existent namespace
            bundle_dir = base / "test-bundle"
            bundle_dir.mkdir()
            (bundle_dir / "bundle.yaml").write_text(
                "bundle:\n"
                "  name: test-bundle\n"
                "  version: 1.0.0\n"
                "includes:\n"
                "  - nonexistent-namespace:some/path\n"
            )

            registry = BundleRegistry(home=base / "home", strict=True)

            with pytest.raises(BundleDependencyError, match="strict mode"):
                await registry._load_single(f"file://{bundle_dir}")

    @pytest.mark.asyncio
    async def test_strict_mode_succeeds_with_valid_includes(self) -> None:
        """Strict mode does not interfere when all includes resolve successfully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # Create included bundle
            child_dir = base / "child-bundle"
            child_dir.mkdir()
            (child_dir / "bundle.yaml").write_text(
                "bundle:\n  name: child-bundle\n  version: 1.0.0\n"
            )

            # Create parent bundle that includes child via file URI
            parent_dir = base / "parent-bundle"
            parent_dir.mkdir()
            (parent_dir / "bundle.yaml").write_text(
                "bundle:\n"
                "  name: parent-bundle\n"
                "  version: 1.0.0\n"
                "includes:\n"
                f"  - file://{child_dir}\n"
            )

            registry = BundleRegistry(home=base / "home", strict=True)
            bundle = await registry._load_single(f"file://{parent_dir}")

            # Should succeed - all includes are valid
            assert bundle is not None
            assert bundle.name == "parent-bundle"

    def test_strict_defaults_to_false(self) -> None:
        """BundleRegistry strict parameter defaults to False."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            registry = BundleRegistry(home=base / "home")
            assert registry._strict is False

    def test_strict_can_be_set_to_true(self) -> None:
        """BundleRegistry strict parameter can be set to True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            registry = BundleRegistry(home=base / "home", strict=True)
            assert registry._strict is True

    @pytest.mark.asyncio
    async def test_strict_mode_raises_on_include_load_failure(self) -> None:
        """Strict mode raises when a resolved include fails to load (Phase 2).

        A child bundle with broken YAML resolves in Phase 1 (URI is valid)
        but fails to parse in Phase 2 (_load_single raises a non-circular error).
        """
        from amplifier_foundation.exceptions import BundleDependencyError

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # Create a child bundle with broken YAML that will fail to parse
            child_dir = base / "broken-bundle"
            child_dir.mkdir()
            (child_dir / "bundle.yaml").write_text("{{{{ not valid yaml at all")

            # Create parent that includes the child via file URI
            parent_dir = base / "parent-bundle"
            parent_dir.mkdir()
            (parent_dir / "bundle.yaml").write_text(
                "bundle:\n"
                "  name: parent-bundle\n"
                "  version: 1.0.0\n"
                "includes:\n"
                f"  - file://{child_dir}\n"
            )

            registry = BundleRegistry(home=base / "home", strict=True)

            with pytest.raises(BundleDependencyError, match="strict mode"):
                await registry._load_single(f"file://{parent_dir}")

    @pytest.mark.asyncio
    async def test_non_strict_skips_include_load_failure(self) -> None:
        """Non-strict mode logs warning and continues when include fails to load."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # Create a child bundle with broken YAML that will fail to parse
            child_dir = base / "broken-bundle"
            child_dir.mkdir()
            (child_dir / "bundle.yaml").write_text("{{{{ not valid yaml at all")

            # Create parent that includes the child
            parent_dir = base / "parent-bundle"
            parent_dir.mkdir()
            (parent_dir / "bundle.yaml").write_text(
                "bundle:\n"
                "  name: parent-bundle\n"
                "  version: 1.0.0\n"
                "includes:\n"
                f"  - file://{child_dir}\n"
            )

            registry = BundleRegistry(home=base / "home")  # default non-strict
            bundle = await registry._load_single(f"file://{parent_dir}")

            # Should succeed - failed include is skipped
            assert bundle is not None
            assert bundle.name == "parent-bundle"


class TestLoadBundleConvenience:
    """Tests for load_bundle() convenience function."""

    @pytest.mark.asyncio
    async def test_load_bundle_strict_with_registry_raises(self) -> None:
        """Passing strict=True with an existing registry raises ValueError."""
        from amplifier_foundation.registry import load_bundle

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            registry = BundleRegistry(home=base / "home")

            with pytest.raises(ValueError, match="Cannot pass strict=True"):
                await load_bundle("some-bundle", strict=True, registry=registry)

    @pytest.mark.asyncio
    async def test_load_bundle_strict_without_registry_creates_strict_registry(
        self,
    ) -> None:
        """Passing strict=True without registry creates a strict registry."""
        from amplifier_foundation.exceptions import BundleDependencyError
        from amplifier_foundation.registry import load_bundle

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # Create bundle with unresolvable include
            bundle_dir = base / "test-bundle"
            bundle_dir.mkdir()
            (bundle_dir / "bundle.yaml").write_text(
                "bundle:\n"
                "  name: test-bundle\n"
                "  version: 1.0.0\n"
                "includes:\n"
                "  - nonexistent-namespace:some/path\n"
            )

            with pytest.raises(BundleDependencyError, match="strict mode"):
                await load_bundle(f"file://{bundle_dir}", strict=True)
