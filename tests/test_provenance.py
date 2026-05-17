"""Tests for Bundle.origins field and provenance tracking in compose()."""

from pathlib import Path

from amplifier_foundation.bundle import Bundle
from amplifier_foundation.bundle._provenance import tag_container_provenance
from amplifier_foundation.configurator._types import Origin


class TestProvenance:
    """Tests for provenance tracking in Bundle.compose()."""

    def test_fresh_bundle_empty_origins(self) -> None:
        """Fresh bundles have empty origins dict."""
        bundle = Bundle(name="test")
        assert isinstance(bundle.origins, dict)
        assert bundle.origins == {}

    def test_context_provenance(self) -> None:
        """compose() tracks which bundle contributed each context entry."""
        base = Bundle(name="base", context={"readme": Path("/path/readme.md")})
        child = Bundle(name="child", context={"guide": Path("/path/guide.md")})
        result = base.compose(child)
        # base contributed base:readme (prefixed during compose)
        assert result.origins["context:base:readme"] == [Origin("base", None)]
        # child contributed child:guide (prefixed during compose)
        assert result.origins["context:child:guide"] == [Origin("child", None)]

    def test_tool_provenance(self) -> None:
        """compose() tracks which bundle contributed each tool."""
        base = Bundle(name="base", tools=[{"module": "tool-bash"}])
        child = Bundle(name="child", tools=[{"module": "tool-python"}])
        result = base.compose(child)
        assert result.origins["tool:tool-bash"] == [Origin("base", None)]
        assert result.origins["tool:tool-python"] == [Origin("child", None)]

    def test_provider_provenance(self) -> None:
        """compose() tracks which bundle contributed each provider."""
        base = Bundle(name="base", providers=[{"module": "provider-a"}])
        child = Bundle(name="child", providers=[{"module": "provider-b"}])
        result = base.compose(child)
        assert result.origins["provider:provider-a"] == [Origin("base", None)]
        assert result.origins["provider:provider-b"] == [Origin("child", None)]

    def test_hook_provenance(self) -> None:
        """compose() tracks which bundle contributed each hook."""
        base = Bundle(name="base", hooks=[{"module": "hook-logging"}])
        child = Bundle(name="child", hooks=[{"module": "hook-audit"}])
        result = base.compose(child)
        assert result.origins["hook:hook-logging"] == [Origin("base", None)]
        assert result.origins["hook:hook-audit"] == [Origin("child", None)]

    def test_agent_provenance(self) -> None:
        """compose() tracks which bundle contributed each agent."""
        base = Bundle(name="base", agents={"my-agent": {"name": "my-agent"}})
        child = Bundle(name="child", agents={"other-agent": {"name": "other-agent"}})
        result = base.compose(child)
        assert result.origins["agent:my-agent"] == [Origin("base", None)]
        assert result.origins["agent:other-agent"] == [Origin("child", None)]

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
        assert result.origins["tool:tool-bash"] == [Origin("base", None)]

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
        assert b.origins["tool:tool-x"] == [Origin("a", None)]
        assert b.origins["tool:tool-y"] == [Origin("b", None)]

        # Level 3: c composes b
        c = Bundle(name="c")
        result = c.compose(b)

        # tool-x: "a" (original) with via_behavior="b" (how it reached c through b)
        assert any(o.bundle == "a" for o in result.origins["tool:tool-x"])
        # tool-y should be attributed to "b" (its direct contributor)
        assert any(o.bundle == "b" for o in result.origins["tool:tool-y"])

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
        assert result.origins.get("context:base:context/guide.md") == [
            Origin("base", None)
        ]

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
        assert result.origins.get("context:child:context/notes.md") == [
            Origin("child", None)
        ]

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
        assert result.origins.get("context:child:context/notes.md") == [
            Origin("child", None)
        ]
        # After resolution the key is unchanged (pending key == final context key),
        # so the provenance lookup still works
        assert "context:child:context/notes.md" in result.origins


