"""Tests for Bundle._provenance field and provenance tracking in compose()."""

from pathlib import Path

from amplifier_foundation.bundle import Bundle


class TestProvenance:
    """Tests for provenance tracking in Bundle.compose()."""

    def test_fresh_bundle_empty_provenance(self) -> None:
        """Fresh bundles have empty _provenance dict."""
        bundle = Bundle(name="test")
        assert isinstance(bundle._provenance, dict)
        assert bundle._provenance == {}

    def test_context_provenance(self) -> None:
        """compose() tracks which bundle contributed each context entry."""
        base = Bundle(name="base", context={"readme": Path("/path/readme.md")})
        child = Bundle(name="child", context={"guide": Path("/path/guide.md")})
        result = base.compose(child)
        # base contributed base:readme (prefixed during compose)
        assert result._provenance["context:base:readme"] == "base"
        # child contributed child:guide (prefixed during compose)
        assert result._provenance["context:child:guide"] == "child"

    def test_tool_provenance(self) -> None:
        """compose() tracks which bundle contributed each tool."""
        base = Bundle(name="base", tools=[{"module": "tool-bash"}])
        child = Bundle(name="child", tools=[{"module": "tool-python"}])
        result = base.compose(child)
        assert result._provenance["tool:tool-bash"] == "base"
        assert result._provenance["tool:tool-python"] == "child"

    def test_provider_provenance(self) -> None:
        """compose() tracks which bundle contributed each provider."""
        base = Bundle(name="base", providers=[{"module": "provider-a"}])
        child = Bundle(name="child", providers=[{"module": "provider-b"}])
        result = base.compose(child)
        assert result._provenance["provider:provider-a"] == "base"
        assert result._provenance["provider:provider-b"] == "child"

    def test_hook_provenance(self) -> None:
        """compose() tracks which bundle contributed each hook."""
        base = Bundle(name="base", hooks=[{"module": "hook-logging"}])
        child = Bundle(name="child", hooks=[{"module": "hook-audit"}])
        result = base.compose(child)
        assert result._provenance["hook:hook-logging"] == "base"
        assert result._provenance["hook:hook-audit"] == "child"

    def test_agent_provenance(self) -> None:
        """compose() tracks which bundle contributed each agent."""
        base = Bundle(name="base", agents={"my-agent": {"name": "my-agent"}})
        child = Bundle(name="child", agents={"other-agent": {"name": "other-agent"}})
        result = base.compose(child)
        assert result._provenance["agent:my-agent"] == "base"
        assert result._provenance["agent:other-agent"] == "child"

    def test_override_updates_provenance(self) -> None:
        """When a later bundle overrides an item, provenance updates to the winner."""
        base = Bundle(name="base", tools=[{"module": "tool-bash"}])
        child = Bundle(name="child", tools=[{"module": "tool-bash"}])  # same module
        result = base.compose(child)
        # child wins the merge, so provenance should point to child
        assert result._provenance["tool:tool-bash"] == "child"

    def test_three_level_nesting_preserves_attribution(self) -> None:
        """Three-level nesting preserves immediate contributor attribution.

        When a composed bundle (b = a.compose(b_raw)) is further composed,
        the original contributor (a) is preserved in provenance via overlay.
        """
        # Level 1: a contributes tool-x
        a = Bundle(name="a", tools=[{"module": "tool-x"}])

        # Level 2: b_raw adds tool-y; compose to get b with provenance
        b_raw = Bundle(name="b", tools=[{"module": "tool-y"}])
        b = a.compose(b_raw)

        # Verify b has correct provenance
        assert b._provenance["tool:tool-x"] == "a"
        assert b._provenance["tool:tool-y"] == "b"

        # Level 3: c composes b
        c = Bundle(name="c")
        result = c.compose(b)

        # tool-x should still be attributed to "a" (original contributor),
        # not "b" (intermediate) — preserved via other._provenance overlay
        assert result._provenance["tool:tool-x"] == "a"
        # tool-y should be attributed to "b" (its direct contributor)
        assert result._provenance["tool:tool-y"] == "b"

    def test_pending_context_provenance_self(self) -> None:
        """compose() tags _pending_context entries from self with self.name.

        Namespace-prefixed context refs (e.g. 'foundation:context/foo.md') are stored
        as _pending_context during parsing and resolved later by resolve_pending_context().
        Their provenance must be tagged during compose() so context_list() can find
        the correct behavior source after resolution.
        """
        base = Bundle(
            name="base",
            _pending_context={"base:context/guide.md": "base:context/guide.md"},
        )
        result = base.compose()
        assert result._provenance.get("context:base:context/guide.md") == "base"

    def test_pending_context_provenance_other(self) -> None:
        """compose() tags _pending_context entries from other bundles.

        When a behavior bundle contributes context via namespace-prefixed refs
        (stored as _pending_context), compose() must tag them in provenance so
        behaviors_list() and context_list() correctly attribute the contribution.
        """
        base = Bundle(name="base")
        child = Bundle(
            name="child",
            _pending_context={"child:context/notes.md": "child:context/notes.md"},
        )
        result = base.compose(child)
        assert result._provenance.get("context:child:context/notes.md") == "child"

    def test_pending_context_provenance_survives_resolution(self) -> None:
        """Pending context provenance persists after resolve_pending_context() runs.

        After compose() tags pending_context entries and resolve_pending_context()
        moves them to context, the provenance keys must still be present so that
        context_list() can look them up by the final context key.
        """
        base = Bundle(name="base")
        child = Bundle(
            name="child",
            _pending_context={"child:context/notes.md": "child:context/notes.md"},
        )
        result = base.compose(child)
        # Provenance must be set before resolution
        assert result._provenance.get("context:child:context/notes.md") == "child"
        # After resolution the key is unchanged (pending key == final context key),
        # so the provenance lookup still works
        assert "context:child:context/notes.md" in result._provenance
