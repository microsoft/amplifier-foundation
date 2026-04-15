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
        assert result._provenance["context:base:readme"] == ["base"]
        # child contributed child:guide (prefixed during compose)
        assert result._provenance["context:child:guide"] == ["child"]

    def test_tool_provenance(self) -> None:
        """compose() tracks which bundle contributed each tool."""
        base = Bundle(name="base", tools=[{"module": "tool-bash"}])
        child = Bundle(name="child", tools=[{"module": "tool-python"}])
        result = base.compose(child)
        assert result._provenance["tool:tool-bash"] == ["base"]
        assert result._provenance["tool:tool-python"] == ["child"]

    def test_provider_provenance(self) -> None:
        """compose() tracks which bundle contributed each provider."""
        base = Bundle(name="base", providers=[{"module": "provider-a"}])
        child = Bundle(name="child", providers=[{"module": "provider-b"}])
        result = base.compose(child)
        assert result._provenance["provider:provider-a"] == ["base"]
        assert result._provenance["provider:provider-b"] == ["child"]

    def test_hook_provenance(self) -> None:
        """compose() tracks which bundle contributed each hook."""
        base = Bundle(name="base", hooks=[{"module": "hook-logging"}])
        child = Bundle(name="child", hooks=[{"module": "hook-audit"}])
        result = base.compose(child)
        assert result._provenance["hook:hook-logging"] == ["base"]
        assert result._provenance["hook:hook-audit"] == ["child"]

    def test_agent_provenance(self) -> None:
        """compose() tracks which bundle contributed each agent."""
        base = Bundle(name="base", agents={"my-agent": {"name": "my-agent"}})
        child = Bundle(name="child", agents={"other-agent": {"name": "other-agent"}})
        result = base.compose(child)
        assert result._provenance["agent:my-agent"] == ["base"]
        assert result._provenance["agent:other-agent"] == ["child"]

    def test_override_updates_provenance(self) -> None:
        """Only the first bundle to introduce an item is recorded as its claimant.

        When child has the same tool as base but base introduced it first, child
        should NOT be added as an additional claimant — base is the true origin.
        This is the corrected behavior: only the bundle that genuinely adds the
        item to the composition is tracked, not bundles that inherit it.
        """
        base = Bundle(name="base", tools=[{"module": "tool-bash"}])
        child = Bundle(name="child", tools=[{"module": "tool-bash"}])  # same module
        result = base.compose(child)
        # Only base introduced tool-bash; child merely duplicated it
        assert result._provenance["tool:tool-bash"] == ["base"]

    def test_three_level_nesting_preserves_attribution(self) -> None:
        """Three-level nesting preserves original contributor in multi-claimant list.

        When a composed bundle (b = a.compose(b_raw)) is further composed,
        the original contributor (a) is preserved in provenance via overlay.
        """
        # Level 1: a contributes tool-x
        a = Bundle(name="a", tools=[{"module": "tool-x"}])

        # Level 2: b_raw adds tool-y; compose to get b with provenance
        b_raw = Bundle(name="b", tools=[{"module": "tool-y"}])
        b = a.compose(b_raw)

        # Verify b has correct provenance (single claimants)
        assert b._provenance["tool:tool-x"] == ["a"]
        assert b._provenance["tool:tool-y"] == ["b"]

        # Level 3: c composes b
        c = Bundle(name="c")
        result = c.compose(b)

        # tool-x: "a" (original) and "b" (carries tool in its list) are both claimants
        assert "a" in result._provenance["tool:tool-x"]
        # tool-y should be attributed to "b" (its direct contributor)
        assert result._provenance["tool:tool-y"] == ["b"]

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
        assert result._provenance.get("context:base:context/guide.md") == ["base"]

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
        assert result._provenance.get("context:child:context/notes.md") == ["child"]

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
        assert result._provenance.get("context:child:context/notes.md") == ["child"]
        # After resolution the key is unchanged (pending key == final context key),
        # so the provenance lookup still works
        assert "context:child:context/notes.md" in result._provenance


