"""The input-budget guard: local, network-free, and deliberately pessimistic.

FR-030 must refuse an over-budget document, and FR-041 requires that refusal to
happen **before anything is transmitted**. The only exact token count available
is an API call that transmits the document in order to answer -- precisely what
the guard exists to avoid, plus a round trip on every extraction. A third-party
tokenizer is worse: it is the wrong provider's, and it would put a base-install
dependency there to produce a wrong number.

So the guard estimates locally, and estimates **high**. The consequence to accept
is that a document which would actually have fitted can be refused. That is the
correct direction to be wrong in -- refusing a document the caller can narrow
with ``Document.slice`` beats transmitting one the provider will reject -- but it
is a real limitation, not a rounding detail (research.md R5).

The provider's own too-long rejection maps to this same error, so a caller sees
one condition rather than two.
"""

from __future__ import annotations

from docdoc.extraction.errors import ExtractionError

__all__ = [
    "CHARS_PER_TOKEN",
    "DEFAULT_INPUT_BUDGET_TOKENS",
    "SAFETY_MARGIN",
    "estimate_tokens",
    "guard_input_budget",
]

# ---------------------------------------------------------------------------
# PROVISIONAL until T079 measures them (specs/003 research.md R5).
#
# Both numbers below are guesses, and the task list says so out loud rather than
# letting the MVP imply they were measured. T079 calibrates them against every
# committed fixture using the provider's own token count as ground truth, after
# the real adapter exists -- which is why it could not live in Phase 2, where it
# was originally written.
#
# The ratio is pessimistic on purpose. English prose runs about 4 characters per
# token; dense tabular invoice text, punctuation, and non-Latin scripts run
# lower. Assuming 2.5 over-counts ordinary text by roughly 40%, and the margin
# adds headroom on top. Wrong in the refusing direction, never the transmitting
# one.
# ---------------------------------------------------------------------------
CHARS_PER_TOKEN = 2.5
SAFETY_MARGIN = 1.15

#: Comfortably under the default model's 1M-token context window, leaving room
#: for the response and for reasoning, which share the output budget. Also
#: provisional until T079.
DEFAULT_INPUT_BUDGET_TOKENS = 200_000


def estimate_tokens(text: str) -> int:
    """A deliberate over-estimate of what ``text`` will cost in input tokens.

    Never call this an exact count anywhere it might be mistaken for one.
    """
    return int((len(text) / CHARS_PER_TOKEN) * SAFETY_MARGIN) + 1


def guard_input_budget(
    text: str,
    *,
    budget_tokens: int,
    document_id: str | None = None,
    schema_identity: str | None = None,
) -> int:
    """Refuse an over-budget request before anything leaves the process.

    Returns the estimate when it passes, so a caller can log it without
    recomputing.
    """
    estimate = estimate_tokens(text)
    if estimate <= budget_tokens:
        return estimate
    raise ExtractionError(
        f"document exceeds the input budget (estimate ~{estimate:,} tokens > "
        f"{budget_tokens:,}). Narrow the document with Document.slice and extract from the "
        "result -- slice preserves original page numbers and geometry, so a value extracted "
        "from a narrowed document can still be grounded back to its true page. The estimate "
        "is deliberately high, so a document near the bound may fit in practice",
        reason="input_budget",
        document_id=document_id,
        schema_identity=schema_identity,
    )
