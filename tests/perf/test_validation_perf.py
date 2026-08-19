"""T078 — SC-020's bound, and the adversarial case the pattern dialect exists for.

The bound was *derived* in research.md R8 from measured unit costs (a pattern
check at 8.84 us, a 200-entry sum at 26.4 us, a tolerance comparison at 688 ns)
rather than asserted, and one row of that derivation — constructing the result's
pydantic models — was an estimate. This file is where the estimate is either
confirmed or found to dominate.

Targets sit well above measurements for the reason Milestones 2, 3 and 4 all
recorded: a perf test that trips on machine noise gets disabled, and a disabled
test protects nothing. What these catch is a pattern recompiled per value instead
of per schema, or an enumeration that went quadratic in entry count.
"""

from __future__ import annotations

import time
from decimal import Decimal

import pytest

from docdoc.extraction.identity import schema_hash_for
from docdoc.extraction.schema import Cardinality, FieldSpec, FieldType, Schema
from docdoc.grounding import ground
from docdoc.validation import validate
from docdoc.validation.pattern import compile_pattern
from tests.fixtures.validation import rules as rule_fixtures
from tests.support import make_document, make_extracted, make_extraction

pytestmark = pytest.mark.perf

_TEXT = "Line item Widget 12.00 total 2400.00 ref INV-2026-001\n" * 40


def _large_case(entries: int = 100):
    """A schema of ~200 values and 20 rules, the shape SC-020 names."""
    rules = tuple(
        rule_fixtures.sum_rule().model_copy(update={"id": f"total_matches_{index}"})
        for index in range(20)
    )
    schema = Schema(
        name="perf_probe",
        version=1,
        rules=rules,
        fields=(
            FieldSpec(
                name="total",
                type=FieldType.DECIMAL,
                required=True,
                constraints={"minimum": 0},
            ),
            FieldSpec(
                name="reference",
                type=FieldType.STRING,
                required=True,
                constraints={"pattern": r"INV-\d{4}-\d{3}"},
            ),
            FieldSpec(
                name="line_items",
                cardinality=Cardinality.REPEATING_GROUP,
                fields=(
                    FieldSpec(
                        name="description",
                        type=FieldType.STRING,
                        required=True,
                        constraints={"max_length": 64},
                    ),
                    FieldSpec(name="amount", type=FieldType.DECIMAL, required=True),
                ),
            ),
        ),
    )
    document = make_document(_TEXT)
    line = {
        "description": make_extracted("d", value="Widget", claimed_text="Widget"),
        "amount": make_extracted("a", value=Decimal("12.00"), claimed_text="12.00"),
    }
    values = {
        "total": make_extracted("total", value=Decimal("12.00") * entries, claimed_text="2400.00"),
        "reference": make_extracted("reference", value="INV-2026-001", claimed_text="INV-2026-001"),
        "line_items": tuple(line for _ in range(entries)),
    }
    extraction = make_extraction(
        values,
        document=document,
        schema_identity=schema.identity,
        schema_hash=schema_hash_for(schema),
    )
    return extraction, ground(document, extraction), schema


def _best_of(runs: int, call) -> float:
    return min(_timed(call) for _ in range(runs))


def _timed(call) -> float:
    started = time.perf_counter()
    call()
    return (time.perf_counter() - started) * 1000


def test_a_two_hundred_value_result_validates_within_the_bound() -> None:
    """SC-020 — under 50 ms for ~200 values against 20 rules."""
    extraction, grounding, schema = _large_case()
    elapsed = _best_of(3, lambda: validate(extraction, grounding, schema))
    assert elapsed < 50, f"validation took {elapsed:.1f} ms against a 50 ms bound"


def test_the_bound_holds_for_an_adversarial_pattern() -> None:
    """The input CPython's `re` cannot survive, inside a real validation run."""
    compiled = compile_pattern(r"(a+)+")
    text = "a" * 10_000 + "!"
    elapsed = _best_of(3, lambda: compiled.fullmatch(text))
    assert elapsed < 100, f"the adversarial pattern took {elapsed:.1f} ms"


def test_patterns_are_compiled_once_not_once_per_value() -> None:
    """The regression this test exists for: compilation moved into the hot path.

    Compiling is the expensive half of the dialect, so a per-value compile would
    not fail the bound above on a fast machine — it would just make the cost scale
    with the wrong thing. Measured directly instead.
    """
    extraction, grounding, schema = _large_case(entries=20)
    small = _best_of(3, lambda: validate(extraction, grounding, schema))
    extraction, grounding, schema = _large_case(entries=200)
    large = _best_of(3, lambda: validate(extraction, grounding, schema))

    # Ten times the entries costs well under ten times the work only if the fixed
    # costs — compilation among them — really are fixed.
    assert large < small * 12, f"{small:.1f} ms at 20 entries, {large:.1f} ms at 200"