class TestMultiClaimantProvenance:
    """Tests for multi-claimant provenance tracking (list[str] values)."""

    def test_multi_claimant_provenance_type(self) -> None:
        """_provenance values are list[str] not str."""
        base = Bundle(name="base", tools=[{"module": "tool-bash"}])
        child = Bundle(name="child", tools=[{"module": "tool-python"}])
        result = base.compose(child)
        for value in result._provenance.values():
            assert isinstance(value, list), (
                f"Expected list, got {type(value)}: {value!r}"
            )
            for item in value:
                assert isinstance(item, str), (
                    f"Expected str items, got {type(item)}: {item!r}"
                )

    def test_multi_claimant_shared_tool(self) -> None:
        """First introducer wins: only the bundle that genuinely adds a tool is a claimant.

        When child duplicates a tool already in base, base is the sole claimant.
        This prevents over-attribution from bundles that inherit rather than declare.
        """
        base = Bundle(name="base", tools=[{"module": "shared-tool"}])
        child = Bundle(name="child", tools=[{"module": "shared-tool"}])
        result = base.compose(child)
        # Only base introduced shared-tool; child merely duplicated it
        assert result._provenance["tool:shared-tool"] == ["base"]

    def test_multi_claimant_no_duplicates(self) -> None:
        """Same bundle claiming same item multiple times results in no duplicates."""
        a = Bundle(name="a", tools=[{"module": "tool-x"}])
        # Compose a with itself — a claims tool-x twice
        result = a.compose(a)
        # "a" should appear only once
        assert result._provenance["tool:tool-x"] == ["a"]

    def test_multi_claimant_context(self) -> None:
        """First introducer wins for context: only the bundle that adds the key is claimant.

        When both bundles reference the same pending context key, only the first
        (part of self in compose()) is attributed. Bundle b merely duplicates it.
        """
        # Both bundles reference the same cross-bundle context
        a = Bundle(
            name="a",
            _pending_context={"shared:context/foo.md": "shared:context/foo.md"},
        )
        b = Bundle(
            name="b",
            _pending_context={"shared:context/foo.md": "shared:context/foo.md"},
        )
        result = a.compose(b)
        claimants = result._provenance["context:shared:context/foo.md"]
        # Only "a" introduced this context key; "b" duplicated it
        assert "a" in claimants
        assert "b" not in claimants

    def test_multi_claimant_agents(self) -> None:
        """First introducer wins for agents: only the first bundle is the claimant.

        When both bundles declare the same agent, only the first (part of self)
        is attributed. Bundle b merely duplicates the agent definition.
        """
        a = Bundle(name="a", agents={"shared-agent": {"name": "shared-agent"}})
        b = Bundle(name="b", agents={"shared-agent": {"name": "shared-agent"}})
        result = a.compose(b)
        claimants = result._provenance["agent:shared-agent"]
        # Only "a" introduced shared-agent; "b" duplicated it
        assert "a" in claimants
        assert "b" not in claimants

    def test_three_level_nesting_multi_claimant(self) -> None:
        """Three-level nesting: only the original introducer remains the claimant.

        When multiple bundles share the same tool (due to inheritance), only the
        bundle that genuinely first introduced it is tracked — not every bundle
        that carries it in its (potentially inherited) tools list.
        """
        # Level 1+2: a introduces tool-x first; b_raw also lists it
        a = Bundle(name="a", tools=[{"module": "tool-x"}])
        b_raw = Bundle(name="b", tools=[{"module": "tool-x"}])
        ab = a.compose(b_raw)
        # Only "a" introduced tool-x; "b" merely duplicated it
        assert "a" in ab._provenance["tool:tool-x"]
        assert "b" not in ab._provenance["tool:tool-x"]

        # Level 3: d also lists tool-x (still already in result)
        d = Bundle(name="d", tools=[{"module": "tool-x"}])
        result = ab.compose(d)
        # Still only "a" is the original introducer
        assert "a" in result._provenance["tool:tool-x"]
        assert "b" not in result._provenance["tool:tool-x"]
        assert "d" not in result._provenance["tool:tool-x"]


