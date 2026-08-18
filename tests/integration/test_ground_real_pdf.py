"""T016 — grounding a real parsed PDF, end to end, offline (SC-001, SC-002, SC-003).

No credentials, no network, no database, no object storage. The document comes
from the Milestone 2 fixtures and is parsed by the real PDF parser; the claims
are lifted from that document's own text, which is what an extraction would have
returned had it been asked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from docdoc.grounding import GroundingStatus, ground

pymupdf = pytest.importorskip("pymupdf", reason="the pdf extra supplies the fixture parser")

from tests.support import make_extracted, make_extraction  # noqa: E402

from docdoc.ingest import parse  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "pdf" / "digital_invoice.pdf"


@pytest.fixture(scope="module")
def document():
    return parse(FIXTURE.read_bytes())


class TestPointsAtSource:
    """US1's independent test: hand-verified claims land on hand-verified ranges."""

    def test_every_claim_present_in_the_document_resolves(self, document) -> None:
        claims = {"number": "INV-001", "supplier": "ACME SUPPLIES LIMITED"}
        for name, claim in claims.items():
            assert claim in document.text, f"fixture drifted: {name}"
        extraction = make_extraction(
            {k: make_extracted(k, value=k, claimed_text=v) for k, v in claims.items()},
            document=document,
        )
        result = ground(document, extraction)
        for name in claims:
            assert result.outcomes[name].status is GroundingStatus.EXACT, name

    def test_the_range_reads_back_as_the_claim(self, document) -> None:
        claim = "ACME SUPPLIES LIMITED"
        extraction = make_extraction(
            {"supplier": make_extracted("supplier", value="ACME", claimed_text=claim)},
            document=document,
        )
        span = ground(document, extraction).outcomes["supplier"].span
        assert span is not None
        assert document.text[span.start : span.end] == claim

    def test_it_carries_a_page_and_a_box(self, document) -> None:
        extraction = make_extraction(
            {"number": make_extracted("number", value="INV-001", claimed_text="INV-001")},
            document=document,
        )
        outcome = ground(document, extraction).outcomes["number"]
        assert outcome.pages == (0,)
        assert outcome.geometry is not None
        assert len(outcome.geometry) >= 1
        box = outcome.geometry[0].bbox
        assert 0.0 <= box.x0 < box.x1 <= 1.0
        assert 0.0 <= box.y0 < box.y1 <= 1.0

    def test_a_fabricated_claim_is_ungrounded_and_does_not_raise(self, document) -> None:
        extraction = make_extraction(
            {
                "bogus": make_extracted(
                    "bogus", value="?", claimed_text="Chairman of the Interstellar Board"
                )
            },
            document=document,
        )
        outcome = ground(document, extraction).outcomes["bogus"]
        assert outcome.status is GroundingStatus.UNGROUNDED
        assert outcome.span is None
        assert outcome.score is None

    def test_the_document_is_unchanged(self, document) -> None:
        before = document.text
        extraction = make_extraction(
            {"number": make_extracted("number", value="INV-001", claimed_text="INV-001")},
            document=document,
        )
        ground(document, extraction)
        assert document.text == before

    def test_counts_make_the_grounding_rate_computable(self, document) -> None:
        extraction = make_extraction(
            {
                "number": make_extracted("number", value="INV-001", claimed_text="INV-001"),
                "bogus": make_extracted("bogus", value="?", claimed_text="nowhere at all"),
                "absent": make_extracted("absent", present=False),
            },
            document=document,
        )
        counts = ground(document, extraction).counts
        assert counts.exact == 1
        assert counts.ungrounded == 1
        assert counts.not_applicable == 1
        assert counts.grounding_rate == pytest.approx(0.5)
