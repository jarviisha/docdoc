"""T062 — the counts add up, for any schema and any result (FR-012, SC-002).

Reconciliation is what makes a rule that never ran *visible*. Without it, a
missing check is an absence, and an absence looks exactly like a check that was
never declared. With it, the gap between `declared` and `evaluated` is a number
somebody can put on a dashboard.

Randomised over generated schemas and value trees rather than over the invoice
fixture, because the fixture is the case that was thought about — and a
reconciliation bug would live in the case that was not.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from docdoc.extraction.identity import schema_hash_for
from docdoc.extraction.schema import Cardinality, FieldSpec, FieldType, Schema
from docdoc.grounding import ground
from docdoc.validation import Verdict, validate
from docdoc.validation.result import Outcome
from tests.support import make_document, make_extracted, make_extraction

_TEXT = "Alpha 12.00 Beta 7.50 Gamma 19.50 total 19.50 ref ABC-123\n"

_NAMES = st.sampled_from(["alpha", "beta", "gamma", "delta", "epsilon"])


@st.composite
def _scalar(draw: Any, name: str) -> FieldSpec:
    kind = draw(st.sampled_from([FieldType.STRING, FieldType.DECIMAL, FieldType.INTEGER]))
    constraints: dict[str, Any] = {}
    if kind is FieldType.STRING and draw(st.booleans()):
        constraints = draw(
            st.sampled_from([{"min_length": 1}, {"max_length": 4}, {"pattern": r"[A-Z\-0-9]+"}])
        )
    elif kind is not FieldType.STRING and draw(st.booleans()):
        constraints = draw(st.sampled_from([{"minimum": 0}, {"maximum": 100}]))
    return FieldSpec(
        name=name,
        type=kind,
        required=draw(st.booleans()),
        constraints=constraints,
    )


@st.composite
def _schema(draw: Any) -> Schema:
    names = draw(st.lists(_NAMES, min_size=1, max_size=4, unique=True))
    fields = [draw(_scalar(name)) for name in names]
    if draw(st.booleans()):
        inner = draw(st.lists(_NAMES, min_size=1, max_size=2, unique=True))
        group_name = "items" if draw(st.booleans()) else "lines"
        fields.append(
            FieldSpec(
                name=group_name,
                cardinality=Cardinality.REPEATING_GROUP,
                required=draw(st.booleans()),
                fields=tuple(draw(_scalar(name)) for name in inner),
            )
        )
    return Schema(name="probe", version=1, fields=tuple(fields))


def _value_for(field: FieldSpec, present: bool, path: str) -> Any:
    if not present:
        return make_extracted(path, present=False)
    payload: Any
    if field.type is FieldType.STRING:
        payload = "ABC-123"
    elif field.type is FieldType.DECIMAL:
        payload = Decimal("19.50")
    else:
        payload = 12
    return make_extracted(path, value=payload, claimed_text=str(payload))


@st.composite
def _tree(draw: Any, schema: Schema) -> dict[str, Any]:
    tree: dict[str, Any] = {}
    for field in schema.fields:
        if field.cardinality is Cardinality.REPEATING_GROUP:
            entries = draw(st.integers(min_value=0, max_value=3))
            tree[field.name] = tuple(
                {
                    child.name: _value_for(
                        child,
                        draw(st.booleans()),
                        f"{field.name}[{index}].{child.name}",
                    )
                    for child in field.fields
                }
                for index in range(entries)
            )
        else:
            tree[field.name] = _value_for(field, draw(st.booleans()), field.name)
    return tree


@st.composite
def _cases(draw: Any) -> tuple[Schema, dict[str, Any]]:
    schema = draw(_schema())
    return schema, draw(_tree(schema))


@given(_cases())
@settings(max_examples=150, suppress_health_check=[HealthCheck.too_slow], deadline=None)
def test_counts_always_reconcile(case: tuple[Schema, dict[str, Any]]) -> None:
    schema, tree = case
    document = make_document(_TEXT)
    extraction = make_extraction(
        tree,
        document=document,
        schema_identity=schema.identity,
        schema_hash=schema_hash_for(schema),
    )
    result = validate(extraction, ground(document, extraction), schema)
    counts = result.counts

    assert counts.declared == counts.passed + counts.failed + counts.not_evaluated
    assert counts.evaluated == counts.passed + counts.failed
    assert counts.declared == len(result.checks)


@given(_cases())
@settings(max_examples=150, suppress_health_check=[HealthCheck.too_slow], deadline=None)
def test_every_check_appears_exactly_once(case: tuple[Schema, dict[str, Any]]) -> None:
    schema, tree = case
    document = make_document(_TEXT)
    extraction = make_extraction(
        tree,
        document=document,
        schema_identity=schema.identity,
        schema_hash=schema_hash_for(schema),
    )
    result = validate(extraction, ground(document, extraction), schema)

    ids = [check.check_id for check in result.checks]
    assert len(ids) == len(set(ids))


@given(_cases())
@settings(max_examples=150, suppress_health_check=[HealthCheck.too_slow], deadline=None)
def test_findings_are_exactly_the_non_passing_checks(case: tuple[Schema, dict[str, Any]]) -> None:
    """The two views are derived from one record list and cannot disagree."""
    schema, tree = case
    document = make_document(_TEXT)
    extraction = make_extraction(
        tree,
        document=document,
        schema_identity=schema.identity,
        schema_hash=schema_hash_for(schema),
    )
    result = validate(extraction, ground(document, extraction), schema)

    non_passing = [c.check_id for c in result.checks if c.outcome is not Outcome.PASSED]
    assert [f.check_id for f in result.findings] == non_passing


@given(_cases())
@settings(max_examples=150, suppress_health_check=[HealthCheck.too_slow], deadline=None)
def test_the_verdict_matches_its_definition(case: tuple[Schema, dict[str, Any]]) -> None:
    schema, tree = case
    document = make_document(_TEXT)
    extraction = make_extraction(
        tree,
        document=document,
        schema_identity=schema.identity,
        schema_hash=schema_hash_for(schema),
    )
    result = validate(extraction, ground(document, extraction), schema)

    from docdoc.validation import Severity

    has_error = any(f.severity is Severity.ERROR and not f.not_evaluated for f in result.findings)
    skipped = result.counts.not_evaluated > 0
    expected = Verdict.INVALID if has_error else Verdict.INCOMPLETE if skipped else Verdict.VALID
    assert result.verdict is expected
