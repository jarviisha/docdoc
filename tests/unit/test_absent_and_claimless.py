"""T041 — the two absences, which are not the same thing (FR-008, FR-009, FR-026).

The distinction most likely to be implemented wrong, stated as the table it is:

    present=False                 no outcome at all      not in the denominator
    present=True, claim is None   ungrounded             in the denominator
    present=True, claim is ""     ungrounded             in the denominator

A field the model correctly reported as absent is not a grounding failure, and
counting it as one would make the grounding rate depend on how many fields a
schema happens to declare -- so adding an optional field nobody fills in would
appear to degrade quality. A value asserted with no evidence *is* a failure, and
must count.
"""

from __future__ import annotations

import pytest

from docdoc.grounding import GroundingStatus, ground
from tests.support import make_document, make_extracted, make_extraction

TEXT = "Invoice INV-001 total 1,240.00"


def ground_values(**values):
    doc = make_document(TEXT)
    return ground(doc, make_extraction(values, document=doc))


class TestAModelReportedAbsence:
    def test_produces_no_outcome_at_all(self) -> None:
        result = ground_values(missing=make_extracted("missing", present=False))
        assert "missing" not in result.outcomes
        assert result.outcomes == {}

    def test_is_counted_as_not_applicable(self) -> None:
        result = ground_values(missing=make_extracted("missing", present=False))
        assert result.counts.not_applicable == 1
        assert result.counts.ungrounded == 0

    def test_stays_out_of_the_grounding_rate(self) -> None:
        found = make_extracted("found", value="x", claimed_text="INV-001")
        with_absence = ground_values(found=found, missing=make_extracted("m", present=False))
        without = ground_values(found=found)
        assert with_absence.counts.grounding_rate == without.counts.grounding_rate == 1.0

    def test_ten_absent_fields_do_not_move_the_rate(self) -> None:
        """The regression this rule prevents: a schema growing optional fields."""
        values = {"found": make_extracted("found", value="x", claimed_text="INV-001")}
        values.update({f"m{i}": make_extracted(f"m{i}", present=False) for i in range(10)})
        result = ground_values(**values)
        assert result.counts.grounding_rate == 1.0
        assert result.counts.not_applicable == 10


class TestAValueAssertedWithoutEvidence:
    def test_a_missing_claim_is_ungrounded(self) -> None:
        result = ground_values(f=make_extracted("f", value="x", claimed_text=None))
        assert result.outcomes["f"].status is GroundingStatus.UNGROUNDED

    def test_an_empty_claim_is_ungrounded(self) -> None:
        result = ground_values(f=make_extracted("f", value="x", claimed_text=""))
        assert result.outcomes["f"].status is GroundingStatus.UNGROUNDED

    def test_a_whitespace_only_claim_is_ungrounded(self) -> None:
        result = ground_values(f=make_extracted("f", value="x", claimed_text="  \n "))
        assert result.outcomes["f"].status is GroundingStatus.UNGROUNDED

    def test_it_counts_against_the_rate(self) -> None:
        result = ground_values(
            found=make_extracted("found", value="x", claimed_text="INV-001"),
            claimless=make_extracted("claimless", value="x", claimed_text=None),
        )
        assert result.counts.ungrounded == 1
        assert result.counts.not_applicable == 0
        assert result.counts.grounding_rate == pytest.approx(0.5)


class TestTheDenominatorDirectly:
    def test_the_two_absences_produce_different_rates(self) -> None:
        """The whole file in one assertion."""
        found = make_extracted("found", value="x", claimed_text="INV-001")
        reported_absent = ground_values(found=found, other=make_extracted("other", present=False))
        no_evidence = ground_values(
            found=found, other=make_extracted("other", value="x", claimed_text=None)
        )
        assert reported_absent.counts.grounding_rate == 1.0
        assert no_evidence.counts.grounding_rate == pytest.approx(0.5)

    def test_an_empty_extraction_has_no_rate_rather_than_a_rate_of_zero(self) -> None:
        """`None`, not 0.0: nothing failed, because nothing was asked."""
        assert ground_values().counts.grounding_rate is None

    def test_a_result_of_only_absences_has_no_rate(self) -> None:
        result = ground_values(a=make_extracted("a", present=False))
        assert result.counts.grounding_rate is None
        assert result.counts.not_applicable == 1
