"""T033a — what the match view is actually worth, measured (SC-007).

ADR-0006 grew the milestone's scope on an argument: that without folding,
"ligatures, soft hyphens, hyphenated line breaks, non-breaking spaces, and
irregular whitespace push a large share of otherwise-correct values to fuzzy or
ungrounded for purely cosmetic reasons".

This is the only test that checks the argument was right. It resolves the same
claims twice -- once against the view, once against raw source text -- and
**reports** both rates, because SC-007 asks for the increase to be reported
rather than asserted. If a future change to the folding rules stopped earning
their complexity, this is where that would show up.
"""

from __future__ import annotations

from tests.fixtures.grounding import TYPESETTING_CASES
from tests.support import make_document, make_extracted, make_extraction

from docdoc.grounding import GroundingStatus, ground
from docdoc.grounding.view import fold_claim


def exact_rate_with_view() -> tuple[int, int]:
    """How many fixture claims reach the exact tier through the real pipeline."""
    exact = 0
    for _name, text, claim, _want in TYPESETTING_CASES:
        doc = make_document(text)
        extraction = make_extraction(
            {"f": make_extracted("f", value="x", claimed_text=claim)}, document=doc
        )
        if ground(doc, extraction).outcomes["f"].status is GroundingStatus.EXACT:
            exact += 1
    return exact, len(TYPESETTING_CASES)


def exact_rate_without_view() -> tuple[int, int]:
    """The counterfactual: plain substring search over untouched source text.

    This is what the kernel's own `find` does, and what grounding would have been
    limited to had ADR-0006 been decided the other way.
    """
    exact = sum(1 for _n, text, claim, _w in TYPESETTING_CASES if claim in text)
    return exact, len(TYPESETTING_CASES)


class TestTheViewEarnsItsScope:
    def test_the_exact_rate_is_strictly_higher_with_the_view(self) -> None:
        with_view, total = exact_rate_with_view()
        without_view, _ = exact_rate_without_view()
        assert with_view > without_view, (
            f"the match view resolved {with_view}/{total} at the exact tier and raw "
            f"matching resolved {without_view}/{total}. If these are equal, the "
            "folding rules are no longer earning the scope ADR-0006 added."
        )

    def test_raw_matching_resolves_almost_nothing_on_typeset_text(self) -> None:
        """The premise ADR-0006 rests on, checked rather than assumed."""
        without_view, total = exact_rate_without_view()
        assert without_view <= 1, (
            f"raw matching resolved {without_view}/{total}; the fixtures are supposed "
            "to be typeset the way real PDFs are"
        )

    def test_the_view_resolves_everything_it_is_designed_to(self) -> None:
        with_view, total = exact_rate_with_view()
        designed_for = sum(1 for *_rest, want in TYPESETTING_CASES if want)
        assert with_view == designed_for
        assert designed_for == total - 1  # the `well-known` residual, by design

    def test_the_increase_is_reported(self, capsys) -> None:
        """SC-007 asks for the increase to be *reported*, not merely asserted."""
        with_view, total = exact_rate_with_view()
        without_view, _ = exact_rate_without_view()
        lift = (with_view - without_view) / total
        print(
            f"\nmatch view lift (SC-007): exact tier {without_view}/{total} raw "
            f"-> {with_view}/{total} folded  (+{lift:.0%})"
        )
        captured = capsys.readouterr()
        assert "match view lift" in captured.out


class TestEachRuleIndividually:
    """Which fixture each transformation rescues, so a regression names itself."""

    def test_every_designed_case_resolves_and_the_residual_does_not(self) -> None:
        for name, text, claim, want_exact in TYPESETTING_CASES:
            doc = make_document(text)
            extraction = make_extraction(
                {"f": make_extracted("f", value="x", claimed_text=claim)}, document=doc
            )
            outcome = ground(doc, extraction).outcomes["f"]
            got_exact = outcome.status is GroundingStatus.EXACT
            assert got_exact is want_exact, f"{name}: expected exact={want_exact}"
            # Whether or not it reached the exact tier, it must still be located.
            assert outcome.span is not None, name

    def test_the_residual_case_still_grounds_fuzzily(self) -> None:
        """`well-known` -> `wellknown` at exactly 0.900 -- located, just not verbatim."""
        name, text, claim, _ = next(c for c in TYPESETTING_CASES if c[0] == "compound_broken")
        doc = make_document(text)
        extraction = make_extraction(
            {"f": make_extracted("f", value="x", claimed_text=claim)}, document=doc
        )
        outcome = ground(doc, extraction).outcomes["f"]
        assert outcome.status is GroundingStatus.FUZZY, name
        assert outcome.score == 0.9

    def test_folding_a_claim_is_what_makes_both_sides_comparable(self) -> None:
        """FR-018 in isolation: the document folds, and so must the claim."""
        for _name, _text, claim, _want in TYPESETTING_CASES:
            assert fold_claim(claim) == fold_claim(fold_claim(claim)), "folding is idempotent"
