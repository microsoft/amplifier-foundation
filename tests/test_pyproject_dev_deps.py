"""
Test that pyproject.toml dev dependency and optional-dependencies structure is correct.

This ensures that grpcio and protobuf are properly configured:
- As optional dependencies in the 'grpc-adapter' extra
- NOT in main project dependencies (to avoid imposing them on all users)
- In the dev group (so they're available for testing)

These tests prevent accidental moves to main dependencies that broke production previously.
"""

from pathlib import Path

import tomllib


def _get_pyproject() -> dict:
    """Load and return the parsed pyproject.toml."""
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        return tomllib.load(f)


def test_grpcio_in_optional_grpc_adapter() -> None:
    """grpcio must be in [project.optional-dependencies] grpc-adapter extra.

    This ensures grpcio is available only when the user explicitly requests it,
    not forced on all users of amplifier-foundation.
    """
    pyproject = _get_pyproject()
    extras = pyproject["project"]["optional-dependencies"]
    assert "grpc-adapter" in extras, "grpc-adapter extra not defined"
    assert any("grpcio" in dep for dep in extras["grpc-adapter"]), (
        f"grpcio not found in grpc-adapter optional-dependencies: {extras['grpc-adapter']}"
    )


def test_protobuf_in_optional_grpc_adapter() -> None:
    """protobuf must be in [project.optional-dependencies] grpc-adapter extra.

    This ensures protobuf is available only when the user explicitly requests it.
    """
    pyproject = _get_pyproject()
    extras = pyproject["project"]["optional-dependencies"]
    assert "grpc-adapter" in extras, "grpc-adapter extra not defined"
    assert any("protobuf" in dep for dep in extras["grpc-adapter"]), (
        f"protobuf not found in grpc-adapter optional-dependencies: {extras['grpc-adapter']}"
    )


def test_grpcio_not_in_main_dependencies() -> None:
    """grpcio must NOT be in main [project.dependencies] array.

    Moving grpcio to main dependencies breaks all foundation users by forcing
    gRPC/protobuf installation on everyone. A production incident occurred when
    this was violated.
    """
    pyproject = _get_pyproject()
    main_deps = pyproject["project"]["dependencies"]
    assert not any("grpcio" in dep for dep in main_deps), (
        f"grpcio must stay optional — moving it to main deps breaks all foundation users. "
        f"Found in main dependencies: {main_deps}"
    )


def test_grpcio_in_dev_dependency_group() -> None:
    """grpcio must be in the [dependency-groups] dev group.

    This ensures grpcio is available during testing and development
    via `uv sync --group dev`.
    """
    pyproject = _get_pyproject()
    dev_group = pyproject.get("dependency-groups", {}).get("dev", [])
    assert any("grpcio" in dep for dep in dev_group), (
        f"grpcio not found in dev dependency group. "
        f"Dev group must include grpcio for tests to run. Found: {dev_group}"
    )
