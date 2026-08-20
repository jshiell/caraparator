"""View-layer canonicalisation. The database is never rewritten.

The two sources disagree about spelling — CUPRA writes `Rear-wheel drive` where
Volkswagen writes `Rear wheel drive`, and Volkswagen carries both `ID.3` and
`Id.3` — so the same car would otherwise appear as two filter options. Folding
happens at filter, sort and render time, in one place, so the SQL and the
displayed text can be checked against each other.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Callable, Iterable

# `lower()`, not `casefold()`, so these agree exactly with SQLite's LOWER().
MODEL_KEY_SQL = "LOWER(TRIM(model))"
DRIVETRAIN_KEY_SQL = (
    "REPLACE(REPLACE(REPLACE(LOWER(TRIM(drivetrain)), '-', ' '), '  ', ' '), '  ', ' ')"
)

_RUN_OF_SEPARATORS = re.compile(r"[-\s]+")


def model_key(value: str | None) -> str | None:
    """Fold `ID.3` and `Id.3` onto one option."""
    return None if value is None else value.strip().lower()


def drivetrain_key(value: str | None) -> str | None:
    """Fold on case and on hyphen-versus-space."""
    if value is None:
        return None
    return _RUN_OF_SEPARATORS.sub(" ", value.strip().lower())


def canonical_forms(
    values: Iterable[str | None], key: Callable[[str | None], str | None]
) -> dict[str, str]:
    """Map each folded key to the spelling to display for it.

    The display form is always one the source actually used — the most common,
    with ties broken by sort order so the choice does not depend on row order.
    Inventing a canonical spelling would put words in a source's mouth.
    """
    seen: dict[str, Counter] = {}
    for value in values:
        folded = key(value)
        if folded is None:
            continue
        seen.setdefault(folded, Counter())[value] += 1
    return {
        folded: max(sorted(spellings), key=spellings.__getitem__)
        for folded, spellings in seen.items()
    }
