"""T036 — every recognised constraint key has an enforcement path (SC-005).

This is the anti-regression test of the whole milestone. Milestone 3 recognised
eight constraint keys and applied none of them; the defect was invisible because
nothing connected the list of keys to the list of things that check them. This
test makes that connection mechanical, so a *newly* recognised key cannot ship
silently unenforced — the failure mode would otherwise repeat exactly, one key at
a time.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from docdoc.extraction.schema import CONSTRAINT_KEYS, CONSTRAINT_TYPE_DOMAINS
from docdoc.validation.constraints import _EVALUATORS
from tests.fixtures.validation.schemas import EVERY_CONSTRAINT_KEY


def test_every_recognised_key_has_an_evaluator() -> None:
    assert set(_EVALUATORS) == set(CONSTRAINT_KEYS), (
        "a constraint key is recognised by the schema layer with nothing behind it. "
        "A declared constraint that is never enforced is a rule that lies — add an "
        "evaluator in src/docdoc/validation/constraints.py, or stop recognising the key"
    )


def test_every_recognised_key_has_a_declared_type_domain() -> None:
    """FR-025 needs to know, for each key, which types can carry it."""
    assert set(CONSTRAINT_TYPE_DOMAINS) == set(CONSTRAINT_KEYS)


def test_every_recognised_key_has_a_fixture() -> None:
    """So the behaviour tests iterate the set rather than restate it."""
    assert set(EVERY_CONSTRAINT_KEY) == set(CONSTRAINT_KEYS)


@pytest.mark.parametrize("key", sorted(CONSTRAINT_KEYS))
def test_each_evaluator_can_both_pass_and_fail(key: str) -> None:
    """An evaluator that always returns None would pass this file's first test."""
    from tests.unit.test_constraints import _check

    field_type, constraints = EVERY_CONSTRAINT_KEY[key]
    outcomes = {
        check.outcome for value in _PROBES[key] for check in _check(constraints, value, field_type)
    }
    assert len(outcomes) == 2, f"{key} never distinguishes a passing value from a failing one"


#: One value each side of every declared constraint in `EVERY_CONSTRAINT_KEY`.
_PROBES: dict[str, tuple[object, object]] = {
    "enum": ("EUR", "GBP"),
    "const": ("INVOICE", "RECEIPT"),
    "pattern": ("INV-2026-001", "INV-26-1"),
    "minimum": (Decimal("1"), Decimal("-1")),
    "maximum": (Decimal("1"), Decimal("2000000")),
    "multiple_of": (Decimal("1.23"), Decimal("1.234")),
    "min_length": ("abc", "ab"),
    "max_length": ("abc", "x" * 65),
}