class TestProvenanceNoOverAttribution:
    """Regression tests for the provenance over-attribution bug.

    Verifies that items inherited through bundle includes are NOT re-attributed
    to the inheriting bundle — only the true original introducer is tracked.

    Real-world scenario: behavior bundles resolve their `includes:` BEFORE being
    composed into the final session bundle. Their `.tools` lists therefore contain
    all inherited tools from included bundles (e.g., foundation). compose() must
    only tag items that are genuinely NEW to the result, not inherited ones.
    """

    def test_inherited_tools_not_reattributed(self) -> None:
        """Tools from earlier in the chain are not re-attributed to later behaviors.

        Simulates the real-world scenario: foundation introduces tool-todo;
        then two behavior bundles are composed in, each carrying tool-todo in
        their tools lists (because they resolved their own foundation include).
        Only foundation should be the claimant for tool-todo.
        """
        # foundation declares tool-todo
        foundation = Bundle(name="foundation", tools=[{"module": "tool-todo"}])

        # behavior-a carries tool-todo because it resolved its includes
        behavior_a = Bundle(
            name="behavior-a",
            tools=[{"module": "tool-todo"}, {"module": "tool-a-only"}],
        )
        # behavior-b also carries tool-todo for the same reason
        behavior_b = Bundle(
            name="behavior-b",
            tools=[{"module": "tool-todo"}, {"module": "tool-b-only"}],
        )

        # Compose all together (as app-cli does)
        result = foundation.compose(behavior_a, behavior_b)

        # tool-todo: only foundation introduced it
        assert result._provenance["tool:tool-todo"] == ["foundation"]
        # behavior-a added a new tool
        assert result._provenance["tool:tool-a-only"] == ["behavior-a"]
        # behavior-b added a new tool
        assert result._provenance["tool:tool-b-only"] == ["behavior-b"]

    def test_only_new_tools_tagged(self) -> None:
        """Only tools that weren't in result before the merge are attributed to other.

        When A has tool-x and B has both tool-x (inherited) and tool-y (new),
        composing A with B should attribute tool-x only to A and tool-y only to B.
        """
        a = Bundle(name="a", tools=[{"module": "tool-x"}])
        b = Bundle(name="b", tools=[{"module": "tool-x"}, {"module": "tool-y"}])
        result = a.compose(b)

        # tool-x was already in result from a — don't attribute to b
        assert result._provenance["tool:tool-x"] == ["a"]
        # tool-y is genuinely new from b
        assert result._provenance["tool:tool-y"] == ["b"]

    def test_three_level_nesting_no_over_attribution(self) -> None:
        """Three-level nesting doesn't propagate over-attribution.

        A introduces tool-x. B (composed from A) also has tool-x in its list
        (inherited). C composes A→B→C. tool-x should only be attributed to A.
        tool-y (added by B) should only be attributed to B.
        tool-z (added by C) should only be attributed to C.
        """
        # Level 1: a has tool-x
        a = Bundle(name="a", tools=[{"module": "tool-x"}])

        # Level 2: b adds tool-y; it also carries tool-x from its resolved includes
        b_raw = Bundle(name="b", tools=[{"module": "tool-y"}])
        b = a.compose(b_raw)
        # Verify b's provenance is correct before going to level 3
        assert b._provenance["tool:tool-x"] == ["a"]
        assert b._provenance["tool:tool-y"] == ["b"]

        # Level 3: c adds tool-z; compose with b (which carries tool-x and tool-y)
        c_raw = Bundle(name="c", tools=[{"module": "tool-z"}])
        result = b.compose(c_raw)

        # tool-x: originated in a, passed through b — still only a
        assert result._provenance["tool:tool-x"] == ["a"]
        # tool-y: originated in b — still only b
        assert result._provenance["tool:tool-y"] == ["b"]
        # tool-z: new from c
        assert result._provenance["tool:tool-z"] == ["c"]
