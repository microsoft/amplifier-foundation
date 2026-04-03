"""Tests for token estimation and color tier classification."""

import pytest

from amplifier_foundation.bundle_docs.token_cost import (
    COLOR_GREEN,
    COLOR_ORANGE,
    COLOR_RED,
    COLOR_YELLOW,
    THRESHOLDS,
    color_tier,
    estimate_tokens,
)


class TestEstimateTokens:
    """Tests for estimate_tokens()."""

    def test_empty_string(self) -> None:
        assert estimate_tokens("") == 0

    def test_short_string(self) -> None:
        assert estimate_tokens("abcd") == 1

    def test_known_length(self) -> None:
        assert estimate_tokens("x" * 400) == 100

    def test_remainder_truncated(self) -> None:
        assert estimate_tokens("abc") == 0
        assert estimate_tokens("abcde") == 1


class TestColorTier:
    """Tests for color_tier()."""

    def test_green_below_threshold(self) -> None:
        assert color_tier(100, "agent_description") == COLOR_GREEN

    def test_yellow_at_boundary(self) -> None:
        assert color_tier(500, "agent_description") == COLOR_YELLOW

    def test_orange_in_range(self) -> None:
        assert color_tier(1500, "agent_description") == COLOR_ORANGE

    def test_red_above_max(self) -> None:
        assert color_tier(3000, "agent_description") == COLOR_RED

    @pytest.mark.parametrize("category", list(THRESHOLDS.keys()))
    def test_all_categories_have_valid_thresholds(self, category: str) -> None:
        result = color_tier(0, category)
        assert result.startswith("#")

    def test_unknown_category_raises(self) -> None:
        with pytest.raises(KeyError):
            color_tier(100, "nonexistent_category")

    @pytest.mark.parametrize(
        "tokens,expected",
        [
            (299, COLOR_GREEN),
            (300, COLOR_YELLOW),
            (599, COLOR_YELLOW),
            (600, COLOR_ORANGE),
            (999, COLOR_ORANGE),
            (1000, COLOR_RED),
        ],
    )
    def test_tool_schema_boundaries(self, tokens: int, expected: str) -> None:
        assert color_tier(tokens, "tool_schema") == expected
