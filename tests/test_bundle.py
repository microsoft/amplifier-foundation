"""Tests for Bundle class."""

from pathlib import Path
from tempfile import TemporaryDirectory

from amplifier_foundation.bundle import Bundle


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


class TestBundleAgentLoading:
    """Tests for agent content loading from .md files."""

    def test_load_agent_content_with_frontmatter(self) -> None:
        """load_agent_content parses frontmatter and body from .md file."""
        with TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            agents_dir = base / "agents"
            agents_dir.mkdir()

            # Create agent file with frontmatter
            agent_file = agents_dir / "test-agent.md"
            agent_file.write_text("""---
meta:
  name: test-agent
  description: A test agent for debugging
---
# Test Agent

You are a helpful test agent.

## Instructions

Be helpful and thorough.
""")

            bundle = Bundle(name="test", base_path=base)
            result = bundle.load_agent_content("test-agent")

            assert result is not None
            assert result["name"] == "test-agent"
            assert result["description"] == "A test agent for debugging"
            assert "system" in result
            assert "instruction" in result["system"]
            assert "# Test Agent" in result["system"]["instruction"]
            assert "Be helpful and thorough" in result["system"]["instruction"]

    def test_load_agent_content_namespaced(self) -> None:
        """load_agent_content resolves namespaced agent references."""
        with TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            other_base = Path(tmpdir) / "other"
            other_base.mkdir()
            other_agents = other_base / "agents"
            other_agents.mkdir()

            # Create agent in "other" namespace
            agent_file = other_agents / "helper.md"
            agent_file.write_text("""---
meta:
  name: helper
  description: Helper agent
---
You are a helper.
""")

            bundle = Bundle(
                name="main",
                base_path=base,
                source_base_paths={"other": other_base},
            )

            result = bundle.load_agent_content("other:helper")

            assert result is not None
            assert result["name"] == "helper"
            assert result["description"] == "Helper agent"
            assert "You are a helper" in result["system"]["instruction"]

    def test_load_agent_content_not_found(self) -> None:
        """load_agent_content returns None for missing agent."""
        with TemporaryDirectory() as tmpdir:
            bundle = Bundle(name="test", base_path=Path(tmpdir))
            result = bundle.load_agent_content("nonexistent")
            assert result is None

    def test_load_agent_content_empty_body(self) -> None:
        """load_agent_content handles files with frontmatter but empty body."""
        with TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            agents_dir = base / "agents"
            agents_dir.mkdir()

            agent_file = agents_dir / "metadata-only.md"
            agent_file.write_text("""---
meta:
  name: metadata-only
  description: Agent with no instructions
---
""")

            bundle = Bundle(name="test", base_path=base)
            result = bundle.load_agent_content("metadata-only")

            assert result is not None
            assert result["name"] == "metadata-only"
            assert result["description"] == "Agent with no instructions"
            assert "system" not in result  # No instruction = no system key

    def test_resolve_agents_populates_content(self) -> None:
        """resolve_agents loads content for agents with only names."""
        with TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            agents_dir = base / "agents"
            agents_dir.mkdir()

            # Create agent file
            agent_file = agents_dir / "my-agent.md"
            agent_file.write_text("""---
meta:
  name: my-agent
  description: My special agent
---
You are my special agent with custom instructions.
""")

            bundle = Bundle(
                name="test",
                base_path=base,
                agents={"my-agent": {"name": "my-agent"}},  # Only has name, no content
            )

            bundle.resolve_agents()

            # Should now have full content
            agent = bundle.agents["my-agent"]
            assert agent["description"] == "My special agent"
            assert "system" in agent
            assert "custom instructions" in agent["system"]["instruction"]

    def test_resolve_agents_preserves_inline_definitions(self) -> None:
        """resolve_agents doesn't overwrite agents with existing instruction."""
        with TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            agents_dir = base / "agents"
            agents_dir.mkdir()

            # Create agent file (would be different from inline)
            agent_file = agents_dir / "inline-agent.md"
            agent_file.write_text("""---
meta:
  name: inline-agent
---
File-based instructions.
""")

            bundle = Bundle(
                name="test",
                base_path=base,
                agents={
                    "inline-agent": {
                        "name": "inline-agent",
                        "system": {
                            "instruction": "Inline instructions should be kept."
                        },
                    }
                },
            )

            bundle.resolve_agents()

            # Inline instruction should be preserved
            assert (
                bundle.agents["inline-agent"]["system"]["instruction"]
                == "Inline instructions should be kept."
            )

    def test_resolve_agents_handles_namespaced_agents(self) -> None:
        """resolve_agents works with namespaced agent references."""
        with TemporaryDirectory() as tmpdir:
            ns_base = Path(tmpdir) / "namespace"
            ns_base.mkdir()
            agents_dir = ns_base / "agents"
            agents_dir.mkdir()

            agent_file = agents_dir / "ns-agent.md"
            agent_file.write_text("""---
meta:
  name: ns-agent
  description: Namespaced agent
---
Namespaced instructions here.
""")

            bundle = Bundle(
                name="main",
                base_path=Path(tmpdir),
                source_base_paths={"myns": ns_base},
                agents={"myns:ns-agent": {"name": "myns:ns-agent"}},
            )

            bundle.resolve_agents()

            agent = bundle.agents["myns:ns-agent"]
            assert agent["description"] == "Namespaced agent"
            assert "Namespaced instructions" in agent["system"]["instruction"]


class TestBundleAgentSecurity:
    """Security tests for agent loading."""

    def test_path_traversal_with_dotdot_rejected(self) -> None:
        """Path traversal attempts with .. should return None."""
        with TemporaryDirectory() as tmpdir:
            bundle = Bundle(name="test", base_path=Path(tmpdir))

            malicious_names = [
                "../../../etc/passwd",
                "..%2f..%2fetc/passwd",
                "foo/../../../etc/passwd",
            ]

            for name in malicious_names:
                result = bundle.load_agent_content(name)
                assert result is None, f"Should reject: {name}"

    def test_path_traversal_namespaced_rejected(self) -> None:
        """Namespaced path traversal attempts should return None."""
        with TemporaryDirectory() as tmpdir:
            ns_base = Path(tmpdir) / "namespace"
            ns_base.mkdir()

            bundle = Bundle(
                name="main",
                base_path=Path(tmpdir),
                source_base_paths={"ns": ns_base},
            )

            malicious_names = [
                "ns:../../../etc/passwd",
                "ns:foo/../bar",
            ]

            for name in malicious_names:
                result = bundle.load_agent_content(name)
                assert result is None, f"Should reject: {name}"

    def test_absolute_path_rejected(self) -> None:
        """Absolute paths in agent names should return None."""
        with TemporaryDirectory() as tmpdir:
            bundle = Bundle(name="test", base_path=Path(tmpdir))

            malicious_names = [
                "/etc/passwd",
                "ns:/etc/passwd",
            ]

            for name in malicious_names:
                result = bundle.load_agent_content(name)
                assert result is None, f"Should reject: {name}"
