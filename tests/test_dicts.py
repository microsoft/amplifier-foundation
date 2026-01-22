"""Tests for dict utilities."""

import pytest

from amplifier_foundation.dicts.merge import deep_merge
from amplifier_foundation.dicts.merge import merge_module_lists
from amplifier_foundation.dicts.navigation import get_nested
from amplifier_foundation.dicts.navigation import set_nested


class TestDeepMerge:
    """Tests for deep_merge function."""

    def test_empty_dicts(self) -> None:
        """Empty dicts merge to empty dict."""
        assert deep_merge({}, {}) == {}

    def test_child_overrides_parent_scalars(self) -> None:
        """Child scalars override parent scalars."""
        parent = {"a": 1, "b": 2}
        child = {"b": 3, "c": 4}
        result = deep_merge(parent, child)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_dict_merge(self) -> None:
        """Nested dicts are merged recursively."""
        parent = {"config": {"a": 1, "b": 2}}
        child = {"config": {"b": 3, "c": 4}}
        result = deep_merge(parent, child)
        assert result == {"config": {"a": 1, "b": 3, "c": 4}}

    def test_child_list_replaces_parent_list(self) -> None:
        """Child lists replace parent lists entirely."""
        parent = {"items": [1, 2, 3]}
        child = {"items": [4, 5]}
        result = deep_merge(parent, child)
        assert result == {"items": [4, 5]}

    def test_parent_unchanged(self) -> None:
        """Original parent dict is not mutated."""
        parent = {"a": {"b": 1}}
        child = {"a": {"c": 2}}
        deep_merge(parent, child)
        assert parent == {"a": {"b": 1}}


class TestMergeModuleLists:
    """Tests for merge_module_lists function."""

    def test_empty_lists(self) -> None:
        """Empty lists merge to empty list."""
        assert merge_module_lists([], []) == []

    def test_child_adds_new_modules(self) -> None:
        """Child modules not in parent are added."""
        parent = [{"module": "a"}]
        child = [{"module": "b"}]
        result = merge_module_lists(parent, child)
        assert len(result) == 2
        assert {"module": "a"} in result
        assert {"module": "b"} in result

    def test_child_config_overrides_parent(self) -> None:
        """Child config overrides parent config for same module."""
        parent = [{"module": "a", "config": {"x": 1, "y": 2}}]
        child = [{"module": "a", "config": {"y": 3, "z": 4}}]
        result = merge_module_lists(parent, child)
        assert len(result) == 1
        assert result[0]["module"] == "a"
        assert result[0]["config"] == {"x": 1, "y": 3, "z": 4}

    def test_preserves_order(self) -> None:
        """Parent modules come before new child modules."""
        parent = [{"module": "a"}, {"module": "b"}]
        child = [{"module": "c"}]
        result = merge_module_lists(parent, child)
        modules = [m["module"] for m in result]
        assert modules == ["a", "b", "c"]

    def test_raises_typeerror_on_string_in_parent(self) -> None:
        """Raises TypeError when parent list contains a string instead of dict."""
        parent = ["tool-bash", {"module": "tool-file"}]  # type: ignore[list-item]
        child: list[dict[str, str]] = []
        with pytest.raises(TypeError) as exc_info:
            merge_module_lists(parent, child)  # type: ignore[arg-type]
        assert "Malformed module config at index 0" in str(exc_info.value)
        assert "expected dict with 'module' key" in str(exc_info.value)
        assert "got str" in str(exc_info.value)
        assert "'tool-bash'" in str(exc_info.value)

    def test_raises_typeerror_on_string_in_child(self) -> None:
        """Raises TypeError when child list contains a string instead of dict."""
        parent = [{"module": "tool-file"}]
        child = [{"module": "tool-bash"}, "provider-anthropic"]  # type: ignore[list-item]
        with pytest.raises(TypeError) as exc_info:
            merge_module_lists(parent, child)  # type: ignore[arg-type]
        assert "Malformed module config at index 1" in str(exc_info.value)
        assert "expected dict with 'module' key" in str(exc_info.value)
        assert "got str" in str(exc_info.value)
        assert "'provider-anthropic'" in str(exc_info.value)

    def test_raises_typeerror_on_non_dict_types(self) -> None:
        """Raises TypeError for various non-dict types in list."""
        # Test with integer
        with pytest.raises(TypeError) as exc_info:
            merge_module_lists([123], [])  # type: ignore[list-item]
        assert "got int" in str(exc_info.value)

        # Test with list inside list
        with pytest.raises(TypeError) as exc_info:
            merge_module_lists([[{"module": "nested"}]], [])  # type: ignore[list-item]
        assert "got list" in str(exc_info.value)


class TestGetNested:
    """Tests for get_nested function."""

    def test_simple_path(self) -> None:
        """Gets value at simple path."""
        data = {"a": {"b": {"c": 1}}}
        assert get_nested(data, ["a", "b", "c"]) == 1

    def test_missing_path_returns_default(self) -> None:
        """Missing path returns default value."""
        data = {"a": 1}
        assert get_nested(data, ["a", "b", "c"]) is None
        assert get_nested(data, ["x", "y"], default="missing") == "missing"

    def test_empty_path_returns_data(self) -> None:
        """Empty path returns the data itself."""
        data = {"a": 1}
        assert get_nested(data, []) == data


class TestSetNested:
    """Tests for set_nested function."""

    def test_simple_path(self) -> None:
        """Sets value at simple path."""
        data: dict = {}
        set_nested(data, ["a", "b", "c"], 1)
        assert data == {"a": {"b": {"c": 1}}}

    def test_overwrites_existing(self) -> None:
        """Overwrites existing value."""
        data = {"a": {"b": 1}}
        set_nested(data, ["a", "b"], 2)
        assert data == {"a": {"b": 2}}

    def test_creates_intermediate_dicts(self) -> None:
        """Creates intermediate dicts as needed."""
        data: dict = {}
        set_nested(data, ["a", "b", "c", "d"], "value")
        assert data["a"]["b"]["c"]["d"] == "value"
