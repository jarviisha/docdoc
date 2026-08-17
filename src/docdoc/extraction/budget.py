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
# MEASURED at T079 against the provider's own `count_tokens`, over the committed
# fixtures plus deliberately dense content. The first values here were guesses --
# 2.5 chars per token with a 1.15 margin -- and the measurement showed them
# **under-estimating by up to 1.72x** on exactly the content this engine reads:
#
#     dense tabular invoice text   1.27 chars/token
#     Vietnamese with diacritics   1.62
#     CJK                          1.18
#     numeric tables / emoji       1.10   <- the floor
#     English prose                5.13
#
# That is the wrong direction to be wrong in. The guard exists to refuse before
# transmitting; one that under-counts lets a doomed request through, which is the
# failure it was written to prevent.
#
# 1.10 is the measured floor, so 1.10 x 1.15 = 1.26 is the largest ratio that
# still over-estimates it. 1.20 is used, leaving headroom for content denser than
# anything measured.
CHARS_PER_TOKEN = 1.20
SAFETY_MARGIN = 1.15

# The cost of being safe, stated rather than hidden: a single linear ratio cannot
# serve both 1.10 and 5.13 chars per token. Tuned to the dense floor, ordinary
# English prose is over-estimated roughly 5x, so a document of ~215k characters is
# refused against a 200k-token budget when it would truly cost ~42k tokens.
#
# That is a real usability cost and the escape hatch is `Document.slice`, which is
# friction. The alternative -- the provider's exact `count_tokens` -- transmits the
# document to answer, which is the thing FR-041 forbids doing before validation.
# So the trade is deliberate: refuse some documents that would have fitted, rather
# than transmit any that will not. Revisit it with FR-041 open on the table, not
# by loosening this constant.

#: Comfortably under the 1M-token context window of the models measured, leaving
#: room for the response and for reasoning, which share the output allowance.
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
