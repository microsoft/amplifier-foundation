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


def test_grpcio_optional_version_floor_is_1_78_0() -> None:
    """grpcio in [project.optional-dependencies] grpc-adapter must specify >=1.78.0.

    The generated stubs in amplifier_core/_grpc_gen/amplifier_module_pb2_grpc.py
    enforce GRPC_GENERATED_VERSION = '1.78.0' with a RuntimeError on mismatch.
    Specifying >=1.60.0 allows installations that fail at runtime with:
        RuntimeError: The grpc package installed is at version ..., but the
        generated code in amplifier_module_pb2_grpc.py depends on grpclib>=1.78.0
    """
    import re

    pyproject = _get_pyproject()
    extras = pyproject["project"]["optional-dependencies"]
    grpc_adapter = extras.get("grpc-adapter", [])
    grpcio_deps = [dep for dep in grpc_adapter if "grpcio" in dep]
    assert grpcio_deps, "grpcio not found in grpc-adapter optional-dependencies"

    grpcio_dep = grpcio_deps[0]
    match = re.search(r">=(\d+\.\d+\.\d+)", grpcio_dep)
    assert match, f"grpcio optional dependency lacks a version floor: {grpcio_dep}"

    floor = tuple(int(x) for x in match.group(1).split("."))
    assert floor >= (1, 78, 0), (
        f"grpcio version floor must be >=1.78.0 to match generated stub requirement "
        f"(GRPC_GENERATED_VERSION = '1.78.0' in amplifier_module_pb2_grpc.py). "
        f"Current: {grpcio_dep}"
    )


def test_grpcio_dev_group_version_floor_is_1_78_0() -> None:
    """grpcio in [dependency-groups] dev must also specify >=1.78.0.

    The dev group is used for running tests. If the floor is too low,
    a developer could have grpcio 1.60-1.77 installed which satisfies the
    dev dependency but causes a RuntimeError when gRPC stubs are imported.
    """
    import re

    pyproject = _get_pyproject()
    dev_group = pyproject.get("dependency-groups", {}).get("dev", [])
    grpcio_deps = [dep for dep in dev_group if "grpcio" in dep]
    assert grpcio_deps, "grpcio not found in dev dependency group"

    grpcio_dep = grpcio_deps[0]
    match = re.search(r">=(\d+\.\d+\.\d+)", grpcio_dep)
    assert match, f"grpcio dev dependency lacks a version floor: {grpcio_dep}"

    floor = tuple(int(x) for x in match.group(1).split("."))
    assert floor >= (1, 78, 0), (
        f"grpcio dev version floor must be >=1.78.0 to match generated stub requirement. "
        f"Current: {grpcio_dep}"
    )
