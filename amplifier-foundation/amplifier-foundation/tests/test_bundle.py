"""Tests for Bundle class."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from amplifier_foundation.bundle import Bundle
from amplifier_foundation.exceptions import BundleValidationError


class TestBundle:
    """Tests for Bundle dataclass."""

    def test_create_minimal(self) -> None:
        """Can create bundle with just name."""
        bundle = Bundle(name="test")
        assert bundle.name == "test"
        assert bundle.version == "1.0.0"
        assert bundle.providers == []
        assert bundle.tools == []
        assert bundle.hooks == []

    def test_from_dict_minimal(self) -> None:
        """Can create bundle from minimal dict."""
        data = {"bundle": {"name": "test"}}
        bundle = Bundle.from_dict(data)
        assert bundle.name == "test"

    def test_from_dict_full(self) -> None:
        """Can create bundle from full config dict."""
        data = {
            "bundle": {
                "name": "full-test",
                "version": "2.0.0",
                "description": "A full test bundle",
            },
            "session": {"orchestrator": "loop-basic"},
            "providers": [
                {"module": "provider-anthropic", "config": {"model": "test"}}
            ],
            "tools": [{"module": "tool-bash"}],
            "hooks": [{"module": "hooks-logging"}],
            "includes": ["base-bundle"],
        }
        bundle = Bundle.from_dict(data)
        assert bundle.name == "full-test"
        assert bundle.version == "2.0.0"
        assert bundle.session == {"orchestrator": "loop-basic"}
        assert len(bundle.providers) == 1
        assert len(bundle.tools) == 1
        assert len(bundle.hooks) == 1
        assert bundle.includes == ["base-bundle"]


class TestBundleCompose:
    """Tests for Bundle.compose method."""

    def test_compose_empty_bundles(self) -> None:
        """Composing empty bundles returns empty bundle."""
        base = Bundle(name="base")
        child = Bundle(name="child")
        result = base.compose(child)
        assert result.name == "child"
        assert result.providers == []

    def test_compose_session_deep_merge(self) -> None:
        """Session configs are deep merged."""
        base = Bundle(
            name="base", session={"orchestrator": "loop-basic", "context": "simple"}
        )
        child = Bundle(name="child", session={"orchestrator": "loop-streaming"})
        result = base.compose(child)
        assert result.session["orchestrator"] == "loop-streaming"
        assert result.session["context"] == "simple"

    def test_compose_providers_merge_by_module(self) -> None:
        """Providers are merged by module ID."""
        base = Bundle(
            name="base",
            providers=[{"module": "provider-a", "config": {"x": 1, "y": 2}}],
        )
        child = Bundle(
            name="child",
            providers=[{"module": "provider-a", "config": {"y": 3, "z": 4}}],
        )
        result = base.compose(child)
        assert len(result.providers) == 1
        assert result.providers[0]["config"] == {"x": 1, "y": 3, "z": 4}

    def test_compose_multiple_bundles(self) -> None:
        """Can compose multiple bundles at once."""
        base = Bundle(name="base", providers=[{"module": "a"}])
        mid = Bundle(name="mid", providers=[{"module": "b"}])
        top = Bundle(name="top", providers=[{"module": "c"}])
        result = base.compose(mid, top)
        assert result.name == "top"
        modules = [p["module"] for p in result.providers]
        assert set(modules) == {"a", "b", "c"}

    def test_compose_instruction_replaced(self) -> None:
        """Later instruction replaces earlier."""
        base = Bundle(name="base", instruction="Base instruction")
        child = Bundle(name="child", instruction="Child instruction")
        result = base.compose(child)
        assert result.instruction == "Child instruction"


class TestBundleToMountPlan:
    """Tests for Bundle.to_mount_plan method."""

    def test_minimal_mount_plan(self) -> None:
        """Empty bundle produces empty mount plan."""
        bundle = Bundle(name="test")
        plan = bundle.to_mount_plan()
        assert plan == {}

    def test_full_mount_plan(self) -> None:
        """Bundle produces complete mount plan."""
        bundle = Bundle(
            name="test",
            session={"orchestrator": "loop-basic"},
            providers=[{"module": "provider-anthropic"}],
            tools=[{"module": "tool-bash"}],
            hooks=[{"module": "hooks-logging"}],
            agents={"my-agent": {"name": "my-agent"}},
        )
        plan = bundle.to_mount_plan()
        assert plan["session"] == {"orchestrator": "loop-basic"}
        assert len(plan["providers"]) == 1
        assert len(plan["tools"]) == 1
        assert len(plan["hooks"]) == 1
        assert "my-agent" in plan["agents"]


class TestBundleResolveContext:
    """Tests for Bundle.resolve_context_path method."""

    def test_resolve_registered_context(self) -> None:
        """Resolves context from registered context dict."""
        bundle = Bundle(name="test", context={"myfile": Path("/tmp/myfile.md")})
        result = bundle.resolve_context_path("myfile")
        assert result == Path("/tmp/myfile.md")

    def test_resolve_from_base_path(self) -> None:
        """Resolves context from base path if file exists."""
        with TemporaryDirectory() as tmpdir:
            # Create a context file
            context_dir = Path(tmpdir) / "context"
            context_dir.mkdir()
            context_file = context_dir / "test.md"
            context_file.write_text("Test content")

            bundle = Bundle(name="test", base_path=Path(tmpdir))
            # Context paths are explicit - include full path relative to bundle root
            result = bundle.resolve_context_path("context/test.md")
            assert result is not None
            assert result.exists()

    def test_resolve_not_found(self) -> None:
        """Returns None for unknown context."""
        bundle = Bundle(name="test")
        result = bundle.resolve_context_path("unknown")
        assert result is None


class TestBundlePendingContext:
    """Tests for deferred namespace context resolution."""

    def test_parse_context_defers_namespaced_refs(self) -> None:
        """Context includes with namespace prefixes are stored as pending."""
        data = {
            "bundle": {"name": "test"},
            "context": {
                "include": [
                    "local-file.md",
                    "myns:context/namespaced-file.md",
                ]
            },
        }
        bundle = Bundle.from_dict(data, base_path=Path("/base"))

        # Local file should be resolved immediately
        assert "local-file.md" in bundle.context
        assert bundle.context["local-file.md"] == Path("/base/local-file.md")

        # Namespaced file should be pending
        assert "myns:context/namespaced-file.md" not in bundle.context
        assert "myns:context/namespaced-file.md" in bundle._pending_context

    def test_resolve_pending_context_with_source_base_paths(self) -> None:
        """Pending context is resolved using source_base_paths."""
        bundle = Bundle(
            name="test",
            _pending_context={"myns:context/file.md": "myns:context/file.md"},
            source_base_paths={"myns": Path("/namespace/root")},
        )

        bundle.resolve_pending_context()

        # Should be resolved now
        assert "myns:context/file.md" in bundle.context
        assert bundle.context["myns:context/file.md"] == Path(
            "/namespace/root/context/file.md"
        )
        # Should be removed from pending
        assert "myns:context/file.md" not in bundle._pending_context

    def test_resolve_pending_context_self_reference(self) -> None:
        """Pending context with self-namespace uses base_path."""
        bundle = Bundle(
            name="myns",
            base_path=Path("/bundle/root"),
            _pending_context={"myns:context/file.md": "myns:context/file.md"},
        )

        bundle.resolve_pending_context()

        # Should be resolved using base_path (self-reference)
        assert "myns:context/file.md" in bundle.context
        assert bundle.context["myns:context/file.md"] == Path(
            "/bundle/root/context/file.md"
        )

    def test_compose_merges_pending_context(self) -> None:
        """Compose merges pending context from both bundles."""
        base = Bundle(
            name="base",
            _pending_context={"ns1:file1.md": "ns1:file1.md"},
        )
        child = Bundle(
            name="child",
            _pending_context={"ns2:file2.md": "ns2:file2.md"},
        )

        result = base.compose(child)

        assert "ns1:file1.md" in result._pending_context
        assert "ns2:file2.md" in result._pending_context

    def test_pending_context_resolved_after_compose(self) -> None:
        """After compose, pending context can be resolved with merged source_base_paths."""
        base = Bundle(
            name="base",
            base_path=Path("/base/root"),
            source_base_paths={"ns1": Path("/ns1/root")},
            _pending_context={"ns1:context/a.md": "ns1:context/a.md"},
        )
        child = Bundle(
            name="child",
            base_path=Path("/child/root"),
            source_base_paths={"ns2": Path("/ns2/root")},
            _pending_context={"ns2:context/b.md": "ns2:context/b.md"},
        )

        result = base.compose(child)

        # Both namespaces should be available in result
        assert "ns1" in result.source_base_paths
        assert "ns2" in result.source_base_paths

        # Resolve pending context
        result.resolve_pending_context()

        # Both should be resolved
        assert "ns1:context/a.md" in result.context
        assert "ns2:context/b.md" in result.context
        assert result.context["ns1:context/a.md"] == Path("/ns1/root/context/a.md")
        assert result.context["ns2:context/b.md"] == Path("/ns2/root/context/b.md")


class TestBundleValidation:
    """Tests for Bundle.from_dict validation of malformed configs."""

    def test_raises_on_string_tools(self) -> None:
        """Raises BundleValidationError when tools contains strings instead of dicts."""
        data = {
            "bundle": {"name": "m365-collab"},
            "tools": ["m365_collab", "sharepoint"],  # Wrong: should be list of dicts
        }
        with pytest.raises(BundleValidationError) as exc_info:
            Bundle.from_dict(data)
        error_msg = str(exc_info.value)
        assert "m365-collab" in error_msg  # Bundle name in error
        assert "tools[0]" in error_msg  # Field and index
        assert "expected dict" in error_msg  # What was expected
        assert "got str" in error_msg  # What was found
        assert "'m365_collab'" in error_msg  # The bad value
        assert "module" in error_msg  # Correct format hint

    def test_raises_on_string_providers(self) -> None:
        """Raises BundleValidationError when providers contains strings instead of dicts."""
        data = {
            "bundle": {"name": "test-bundle"},
            "providers": ["provider-anthropic"],  # Wrong: should be list of dicts
        }
        with pytest.raises(BundleValidationError) as exc_info:
            Bundle.from_dict(data)
        error_msg = str(exc_info.value)
        assert "test-bundle" in error_msg
        assert "providers[0]" in error_msg
        assert "expected dict" in error_msg
        assert "got str" in error_msg

    def test_raises_on_string_hooks(self) -> None:
        """Raises BundleValidationError when hooks contains strings instead of dicts."""
        data = {
            "bundle": {"name": "test-bundle"},
            "hooks": [{"module": "hook-a"}, "hook-b"],  # Second item is wrong
        }
        with pytest.raises(BundleValidationError) as exc_info:
            Bundle.from_dict(data)
        error_msg = str(exc_info.value)
        assert "hooks[1]" in error_msg  # Index 1, not 0
        assert "got str" in error_msg
        assert "'hook-b'" in error_msg

    def test_error_uses_base_path_when_no_name(self) -> None:
        """Uses base_path in error when bundle has no name."""
        data = {
            "bundle": {},  # No name
            "tools": ["bad-tool"],
        }
        with pytest.raises(BundleValidationError) as exc_info:
            Bundle.from_dict(data, base_path=Path("/path/to/bundle"))
        error_msg = str(exc_info.value)
        assert "/path/to/bundle" in error_msg

    def test_error_shows_correct_format_example(self) -> None:
        """Error message includes example of correct format."""
        data = {
            "bundle": {"name": "test"},
            "tools": ["wrong"],
        }
        with pytest.raises(BundleValidationError) as exc_info:
            Bundle.from_dict(data)
        error_msg = str(exc_info.value)
        assert "Correct format:" in error_msg
        assert "module" in error_msg
        assert "source" in error_msg

    def test_valid_config_passes(self) -> None:
        """Valid configuration with proper dict format passes validation."""
        data = {
            "bundle": {"name": "valid-bundle"},
            "providers": [
                {"module": "provider-anthropic", "source": "git+https://..."}
            ],
            "tools": [{"module": "tool-bash"}],
            "hooks": [{"module": "hooks-logging", "config": {"level": "debug"}}],
        }
        bundle = Bundle.from_dict(data)
        assert bundle.name == "valid-bundle"
        assert len(bundle.providers) == 1
        assert len(bundle.tools) == 1
        assert len(bundle.hooks) == 1

    def test_empty_lists_pass(self) -> None:
        """Empty lists for tools/providers/hooks pass validation."""
        data = {
            "bundle": {"name": "empty-lists"},
            "providers": [],
            "tools": [],
            "hooks": [],
        }
        bundle = Bundle.from_dict(data)
        assert bundle.providers == []
        assert bundle.tools == []
        assert bundle.hooks == []

    def test_missing_lists_pass(self) -> None:
        """Missing tools/providers/hooks (not specified) pass validation."""
        data = {"bundle": {"name": "minimal"}}
        bundle = Bundle.from_dict(data)
        assert bundle.providers == []
        assert bundle.tools == []
        assert bundle.hooks == []
