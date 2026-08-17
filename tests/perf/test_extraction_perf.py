"""T070 — performance targets for the extraction layer (SC-021).

Marked ``perf`` and deselectable:

    uv run pytest -m perf

**Only the deterministic work is bounded.** The model call is a property of the
provider and is *recorded* per extraction rather than targeted — a target
spanning it would make the check depend on the provider's latency instead of on
docdoc's code, and would need credentials to run at all. So every measurement
here runs against the in-repo echo adapter.

Targets are deliberately loose relative to the measured numbers, for the reason
Milestone 2 recorded: a perf test that trips on ordinary machine noise gets
disabled, and a disabled test protects nothing. What these catch is an accidental
per-extraction schema recompile or a quadratic in the conformance walk, not
constant-factor drift.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from docdoc.extraction import (
    ExtractionOptions,
    SchemaRegistry,
    extract,
    response_shape_for,
    schema_hash_for,
)
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.extraction.conform import conform
from docdoc.extraction.schema import Cardinality, FieldSpec, FieldType, Schema
from tests.support import make_document

pytestmark = pytest.mark.perf

ECHO_FIXTURES = Path("tests/fixtures/echo")

#: SC-021's document: 20 pages of invoice-shaped text.
_PAGE = (
    "BEISPIEL GMBH\nMusterstrasse 1, 10115 Berlin\nINVOICE INV-2026-0042\n"
    "Issue date: 01/03/2026\nConsulting, March 10 x 200,00 EUR 2.000,00\n"
    "VAT 24% 480,50\nTOTAL EUR 2.480,50\n"
)
DOCUMENT_TEXT = _PAGE * 20


def timed(work: Callable[[], Any], repeats: int = 5) -> float:
    """Best of N, in milliseconds. Best-of resists a noisy neighbour."""
    durations = []
    for _ in range(repeats):
        started = time.perf_counter()
        work()
        durations.append((time.perf_counter() - started) * 1000)
    return min(durations)


def _wide_schema(field_count: int) -> Schema:
    return Schema(
        name="wide",
        version=1,
        fields=tuple(
            FieldSpec(
                name=f"field_{i:03d}",
                type=FieldType.STRING,
                description=f"the {i}th field, with a description of realistic length attached",
                constraints={"max_length": 64},
            )
            for i in range(field_count)
        ),
    )


def test_one_extraction_stays_under_the_deterministic_budget() -> None:
    """SC-021 — 100 ms for a 20-page document against a 20-field schema.

    ``invoice@1`` declares 13 paths across scalars, a group, and a repeating
    group, which is the shape the criterion means rather than a flat 20.
    """
    registry = SchemaRegistry.from_paths(["schemas"])
    adapter = EchoAdapter.from_fixtures(ECHO_FIXTURES)
    document = make_document(DOCUMENT_TEXT)

    elapsed = timed(
        lambda: extract(document, schema="invoice@1", registry=registry, adapter=adapter)
    )
    print(f"\n  extract(), 20-page doc / invoice@1: {elapsed:.2f} ms")
    assert elapsed < 100.0, f"SC-021 budget is 100 ms, measured {elapsed:.2f} ms"


def test_a_schema_is_not_recompiled_per_extraction() -> None:
    """The specific regression the budget exists to catch.

    If any per-schema work moved from registration into ``extract()``, the
    hundredth extraction would cost what the first did. Comparing a batch against
    a single call catches that without depending on absolute speed.
    """
    registry = SchemaRegistry.from_paths(["schemas"])
    adapter = EchoAdapter.from_fixtures(ECHO_FIXTURES)
    document = make_document(DOCUMENT_TEXT)

    one = timed(lambda: extract(document, schema="invoice@1", registry=registry, adapter=adapter))

    def fifty() -> None:
        for _ in range(50):
            extract(document, schema="invoice@1", registry=registry, adapter=adapter)

    batch = timed(fifty, repeats=2)
    per_call = batch / 50
    print(f"  single call {one:.2f} ms; per-call in a batch of 50 {per_call:.2f} ms")
    assert per_call < one * 1.5 + 5.0, (
        f"per-call cost in a batch ({per_call:.2f} ms) should not exceed a single call "
        f"({one:.2f} ms) by much; a gap here means per-schema work leaked into extract()"
    )


def test_registration_of_every_committed_schema_is_fast() -> None:
    """Registration is once per process, so its budget is looser than extraction's."""
    elapsed = timed(lambda: SchemaRegistry.from_paths(["schemas"]), repeats=3)
    print(f"  registry load, 3 schemas + prompts: {elapsed:.2f} ms")
    assert elapsed < 200.0


