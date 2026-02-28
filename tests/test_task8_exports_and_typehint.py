"""Tests for task-8: Foundation exports and bundle.py spawn type hint."""

from __future__ import annotations

import inspect


class TestFoundationExports:
    """Verify ClassPreference, RoutingConfig, preference_from_dict, resolve_model_class are exported."""

    def test_import_class_preference(self) -> None:
        from amplifier_foundation import ClassPreference

        assert ClassPreference is not None

    def test_import_routing_config(self) -> None:
        from amplifier_foundation import RoutingConfig

        assert RoutingConfig is not None

    def test_import_preference_from_dict(self) -> None:
        from amplifier_foundation import preference_from_dict

        assert callable(preference_from_dict)

    def test_import_resolve_model_class(self) -> None:
        from amplifier_foundation import resolve_model_class

        assert callable(resolve_model_class)

    def test_all_in_dunder_all(self) -> None:
        import amplifier_foundation

        for name in [
            "ClassPreference",
            "RoutingConfig",
            "preference_from_dict",
            "resolve_model_class",
        ]:
            assert name in amplifier_foundation.__all__, f"{name} not in __all__"


class TestSpawnTypeHint:
    """Verify PreparedBundle.spawn() provider_preferences has proper union type."""

    def test_spawn_provider_preferences_typed_union(self) -> None:
        """The spawn() parameter should have a proper ClassPreference | ProviderPreference union type."""
        from amplifier_foundation.bundle import PreparedBundle

        sig = inspect.signature(PreparedBundle.spawn)
        param = sig.parameters["provider_preferences"]
        annotation = param.annotation

        # With `from __future__ import annotations`, annotations are strings
        ann_str = str(annotation)

        # Should include both preference types in the union
        assert "ClassPreference" in ann_str, (
            f"Expected 'ClassPreference' in annotation, got: {ann_str}"
        )
        assert "ProviderPreference" in ann_str, (
            f"Expected 'ProviderPreference' in annotation, got: {ann_str}"
        )
        # Should be optional (None)
        assert "None" in ann_str, f"Expected 'None' in annotation, got: {ann_str}"
