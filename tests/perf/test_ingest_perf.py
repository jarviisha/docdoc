"""T052 — performance targets for the ingest layer.

Marked ``perf`` and deselectable. The figures in plan.md were written as targets
before PyMuPDF was installed; this file is what replaces them with measurements.

    uv run pytest -m perf

Targets are deliberately loose relative to the measured numbers. A perf test
that trips on ordinary machine noise gets disabled, and a disabled test protects
nothing.
"""

from __future__ import annotations

import time
from pathlib import Path

import pymupdf
import pytest

from docdoc.ingest import parse
from docdoc.ingest.assess import assess_text_layer
from docdoc.ingest.source import SourceFile

pytestmark = pytest.mark.perf

FIXTURES = Path(__file__).parent.parent / "fixtures"


def _many_pages(count: int) -> bytes:
    """A synthetic text-bearing document of ``count`` pages."""
    with pymupdf.open(FIXTURES / "pdf" / "digital_invoice.pdf") as one:
        out = pymupdf.open()
        while len(out) < count:
            out.insert_pdf(one)
        data: bytes = out.tobytes(garbage=4, deflate=True)
        out.close()
    return data


def timed(work: object, repeats: int = 3) -> float:
    """Best of N, in milliseconds. Best-of resists a noisy neighbour."""
    durations = []
    for _ in range(repeats):
        started = time.perf_counter()
        work()  # type: ignore[operator]
        durations.append((time.perf_counter() - started) * 1000)
    return min(durations)


class TestNativePath:
    def test_a_20_page_text_pdf_parses_well_inside_the_success_criterion(self) -> None:
        """SC-012 allows 5 s. The target is 1 s; the gap is deliberate headroom
        for slower machines and future page-level work."""
        data = _many_pages(20)

        elapsed = timed(lambda: parse(data))

        print(f"\n  native parse, 20 pages: {elapsed:.0f} ms")
        assert elapsed < 1000

    def test_a_200_page_text_pdf_stays_roughly_linear(self) -> None:
        twenty = timed(lambda: parse(_many_pages(20)))
        two_hundred = timed(lambda: parse(_many_pages(200)))

        print(f"\n  native parse, 200 pages: {two_hundred:.0f} ms")
        assert two_hundred < 8000
        # Linear within a wide factor: what this catches is an accidental
        # quadratic, not a constant-factor regression.
        assert two_hundred < twenty * 40


class TestAssessment:
    def test_the_verdict_is_cheap_enough_to_precede_routing(self) -> None:
        """The assessment runs before a parser is chosen, so it must cost far
        less than the parse it might avoid (research.md R4)."""
        source = SourceFile.from_bytes(_many_pages(20))

        elapsed = timed(lambda: assess_text_layer(source))

        print(f"\n  assessment, 20 pages: {elapsed:.0f} ms")
        assert elapsed < 500

    def test_assessment_is_a_fraction_of_a_full_parse(self) -> None:
        data = _many_pages(20)
        source = SourceFile.from_bytes(data)

        assessment = timed(lambda: assess_text_layer(source))
        full = timed(lambda: parse(data))

        assert assessment < full, "a decision that costs more than the work it gates is no saving"
