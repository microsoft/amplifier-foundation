"""Tests for grpc_adapter package."""


class TestGrpcAdapterPackage:
    """Tests that the grpc_adapter package is properly structured."""

    def test_package_is_importable(self) -> None:
        """grpc_adapter package can be imported."""
        import amplifier_foundation.grpc_adapter  # noqa: F401

    def test_package_has_docstring(self) -> None:
        """grpc_adapter __init__ has the required docstring."""
        import amplifier_foundation.grpc_adapter

        doc = amplifier_foundation.grpc_adapter.__doc__
        assert doc is not None, "Package must have a docstring"
        assert "Python gRPC adapter for Amplifier modules" in doc
        assert "Bridges Python modules to non-Python hosts via gRPC" in doc
        assert "python -m amplifier_foundation.grpc_adapter" in doc
        assert "docs/plans/2026-03-17-python-grpc-adapter-design.md" in doc
