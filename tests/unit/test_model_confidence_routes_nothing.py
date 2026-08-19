"""T064 — the model's self-report influences nothing here (FR-045, SC-012).

ADR-0004 keeps two trust levels apart by giving them separate fields. Milestone 4
proved its own stage ignores the untrusted one by grounding a set twice with the
number altered; this is the same proof one stage later, and it matters more here
because *this* is the stage whose output a routing decision will read.

The assertion is on the whole result, artifact id included: if confidence ever
reached the identity, two runs of the same document would cache differently.
"""

from __future__ import annotations

from typing import Any

from docdoc.extraction.value import ExtractedValue
from docdoc.validation import validate
from tests.fixtures.validation import artifacts
from tests.fixtures.validation import rules as rule_fixtures
from tests.fixtures.validation.schemas import invoice_schema


def _with_confidence(node: Any, score: float) -> Any:
    if isinstance(node, ExtractedValue):
        return node.model_copy(update={"model_confidence": score})
    if isinstance(node, dict):
        return {key: _with_confidence(child, score) for key, child in node.items()}
    if isinstance(node, tuple):
        return tuple(_with_confidence(child, score) for child in node)
    return node


def _validated(score: float | None, **kwargs):
    schema = invoice_schema(rules=rule_fixtures.every_kind())
    pair = artifacts.build(schema=schema, **kwargs)
    values = (
        pair.extraction.values if score is None else _with_confidence(pair.extraction.values, score)
    )
    extraction = pair.extraction.model_copy(update={"values": values})
    return validate(extraction, pair.grounding, schema)


def test_a_clean_result_is_identical_at_any_confidence() -> None:
    baseline = _validated(None)
    for score in (0.0, 0.5, 1.0):
        assert _validated(score).model_dump() == baseline.model_dump()


def test_a_failing_result_is_identical_at_any_confidence() -> None:
    """A confident model does not rescue an invoice that does not add up."""
    kwargs = {"total": "1000.00", "total_claim": "1420.00"}
    baseline = _validated(None, **kwargs)
    for score in (0.0, 0.99):
        assert _validated(score, **kwargs).model_dump() == baseline.model_dump()


def test_the_artifact_id_does_not_move_with_confidence() -> None:
    """It is not a result-affecting input, so folding it would be wrong twice over."""
    assert _validated(0.1).artifact_id == _validated(0.9).artifact_id


def test_confidence_is_passed_through_untouched() -> None:
    """Read by nobody here, and still not erased: a calibrator will want it."""
    schema = invoice_schema()
    pair = artifacts.build(schema=schema)
    values = _with_confidence(pair.extraction.values, 0.42)
    extraction = pair.extraction.model_copy(update={"values": values})
    validate(extraction, pair.grounding, schema)
    assert extraction.values["total"].model_confidence == 0.42


def test_no_validation_module_reads_the_field() -> None:
    """The behavioural test above passes if the field is read and then ignored.

    This one fails if it is read at all, which is the property FR-045 states.
    """
    import pathlib

    for path in sorted(pathlib.Path("src/docdoc/validation").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#"))
        assert "model_confidence" not in code, (
            f"{path.name} reads model_confidence. It is untrusted, it routes nothing, "
            "and MVP acceptance decisions must not read it (ADR-0004, FR-045)"
        )
