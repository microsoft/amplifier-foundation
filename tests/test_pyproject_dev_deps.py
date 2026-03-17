"""
Test that pyproject.toml dev dependency group includes required gRPC/protobuf packages.

This ensures that grpcio and protobuf are available as dev dependencies so that
test coverage for the gRPC adapter works correctly.
"""

from pathlib import Path

import tomllib


def _get_dev_deps() -> list[str]:
    pyproject_path = Path(__file__).parent.parent / "pyproject.toml"
    with open(pyproject_path, "rb") as f:
        data = tomllib.load(f)
    return data.get("dependency-groups", {}).get("dev", [])


def test_grpcio_in_dev_deps():
    """grpcio>=1.60.0 must be in the dev dependency group."""
    dev_deps = _get_dev_deps()
    assert any("grpcio" in dep for dep in dev_deps), (
        f"grpcio not found in dev dependencies: {dev_deps}"
    )


def test_protobuf_in_dev_deps():
    """protobuf>=4.0.0 must be in the dev dependency group."""
    dev_deps = _get_dev_deps()
    assert any("protobuf" in dep for dep in dev_deps), (
        f"protobuf not found in dev dependencies: {dev_deps}"
    )


def test_dev_deps_alphabetical_order():
    """Dev dependencies must be in alphabetical order."""
    dev_deps = _get_dev_deps()
    # Extract package names (before >=)
    names = [
        dep.split(">=")[0].split("==")[0].split("!=")[0].strip() for dep in dev_deps
    ]
    assert names == sorted(names), (
        f"Dev dependencies are not in alphabetical order: {names}"
    )


def test_dev_deps_complete_set():
    """Dev dependency group must contain exactly the required packages."""
    dev_deps = _get_dev_deps()
    required = {
        "grpcio",
        "notebook",
        "protobuf",
        "pytest",
        "pytest-asyncio",
        "pytest-timeout",
    }
    present = {
        dep.split(">=")[0].split("==")[0].split("!=")[0].strip() for dep in dev_deps
    }
    missing = required - present
    assert not missing, f"Missing dev dependencies: {missing}"
