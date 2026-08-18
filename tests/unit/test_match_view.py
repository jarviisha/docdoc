"""T013 — the match view's four rules produce ADR-0006's six effects (GRD-2, GRD-4).

The measurements here are the justification for the implementation having four
transformation rules where the ADR lists six. If NFKC ever stopped doing two of
them, these tests are what would say so -- rather than the grounding rate quietly
dropping on documents nobody re-measures.
"""

from __future__ import annotations

import unicodedata

from docdoc.grounding.view import MATCH_VIEW_VERSION, MatchView, fold_claim
from docdoc.grounding.view import _fold as fold
from tests.fixtures.grounding import (
    COMBINING_MARK,
    EXOTIC_SPACES,
    LIGATURE,
    SOFT_HYPHEN,
    TYPESETTING_CASES,
)
from tests.support import make_document


class TestNfkcDoesTwoOfTheSix:
    """Why there are four rules and not six (research.md R5)."""

    def test_nfkc_expands_ligatures_so_no_separate_rule_is_needed(self) -> None:
        assert unicodedata.normalize("NFKC", "ﬁ") == "fi"
        assert unicodedata.normalize("NFKC", "ﬃ") == "ffi"
        assert "final" in fold(LIGATURE)[0]

    def test_nfkc_folds_every_exotic_space_so_no_separate_rule_is_needed(self) -> None:
        for space in (" ", " ", " "):
            assert unicodedata.normalize("NFKC", f"a{space}b") == "a b"
        assert fold(EXOTIC_SPACES)[0] == "Amount due 1,240.00 EUR"

    def test_nfkc_does_not_remove_soft_hyphens_which_is_why_that_rule_is_explicit(self) -> None:
        assert unicodedata.normalize("NFKC", "in­voice") == "in­voice"
        assert "invoice" in fold(SOFT_HYPHEN)[0]


class TestTheViewChangesLengthInBothDirections:
    """The measurement that makes an arithmetic offset map impossible."""

    def test_a_ligature_makes_the_view_longer(self) -> None:
        view, _ = fold(LIGATURE)
        assert len(view) > len(LIGATURE)

    def test_a_combining_mark_makes_the_view_shorter(self) -> None:
        view, _ = fold(COMBINING_MARK)
        assert len(view) < len(COMBINING_MARK)
        assert view == "Société Générale reference"


class TestWhitespaceCollapsing:
    def test_a_run_of_whitespace_becomes_one_space(self) -> None:
        assert fold("a  \t\n  b")[0] == "a b"

    def test_a_single_space_is_left_alone(self) -> None:
        assert fold("a b")[0] == "a b"


class TestClaimAndDocumentAreFoldedTheSameWay:
    """FR-018 — folding only the document leaves a NBSP-bearing claim unmatchable."""

    def test_a_claim_containing_a_non_breaking_space_matches_a_folded_document(self) -> None:
        view, _ = fold("Amount due 1,240.00")
        assert fold_claim("Amount due 1,240.00") in view

    def test_every_fixture_claim_reaches_the_exact_tier_as_designed(self) -> None:
        for name, text, claim, want_exact in TYPESETTING_CASES:
            view, _ = fold(text)
            assert (fold_claim(claim) in view) is want_exact, name


class TestViewIdentity:
    """GRD-6 — a view is identified by the document and the rule version."""

    def test_the_view_records_its_document_and_version(self) -> None:
        doc = make_document("Invoice INV-001")
        view = MatchView.build(doc)
        assert view.document_id == doc.id
        assert view.version == MATCH_VIEW_VERSION
        assert view.view_id.startswith("sha256:")

    def test_the_same_document_yields_the_same_view_id(self) -> None:
        doc = make_document("Invoice INV-001")
        assert MatchView.build(doc).view_id == MatchView.build(doc).view_id

    def test_a_different_document_yields_a_different_view_id(self) -> None:
        # Different *bytes*, not merely different text. `document_id` derives
        # from the blob and the parse (ADR-0002), so two documents built from
        # one fake blob share an id however their text differs -- which is
        # correct, and worth knowing before writing a test that assumes
        # otherwise.
        a = MatchView.build(make_document("Invoice INV-001", data=b"%PDF-1.7 one"))
        b = MatchView.build(make_document("Invoice INV-002", data=b"%PDF-1.7 two"))
        assert a.document_id != b.document_id
        assert a.view_id != b.view_id
