"""Tests for YAML hygiene in recipe files.

Detects duplicate mapping keys in recipe YAML files.
PyYAML silently resolves duplicate mapping keys to the last value,
causing silent data loss. This test uses a strict loader that raises
an error when a duplicate key is encountered.

Bug class: duplicate `depends_on:` keys in a single step mapping.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Strict loader — raises on duplicate mapping keys
# ---------------------------------------------------------------------------


class _DuplicateKeyError(yaml.YAMLError):
    """Raised when a YAML mapping contains a duplicate key."""


class _StrictLoader(yaml.SafeLoader):
    """PyYAML loader that raises _DuplicateKeyError on duplicate mapping keys.

    PyYAML's default SafeLoader silently overwrites the first value when a
    mapping contains duplicate keys.  This subclass overrides
    ``construct_mapping`` to detect and reject such mappings before the
    default resolution occurs.
    """

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:  # type: ignore[override]
        # Resolve keys first so we can compare them properly.
        seen: dict[object, yaml.Node] = {}
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                mark = key_node.start_mark
                raise _DuplicateKeyError(
                    f"Duplicate YAML key {key!r} at "
                    f"line {mark.line + 1}, column {mark.column + 1}"
                    f" in {mark.name!r}"
                )
            seen[key] = key_node
        return super().construct_mapping(node, deep=deep)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

RECIPE_DIR = Path(__file__).parent.parent / "recipes"


def _load_strict(path: Path) -> None:
    """Load *path* with the strict loader; raises _DuplicateKeyError on dups."""
    with path.open(encoding="utf-8") as fh:
        yaml.load(fh, Loader=_StrictLoader)  # noqa: S506 — intentional custom loader


def _recipe_paths() -> list[Path]:
    """Return all *.yaml files under the recipes directory."""
    return sorted(RECIPE_DIR.glob("*.yaml"))


# ---------------------------------------------------------------------------
# Unit tests for the strict loader itself
# ---------------------------------------------------------------------------


class TestStrictLoader:
    """Verify the strict loader correctly detects duplicate keys."""

    def test_clean_yaml_loads_without_error(self) -> None:
        """A YAML file with no duplicate keys must load cleanly."""
        clean = """
        name: example
        version: "1.0.0"
        steps:
          - id: step-one
            depends_on: ["setup"]
          - id: step-two
            depends_on: ["step-one"]
        """
        yaml.load(clean, Loader=_StrictLoader)  # must not raise

    def test_duplicate_top_level_key_raises(self) -> None:
        """A duplicate top-level key must raise _DuplicateKeyError."""
        dup = """
        name: first
        name: second
        """
        with pytest.raises(_DuplicateKeyError, match="Duplicate YAML key 'name'"):
            yaml.load(dup, Loader=_StrictLoader)

    def test_duplicate_nested_key_raises(self) -> None:
        """A duplicate key inside a nested mapping must raise _DuplicateKeyError."""
        dup = """
        steps:
          - id: my-step
            depends_on: ["setup"]
            prompt: "do something"
            depends_on: ["quality-classification"]
        """
        with pytest.raises(_DuplicateKeyError, match="Duplicate YAML key 'depends_on'"):
            yaml.load(dup, Loader=_StrictLoader)

    def test_duplicate_depends_on_in_list_item_raises(self) -> None:
        """Duplicate depends_on inside a list-item mapping must be caught."""
        dup = """
        - id: composition-analysis
          depends_on: ["set-default-composition-analysis"]
          output: composition_analysis
          depends_on: ["quality-classification"]
        """
        with pytest.raises(_DuplicateKeyError, match="Duplicate YAML key 'depends_on'"):
            yaml.load(dup, Loader=_StrictLoader)


# ---------------------------------------------------------------------------
# Integration tests: all recipe YAML files must be duplicate-key-free
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "recipe_path",
    _recipe_paths(),
    ids=lambda p: p.name,
)
def test_recipe_has_no_duplicate_yaml_keys(recipe_path: Path) -> None:
    """Every recipe YAML file must contain no duplicate mapping keys.

    PyYAML silently discards the first of any duplicate keys, causing
    silent data loss.  The canonical example is two ``depends_on:`` keys
    in a single step: the first is silently dropped, leaving the step
    with an incorrect dependency list.
    """
    try:
        _load_strict(recipe_path)
    except _DuplicateKeyError as exc:
        pytest.fail(
            f"{recipe_path.name} contains a duplicate YAML mapping key.\n"
            f"PyYAML silently resolves duplicates to the last value, "
            f"causing silent data loss.\n\n"
            f"Detail: {exc}"
        )
