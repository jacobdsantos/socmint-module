"""Prose helpers.

These exist because "3 post(s)" kept reaching analyst-facing output. The tests
pin the two ways that happened: the noun, and the verb that follows it.
"""

from __future__ import annotations

import pytest

from socmint.prose import agreement, listing, plural


@pytest.mark.parametrize(
    ("count", "expected"),
    [(0, "0 posts"), (1, "1 post"), (2, "2 posts"), (527, "527 posts")],
)
def test_plural_agrees_with_the_count(count: int, expected: str) -> None:
    assert plural(count, "post") == expected


def test_plural_accepts_an_irregular_form() -> None:
    assert plural(1, "match", "matches") == "1 match"
    assert plural(3, "match", "matches") == "3 matches"


def test_agreement_switches_the_verb_not_the_noun() -> None:
    assert f"{plural(1, 'post')} {agreement(1, 'was', 'were')} excluded" == "1 post was excluded"
    assert f"{plural(2, 'post')} {agreement(2, 'was', 'were')} excluded" == "2 posts were excluded"


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        ([], ""),
        (["a"], "a"),
        (["a", "b"], "a and b"),
        (["a", "b", "c"], "a, b and c"),
    ],
)
def test_listing_joins_without_an_oxford_comma(parts: list[str], expected: str) -> None:
    assert listing(parts) == expected
