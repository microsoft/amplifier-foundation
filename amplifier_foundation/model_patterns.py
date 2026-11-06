"""Pure model-ID matching and ranking, independent of providers and routing.

These functions act only on supplied inventory. They do not discover models,
filter recommendations, infer entitlement, or rank product tiers by quality/cost.
Exact-ID passthrough is a caller policy (see ``resolve_model_pattern``), not an
inventory match: ``select_model_pattern("unlisted", [])`` returns ``None``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from fnmatch import fnmatchcase

# Snapshot grammar: a nonempty ID followed by a hyphen and a valid Gregorian
# date in 2000..2099, in compact or ISO form. Other numeric suffixes are versions.
# Use ASCII digits explicitly, not Unicode \d or an arbitrary 8-digit suffix.
_SNAPSHOT_SUFFIX = re.compile(
    r"^(.+)-(20[0-9]{2})(?:([0-9]{2})([0-9]{2})|-([0-9]{2})-([0-9]{2}))$"
)
_DIGIT_RUN = re.compile(r"([0-9]+)")


def is_glob_pattern(model_hint: str) -> bool:
    """Detect ``*``, ``?`` or ``[`` using the existing fnmatch grammar.

    An unmatched ``[`` still marks a pattern; fnmatch treats it literally.
    Malformed ranges follow Python's fnmatch semantics (they do not raise).
    """
    return any(c in model_hint for c in "*?[")


def model_matches_pattern(model_id: str, pattern: str) -> bool:
    """Match case-insensitively, without OS path normalization or ID rewriting."""
    return fnmatchcase(model_id.lower(), pattern.lower())


def _snapshot(model_id: str) -> tuple[str, int | None]:
    match = _SNAPSHOT_SUFFIX.fullmatch(model_id)
    if match:
        base, year, month, day, iso_month, iso_day = match.groups()
        try:
            stamp = date(int(year), int(month or iso_month), int(day or iso_day))
        except ValueError:
            pass
        else:
            return base, stamp.toordinal()
    return model_id, None


def model_pattern_sort_key(model_id: str) -> tuple:
    """Return a deterministic key for descending (best-first) model-ID order.

    Compare digit runs numerically and text case-insensitively. Strip only the
    valid snapshot suffix described above before comparing versions. For the
    same base, prefer the undated alias, then the newest snapshot. Equal numeric
    spellings prefer shorter IDs; remaining ties use lowercase then original
    spelling, descending. No input/API order participates in the tie-break.

    Text across different product tiers remains lexical, not a quality or cost
    recommendation. Use a narrower pattern to select a particular tier.
    The tuple layout is an implementation detail; callers may compare keys.
    """
    base, stamp = _snapshot(model_id.lower())
    # Tagged parts keep comparisons total even for digit-leading/unusual IDs.
    primary = tuple(
        (1, int(part)) if part.isascii() and part.isdigit() else (0, part)
        for part in _DIGIT_RUN.split(base)
    )
    return (
        primary,
        stamp is None,
        stamp or 0,
        -len(model_id),
        model_id.lower(),
        model_id,
    )


def match_model_pattern(pattern: str, model_ids: Iterable[str]) -> list[str]:
    """Return all matching IDs, best first, retaining original spelling.

    Empty/unmatched inventory returns ``[]``. Input is not mutated; duplicate
    inventory entries are retained. Exact strings are matched against inventory,
    not passed through. Patterns use :func:`model_matches_pattern` semantics.
    """
    return sorted(
        (model_id for model_id in model_ids if model_matches_pattern(model_id, pattern)),
        key=model_pattern_sort_key,
        reverse=True,
    )


def select_model_pattern(pattern: str, model_ids: Iterable[str]) -> str | None:
    """Return the best inventory match, or ``None``; never invent a fallback."""
    return next(iter(match_model_pattern(pattern, model_ids)), None)