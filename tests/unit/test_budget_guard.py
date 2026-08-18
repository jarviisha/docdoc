"""T030 — the input-budget guard (EXT-20, research.md R5).

Two properties matter, and only one of them is about correctness of the number.

The guard must **over**-estimate, never under-estimate. It cannot be exact,
because the only exact count available is an API call that transmits the document
to answer it -- the very thing the guard exists to avoid. So the requirement is a
direction, not an accuracy: wrong towards refusing, never towards transmitting.

And it must run before anything leaves the process (FR-041), which is asserted at
the ``extract()`` level in ``test_extract_echo.py`` against a recording adapter.
"""

from __future__ import annotations

import pytest

from docdoc.extraction import ExtractionError
from docdoc.extraction.budget import (
    CHARS_PER_TOKEN,
    DEFAULT_INPUT_BUDGET_TOKENS,
    SAFETY_MARGIN,
    estimate_tokens,
    guard_input_budget,
)

#: The **measured** floor (T079), not a guess. Across the committed fixtures plus
#: deliberately dense content, the densest real material ran 1.10 characters per
#: token -- emoji and numeric tables -- with CJK at 1.18 and dense tabular invoice
#: text at 1.27. This constant was 6.0 while it was a guess, which made the
#: assertions below far weaker than they could be.
MOST_CHARS_A_TOKEN_COULD_HOLD = 1.10


def test_the_ratio_over_estimates_even_the_densest_measured_content() -> None:
    """The invariant that keeps the guard wrong in the safe direction.

    The guessed ratio failed exactly this: 2.5 chars per token under-estimated
    dense tabular invoice text by 1.72x, letting an over-budget document through
    to be transmitted -- the failure the guard exists to prevent.
    """
    assert CHARS_PER_TOKEN <= MOST_CHARS_A_TOKEN_COULD_HOLD * SAFETY_MARGIN, (
        f"CHARS_PER_TOKEN={CHARS_PER_TOKEN} would under-estimate content at "
        f"{MOST_CHARS_A_TOKEN_COULD_HOLD} chars/token even with the margin applied"
    )
    assert SAFETY_MARGIN >= 1.0


def test_the_estimate_grows_with_the_text() -> None:
    assert estimate_tokens("") < estimate_tokens("a" * 100) < estimate_tokens("a" * 10_000)


def test_the_estimate_never_returns_zero() -> None:
    """A zero estimate would let an empty budget pass anything."""
    assert estimate_tokens("") >= 1


@pytest.mark.parametrize("length", [1, 10, 500, 5_000, 200_000])
def test_the_estimate_exceeds_a_generous_true_count(length: int) -> None:
    """The direction, stated as an assertion rather than as a comment.

    ``length / MOST_CHARS_A_TOKEN_COULD_HOLD`` is a deliberately *low* stand-in
    for the true token count. The estimate must sit above it for every size, or
    the guard would let an over-budget document through to be transmitted.
    """
    generous_true_count = length / MOST_CHARS_A_TOKEN_COULD_HOLD
    assert estimate_tokens("x" * length) > generous_true_count


def test_over_estimation_holds_for_real_fixture_text() -> None:
    """Prose, dense tabular text, and non-Latin script -- the shapes that differ most."""
    import pathlib

    samples = [
        pathlib.Path("schemas/prompts/invoice@1.md").read_text(encoding="utf-8"),
        "INV-001 | 2026-03-01 | 1,240.00 | USD\n" * 200,
        "Hóa đơn số 001 — Tổng cộng 1.240.000 ₫\n" * 200,
    ]
    for text in samples:
        assert estimate_tokens(text) > len(text) / MOST_CHARS_A_TOKEN_COULD_HOLD


def test_a_passing_guard_returns_the_estimate() -> None:
    """So a caller can log it without recomputing."""
    assert guard_input_budget("short", budget_tokens=1_000) == estimate_tokens("short")


def test_an_over_budget_document_raises_with_the_right_reason() -> None:
    with pytest.raises(ExtractionError) as caught:
        guard_input_budget("x" * 10_000, budget_tokens=10)
    assert caught.value.reason == "input_budget"


def test_the_error_names_the_document_the_bound_and_the_estimate() -> None:
    with pytest.raises(ExtractionError) as caught:
        guard_input_budget(
            "x" * 10_000,
            budget_tokens=10,
            document_id="sha256:doc",
            schema_identity="invoice@1",
        )
    message = str(caught.value)
    assert "10" in message, "the bound"
    assert "estimate" in message
    assert caught.value.document_id == "sha256:doc"
    assert caught.value.schema_identity == "invoice@1"


def test_the_error_names_narrowing_as_the_way_forward() -> None:
    """FR-046 -- an error that only says "too big" leaves the caller stuck."""
    with pytest.raises(ExtractionError) as caught:
        guard_input_budget("x" * 10_000, budget_tokens=10)
    message = str(caught.value)
    assert "Document.slice" in message
    assert "grounded back to its true page" in message


def test_the_error_admits_the_estimate_is_high() -> None:
    """Honesty about the limitation, in the place a user will actually read."""
    with pytest.raises(ExtractionError) as caught:
        guard_input_budget("x" * 10_000, budget_tokens=10)
    assert "deliberately high" in str(caught.value)


def test_the_boundary_is_inclusive() -> None:
    """A document estimated at exactly the budget fits. Off-by-one here is a
    document refused for no reason."""
    text = "x" * 1_000
    assert guard_input_budget(text, budget_tokens=estimate_tokens(text)) > 0
    with pytest.raises(ExtractionError):
        guard_input_budget(text, budget_tokens=estimate_tokens(text) - 1)


def test_the_default_budget_leaves_headroom_under_the_context_window() -> None:
    """R14 -- `max_tokens` caps thinking *plus* response on the default model."""
    assert DEFAULT_INPUT_BUDGET_TOKENS == 200_000
    assert DEFAULT_INPUT_BUDGET_TOKENS < 1_000_000 // 2, (
        "the default must leave room for the response and for reasoning"
    )
