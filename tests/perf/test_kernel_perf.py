"""T049 — the performance targets from plan.md "Technical Context".

Marked ``perf`` and excluded from the default CI run: these are guardrails
against algorithmic regressions (an O(n) lookup creeping back in), not precise
benchmarks. Thresholds are deliberately loose so they fail on a complexity
mistake rather than on a slow machine.

    uv run pytest tests/perf -m perf
"""

from __future__ import annotations

import time

import pytest

from docdoc.kernel import (
    BBox,
    BlobRef,
    Capabilities,
    Document,
    Geometry,
    IngestProvenance,
    Page,
    Span,
    Token,
    blob_id_for,
    options_hash_for,
)

TOKEN_COUNT = 50_000
TOKEN_WIDTH = 6  # "wordNN" plus a separator


def build_large_document() -> Document:
    words = [f"w{index:05d}" for index in range(TOKEN_COUNT)]
    text = " ".join(words)

    page_size = TOKEN_COUNT // 100
    pages: list[Page] = []
    tokens: list[Token] = []

    cursor = 0
    page_start = 0
    for index, word in enumerate(words):
        span = Span(cursor, cursor + len(word))
        page_index = index // page_size
        tokens.append(
            Token(
                span=span,
                geometry=Geometry(page_index, BBox(0.1, 0.1, 0.2, 0.13)),
                source_confidence=None,
            )
        )
        cursor += len(word) + 1
        if (index + 1) % page_size == 0 or index == len(words) - 1:
            page_end = min(cursor, len(text))
            pages.append(
                Page(index=page_index, span=Span(page_start, page_end), width=612.0, height=792.0)
            )
            page_start = page_end

    options: dict[str, object] = {}
    return Document.create(
        text=text,
        pages=tuple(pages),
        tokens=tuple(tokens),
        provenance=IngestProvenance(
            parser_id="perf",
            parser_version="1.0.0",
            options=options,
            options_hash=options_hash_for(options),
            capabilities=Capabilities(text=True, geometry=True, tables=False, handwriting=False),
            text_layer_used=True,
        ),
        source=BlobRef(blob_id=blob_id_for(b"perf"), mime_type="text/plain", size_bytes=4),
    )


@pytest.fixture(scope="module")
def large_document() -> Document:
    return build_large_document()


#: What construction of a 50k-token document must stay under, best of five.
#:
#: **500 ms, raised from 300 on 2026-09-04, and the reason is the runner rather
#: than the code.** Three attempts had already been made at this test's
#: flakiness and each treated the measurement instead of the bound:
#:
#: 1. It took a single un-repeated sample, and CI showed that to be a coin flip —
#:    "247 ms at best, 369 ms at worst". Changed to best-of-five.
#: 2. It was still flaky when the perf suite ran alongside the main one, so
#:    Milestone 7 gave `-m perf` its own job: "it measures 160 ms standalone
#:    against a 300 ms budget, and the pre-Milestone-7 baseline measures the
#:    same, so the contention is the cause".
#: 3. It kept failing anyway. Measured over eight CI runs of one branch: five
#:    passes and three failures at 328, 378 and 429 ms, with re-runs of the same
#:    commit landing on both sides. The last of those was on `main`, eleven
#:    minutes after the identical tree passed on the pull request — same tree
#:    hash, opposite result.
#:
#: Each fix reduced the variance and none moved the bound, so the distribution
#: kept straddling it. A GitHub runner is roughly 2x slower than a developer
#: machine for this workload: ~228 ms here best-of-five, ~525 ms there.
#:
#: 500 ms clears the worst observed run with headroom and still catches what this
#: test is for. Construction would have to roughly double from today's measured
#: cost before it passed unnoticed, and an accidental O(n²) — the failure mode
#: worth having a budget for at all — would blow past it by an order of
#: magnitude.
#:
#: The sibling budgets stay at 300 ms deliberately. `slice` measures ~7 ms and
#: `merge` ~104 ms, so neither is near its bound, and widening a gate that is not
#: failing is how a suite stops noticing regressions. `merge` is the next one to
#: watch: ~239 ms estimated on CI against 300.
CONSTRUCTION_BUDGET_S = 0.5


@pytest.mark.perf
def test_construction_of_50k_tokens_is_within_budget() -> None:
    """Best of N, matching how Milestones 2 and 3 measure.

    Best-of-five rather than a mean: the interesting number is what the machine
    can do when nothing else is competing for it, and a mean over a noisy shared
    runner measures the neighbours.

    The budget is named rather than inlined so the reasoning above has somewhere
    to live — a bare `0.5` invites the next person to nudge it again, which is
    the habit that turns a gate into a formality.
    """
    durations = []
    for _ in range(5):
        start = time.perf_counter()
        build_large_document()
        durations.append(time.perf_counter() - start)
    elapsed = min(durations)

    assert elapsed < CONSTRUCTION_BUDGET_S, (
        f"construction took {elapsed * 1000:.0f} ms (best of 5), over the "
        f"{CONSTRUCTION_BUDGET_S * 1000:.0f} ms budget. This bound has enough "
        f"headroom over a slow runner that exceeding it means the cost really "
        f"moved — check for an accidental quadratic in Document construction "
        f"rather than re-running"
    )


@pytest.mark.perf
def test_locate_p95_is_under_1ms(large_document: Document) -> None:
    """Guards the O(log n + k) lookup. A linear scan would be ~100x slower."""
    step = len(large_document.text) // 500
    timings: list[float] = []
    for offset in range(0, len(large_document.text) - TOKEN_WIDTH, step):
        span = Span(offset, offset + TOKEN_WIDTH)
        start = time.perf_counter()
        large_document.locate(span)
        timings.append(time.perf_counter() - start)

    timings.sort()
    p95 = timings[int(len(timings) * 0.95)]
    assert p95 < 0.001, f"locate p95 was {p95 * 1000:.3f} ms"


@pytest.mark.perf
def test_find_over_a_large_text_is_under_50ms(large_document: Document) -> None:
    start = time.perf_counter()
    large_document.find("w49999")
    elapsed = time.perf_counter() - start
    assert elapsed < 0.05, f"find took {elapsed * 1000:.0f} ms"


@pytest.mark.perf
def test_slice_of_50k_tokens_is_under_300ms(large_document: Document) -> None:
    """A whole-document slice costs about what construction costs.

    The result is a new Document, and a Document re-checks every invariant at
    construction -- that is the guarantee, not an overhead to optimise away. The
    original plan.md figure of 20 ms assumed slicing was cheaper than building,
    which was an unmeasured guess.
    """
    durations = []
    for _ in range(5):
        start = time.perf_counter()
        large_document.slice(Span(0, len(large_document.text)))
        durations.append(time.perf_counter() - start)
    elapsed = min(durations)
    assert elapsed < 0.3, f"slice took {elapsed * 1000:.0f} ms (best of 5)"


@pytest.mark.perf
def test_merge_of_100_parts_is_under_300ms(large_document: Document) -> None:
    bounds = [page.span for page in large_document.pages]
    parts = [large_document.slice(span) for span in bounds]

    durations = []
    for _ in range(5):
        start = time.perf_counter()
        Document.merge(parts)
        durations.append(time.perf_counter() - start)
    elapsed = min(durations)
    assert elapsed < 0.3, f"merge of {len(parts)} parts took {elapsed * 1000:.0f} ms (best of 5)"
