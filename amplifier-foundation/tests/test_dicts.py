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

    def test_child_list_concatenates_with_parent_list(self) -> None:
        """Child lists are concatenated with parent lists (not replaced)."""
        parent = {"items": [1, 2, 3]}
        child = {"items": [4, 5]}
        result = deep_merge(parent, child)
        assert result == {"items": [1, 2, 3, 4, 5]}

    def test_list_concatenation_deduplicates(self) -> None:
        """Duplicate entries are removed during list concatenation."""
        parent = {"items": [1, 2, 3]}
        child = {"items": [2, 3, 4]}
        result = deep_merge(parent, child)
        assert result == {"items": [1, 2, 3, 4]}

    def test_list_concatenation_with_strings(self) -> None:
        """String lists are concatenated and deduplicated."""
        parent = {"skills": ["a", "b"]}
        child = {"skills": ["b", "c"]}
        result = deep_merge(parent, child)
        assert result == {"skills": ["a", "b", "c"]}

    def test_list_concatenation_with_dicts(self) -> None:
        """Lists of dicts are concatenated (no deduplication needed for distinct dicts)."""
        parent = {"modules": [{"x": 1}]}
        child = {"modules": [{"y": 2}]}
        result = deep_merge(parent, child)
        assert result == {"modules": [{"x": 1}, {"y": 2}]}

    def test_list_concatenation_preserves_order(self) -> None:
        """Parent items come first, child items are appended after."""
        parent = {"items": ["p1", "p2"]}
        child = {"items": ["c1", "c2"]}
        result = deep_merge(parent, child)
        assert result == {"items": ["p1", "p2", "c1", "c2"]}

    def test_nested_dict_with_list_concatenation(self) -> None:
        """Real-world scenario: nested config.skills lists from two behaviors are merged."""
        parent = {
            "config": {
                "skills": ["git+https://example.com/bundle-a@main#subdirectory=skills"]
            }
        }
        child = {
            "config": {
                "skills": ["git+https://example.com/bundle-b@main#subdirectory=skills"]
            }
        }
        result = deep_merge(parent, child)
        assert result == {
            "config": {
                "skills": [
                    "git+https://example.com/bundle-a@main#subdirectory=skills",
                    "git+https://example.com/bundle-b@main#subdirectory=skills",
                ]
            }
        }

    def test_session_config_with_list_values(self) -> None:
        """Verify concatenation behavior for session-level list config."""
        parent = {"orchestrator": {"config": {"allowed_tools": ["tool-A"]}}}
        child = {"orchestrator": {"config": {"allowed_tools": ["tool-B"]}}}
        result = deep_merge(parent, child)
        assert result["orchestrator"]["config"]["allowed_tools"] == ["tool-A", "tool-B"]

    def test_list_dedup_insensitive_to_dict_key_order(self) -> None:
        """Same dict content with different key order should deduplicate."""
        parent = {"items": [{"module": "tool-bash", "source": "git+x"}]}
        child = {"items": [{"source": "git+x", "module": "tool-bash"}]}
        result = deep_merge(parent, child)
        assert len(result["items"]) == 1

    def test_no_cross_type_dedup_collision(self) -> None:
        """String that looks like a dict repr should not collide with actual dict."""
        parent = {"items": [{"a": 1}]}
        child = {"items": ["{'a': 1}"]}
        result = deep_merge(parent, child)
        assert len(result["items"]) == 2  # both survive — different types

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


class TestMergeModuleListsMultiInstance:
    """Tests for multi-instance provider support via optional id field."""

    def test_merge_module_lists_with_same_module_different_id(self) -> None:
        """Two entries with same module but different id should both survive merge."""
        parent = [
            {
                "module": "provider-anthropic",
                "id": "anthropic-team-a",
                "config": {"key": "a"},
            },
        ]
        child = [
            {
                "module": "provider-anthropic",
                "id": "anthropic-team-b",
                "config": {"key": "b"},
            },
        ]
        result = merge_module_lists(parent, child)
        assert len(result) == 2
        ids = [r.get("id") for r in result]
        assert "anthropic-team-a" in ids
        assert "anthropic-team-b" in ids

    def test_merge_module_lists_without_id_uses_module(self) -> None:
        """Backward compat: entries without id merge on module as before."""
        parent = [{"module": "provider-anthropic", "config": {"x": 1, "y": 2}}]
        child = [{"module": "provider-anthropic", "config": {"y": 3, "z": 4}}]
        result = merge_module_lists(parent, child)
        assert len(result) == 1
        assert result[0]["module"] == "provider-anthropic"
        assert result[0]["config"] == {"x": 1, "y": 3, "z": 4}

    def test_merge_module_lists_same_id_merges(self) -> None:
        """Two entries with the same id should merge (deep merge on config)."""
        parent = [
            {
                "module": "provider-anthropic",
                "id": "anthropic-prod",
                "config": {"x": 1, "y": 2},
            },
        ]
        child = [
            {
                "module": "provider-anthropic",
                "id": "anthropic-prod",
                "config": {"y": 3, "z": 4},
            },
        ]
        result = merge_module_lists(parent, child)
        assert len(result) == 1
        assert result[0]["id"] == "anthropic-prod"
        assert result[0]["config"] == {"x": 1, "y": 3, "z": 4}