class TestMultiClaimantProvenance:
    """Tests for multi-claimant provenance tracking (list[Origin] values)."""

    def test_multi_claimant_provenance_type(self) -> None:
        """origins values are list[Origin] not list[str]."""
        base = Bundle(name="base", tools=[{"module": "tool-bash"}])
        child = Bundle(name="child", tools=[{"module": "tool-python"}])
        result = base.compose(child)
        for value in result.origins.values():
            assert isinstance(value, list), (
                f"Expected list, got {type(value)}: {value!r}"
            )
            for item in value:
                assert isinstance(item, Origin), (
                    f"Expected Origin items, got {type(item)}: {item!r}"
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
        assert result.origins["tool:shared-tool"] == [Origin("base", None)]

    def test_multi_claimant_no_duplicates(self) -> None:
        """Same bundle claiming same item multiple times results in no duplicates."""
        a = Bundle(name="a", tools=[{"module": "tool-x"}])
        # Compose a with itself — a claims tool-x twice
        result = a.compose(a)
        # "a" should appear only once (deduplicated by (bundle, via_behavior))
        bundles = [o.bundle for o in result.origins["tool:tool-x"]]
        assert bundles.count("a") == 1

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
        claimants = [o.bundle for o in result.origins["context:shared:context/foo.md"]]
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
        claimants = [o.bundle for o in result.origins["agent:shared-agent"]]
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
        bundles_ab = [o.bundle for o in ab.origins["tool:tool-x"]]
        assert "a" in bundles_ab
        assert "b" not in bundles_ab

        # Level 3: d also lists tool-x (still already in result)
        d = Bundle(name="d", tools=[{"module": "tool-x"}])
        result = ab.compose(d)
        # Still only "a" is the original introducer
        bundles_result = [o.bundle for o in result.origins["tool:tool-x"]]
        assert "a" in bundles_result
        assert "b" not in bundles_result
        assert "d" not in bundles_result


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
        assert result.origins["tool:tool-todo"] == [Origin("foundation", None)]
        # behavior-a added a new tool
        assert result.origins["tool:tool-a-only"] == [Origin("behavior-a", None)]
        # behavior-b added a new tool
        assert result.origins["tool:tool-b-only"] == [Origin("behavior-b", None)]

    def test_only_new_tools_tagged(self) -> None:
        """Only tools that weren't in result before the merge are attributed to other.

        When A has tool-x and B has both tool-x (inherited) and tool-y (new),
        composing A with B should attribute tool-x only to A and tool-y only to B.
        """
        a = Bundle(name="a", tools=[{"module": "tool-x"}])
        b = Bundle(name="b", tools=[{"module": "tool-x"}, {"module": "tool-y"}])
        result = a.compose(b)

        # tool-x was already in result from a — don't attribute to b
        assert result.origins["tool:tool-x"] == [Origin("a", None)]
        # tool-y is genuinely new from b
        assert result.origins["tool:tool-y"] == [Origin("b", None)]

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
        assert b.origins["tool:tool-x"] == [Origin("a", None)]
        assert b.origins["tool:tool-y"] == [Origin("b", None)]

        # Level 3: c adds tool-z; compose with b (which carries tool-x and tool-y)
        c_raw = Bundle(name="c", tools=[{"module": "tool-z"}])
        result = b.compose(c_raw)

        # tool-x: originated in a, passed through b — still only a (with via_behavior=b)
        assert any(o.bundle == "a" for o in result.origins["tool:tool-x"])
        # tool-y: originated in b — still only b
        assert any(o.bundle == "b" for o in result.origins["tool:tool-y"])
        # tool-z: new from c
        assert result.origins["tool:tool-z"] == [Origin("c", None)]


class TestSessionSpawnInstructionProvenance:
    """New tests: session/spawn/instruction keys in origins after compose()."""

    def test_session_orchestrator_provenance(self) -> None:
        """compose() tracks which bundle introduced session.orchestrator."""
        base = Bundle(name="base")
        child = Bundle(
            name="child",
            session={
                "orchestrator": {
                    "module": "orchestrator-loop",
                    "source": "git+https://...",
                }
            },
        )
        result = base.compose(child)
        assert "session.orchestrator:orchestrator-loop" in result.origins
        bundles = [
            o.bundle for o in result.origins["session.orchestrator:orchestrator-loop"]
        ]
        assert "child" in bundles

    def test_session_context_provenance(self) -> None:
        """compose() tracks which bundle introduced session.context."""
        base = Bundle(name="base")
        child = Bundle(
            name="child",
            session={
                "context": {"module": "context-manager", "source": "git+https://..."}
            },
        )
        result = base.compose(child)
        assert "session.context:context-manager" in result.origins
        bundles = [o.bundle for o in result.origins["session.context:context-manager"]]
        assert "child" in bundles

    def test_spawn_key_provenance(self) -> None:
        """compose() tracks which bundle introduced spawn keys."""
        base = Bundle(name="base")
        child = Bundle(name="child", spawn={"exclude_tools": ["bash"]})
        result = base.compose(child)
        assert "spawn:exclude_tools" in result.origins
        bundles = [o.bundle for o in result.origins["spawn:exclude_tools"]]
        assert "child" in bundles

    def test_instruction_provenance(self) -> None:
        """compose() tracks which bundle introduced the instruction."""
        base = Bundle(name="base")
        child = Bundle(name="child", instruction="You are a helpful assistant.")
        result = base.compose(child)
        assert "instruction:" in result.origins
        bundles = [o.bundle for o in result.origins["instruction:"]]
        assert "child" in bundles

    def test_origin_chain_transitive(self) -> None:
        """Three-level: Origin chain captures A→B→X via via_behavior.

        When root.compose(ab) is called (root is the base, ab is composed in),
        and ab was itself composed from a (which introduced tool-x),
        the origins chain should contain Origin("a", via_behavior="b") — capturing
        that tool-x came from "a" via the intermediate bundle "b" (ab.name="b").

        Key: root must be the BASE (self), ab must be the OTHER being composed.
        Phase 2 overlay is only triggered when tool-x is NEW to root.
        """
        a = Bundle(name="a", tools=[{"module": "tool-x"}])
        b_raw = Bundle(name="b", tools=[{"module": "tool-y"}])
        b = a.compose(b_raw)

        # b.origins should have tool-x attributed to "a" with no via_behavior
        assert b.origins["tool:tool-x"] == [Origin("a", None)]

        # root.compose(b): root is the base (empty), b is composed in as "other".
        # tool-x is new to root, so phase 2 overlays b.origins["tool:tool-x"]
        # with via_behavior = b.name = "b".
        root = Bundle(name="root", tools=[])
        result = root.compose(b)

        # The origin chain for tool-x should contain "a" with via_behavior="b"
        chains = result.origins.get("tool:tool-x", [])
        assert any(o.bundle == "a" for o in chains), (
            f"Expected Origin with bundle='a' in {chains}"
        )
        # via_behavior captures the intermediate bundle "b"
        via_b = [o for o in chains if o.via_behavior == "b"]
        assert len(via_b) > 0, f"Expected Origin with via_behavior='b' in {chains}"


class TestPhase2ChainPreservation:
    """Tests for the container-provenance chain built by tag_container_provenance.

    These tests verify that the registry's post-compose tagging step correctly
    builds the origin chain entries described in the spec:

        Origin(Z, None)      # direct claimant (behavior-apply-patch)
        Origin(Y, "Z")       # Y (foundation) carries it via Z
        Origin(X, "Y")       # X (amplifier-dev) carries it via Y

    tag_container_provenance() is called by BundleRegistry._load_single after
    _compose_includes completes.  Direct Bundle.compose() calls in tests do NOT
    automatically invoke it — callers must call it explicitly when simulating
    the registry flow.
    """

    def test_two_level_chain_direct_claimant_and_container(self) -> None:
        """Direct claimant (Z) + container (Y) both appear after tag_container_provenance.

        Simulates: foundation includes behavior-apply-patch.
        After _compose_includes and tag_container_provenance("foundation"):
          - Origin("behavior-apply-patch", None)     <- direct claimant (Phase 1)
          - Origin("foundation", "behavior-apply-patch")  <- container (tag_container_provenance)
        """
        beh_ap = Bundle(name="behavior-apply-patch", tools=[{"module": "tool-T"}])
        foundation_raw = Bundle(name="foundation")

        # Simulate registry._compose_includes: included_bundle.compose(outer_raw)
        foundation_composed = beh_ap.compose(foundation_raw)
        # Simulate registry._load_single calling tag_container_provenance
        tag_container_provenance(foundation_composed)

        origins = foundation_composed.origins.get("tool:tool-T", [])
        bundles = {o.bundle for o in origins}

        # Direct claimant must be present
        assert "behavior-apply-patch" in bundles, (
            f"Expected 'behavior-apply-patch' in {origins}"
        )
        # Container (foundation) must also be present
        assert "foundation" in bundles, f"Expected 'foundation' in {origins}"

        # Foundation's via_behavior must point to the direct claimant
        foundation_entry = next((o for o in origins if o.bundle == "foundation"), None)
        assert foundation_entry is not None
        assert foundation_entry.via_behavior == "behavior-apply-patch", (
            f"Expected via_behavior='behavior-apply-patch', got {foundation_entry}"
        )

    def test_three_level_chain_abc(self) -> None:
        """Three-level A→B→C: all three levels appear in the final chain.

        For deeper nesting, each level adds exactly one Origin entry:
          - Origin(C, None)    direct claimant
          - Origin(B, "C")     B includes C, carries via C
          - Origin(A, "B")     A includes B, carries via B
        """
        c = Bundle(name="C", tools=[{"module": "tool-T"}])
        b_raw = Bundle(name="B")

        # B._compose_includes: c.compose(b_raw)
        b_composed = c.compose(b_raw)
        tag_container_provenance(b_composed)

        # Verify B level: C (direct) + B (container via C)
        b_origins = b_composed.origins.get("tool:tool-T", [])
        b_bundles = {o.bundle for o in b_origins}
        assert "C" in b_bundles, f"Expected 'C' in {b_origins}"
        assert "B" in b_bundles, f"Expected 'B' in {b_origins}"

        # A._compose_includes: b_composed.compose(a_raw)
        a_raw = Bundle(name="A")
        a_composed = b_composed.compose(a_raw)
        tag_container_provenance(a_composed)

        a_origins = a_composed.origins.get("tool:tool-T", [])
        a_bundles = {o.bundle for o in a_origins}

        assert "C" in a_bundles, f"Expected 'C' in {a_origins}"
        assert "B" in a_bundles, f"Expected 'B' in {a_origins}"
        assert "A" in a_bundles, f"Expected 'A' in {a_origins}"

        # Check via_behavior links form a proper chain
        b_entry = next(o for o in a_origins if o.bundle == "B")
        assert b_entry.via_behavior == "C", f"B's via_behavior should be 'C': {b_entry}"

        a_entry = next(o for o in a_origins if o.bundle == "A")
        assert a_entry.via_behavior == "B", f"A's via_behavior should be 'B': {a_entry}"

    def test_direct_provider_not_double_tagged(self) -> None:
        """A bundle that directly provides a tool is not tagged again as container.

        When foundation itself lists tool-bash in its tools, tag_container_provenance
        must NOT add a spurious Origin("foundation", ...) entry for tool-bash — it
        was already attributed as Origin("foundation", None) by Phase 1.
        """
        beh_ap = Bundle(name="behavior-apply-patch", tools=[{"module": "tool-T"}])
        foundation_raw = Bundle(
            name="foundation",
            tools=[{"module": "tool-bash"}],  # foundation directly provides tool-bash
        )

        # In _compose_includes, behaviors compose first; foundation_raw is last.
        foundation_composed = beh_ap.compose(foundation_raw)
        tag_container_provenance(foundation_composed)

        # tool-T: behavior-apply-patch (direct) + foundation (container)
        t_origins = foundation_composed.origins.get("tool:tool-T", [])
        assert any(o.bundle == "behavior-apply-patch" for o in t_origins)
        assert any(o.bundle == "foundation" for o in t_origins)

        # tool-bash: foundation is the direct claimant (Origin("foundation", None)).
        # tag_container_provenance must NOT add a duplicate/redundant foundation entry.
        bash_origins = foundation_composed.origins.get("tool:tool-bash", [])
        foundation_bash_entries = [o for o in bash_origins if o.bundle == "foundation"]
        assert len(foundation_bash_entries) == 1, (
            f"Expected exactly 1 'foundation' entry for tool-bash, "
            f"got {foundation_bash_entries}"
        )

    def test_no_self_referential_entries_in_phase2(self) -> None:
        """Phase 2 must not create self-referential Origin(bundle, via=bundle) entries.

        When a bundle B introduces tool-y directly, and root.compose(B) runs,
        Phase 2 previously created Origin("B", via_behavior="B") — a nonsensical
        self-referential entry.  The Phase 2 guard must prevent this.
        """
        a = Bundle(name="a", tools=[{"module": "tool-x"}])
        b_raw = Bundle(name="b", tools=[{"module": "tool-y"}])
        b = a.compose(b_raw)

        # b.origins for tool-y: only Origin("b", None) — b directly introduced it
        assert b.origins.get("tool:tool-y") == [Origin("b", None)]

        # root.compose(b): Phase 2 must NOT add Origin("b", "b")
        root = Bundle(name="root")
        result = root.compose(b)

        y_origins = result.origins.get("tool:tool-y", [])
        assert not any(o.bundle == "b" and o.via_behavior == "b" for o in y_origins), (
            f"Self-referential Origin('b', 'b') must not appear: {y_origins}"
        )
        # Only Origin("b", None) should be present for tool-y
        assert any(o.bundle == "b" for o in y_origins)

    def test_peer_compose_does_not_tag_container(self) -> None:
        """Peer compose (two independent behaviors) does not add container entries.

        tag_container_provenance is only called by the registry after _compose_includes.
        Direct compose() calls (as in tests and peer-bundle composition) must NOT
        automatically add container entries for each other's items.
        """
        a = Bundle(name="a", tools=[{"module": "tool-x"}])
        b_raw = Bundle(name="b", tools=[{"module": "tool-y"}])
        b = a.compose(b_raw)

        # Without tag_container_provenance, origins are exactly what Phase 1/2 set
        x_origins = b.origins.get("tool:tool-x", [])
        # "b" is NOT a direct claimant for tool-x (a introduced it)
        assert not any(o.bundle == "b" and o.via_behavior is None for o in x_origins)
        # "a" IS the direct claimant
        assert any(o.bundle == "a" for o in x_origins)
