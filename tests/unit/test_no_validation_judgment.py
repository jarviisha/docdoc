"""T043a — grounding answers *where*, never *whether* (FR-010, Principle VII).

plan.md's Constitution Check calls this temptation "real and named", and it is:
grounding is the first stage holding both the extracted value and the text it
supposedly came from, so it is the first stage *able* to notice they disagree.
Noticing is exactly what it must not do.

A value of 1240.00 whose claim resolves to text reading 1,420.00 is a **validation**
finding -- Milestone 5's, with its own artifact, its own rules, and its own error
type. Reporting it here would put a semantic rule in the wrong stage and would
make the grounding status mean two things at once.
"""

from __future__ import annotations

from docdoc.grounding import GroundingStatus, ground
from tests.support import make_document, make_extracted, make_extraction


def ground_value(text: str, *, value: object, claim: str):
    doc = make_document(text)
    extraction = make_extraction(
        {"total": make_extracted("total", value=value, claimed_text=claim)}, document=doc
    )
    return doc, ground(doc, extraction).outcomes["total"]


class TestAValueThatDisagreesWithItsOwnSource:
    TEXT = "Subtotal 1,000.00\nTotal 1,420.00\nThank you"

    def test_it_grounds_normally(self) -> None:
        _, outcome = ground_value(self.TEXT, value="1240.00", claim="Total 1,420.00")
        assert outcome.status is GroundingStatus.EXACT
        assert outcome.span is not None

    def test_the_status_is_not_downgraded(self) -> None:
        """The disagreement must not leak into the grounding vocabulary."""
        _, agreeing = ground_value(self.TEXT, value="1420.00", claim="Total 1,420.00")
        _, disagreeing = ground_value(self.TEXT, value="1240.00", claim="Total 1,420.00")
        assert agreeing.status is disagreeing.status
        assert agreeing.score == disagreeing.score

    def test_the_range_is_identical_whatever_the_value_says(self) -> None:
        _, a = ground_value(self.TEXT, value="1420.00", claim="Total 1,420.00")
        _, b = ground_value(self.TEXT, value=None, claim="Total 1,420.00")
        _, c = ground_value(self.TEXT, value={"nonsense": True}, claim="Total 1,420.00")
        assert a.span == b.span == c.span

    def test_no_finding_or_warning_field_exists_to_carry_one(self) -> None:
        """Structural: there is nowhere for a validation verdict to be recorded."""
        _, outcome = ground_value(self.TEXT, value="1240.00", claim="Total 1,420.00")
        fields = set(outcome.model_dump())
        assert not fields & {"valid", "findings", "warnings", "errors", "consistent"}


class TestImplausibleValuesAreStillGrounded:
    def test_a_negative_total_grounds(self) -> None:
        _, outcome = ground_value("Total -500.00 credit", value="-500.00", claim="Total -500.00")
        assert outcome.status is GroundingStatus.EXACT

    def test_a_date_far_in_the_future_grounds(self) -> None:
        _, outcome = ground_value("Due 2199-01-01", value="2199-01-01", claim="Due 2199-01-01")
        assert outcome.status is GroundingStatus.EXACT

    def test_a_value_of_the_wrong_type_entirely_grounds(self) -> None:
        """Type conformance was extraction's question and is not re-asked here."""
        _, outcome = ground_value("Total banana", value=["a", "list"], claim="Total banana")
        assert outcome.status is GroundingStatus.EXACT