class TestMergeModuleListsListConfig:
    """Integration tests: list-typed config values survive merge_module_lists."""

    def test_skills_lists_are_concatenated(self) -> None:
        """Real-world: two behaviors declare tool-skills with different config.skills lists.

        Both skills URLs must survive. This was the live bug: composing superpowers
        (config.skills: [url-a, url-b]) with parallax-discovery (config.skills: [url-c])
        silently dropped url-a and url-b.
        """
        parent = [
            {
                "module": "tool-skills",
                "source": "git+https://github.com/microsoft/amplifier-module-tool-skills@main",
                "config": {
                    "skills": [
                        "git+https://github.com/obra/superpowers@main#subdirectory=skills",
                        "git+https://github.com/microsoft/amplifier-bundle-superpowers@main#subdirectory=skills",
                    ]
                },
            }
        ]
        child = [
            {
                "module": "tool-skills",
                "source": "git+https://github.com/microsoft/amplifier-module-tool-skills@main",
                "config": {
                    "skills": [
                        "git+https://github.com/bkrabach/amplifier-bundle-parallax-discovery@main#subdirectory=skills",
                    ]
                },
            }
        ]
        result = merge_module_lists(parent, child)
        assert len(result) == 1
        assert result[0]["module"] == "tool-skills"
        skills = result[0]["config"]["skills"]
        assert len(skills) == 3
        assert (
            "git+https://github.com/obra/superpowers@main#subdirectory=skills" in skills
        )
        assert (
            "git+https://github.com/microsoft/amplifier-bundle-superpowers@main#subdirectory=skills"
            in skills
        )
        assert (
            "git+https://github.com/bkrabach/amplifier-bundle-parallax-discovery@main#subdirectory=skills"
            in skills
        )

    def test_search_paths_lists_are_concatenated(self) -> None:
        """Real-world: hooks-mode config.search_paths from multiple bundles are all preserved.

        This was the second live collision: superpowers + dev-machine both declare hooks-mode
        with search_paths lists; only the last one survived before this fix.
        """
        parent = [
            {
                "module": "hooks-mode",
                "config": {
                    "search_paths": [
                        "~/.amplifier/bundles/superpowers/modes",
                        "~/.amplifier/bundles/shared/modes",
                    ]
                },
            }
        ]
        child = [
            {
                "module": "hooks-mode",
                "config": {
                    "search_paths": [
                        "~/.amplifier/bundles/dev-machine/modes",
                    ]
                },
            }
        ]
        result = merge_module_lists(parent, child)
        assert len(result) == 1
        assert result[0]["module"] == "hooks-mode"
        paths = result[0]["config"]["search_paths"]
        assert len(paths) == 3
        assert "~/.amplifier/bundles/superpowers/modes" in paths
        assert "~/.amplifier/bundles/shared/modes" in paths
        assert "~/.amplifier/bundles/dev-machine/modes" in paths

    def test_duplicate_urls_deduplicated(self) -> None:
        """If two behaviors declare the same skill URL, it appears only once."""
        shared_url = "git+https://github.com/microsoft/amplifier-bundle-superpowers@main#subdirectory=skills"
        parent = [{"module": "tool-skills", "config": {"skills": [shared_url]}}]
        child = [{"module": "tool-skills", "config": {"skills": [shared_url]}}]
        result = merge_module_lists(parent, child)
        assert len(result) == 1
        assert result[0]["config"]["skills"] == [shared_url]

    def test_three_way_list_merge(self) -> None:
        """A→B→C merges all lists: the primary real-world bug scenario."""
        a = [{"module": "hooks-mode", "config": {"search_paths": ["@modes:modes"]}}]
        b = [{"module": "hooks-mode", "config": {"search_paths": ["@superpowers:modes"]}}]
        c = [{"module": "hooks-mode", "config": {"search_paths": ["@dev-machine:modes"]}}]
        ab = merge_module_lists(a, b)
        abc = merge_module_lists(ab, c)
        paths = abc[0]["config"]["search_paths"]
        assert len(paths) == 3
        assert "@modes:modes" in paths
        assert "@superpowers:modes" in paths
        assert "@dev-machine:modes" in paths
