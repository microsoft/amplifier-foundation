"""Tests for the Bundle.routing field (opaque passthrough for bundle-declared
default routing matrix configuration).

Foundation stores and merges this dict; it does not interpret or validate
its contents (e.g. `matrix`, `overrides`). A separate app-cli PR consumes it.
"""

from amplifier_foundation.bundle import Bundle


class TestBundleRoutingFromDict:
    """Tests for Bundle.from_dict's handling of the routing field."""

    def test_from_dict_no_routing_key_yields_empty_dict(self) -> None:
        """A bundle dict with no 'routing' key produces bundle.routing == {}.

        This is the single most important property: existing bundles with
        no routing: key must behave byte-identically to today.
        """
        data = {"bundle": {"name": "test"}}
        bundle = Bundle.from_dict(data)
        assert bundle.routing == {}

    def test_from_dict_reads_routing_matrix_and_overrides(self) -> None:
        """routing: {matrix, overrides} is read through unchanged (opaque)."""
        data = {
            "bundle": {"name": "test"},
            "routing": {
                "matrix": "openai",
                "overrides": {"coding": {"model": "gpt-5"}},
            },
        }
        bundle = Bundle.from_dict(data)
        assert bundle.routing == {
            "matrix": "openai",
            "overrides": {"coding": {"model": "gpt-5"}},
        }

    def test_from_dict_non_dict_routing_coerces_to_empty(self) -> None:
        """A malformed 'routing' value (bare string) coerces to {} instead
        of raising -- foundation does not validate routing semantics."""
        data = {"bundle": {"name": "test"}, "routing": "openai"}
        bundle = Bundle.from_dict(data)
        assert bundle.routing == {}


class TestBundleRoutingCompose:
    """Tests for Bundle.compose's handling of the routing field."""

    def test_compose_overlay_routing_wins_over_base(self) -> None:
        """Later bundle's routing scalar values win over the base's."""
        base = Bundle(name="base", routing={"matrix": "anthropic"})
        overlay = Bundle(name="overlay", routing={"matrix": "openai"})
        result = base.compose(overlay)
        assert result.routing["matrix"] == "openai"

    def test_compose_deep_merge_preserves_base_matrix_when_overlay_sets_only_overrides(
        self,
    ) -> None:
        """An overlay declaring only 'overrides' keeps the base's 'matrix'
        (deep merge, not replace)."""
        base = Bundle(
            name="base",
            routing={"matrix": "anthropic", "overrides": {"coding": {"x": 1}}},
        )
        overlay = Bundle(
            name="overlay",
            routing={"overrides": {"coding": {"y": 2}}},
        )
        result = base.compose(overlay)
        assert result.routing["matrix"] == "anthropic"
        assert result.routing["overrides"] == {"coding": {"x": 1, "y": 2}}

    def test_compose_base_routing_survives_overlay_without_routing(self) -> None:
        """An overlay bundle with no routing at all does not clobber the
        base's routing config."""
        base = Bundle(name="base", routing={"matrix": "anthropic"})
        overlay = Bundle(name="overlay")
        result = base.compose(overlay)
        assert result.routing == {"matrix": "anthropic"}


class TestBundleRoutingMountPlan:
    """Tests for Bundle.to_mount_plan's handling of the routing field."""

    def test_to_mount_plan_omits_routing(self) -> None:
        """routing is deliberately absent from the mount plan -- it is
        app-layer policy, not a kernel-facing concern."""
        bundle = Bundle(name="test", routing={"matrix": "anthropic"})
        plan = bundle.to_mount_plan()
        assert "routing" not in plan
