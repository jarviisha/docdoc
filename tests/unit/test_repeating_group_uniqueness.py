"""T038a — identical claims inside a repeating group (FR-029, SC-012, GRD-13a).

The bug this prevents: two line items both claiming "Widget" resolve
independently, the tie-break picks the earliest occurrence for both, and every
line item ends up pointing at the first one's bounding box. Well-formed, entirely
plausible, and wrong -- an audit trail that says all three rows came from the
same place.

The **scope** of the rule is as important as the rule. It is per repeating-group
slot, never global: an invoice date read as both issue date and due date must
resolve to the one range it occupies, and a global uniqueness constraint would
force the second field to invent a location somewhere else.
"""

from __future__ import annotations

from docdoc.grounding import GroundingStatus, ground
from tests.support import make_document, make_extracted, make_extraction

TEXT = "Line 1 Widget 25.00\nLine 2 Widget 30.00\nInvoiced 2026-04-13 due 2026-04-13"


def entry(claim: str) -> dict:
    return {"description": make_extracted("description", value="Widget", claimed_text=claim)}


class TestDistinctRangesInEntryOrder:
    def test_two_entries_with_identical_claims_land_in_different_places(self) -> None:
        doc = make_document(TEXT)
        extraction = make_extraction(
            {"line_items": (entry("Widget"), entry("Widget"))}, document=doc
        )
        result = ground(doc, extraction)
        first = result.outcomes["line_items[0].description"].span
        second = result.outcomes["line_items[1].description"].span
        assert first is not None
        assert second is not None
        assert first != second

    def test_they_are_assigned_in_entry_order(self) -> None:
        doc = make_document(TEXT)
        extraction = make_extraction(
            {"line_items": (entry("Widget"), entry("Widget"))}, document=doc
        )
        result = ground(doc, extraction)
        first = result.outcomes["line_items[0].description"].span
        second = result.outcomes["line_items[1].description"].span
        assert first is not None
        assert second is not None
        assert first.start == TEXT.index("Widget")
        assert second.start == TEXT.index("Widget", first.end)

    def test_a_surplus_entry_is_ungrounded_rather_than_given_a_taken_range(self) -> None:
        """Three entries, two occurrences: the third has no place to have come from."""
        doc = make_document(TEXT)
        extraction = make_extraction(
            {"line_items": (entry("Widget"), entry("Widget"), entry("Widget"))}, document=doc
        )
        result = ground(doc, extraction)
        spans = [result.outcomes[f"line_items[{i}].description"].span for i in range(3)]
        assert spans[0] is not None
        assert spans[1] is not None
        assert spans[2] is None
        assert result.outcomes["line_items[2].description"].status is GroundingStatus.UNGROUNDED

    def test_entries_with_different_claims_are_unaffected(self) -> None:
        doc = make_document(TEXT)
        extraction = make_extraction(
            {"line_items": (entry("Widget 25.00"), entry("Widget 30.00"))}, document=doc
        )
        result = ground(doc, extraction)
        for i, expected in enumerate(("Widget 25.00", "Widget 30.00")):
            span = result.outcomes[f"line_items[{i}].description"].span
            assert span is not None
            assert doc.text[span.start : span.end] == expected


class TestTheRuleIsScopedToTheGroup:
    """The half that would break real documents if the rule were global."""

    def test_two_distinct_fields_may_share_one_range(self) -> None:
        doc = make_document("Invoiced 2026-04-13 and payable immediately")
        extraction = make_extraction(
            {
                "issue_date": make_extracted("issue_date", value="d", claimed_text="2026-04-13"),
                "due_date": make_extracted("due_date", value="d", claimed_text="2026-04-13"),
            },
            document=doc,
        )
        result = ground(doc, extraction)
        assert result.outcomes["issue_date"].span == result.outcomes["due_date"].span
        assert result.outcomes["issue_date"].span is not None

    def test_a_field_outside_a_group_never_collides_with_one_inside(self) -> None:
        doc = make_document(TEXT)
        extraction = make_extraction(
            {
                "summary": make_extracted("summary", value="Widget", claimed_text="Widget"),
                "line_items": (entry("Widget"),),
            },
            document=doc,
        )
        result = ground(doc, extraction)
        assert result.outcomes["summary"].span == result.outcomes["line_items[0].description"].span

    def test_different_fields_of_one_group_do_not_exclude_each_other(self) -> None:
        doc = make_document("Widget Widget")
        extraction = make_extraction(
            {
                "line_items": (
                    {
                        "description": make_extracted(
                            "description", value="W", claimed_text="Widget"
                        ),
                        "note": make_extracted("note", value="W", claimed_text="Widget"),
                    },
                )
            },
            document=doc,
        )
        result = ground(doc, extraction)
        assert (
            result.outcomes["line_items[0].description"].span
            == result.outcomes["line_items[0].note"].span
        )


class TestAlternativesAreNotFiltered:
    """GRD-13a — alternatives record what was there, not what was assigned."""

    def test_an_entry_may_list_a_range_another_entry_won(self) -> None:
        doc = make_document(TEXT)
        extraction = make_extraction(
            {"line_items": (entry("Widget"), entry("Widget"))}, document=doc
        )
        result = ground(doc, extraction)
        second = result.outcomes["line_items[1].description"]
        first_span = result.outcomes["line_items[0].description"].span
        assert first_span is not None
        assert any(alt.span == first_span for alt in second.alternatives)
