"""Shared pure vectors plus the public asynchronous resolver boundary."""

import itertools
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import amplifier_foundation as public
from amplifier_foundation.model_patterns import (
    is_glob_pattern,
    match_model_pattern,
    model_matches_pattern,
    model_pattern_sort_key,
    select_model_pattern,
)
from amplifier_foundation.spawn_utils import resolve_model_pattern

VECTORS = json.loads(
    (Path(__file__).parents[1] / "test-fixtures/model-patterns.json").read_text()
)


@pytest.mark.parametrize("vector", VECTORS, ids=lambda v: v["name"])
def test_inventory_vectors(vector):
    for inventory in itertools.permutations(vector["models"]):
        assert match_model_pattern(vector["pattern"], iter(inventory)) == vector["matches"]
        assert select_model_pattern(vector["pattern"], inventory) == next(
            iter(vector["matches"]), None
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("vector", VECTORS, ids=lambda v: v["name"])
async def test_public_resolver_vectors(vector):
    provider = SimpleNamespace(list_models=AsyncMock(return_value=vector["models"]))
    coordinator = Mock()
    coordinator.get.return_value = {"fixture": provider}
    diagnostics = []
    result = await resolve_model_pattern(
        vector["pattern"], "fixture", coordinator, diagnostics=diagnostics
    )
    assert diagnostics == [result]
    assert result.provider == "fixture"
    if not is_glob_pattern(vector["pattern"]):
        assert result.resolved_model == vector["pattern"]
        assert result.pattern is None
        provider.list_models.assert_not_called()
        coordinator.get.assert_not_called()
    else:
        assert result.matched_models == vector["matches"]
        assert result.available_models == vector["models"]
        assert result.resolved_model == next(iter(vector["matches"]), None)
        assert result.status == (
            "resolved" if vector["matches"]
            else "no_matching_model" if vector["models"] else "empty_catalog"
        )


@pytest.mark.parametrize(
    "suffix",
    ["12345678", "20260230", "20260229", "20260001", "20261301", "2026-1-01",
     "19991231", "21000101", "２０２６１００１"],
)
def test_numeric_suffix_is_not_automatically_a_snapshot(suffix):
    # If incorrectly stripped, the alias would win.
    model = f"model-10-{suffix}"
    assert select_model_pattern("*", ["model-10", model]) == model


def test_exports_and_legacy_detector_are_the_same_functions():
    from amplifier_foundation.spawn_utils import is_glob_pattern as legacy

    assert legacy is is_glob_pattern is public.is_glob_pattern
    assert public.match_model_pattern is match_model_pattern
    assert public.select_model_pattern is select_model_pattern
    assert public.model_pattern_sort_key is model_pattern_sort_key
    assert public.model_matches_pattern is model_matches_pattern


def test_original_inventory_is_unchanged_and_duplicates_are_retained():
    models = ["Model-9", "MODEL-10", "MODEL-10"]
    assert match_model_pattern("*", models) == ["MODEL-10", "MODEL-10", "Model-9"]
    assert models == ["Model-9", "MODEL-10", "MODEL-10"]


def test_sort_is_total_for_unusual_ids():
    models = ["", "9", "10", "a", "a9", "a10", "a01", "a1", "①", "a-20260101"]
    expected = match_model_pattern("*", models)
    assert len(expected) == len(models)
    assert match_model_pattern("*", reversed(models)) == expected


def test_matching_is_independent_of_os_path_normalization(monkeypatch):
    import os.path

    monkeypatch.setattr(os.path, "normcase", lambda value: value.replace("/", "\\"))
    assert model_matches_pattern("Model/X", "model/*")
    assert not model_matches_pattern("Model\\X", "model/*")