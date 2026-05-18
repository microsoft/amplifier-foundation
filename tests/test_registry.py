"""Tests for BundleRegistry."""

import tempfile
from pathlib import Path

import pytest
from amplifier_foundation.registry import BundleRegistry, BundleState


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

            # The bundle should have source_base_paths set up.
            # The 'recipes' namespace must resolve to its own directory (behaviors/recipes/),
            # NOT to the checkout root.  Agents and context for 'recipes:' live under
            # behaviors/recipes/agents/ and behaviors/recipes/context/, not root/agents/.
            assert bundle.name == "recipes"
            assert (
                bundle.source_base_paths.get("recipes")
                == (base / "behaviors" / "recipes").resolve()
            )

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

    @pytest.mark.asyncio
    async def test_behavior_namespace_maps_to_subbundle_root_not_checkout_root(
        self,
    ) -> None:
        """Behavior YAML's namespace must map to the sub-bundle root, not the checkout root.

        Regression test for: when a bundle at {checkout}/sub/ includes a behavior YAML at
        {checkout}/sub/behaviors/ that declares bundle.name='subns', the registry was setting
        source_base_paths['subns'] = {checkout}/ (checkout root) via resolved.source_root
        instead of {checkout}/sub/ (the sub-bundle's own directory).

        This caused:
          - resolve_agent_path('subns:foo') to return the WRONG agent file from the checkout
            root's agents/ directory instead of sub/agents/
          - load_agent_metadata() to populate agents with the wrong tools list

        The concrete production failure: build-up:coder was loading foundation:explorer's
        tool list because source_base_paths['build-up'] pointed at the foundation checkout root
        (where foundation's own explorer.md lives at agents/explorer.md) instead of
        experiments/build-up/ (where build-up:explorer.md lives at agents/explorer.md).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            # Checkout root — simulates the foundation git repo root
            (root / "bundle.md").write_text(
                "---\nbundle:\n  name: foundation-root\n  version: 1.0.0\n---\n# Root"
            )

            # Decoy agent at the ROOT level — simulates foundation's own foo.md.
            # With the bug, resolve_agent_path('subns:foo') returns THIS wrong file.
            (root / "agents").mkdir()
            (root / "agents" / "foo.md").write_text(
                "---\nmeta:\n  name: wrong-foo\n  description: Decoy (wrong file)\n---\n# Wrong\n"
            )

            # Sub-bundle directory — simulates experiments/build-up/
            sub = root / "sub"
            sub.mkdir()
            (sub / "agents").mkdir()
            (sub / "behaviors").mkdir()

            # Correct agent at the SUB-BUNDLE level — the right file we expect
            (sub / "agents" / "foo.md").write_text(
                "---\n"
                "meta:\n"
                "  name: foo\n"
                "  description: Correct agent\n"
                "tools:\n"
                "  - module: tool-bash\n"
                "    source: git+https://github.com/example/tool-bash@main\n"
                "---\n"
                "# Foo\n"
            )

            behavior_path = sub / "behaviors" / "config.yaml"

            # Sub-bundle main file — simulates build-up-foundation.md
            # Includes the behavior YAML via a direct file:// URI.
            (sub / "bundle-main.md").write_text(
                "---\n"
                "bundle:\n"
                "  name: bundle-main\n"
                "  version: 1.0.0\n"
                "includes:\n"
                f'  - "file://{behavior_path}"\n'
                "---\n"
            )

            # Behavior YAML — simulates build-up-foundation.yaml with bundle.name: build-up.
            # Note: this file lives in behaviors/ subdirectory, but the 'subns' namespace
            # resources (agents/, context/) live at the parent sub/ level.
            # namespace_root: .. declares this explicitly (the explicit-declaration fix).
            behavior_path.write_text(
                "bundle:\n"
                "  name: subns\n"
                "  version: 1.0.0\n"
                "  namespace_root: ..\n"
                "agents:\n"
                "  include:\n"
                "    - subns:foo\n"
            )

            registry = BundleRegistry(home=root / "home")
            bundle = await registry._load_single(
                f"file://{root}#subdirectory=sub/bundle-main.md"
            )

            # ASSERTION 1: 'subns' namespace must resolve to sub/ (not root/)
            # Bug: resolved.source_root (root/) was used instead of bundle.base_path (sub/)
            assert bundle.source_base_paths.get("subns") == sub.resolve(), (
                f"source_base_paths['subns'] = {bundle.source_base_paths.get('subns')!r}, "
                f"expected {sub.resolve()!r} — was it set to the checkout root instead?"
            )

            # ASSERTION 2: agent resolution must return the correct file from sub/agents/
            # Bug: with source_base_paths['subns'] = root/, it returned root/agents/foo.md (decoy)
            bundle.load_agent_metadata()
            agent_path = bundle.resolve_agent_path("subns:foo")
            assert agent_path is not None, (
                "resolve_agent_path('subns:foo') returned None"
            )
            assert agent_path.resolve() == (sub / "agents" / "foo.md").resolve(), (
                f"resolve_agent_path('subns:foo') = {agent_path!r}, "
                f"expected {(sub / 'agents' / 'foo.md').resolve()!r} — got the checkout-root decoy?"
            )

            # ASSERTION 3: agent tools must come from the correct file (sub/agents/foo.md)
            # Bug: loaded decoy which has no tools, so agent had empty tool list
            assert "subns:foo" in bundle.agents, (
                f"'subns:foo' not in bundle.agents after load_agent_metadata(); "
                f"agents = {list(bundle.agents.keys())}"
            )
            agent_data = bundle.agents["subns:foo"]
            assert "tools" in agent_data, (
                f"No 'tools' key in agent data — loaded from wrong file? "
                f"agent_data = {agent_data}"
            )

    @pytest.mark.asyncio
    async def test_namespace_root_field_is_parsed_from_yaml(self) -> None:
        """namespace_root declared in bundle: YAML block is parsed onto the Bundle.

        TDD gate: this test fails before the namespace_root field and from_dict
        parsing are implemented, then passes after.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "bundle.md").write_text(
                "---\nbundle:\n  name: root\n  version: 1.0.0\n---\n"
            )
            sub = root / "sub"
            sub.mkdir()
            sub_behaviors = sub / "behaviors"
            sub_behaviors.mkdir()
            (sub_behaviors / "config.yaml").write_text(
                "bundle:\n  name: myns\n  version: 1.0.0\n  namespace_root: ..\n"
            )

            from amplifier_foundation.bundle._dataclass import Bundle

            bundle = Bundle.from_dict(
                {
                    "bundle": {
                        "name": "myns",
                        "version": "1.0.0",
                        "namespace_root": "..",
                    }
                },
                base_path=sub_behaviors,
            )

            # namespace_root must be parsed and stored on the Bundle
            assert bundle.namespace_root == "..", (
                f"Expected bundle.namespace_root == '..', got {bundle.namespace_root!r}. "
                "The namespace_root field needs to be added to Bundle and parsed in from_dict()."
            )

    @pytest.mark.asyncio
    async def test_namespace_root_absent_uses_yaml_directory(self) -> None:
        """Without namespace_root, source_base_paths[name] = YAML file's own directory.

        The default (Fix 1) behavior: a behavior YAML's namespace resolves to its own
        directory. Bundle authors whose agents/ live in the same directory as the YAML
        don't need namespace_root at all.

        NOTE: 3+ level nesting (namespace_root pointing above the immediate parent) is
        explicitly out of scope for this release. Only single-level '..' is supported and
        tested here.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "bundle.md").write_text(
                "---\nbundle:\n  name: root\n  version: 1.0.0\n---\n"
            )
            sub = root / "sub"
            sub.mkdir()
            sub_behaviors = sub / "behaviors"
            sub_behaviors.mkdir()

            # agents/ lives in behaviors/ (same directory as the YAML — no namespace_root needed)
            (sub_behaviors / "agents").mkdir()
            (sub_behaviors / "agents" / "bar.md").write_text(
                "---\nmeta:\n  name: bar\n---\n# Bar\n"
            )

            (sub_behaviors / "config.yaml").write_text(
                "bundle:\n"
                "  name: subns\n"
                "  version: 1.0.0\n"
                # deliberately no namespace_root
                "agents:\n"
                "  include:\n"
                "    - subns:bar\n"
            )

            registry = BundleRegistry(home=root / "home")
            bundle = await registry._load_single(
                f"file://{root}#subdirectory=sub/behaviors/config.yaml"
            )

            # Without namespace_root, namespace resolves to the YAML's own directory
            assert bundle.source_base_paths.get("subns") == sub_behaviors.resolve(), (
                f"Expected source_base_paths['subns'] = {sub_behaviors.resolve()!r}, "
                f"got {bundle.source_base_paths.get('subns')!r}. "
                "Default (no namespace_root) must use the YAML file's own directory."
            )

            # Agent at behaviors/agents/bar.md resolves correctly with default
            agent_path = bundle.resolve_agent_path("subns:bar")
            assert agent_path is not None
            assert (
                agent_path.resolve() == (sub_behaviors / "agents" / "bar.md").resolve()
            ), (
                f"Expected {(sub_behaviors / 'agents' / 'bar.md').resolve()!r}, "
                f"got {agent_path!r}"
            )

    @pytest.mark.asyncio
    async def test_top_level_bundle_namespace_resolution_unaffected(self) -> None:
        """Fix 1 (registry.py: source_root → base_path) does not affect top-level bundles.

        The registry only sets source_base_paths[bundle.name] for *nested* bundles
        (bundles where bundle.name != root_bundle.name).  This test proves that Fix 1's
        change leaves top-level (root) bundles — the foundation:* / recipes:* style —
        fully unaffected:
          - source_base_paths[bundle.name] is not set by the registry for root bundles
          - resolve_agent_path('name:foo') still works for root bundles via the
            namespace == self.name fallback path in resolve_agent_path()

        This is the regression guard for the foundation:bug-hunter style reference.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # Top-level root bundle — simulates the foundation repo root
            (base / "agents").mkdir()
            (base / "agents" / "bug-hunter.md").write_text(
                "---\nmeta:\n  name: bug-hunter\n  description: The bug hunter agent\n---\n# Bug Hunter\n"
            )
            (base / "bundle.md").write_text(
                "---\nbundle:\n  name: foundation\n  version: 1.0.0\n---\n# Foundation\n"
            )

            registry = BundleRegistry(home=base / "home")
            # Load the root bundle directly (not via #subdirectory=)
            bundle = await registry._load_single(f"file://{base}")

            # Registry does NOT populate source_base_paths[name] for root bundles
            # (the nested-bundle code path in registry.py is not reached)
            assert "foundation" not in bundle.source_base_paths, (
                "Top-level bundle should not have source_base_paths['foundation'] set "
                "by the registry. Fix 1 may have unintentionally affected root bundles."
            )

            # resolve_agent_path still works via the 'namespace == self.name' fallback
            agent_path = bundle.resolve_agent_path("foundation:bug-hunter")
            assert agent_path is not None, (
                "resolve_agent_path('foundation:bug-hunter') returned None for a root bundle. "
                "The namespace==self.name fallback in resolve_agent_path() is broken."
            )
            assert (
                agent_path.resolve() == (base / "agents" / "bug-hunter.md").resolve()
            ), (
                f"Expected {(base / 'agents' / 'bug-hunter.md').resolve()!r}, "
                f"got {agent_path!r}"
            )

    @pytest.mark.asyncio
    async def test_context_paths_resolve_via_namespace_root(self) -> None:
        """Context path resolution uses source_base_paths set by namespace_root.

        Regression test that the same source_base_paths fix that corrects agent
        resolution also fixes context path resolution.  Same decoy-file pattern:

          - Decoy context file at behaviors/context/doc.md (YAML's own directory)
          - Real context file at sub/context/doc.md (namespace_root parent)
          - namespace_root: .. declared in the YAML

        After loading and calling resolve_pending_context(), the bundle's context
        map must point to the real file, not the decoy.

        NOTE: 3+ level nesting is explicitly out of scope (not tested here).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)

            # Checkout root bundle
            (root / "bundle.md").write_text(
                "---\nbundle:\n  name: root\n  version: 1.0.0\n---\n"
            )

            sub = root / "sub"
            sub.mkdir()

            # Real context file at sub/context/ (where namespace_root points)
            (sub / "context").mkdir()
            (sub / "context" / "doc.md").write_text("# Real document\n")

            # Sub-bundle main file
            behavior_path = sub / "behaviors" / "config.yaml"
            (sub / "behaviors").mkdir()
            (sub / "bundle-main.md").write_text(
                "---\n"
                "bundle:\n"
                "  name: main\n"
                "  version: 1.0.0\n"
                "includes:\n"
                f'  - "file://{behavior_path}"\n'
                "---\n"
            )

            # Decoy context file at behaviors/context/ (wrong location)
            (sub / "behaviors" / "context").mkdir()
            (sub / "behaviors" / "context" / "doc.md").write_text("# Decoy document\n")

            # Behavior YAML with namespace_root: .. and a pending context include
            behavior_path.write_text(
                "bundle:\n"
                "  name: subns\n"
                "  version: 1.0.0\n"
                "  namespace_root: ..\n"
                "context:\n"
                "  include:\n"
                "    - subns:context/doc.md\n"  # pending — resolved via source_base_paths
            )

            registry = BundleRegistry(home=root / "home")
            bundle = await registry._load_single(
                f"file://{root}#subdirectory=sub/bundle-main.md"
            )

            # Resolve pending context (requires source_base_paths to be correct)
            bundle.resolve_pending_context()

            # The context entry for 'subns:context/doc.md' must resolve to
            # sub/context/doc.md (via namespace_root), NOT to behaviors/context/doc.md (decoy)
            context_key = "subns:context/doc.md"
            assert context_key in bundle.context, (
                f"'{context_key}' not found in bundle.context after resolve_pending_context(). "
                f"Available context keys: {list(bundle.context.keys())}"
            )
            resolved = bundle.context[context_key]
            assert resolved.resolve() == (sub / "context" / "doc.md").resolve(), (
                f"Context resolved to {resolved!r}, "
                f"expected {(sub / 'context' / 'doc.md').resolve()!r} (real file). "
                "Did it resolve to the decoy at behaviors/context/doc.md instead?"
            )


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


class TestNestedBundleURIUpdate:
    """Tests for URI update when a nested bundle is reloaded from a different source.

    Regression tests for https://github.com/microsoft-amplifier/amplifier-support/issues/62
    When a parent bundle is overridden to a local path, nested bundles previously
    persisted with git+ URIs must have their registry entries updated.
    """

    @pytest.mark.asyncio
    async def test_persisted_nested_bundle_uri_updates_on_reload(self) -> None:
        """Registry entry URI updates when same bundle loads from a different URI."""
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            home = base / "home"

            # Create local bundle files (the "new" local source)
            local_repo = base / "local-repo"
            local_repo.mkdir()
            (local_repo / "bundle.md").write_text(
                "---\nbundle:\n  name: my-namespace\n  version: 2.0.0\n---\n# Root"
            )
            nested = local_repo / "behaviors" / "my-nested"
            nested.mkdir(parents=True)
            (nested / "bundle.yaml").write_text(
                "bundle:\n  name: my-nested\n  version: 2.0.0\n"
            )

            # Pre-populate registry.json with old git+ URI for the nested bundle
            home.mkdir(parents=True, exist_ok=True)
            old_git_uri = (
                "git+https://github.com/example/my-namespace@main"
                "#subdirectory=behaviors/my-nested"
            )
            registry_data = {
                "version": 1,
                "bundles": {
                    "my-nested": {
                        "uri": old_git_uri,
                        "name": "my-nested",
                        "version": "1.0.0",
                        "loaded_at": None,
                        "checked_at": None,
                        "local_path": None,
                        "is_root": False,
                        "root_name": "my-namespace",
                        "explicitly_requested": False,
                        "app_bundle": False,
                    }
                },
            }
            (home / "registry.json").write_text(json.dumps(registry_data, indent=2))

            # Create registry (loads persisted state)
            registry = BundleRegistry(home=home)

            # Verify old URI is loaded from persisted state
            old_state = registry.get_state("my-nested")
            assert old_state is not None
            assert old_state.uri == old_git_uri

            # Load the same nested bundle from a new file:// URI
            new_uri = f"file://{local_repo}#subdirectory=behaviors/my-nested"
            bundle = await registry._load_single(new_uri)

            # Verify the registry entry's URI got updated
            updated_state = registry.get_state("my-nested")
            assert updated_state is not None
            assert updated_state.uri == new_uri
            assert "git+" not in updated_state.uri
            assert bundle.name == "my-nested"

    @pytest.mark.asyncio
    async def test_persisted_uri_update_survives_save_reload(self) -> None:
        """Updated URI persists across registry save/load cycles."""
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            home = base / "home"

            # Create local bundle files
            local_repo = base / "local-repo"
            local_repo.mkdir()
            (local_repo / "bundle.md").write_text(
                "---\nbundle:\n  name: my-namespace\n  version: 2.0.0\n---\n# Root"
            )
            nested = local_repo / "behaviors" / "my-nested"
            nested.mkdir(parents=True)
            (nested / "bundle.yaml").write_text(
                "bundle:\n  name: my-nested\n  version: 2.0.0\n"
            )

            # Pre-populate registry.json with old git+ URI
            home.mkdir(parents=True, exist_ok=True)
            old_git_uri = (
                "git+https://github.com/example/my-namespace@main"
                "#subdirectory=behaviors/my-nested"
            )
            registry_data = {
                "version": 1,
                "bundles": {
                    "my-nested": {
                        "uri": old_git_uri,
                        "name": "my-nested",
                        "version": "1.0.0",
                        "loaded_at": None,
                        "checked_at": None,
                        "local_path": None,
                        "is_root": False,
                        "root_name": "my-namespace",
                        "explicitly_requested": False,
                        "app_bundle": False,
                    }
                },
            }
            (home / "registry.json").write_text(json.dumps(registry_data, indent=2))

            # Load from new URI to trigger update
            registry = BundleRegistry(home=home)
            new_uri = f"file://{local_repo}#subdirectory=behaviors/my-nested"
            await registry._load_single(new_uri)

            # Save and reload into a fresh registry
            registry.save()
            registry2 = BundleRegistry(home=home)

            # Verify the new URI persisted
            state = registry2.get_state("my-nested")
            assert state is not None
            assert state.uri == new_uri
            assert "git+" not in state.uri

    @pytest.mark.asyncio
    async def test_same_uri_reload_does_not_change_entry(self) -> None:
        """Reloading a bundle from the same URI does not alter the entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            home = base / "home"

            # Create bundle files
            bundle_dir = base / "my-bundle"
            bundle_dir.mkdir()
            (bundle_dir / "bundle.yaml").write_text(
                "bundle:\n  name: my-bundle\n  version: 1.0.0\n"
            )

            uri = f"file://{bundle_dir}"
            registry = BundleRegistry(home=home)

            # First load
            await registry._load_single(uri)
            state1 = registry.get_state("my-bundle")
            assert state1 is not None
            assert state1.uri == uri

            # Second load with same URI
            await registry._load_single(uri)
            state2 = registry.get_state("my-bundle")
            assert state2 is not None
            assert state2.uri == uri


