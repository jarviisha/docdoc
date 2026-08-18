"""T042, T044 — the event, and that nothing in it could leak content (FR-046, FR-047, SC-019).

Two jobs. The event carries what FR-047 names, on every path including refusal.
And the content-leak sweep: document text, claim text, extracted values, and
match-view text never appear in log output.

The leak rule is stricter here than in Milestone 3, because the view is a new
category -- folded document text, which is exactly what someone debugging a
near-miss would be tempted to log.

The ungrounded-distinguishability assertions (FR-034) live here too: whatever
else is true of a value that did not resolve, the log must not describe it as
located.
"""

from __future__ import annotations

import logging

import pytest

from docdoc.grounding import GroundingError, GroundingStatus, ground
from docdoc.grounding.observe import EVENT_NAME
from docdoc.grounding.view import MatchView
from tests.support import make_document, make_extracted, make_extraction

TEXT = "ACME SUPPLIES LIMITED\nInvoice INV-001\nTotal 1,240.00"
SECRET_VALUE = "1240.00"
SECRET_CLAIM = "Total 1,240.00"


def build():
    doc = make_document(TEXT)
    extraction = make_extraction(
        {
            "total": make_extracted("total", value=SECRET_VALUE, claimed_text=SECRET_CLAIM),
            "missing": make_extracted("missing", present=False),
            "bogus": make_extracted("bogus", value="?", claimed_text="nowhere at all whatsoever"),
        },
        document=doc,
    )
    return doc, extraction


@pytest.fixture
def events(caplog):
    caplog.set_level(logging.DEBUG, logger="docdoc.grounding")
    return caplog


def payloads(caplog) -> list[dict]:
    return [r.docdoc for r in caplog.records if hasattr(r, "docdoc")]


class TestTheSuccessEvent:
    def test_exactly_one_event_is_emitted(self, events) -> None:
        ground(*build())
        assert len(payloads(events)) == 1

    def test_it_carries_every_field_the_requirement_names(self, events) -> None:
        ground(*build())
        payload = payloads(events)[0]
        for key in (
            "event",
            "outcome",
            "document_id",
            "extraction_artifact_id",
            "artifact_id",
            "grounding_version",
            "match_view_version",
            "grounder_id",
            "grounder_version",
            "exact",
            "fuzzy",
            "ungrounded",
            "not_applicable",
            "truncated",
            # FR-047 names the duration, and an earlier version of this list
            # omitted it -- so the test passed while `ground()` never timed
            # anything and never passed one. A field list that is shorter than
            # the requirement certifies less than it appears to.
            "duration_ms",
        ):
            assert key in payload, key
        assert payload["event"] == EVENT_NAME
        assert payload["outcome"] == "ok"

    def test_the_counts_match_the_result(self, events) -> None:
        result = ground(*build())
        payload = payloads(events)[0]
        assert payload["exact"] == result.counts.exact == 1
        assert payload["ungrounded"] == result.counts.ungrounded == 1
        assert payload["not_applicable"] == result.counts.not_applicable == 1

    def test_it_carries_the_grounding_rate(self, events) -> None:
        ground(*build())
        assert payloads(events)[0]["grounding_rate"] == pytest.approx(0.5)

    def test_the_duration_is_a_real_measurement(self, events) -> None:
        """Not merely present: a hard-coded zero would satisfy a presence check."""
        ground(*build())
        duration = payloads(events)[0]["duration_ms"]
        assert isinstance(duration, float)
        assert duration > 0.0


class TestTheRefusalEvent:
    def test_a_refused_call_still_emits(self, events) -> None:
        doc = make_document(TEXT, data=b"%PDF-1.7 one")
        other = make_document(TEXT, data=b"%PDF-1.7 two")
        extraction = make_extraction(
            {"f": make_extracted("f", value="x", claimed_text="INV-001")}, document=other
        )
        with pytest.raises(GroundingError):
            ground(doc, extraction)
        payload = payloads(events)[0]
        assert payload["outcome"] == "refused"
        assert payload["reason"] == "document_mismatch"
        assert payload["document_id"] == doc.id
        assert payload["extraction_document_id"] == other.id
        # A refusal is fast by construction, but the field is present on both
        # event shapes so the event is queryable without a null check.
        assert payload["duration_ms"] >= 0.0


class TestNothingLeaks:
    """SC-019 — swept over everything the layer logs."""

    def rendered(self, caplog) -> str:
        return "\n".join(str(getattr(r, "docdoc", "")) + r.getMessage() for r in caplog.records)

    def test_no_document_text(self, events) -> None:
        ground(*build())
        blob = self.rendered(events)
        assert TEXT not in blob
        assert "ACME SUPPLIES LIMITED" not in blob

    def test_no_claim_text(self, events) -> None:
        ground(*build())
        blob = self.rendered(events)
        assert SECRET_CLAIM not in blob
        assert "nowhere at all whatsoever" not in blob

    def test_no_extracted_values(self, events) -> None:
        ground(*build())
        assert SECRET_VALUE not in self.rendered(events)

    def test_no_match_view_text(self, events) -> None:
        """The category this layer adds -- folded document text is still document text."""
        doc, extraction = build()
        view = MatchView.build(doc)
        ground(doc, extraction)
        assert view.text not in self.rendered(events)

    def test_no_field_paths_that_could_carry_content(self, events) -> None:
        ground(*build())
        payload = payloads(events)[0]
        for value in payload.values():
            assert not isinstance(value, (list, dict, tuple)), value


class TestUngroundedStaysDistinguishable:
    """FR-034 — in the result, in the counts, and in the event."""

    def test_in_the_result(self) -> None:
        result = ground(*build())
        assert result.outcomes["bogus"].status is GroundingStatus.UNGROUNDED
        assert result.outcomes["bogus"].span is None
        assert result.outcomes["total"].status is GroundingStatus.EXACT
        assert result.outcomes["total"].span is not None

    def test_in_the_counts(self) -> None:
        counts = ground(*build()).counts
        assert (counts.exact, counts.ungrounded) == (1, 1)

    def test_in_the_event(self, events) -> None:
        ground(*build())
        payload = payloads(events)[0]
        assert payload["exact"] == 1
        assert payload["ungrounded"] == 1

    def test_there_is_no_representation_where_the_two_look_alike(self) -> None:
        """A grounded value always has a span; an ungrounded one never does."""
        result = ground(*build())
        for outcome in result.outcomes.values():
            assert (outcome.span is not None) is outcome.grounded
            assert (outcome.score is not None) is outcome.grounded
