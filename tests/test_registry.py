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
            registry.register({"test-bundle": "git+https://github.com/example/test@main"})

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
            registry.register({
                "bundle-a": "git+https://github.com/example/a@main",
                "bundle-b": "git+https://github.com/example/b@main",
                "bundle-c": "git+https://github.com/example/c@main",
            })

            # Verify all are registered
            assert sorted(registry.list_registered()) == ["bundle-a", "bundle-b", "bundle-c"]

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
            registry.register({"test-bundle": "git+https://github.com/example/test@main"})
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
            registry.register({
                "parent": "git+https://github.com/example/parent@main",
                "child-a": "git+https://github.com/example/child-a@main",
                "child-b": "git+https://github.com/example/child-b@main",
            })

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
            registry.register({
                "parent-a": "git+https://github.com/example/parent-a@main",
                "parent-b": "git+https://github.com/example/parent-b@main",
                "child": "git+https://github.com/example/child@main",
            })

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
            registry.register({
                "bundle-a": "git+https://github.com/example/a@main",
                "bundle-b": "git+https://github.com/example/b@main",
            })

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
            (base / "bundle.md").write_text("---\nbundle:\n  name: root-bundle\n  version: 1.0.0\n---\n# Root Bundle")

            # Create shared context
            context_dir = base / "context"
            context_dir.mkdir()
            (context_dir / "shared.md").write_text("# Shared Context")

            # Create subdirectory bundle (YAML needs nested bundle: key)
            behaviors = base / "behaviors"
            behaviors.mkdir()
            recipes = behaviors / "recipes"
            recipes.mkdir()
            (recipes / "bundle.yaml").write_text("bundle:\n  name: recipes\n  version: 1.0.0")

            # Create registry and load subdirectory bundle via file source
            registry = BundleRegistry(home=base / "home")

            # Load the subdirectory bundle with a subpath
            # This simulates loading via git+https://...#subdirectory=behaviors/recipes
            bundle = await registry._load_single(f"file://{base}#subdirectory=behaviors/recipes")

            # The bundle should have source_base_paths set up
            assert bundle.name == "recipes"
            assert bundle.source_base_paths.get("recipes") == base.resolve()

    @pytest.mark.asyncio
    async def test_root_bundle_no_extra_source_base_paths(self) -> None:
        """Loading root bundle directly doesn't add extra source_base_paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # Create root bundle (bundle.md with frontmatter)
            (base / "bundle.md").write_text("---\nbundle:\n  name: root-bundle\n  version: 1.0.0\n---\n# Root Bundle")

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
            (subdir / "bundle.yaml").write_text("bundle:\n  name: auth\n  version: 1.0.0")

            registry = BundleRegistry(home=base / "home")
            bundle = await registry._load_single(f"file://{base}#subdirectory=components/auth")

            # Without a root bundle, source_base_paths won't be populated
            assert bundle.name == "auth"
            assert "auth" not in bundle.source_base_paths