class TestRootURIPreservedOnNameCollision:
    """Tests that root registry entry is preserved when behavior shares the same name.

    Regression tests for the thin bundle pattern where root bundle.md and
    behaviors/X.yaml both declare the same bundle.name. The root URI must
    be preserved in the registry for update tracking, even when the behavior
    loads afterward and triggers the "update state" block.
    """

    @pytest.mark.asyncio
    async def test_root_uri_not_overwritten_by_same_name_behavior(self) -> None:
        """Root URI preserved when behavior with same name loads via subdirectory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # Create root bundle with name "issues"
            (base / "bundle.md").write_text(
                "---\nbundle:\n  name: issues\n  version: 1.0.0\n---\n# Root"
            )

            # Create behavior with SAME name "issues" in subdirectory
            behaviors = base / "behaviors"
            behaviors.mkdir()
            (behaviors / "issues.yaml").write_text(
                "bundle:\n  name: issues\n  version: 1.0.0\n"
            )

            registry = BundleRegistry(home=base / "home")
            root_uri = f"file://{base}"
            subdirectory_uri = f"file://{base}#subdirectory=behaviors/issues.yaml"

            # Load root first (simulates full bundle install)
            await registry._load_single(root_uri)

            # Verify root registered correctly
            state = registry.get_state("issues")
            assert state is not None
            assert state.is_root is True
            assert "#subdirectory=" not in state.uri

            # Now load the behavior (simulates include processing)
            await registry._load_single(subdirectory_uri)

            # Root URI must be preserved — not overwritten by behavior's URI
            state_after = registry.get_state("issues")
            assert state_after is not None
            assert state_after.is_root is True
            assert "#subdirectory=" not in state_after.uri

    @pytest.mark.asyncio
    async def test_behavior_only_install_preserves_auto_detected_root(self) -> None:
        """When only behavior is loaded, auto-detected root URI is preserved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # Create root bundle with name "issues"
            (base / "bundle.md").write_text(
                "---\nbundle:\n  name: issues\n  version: 1.0.0\n---\n# Root"
            )

            # Create behavior with SAME name "issues" in subdirectory
            behaviors = base / "behaviors"
            behaviors.mkdir()
            (behaviors / "issues.yaml").write_text(
                "bundle:\n  name: issues\n  version: 1.0.0\n"
            )

            registry = BundleRegistry(home=base / "home")
            subdirectory_uri = f"file://{base}#subdirectory=behaviors/issues.yaml"

            # Load ONLY the behavior (simulates behavior-only install)
            # Root auto-detection at line 435 should find bundle.md and register it
            await registry._load_single(subdirectory_uri)

            # The auto-detected root URI should be preserved
            state = registry.get_state("issues")
            assert state is not None
            assert state.is_root is True
            assert "#subdirectory=" not in state.uri

    @pytest.mark.asyncio
    async def test_different_name_behavior_still_updates_normally(self) -> None:
        """Behavior with a DIFFERENT name from root still updates its own entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)

            # Create root bundle with name "my-bundle"
            (base / "bundle.md").write_text(
                "---\nbundle:\n  name: my-bundle\n  version: 1.0.0\n---\n# Root"
            )

            # Create behavior with DIFFERENT name "my-behavior"
            behaviors = base / "behaviors"
            behaviors.mkdir()
            (behaviors / "my-behavior.yaml").write_text(
                "bundle:\n  name: my-behavior\n  version: 1.0.0\n"
            )

            registry = BundleRegistry(home=base / "home")
            subdirectory_uri = f"file://{base}#subdirectory=behaviors/my-behavior.yaml"

            # Load behavior (different name from root — no collision)
            await registry._load_single(subdirectory_uri)

            # Root should be registered under its own name
            root_state = registry.get_state("my-bundle")
            assert root_state is not None
            assert root_state.is_root is True

            # Behavior should be registered under its own name with subdirectory URI
            behavior_state = registry.get_state("my-behavior")
            assert behavior_state is not None


class TestIncludeSourceResolver:
    """Tests for include_source_resolver callback support."""

    def test_default_no_include_source_resolver(self) -> None:
        """By default, _include_source_resolver is None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = BundleRegistry(home=Path(tmpdir) / "home")
            assert registry._include_source_resolver is None

    def test_init_with_include_source_resolver(self) -> None:
        """Constructor stores include_source_resolver callback."""

        def my_resolver(source: str) -> str | None:
            return f"resolved://{source}"

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = BundleRegistry(
                home=Path(tmpdir) / "home",
                include_source_resolver=my_resolver,
            )
            assert registry._include_source_resolver is my_resolver

    def test_set_include_source_resolver_stores_callback(self) -> None:
        """set_include_source_resolver stores callback that is invoked during include resolution."""
        resolved_calls: list[str] = []

        def my_resolver(source: str) -> str | None:
            resolved_calls.append(source)
            return f"resolved://{source}"

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = BundleRegistry(home=Path(tmpdir) / "home")
            registry.set_include_source_resolver(my_resolver)

            # When resolver is set, _resolve_include_source should invoke it
            registry._resolve_include_source("some-namespace:some/path")
            assert "some-namespace:some/path" in resolved_calls

    def test_set_include_source_resolver_clears_with_none(self) -> None:
        """set_include_source_resolver(None) clears the resolver."""

        def my_resolver(source: str) -> str | None:
            return f"resolved://{source}"

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = BundleRegistry(home=Path(tmpdir) / "home")
            registry.set_include_source_resolver(my_resolver)
            assert registry._include_source_resolver is my_resolver

            registry.set_include_source_resolver(None)
            assert registry._include_source_resolver is None

    def test_resolver_returning_override_is_used(self) -> None:
        """Resolver returning a string overrides normal resolution (e.g., for superpowers URIs)."""

        def my_resolver(source: str) -> str | None:
            if "superpowers" in source:
                return "git+https://github.com/override/superpowers#subdirectory=agents/my-agent"
            return None

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = BundleRegistry(
                home=Path(tmpdir) / "home",
                include_source_resolver=my_resolver,
            )
            result = registry._resolve_include_source("superpowers:agents/my-agent")
            assert (
                result
                == "git+https://github.com/override/superpowers#subdirectory=agents/my-agent"
            )

    def test_resolver_returning_none_falls_through(self) -> None:
        """Resolver returning None allows normal resolution to proceed."""

        def my_resolver(source: str) -> str | None:
            return None  # Always defer to normal resolution

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = BundleRegistry(
                home=Path(tmpdir) / "home",
                include_source_resolver=my_resolver,
            )
            # A URI source should still be returned as-is when resolver returns None
            result = registry._resolve_include_source(
                "git+https://github.com/some/repo"
            )
            assert result == "git+https://github.com/some/repo"

    def test_plain_name_unaffected_without_resolver(self) -> None:
        """Plain names pass through unchanged when no resolver is set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = BundleRegistry(home=Path(tmpdir) / "home")
            result = registry._resolve_include_source("plain-bundle-name")
            assert result == "plain-bundle-name"

    def test_resolver_called_for_plain_names_too(self) -> None:
        """Resolver is called for ALL source types, including plain names."""
        resolver_calls: list[str] = []

        def my_resolver(source: str) -> str | None:
            resolver_calls.append(source)
            return f"overridden://{source}"

        with tempfile.TemporaryDirectory() as tmpdir:
            registry = BundleRegistry(
                home=Path(tmpdir) / "home",
                include_source_resolver=my_resolver,
            )
            result = registry._resolve_include_source("plain-bundle-name")
            assert "plain-bundle-name" in resolver_calls
            assert result == "overridden://plain-bundle-name"


# ---------------------------------------------------------------------------
# Tests for Bug B — registry self-include edge / stale include relationships
# ---------------------------------------------------------------------------


class TestRecordIncludeRelationshipsIdempotent:
    """_record_include_relationships replaces stale include edges, not appends.

    Root cause: When a bundle dependency is renamed (e.g., terminal-tester's
    behavior file was renamed from 'terminal-tester' to 'terminal-tester-behavior'),
    _record_include_relationships used to APPEND to the existing includes list,
    leaving stale self-references that created graph cycles.

    After the fix, calling _record_include_relationships with the new child names
    should REPLACE the old includes list and clean up stale included_by entries.
    """

    def test_replaces_stale_self_reference_in_includes(self) -> None:
        """Stale self-reference is removed when _record_include_relationships is
        called with the corrected child names (Bug B exact scenario)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = BundleRegistry(home=Path(tmpdir) / "home")

            # Simulate stale registry state after old behavior name 'terminal-tester'
            registry.register(
                {
                    "terminal-tester": "git+https://github.com/example/terminal-tester@main",
                    "foundation": "git+https://github.com/example/foundation@main",
                    "terminal-tester-behavior": "git+https://github.com/example/terminal-tester@main#subdirectory=behaviors/terminal-tester.yaml",
                }
            )

            tt_state = registry.get_state("terminal-tester")
            assert isinstance(tt_state, BundleState)
            fnd_state = registry.get_state("foundation")
            assert isinstance(fnd_state, BundleState)

            # Stale state: terminal-tester included itself (old behavior name was 'terminal-tester')
            tt_state.includes = ["foundation", "terminal-tester"]  # self-reference!
            tt_state.included_by = [
                "terminal-tester",
                "reality-check-behavior",
            ]  # self-ref!
            fnd_state.included_by = ["terminal-tester"]

            # Now the bundle is re-loaded and the behavior is correctly identified
            # as 'terminal-tester-behavior'. Record the corrected relationships.
            registry._record_include_relationships(
                "terminal-tester",
                ["foundation", "terminal-tester-behavior"],
            )

            # The includes list should be the NEW list — no stale self-reference
            assert tt_state.includes == ["foundation", "terminal-tester-behavior"], (
                f"Expected ['foundation', 'terminal-tester-behavior'], "
                f"got {tt_state.includes}"
            )

            # terminal-tester should NOT include itself
            assert "terminal-tester" not in tt_state.includes, (
                "Self-reference must be removed from includes"
            )

    def test_stale_included_by_cleaned_from_old_child(self) -> None:
        """When a parent's includes change, old children's included_by is cleaned."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = BundleRegistry(home=Path(tmpdir) / "home")

            registry.register(
                {
                    "parent": "git+https://github.com/example/parent@main",
                    "old-child": "git+https://github.com/example/old@main",
                    "new-child": "git+https://github.com/example/new@main",
                }
            )

            parent_state = registry.get_state("parent")
            assert isinstance(parent_state, BundleState)
            old_child_state = registry.get_state("old-child")
            assert isinstance(old_child_state, BundleState)

            # Stale state: parent included 'old-child' (now renamed to 'new-child')
            parent_state.includes = ["old-child"]
            old_child_state.included_by = ["parent"]

            # Register 'new-child' so it has state
            new_child_state = registry.get_state("new-child")
            assert isinstance(new_child_state, BundleState)
            new_child_state.included_by = []

            # Record the corrected relationships
            registry._record_include_relationships("parent", ["new-child"])

            # parent.includes should now be ['new-child'] — stale 'old-child' removed
            assert parent_state.includes == ["new-child"], (
                f"Expected ['new-child'], got {parent_state.includes}"
            )

            # old-child.included_by should no longer contain 'parent'
            assert "parent" not in (old_child_state.included_by or []), (
                f"old-child.included_by should not contain 'parent', "
                f"got {old_child_state.included_by}"
            )

            # new-child.included_by should contain 'parent'
            assert "parent" in (new_child_state.included_by or []), (
                f"new-child.included_by should contain 'parent', "
                f"got {new_child_state.included_by}"
            )

    def test_no_self_reference_in_includes_after_reload(self) -> None:
        """After fix, terminal-tester.includes must not contain 'terminal-tester'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = BundleRegistry(home=Path(tmpdir) / "home")

            registry.register(
                {
                    "terminal-tester": "git+https://github.com/example/tt@main",
                    "foundation": "git+https://github.com/example/foundation@main",
                    "terminal-tester-behavior": "git+https://example.com/tt@main#subdirectory=behaviors/tt.yaml",
                }
            )

            tt_state = registry.get_state("terminal-tester")
            assert isinstance(tt_state, BundleState)
            tt_state.includes = ["foundation", "terminal-tester"]  # stale self-ref
            tt_state.included_by = ["terminal-tester"]  # stale self-ref

            # Simulate reload: record the true includes
            registry._record_include_relationships(
                "terminal-tester", ["foundation", "terminal-tester-behavior"]
            )

            assert "terminal-tester" not in (tt_state.includes or []), (
                "terminal-tester must not be in its own includes after reload"
            )

    def test_multiple_parents_for_same_child_preserved(self) -> None:
        """Multiple distinct parents can both include the same child; each parent's
        reload only clears that parent's stale entries, not others'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            registry = BundleRegistry(home=Path(tmpdir) / "home")

            registry.register(
                {
                    "parent-a": "git+https://github.com/example/a@main",
                    "parent-b": "git+https://github.com/example/b@main",
                    "child": "git+https://github.com/example/child@main",
                }
            )

            child_state = registry.get_state("child")
            assert isinstance(child_state, BundleState)

            # Both parents include child; record parent-a first
            registry._record_include_relationships("parent-a", ["child"])
            registry._record_include_relationships("parent-b", ["child"])

            # child.included_by should have BOTH parents
            assert set(child_state.included_by or []) == {"parent-a", "parent-b"}, (
                f"Both parents should appear in child.included_by, "
                f"got {child_state.included_by}"
            )

            # Now parent-a is reloaded with the same child (idempotent)
            registry._record_include_relationships("parent-a", ["child"])

            # Still both parents; no duplication
            assert set(child_state.included_by or []) == {"parent-a", "parent-b"}
            assert child_state.included_by is not None
            assert child_state.included_by.count("parent-a") == 1, (
                "parent-a must not be duplicated in child.included_by"
            )
