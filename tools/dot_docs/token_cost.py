"""Token estimation and color tier classification for DOT diagrams.

Token estimation uses ``len(content) // 4`` — consistent with the existing
``validate-single-bundle.yaml`` recipe.  Color tiers classify token counts
into green/yellow/orange/red based on per-category thresholds.
"""

# — Color tier hex values
COLOR_GREEN = "#c8e6c9"
COLOR_YELLOW = "#fff9c4"
COLOR_ORANGE = "#ffe0b2"
COLOR_RED = "#ffcdd2"

# — Per-category thresholds: (green_max, yellow_max, orange_max)
# Tokens below green_max → green, green_max..yellow_max → yellow, etc.
# Tokens >= orange_max → red.
THRESHOLDS: dict[str, tuple[int, int, int]] = {
    "agent_description": (500, 1_000, 2_000),
    "all_agent_descriptions": (5_000, 10_000, 20_000),
    "tool_schema": (300, 600, 1_000),
    "all_tool_schemas": (3_000, 6_000, 12_000),
    "context_file": (1_000, 2_000, 4_000),
    "all_context": (4_000, 8_000, 16_000),
    "bundle_body": (500, 1_000, 2_000),
}


def estimate_tokens(content: str) -> int:
    """Estimate token count from a content string.

    Uses ``len(content) // 4`` — the same heuristic as
    ``validate-single-bundle.yaml``.
    """
    return len(content) // 4


def color_tier(tokens: int, category: str) -> str:
    """Return a hex color for *tokens* based on *category* thresholds.

    Args:
        tokens: Estimated token count.
        category: One of the keys in :data:`THRESHOLDS`.

    Returns:
        Hex color string (e.g. ``"#c8e6c9"``).

    Raises:
        KeyError: If *category* is not in :data:`THRESHOLDS`.
    """
    green_max, yellow_max, orange_max = THRESHOLDS[category]
    if tokens < green_max:
        return COLOR_GREEN
    if tokens < yellow_max:
        return COLOR_YELLOW
    if tokens < orange_max:
        return COLOR_ORANGE
    return COLOR_RED
