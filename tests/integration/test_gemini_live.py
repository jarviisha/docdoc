"""T052, T060 — the live path. Marked `provider`; skipped without credentials.

Everything else in the suite runs offline. These are the tests that cannot: they
call a real model, cost real money, and are the only place the request this layer
builds is proved acceptable to the provider rather than merely well-formed.

T060 is deliberately *not* an assertion that the prompt cache hits. Research R15
measured the per-schema prefix at **817 tokens** against a 2,048-4,096 minimum, so
a hit is impossible today. Asserting one would be a test written to fail, and a
test written to fail gets skipped. It measures and records instead.

The number matters more than "a few hundred" would, which is what this said first.
817 against 2,048 says the prefix must grow about 2.5x before the ordering starts
paying, and that is the fact a reader needs to judge whether the decision to leave
it uncached still holds. A rounded-away measurement cannot be re-judged.
"""

from __future__ import annotations

import os

import pytest
from tests.support import make_document

from docdoc.extraction import ExtractionOptions, SchemaRegistry, extract, response_shape_for
from docdoc.extraction.adapters.gemini import GeminiAdapter
from docdoc.extraction.budget import estimate_tokens
from docdoc.extraction.prompt import build_request

pytestmark = pytest.mark.provider

INVOICE = """\
BEISPIEL GMBH
Musterstrasse 1, 10115 Berlin
VAT DE123456789

INVOICE  INV-2026-0042
Issue date: 01/03/2026
Due date:   31/03/2026

Consulting, March      10 x 200,00 EUR    2.000,00
VAT 24%                                     480,50
------------------------------------------------
TOTAL                                  EUR 2.480,50
"""


def _skip_unless_credentialed() -> None:
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        pytest.skip("no GEMINI_API_KEY or GOOGLE_API_KEY configured; live model calls cost money")


@pytest.fixture
def registry() -> SchemaRegistry:
    return SchemaRegistry.from_paths(["schemas"])


@pytest.fixture
def adapter() -> GeminiAdapter:
    _skip_unless_credentialed()
    return GeminiAdapter()


def test_a_real_extraction_produces_the_same_shape_as_the_offline_one(
    registry: SchemaRegistry, adapter: GeminiAdapter
) -> None:
    """US3 — nothing downstream can tell which adapter produced a result."""
    result = extract(make_document(INVOICE), schema="invoice@1", registry=registry, adapter=adapter)

    assert set(result.values) == {f.name for f in registry.resolve("invoice@1").schema.fields}
    assert result.provenance.adapter_id == "gemini"
    assert result.provenance.model_id
    assert result.provenance.extractor_version.startswith("1.0.0+gemini-")
    assert result.artifact_id.startswith("sha256:")

    # Every grounding field is still unresolved — the stage boundary holds against
    # a real model, not only against the fixture (EXT-24).
    assert result.value_at("total").grounding is None

    # The claimed text must be byte-faithful. A model that "helpfully" normalises
    # 2.480,50 to 2480.50 would leave Milestone 4 unable to locate it.
    claimed = result.value_at("total").claimed_text
    assert claimed is not None
    assert claimed.strip() in INVOICE, f"claimed text {claimed!r} is not in the document verbatim"


def test_the_forbidden_parameters_are_accepted(
    registry: SchemaRegistry, adapter: GeminiAdapter
) -> None:
    """R4 — all four exist here, unlike on the provider first planned for.

    If any were rejected the call would raise, so a passing extraction *is* the
    assertion.
    """
    extract(
        make_document(INVOICE),
        schema="invoice@1",
        registry=registry,
        adapter=adapter,
        options=ExtractionOptions(temperature=0.0, top_p=0.95, top_k=40, seed=42),
    )


def test_usage_is_reported_including_reasoning(
    registry: SchemaRegistry, adapter: GeminiAdapter
) -> None:
    usage = extract(
        make_document(INVOICE), schema="invoice@1", registry=registry, adapter=adapter
    ).provenance.usage
    assert usage.input_tokens is not None
    assert usage.input_tokens > 0
    assert usage.output_tokens is not None
    assert usage.output_tokens > 0


def test_the_cache_threshold_arithmetic_rather_than_a_hit(
    registry: SchemaRegistry, adapter: GeminiAdapter
) -> None:
    """T060 — measure, and record why the number is what it is.

    A cache hit needs the shared per-schema prefix to clear the provider's minimum
    (2,048 tokens on the 2.5 tier). This asserts the *relationship* rather than a
    hit, and fails loudly if the prefix ever grows past the threshold while cache
    reads stay at zero — which would then be a real bug in the assembly order.
    """
    minimum = 2_048
    entry = registry.resolve("invoice@1")
    request = build_request(entry, INVOICE, response_shape=response_shape_for(entry.schema))
    prefix_estimate = estimate_tokens(request.prefix)

    first = extract(make_document(INVOICE), schema="invoice@1", registry=registry, adapter=adapter)
    second = extract(
        make_document(INVOICE + "\n(second document)"),
        schema="invoice@1",
        registry=registry,
        adapter=adapter,
    )
    cached = second.provenance.usage.cache_read_input_tokens or 0

    if prefix_estimate < minimum:
        assert cached == 0, (
            f"the per-schema prefix is ~{prefix_estimate} tokens, below the {minimum}-token "
            f"minimum, so a cache read of {cached} is unexpected — recheck R15's arithmetic"
        )
        pytest.skip(
            f"prefix ~{prefix_estimate} tokens < {minimum} minimum: caching is not yet "
            "eligible (research.md R15). Recorded, not asserted."
        )
    else:
        assert cached > 0, (
            f"the prefix is ~{prefix_estimate} tokens, past the {minimum} minimum, yet the "
            "second call read nothing from cache. Something volatile precedes the breakpoint"
        )
    assert first.artifact_id != second.artifact_id