def test_schema_hash_over_a_hundred_field_schema() -> None:
    schema = _wide_schema(100)
    elapsed = timed(lambda: schema_hash_for(schema))
    print(f"  schema_hash, 100 fields: {elapsed:.2f} ms")
    assert elapsed < 10.0


def test_the_projection_is_linear_in_field_count() -> None:
    """A quadratic here would only show on a schema nobody has written yet."""
    small = timed(lambda: response_shape_for(_wide_schema(25)))
    large = timed(lambda: response_shape_for(_wide_schema(200)))
    print(f"  projection: 25 fields {small:.3f} ms, 200 fields {large:.3f} ms")
    # 8x the fields should cost well under 24x the time if the walk is linear.
    assert large < small * 24 + 5.0, f"projection looks super-linear: {small:.3f} -> {large:.3f} ms"


def test_conformance_is_linear_in_repeating_group_length() -> None:
    """The one place a real document can be arbitrarily large.

    An invoice with 500 line items is unusual but not absurd, and the conformance
    walk is the code that would notice.
    """
    schema = Schema(
        name="lines",
        version=1,
        fields=(
            FieldSpec(
                name="items",
                cardinality=Cardinality.REPEATING_GROUP,
                description="lines",
                fields=(
                    FieldSpec(name="description", type=FieldType.STRING, description="what"),
                    FieldSpec(name="amount", type=FieldType.DECIMAL, description="how much"),
                ),
            ),
        ),
    )

    def payload(count: int) -> dict[str, Any]:
        entry = {
            "description": {"value": "Widget", "claimed_text": "Widget", "confidence": None},
            "amount": {"value": "10.00", "claimed_text": "10,00", "confidence": None},
        }
        return {"items": [dict(entry) for _ in range(count)]}

    fifty = timed(lambda: conform(payload(50), schema))
    five_hundred = timed(lambda: conform(payload(500), schema))
    print(f"  conform: 50 items {fifty:.3f} ms, 500 items {five_hundred:.3f} ms")
    assert five_hundred < fifty * 30 + 5.0, (
        f"conformance looks super-linear in repeating-group length: "
        f"{fifty:.3f} -> {five_hundred:.3f} ms"
    )


def test_the_budget_guard_is_negligible() -> None:
    """It runs on every extraction, so a slow guard is a tax on every call."""
    from docdoc.extraction.budget import estimate_tokens

    big = DOCUMENT_TEXT * 50
    elapsed = timed(lambda: estimate_tokens(big))
    print(f"  budget guard over {len(big):,} chars: {elapsed:.4f} ms")
    assert elapsed < 5.0


def test_the_echo_adapter_adds_no_measurable_cost() -> None:
    """Otherwise the numbers above would be measuring the fixture loader."""
    adapter = EchoAdapter.from_fixtures(ECHO_FIXTURES)
    registry = SchemaRegistry.from_paths(["schemas"])
    entry = registry.resolve("invoice@1")
    from docdoc.extraction.prompt import build_request

    request = build_request(entry, DOCUMENT_TEXT, response_shape=response_shape_for(entry.schema))
    elapsed = timed(lambda: adapter.complete(request, ExtractionOptions()))
    print(f"  echo adapter complete(): {elapsed:.4f} ms")
    assert elapsed < 5.0


def test_the_recorded_fixtures_are_the_shape_the_budget_assumes() -> None:
    """Guards the guard: a shrunken fixture would make every budget above trivial."""
    payload = json.loads((ECHO_FIXTURES / "invoice@1.json").read_text(encoding="utf-8"))
    assert len(payload) >= 7
    assert len(payload["line_items"]) >= 2
    assert len(DOCUMENT_TEXT) > 3_000, "the 20-page document must actually be 20 pages of text"
