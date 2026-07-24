"""Relative module sources must resolve against their DECLARING bundle.

Regression oracle for the loop-streaming activation bug: when a bundle that
declares a relative ``session.orchestrator.source`` (e.g. ``./modules/loop-streaming``)
is composed *onto* another bundle with a different ``base_path`` (an app-level
behavior such as adhd-always-on), the orchestrator source was resolved against
the WRONG base_path — the host/behavior directory — producing
``.../behaviors/modules/loop-streaming`` (File not found) and silently dropping
the orchestrator config.

Correct behavior: a bundle's own relative module sources are anchored to that
bundle's own ``base_path``, independent of composition order. Absolute / URL
sources (git+https, file://) must be left untouched.
"""

from pathlib import Path

from amplifier_foundation.bundle import Bundle

DECLARING_BASE = Path("/tmp/amplifier-next-root").resolve()
HOST_BASE = Path("/tmp/adhd-behavior-root").resolve()


def _declaring_bundle() -> Bundle:
    return Bundle.from_dict(
        {
            "bundle": {"name": "amplifier-next"},
            "session": {
                "orchestrator": {
                    "module": "loop-streaming",
                    "source": "./modules/loop-streaming",
                }
            },
            "tools": [
                {
                    "module": "tool-remote",
                    "source": "git+https://github.com/microsoft/x@main",
                }
            ],
        },
        base_path=DECLARING_BASE,
    )


def _host_bundle() -> Bundle:
    return Bundle.from_dict(
        {"bundle": {"name": "adhd-always-on"}},
        base_path=HOST_BASE,
    )


def test_orchestrator_relative_source_anchored_to_declaring_bundle():
    """Composed onto a different-base host, the orchestrator source stays under its own root."""
    composed = _host_bundle().compose(_declaring_bundle())

    src = composed.to_mount_plan()["session"]["orchestrator"]["source"]
    resolved = Path(src)

    assert resolved.is_absolute(), f"source must be resolved to an absolute path, got {src!r}"
    assert str(resolved).startswith(str(DECLARING_BASE)), (
        f"orchestrator source must resolve under the declaring bundle {DECLARING_BASE}, got {src!r}"
    )
    assert str(HOST_BASE) not in str(resolved), (
        f"orchestrator source must NOT resolve under the host/behavior base {HOST_BASE}, got {src!r}"
    )
    assert resolved.name == "loop-streaming"


def test_non_relative_sources_left_untouched():
    """git+https / URL sources must never be rewritten."""
    composed = _host_bundle().compose(_declaring_bundle())
    tools = composed.to_mount_plan()["tools"]
    remote = next(t for t in tools if t["module"] == "tool-remote")
    assert remote["source"] == "git+https://github.com/microsoft/x@main"
