"""T072 — the order is total, and nothing about it depends on the machine.

`sorted` is stable, so an ordering that is merely *consistent* on one run can
still differ on another whose dict iteration order differed. The suite runs under
two `PYTHONHASHSEED` values for exactly this reason; these tests do the part that
can be checked in-process — shuffling the input tree and asserting the output does
not move (FR-043, SC-013).
"""

from __future__ import annotations

import random

from docdoc.validation import validate
from docdoc.validation.record import CheckRecord
from docdoc.validation.result import CheckKind, Outcome, ReasonCode
from docdoc.validation.verdict import sort_key
from tests.fixtures.validation import artifacts
from tests.fixtures.validation import rules as rule_fixtures
from tests.fixtures.validation.schemas import invoice_schema


def _busy():
    schema = invoice_schema(rules=rule_fixtures.every_kind())
    pair = artifacts.build(
        schema=schema,
        number="INV-1",
        currency="GBP",
        total="1000.00",
        total_claim="1420.00",
        due="2020-01-01",
        tax_id=None,
    )
    return pair, schema


def test_repeating_a_run_gives_the_same_order() -> None:
    pair, schema = _busy()
    first = [f.check_id for f in validate(pair.extraction, pair.grounding, schema).findings]
    second = [f.check_id for f in validate(pair.extraction, pair.grounding, schema).findings]
    assert first == second


def test_shuffling_the_value_tree_does_not_move_the_order() -> None:
    """A dict's insertion order is not meaning, and must not become order."""
    pair, schema = _busy()
    expected = [f.check_id for f in validate(pair.extraction, pair.grounding, schema).findings]

    for seed in (1, 2, 3):
        shuffler = random.Random(seed)
        keys = list(pair.extraction.values)
        shuffler.shuffle(keys)
        shuffled = {key: pair.extraction.values[key] for key in keys}
        extraction = pair.extraction.model_copy(update={"values": shuffled})
        assert [
            f.check_id for f in validate(extraction, pair.grounding, schema).findings
        ] == expected


def test_findings_follow_schema_declaration_order() -> None:
    pair, schema = _busy()
    result = validate(pair.extraction, pair.grounding, schema)
    order = [f.field_path for f in result.findings]
    assert order.index("number") < order.index("currency")
    assert order.index("currency") < order.index("total")


def test_entries_are_ordered_by_index() -> None:
    schema = invoice_schema(rules=(rule_fixtures.product_rule(),))
    pair = artifacts.build(schema=schema)
    lines = [dict(line) for line in pair.extraction.values["line_items"]]
    from tests.support import make_extracted

    for index, line in enumerate(lines):
        line["quantity"] = make_extracted(f"line_items[{index}].quantity", value=9)
    values = dict(pair.extraction.values)
    values["line_items"] = tuple(lines)
    result = validate(pair.extraction.model_copy(update={"values": values}), pair.grounding, schema)
    paths = [f.field_path for f in result.findings if f.field_path.startswith("line_items[")]
    assert paths == sorted(paths, key=lambda p: int(p.split("[")[1].split("]")[0]))


def test_the_key_is_total_for_records_at_one_anchor() -> None:
    """Two findings on one field still have distinct check ids, so ties cannot happen."""
    pair, schema = _busy()
    result = validate(pair.extraction, pair.grounding, schema)
    from docdoc.validation.enumerate import walk

    index = walk(schema, pair.extraction.values)
    keys = [
        sort_key(
            CheckRecord(
                check_id=f.check_id,
                field_path=f.field_path,
                kind=f.kind,
                outcome=Outcome.FAILED,
                reason=f.reason,
                severity=f.severity,
            ),
            index,
        )
        for f in result.findings
    ]
    assert len(keys) == len(set(keys))
    assert keys == sorted(keys)


def test_a_path_the_walk_never_produced_sorts_last_rather_than_first() -> None:
    """A missing key must not sort at position zero, which is what `.get` would give."""
    pair, schema = _busy()
    from docdoc.validation.enumerate import walk

    index = walk(schema, pair.extraction.values)
    stray = CheckRecord(
        check_id="rule:ghost@nowhere",
        field_path="nowhere",
        kind=CheckKind.RULE,
        outcome=Outcome.NOT_EVALUATED,
        reason=ReasonCode.OPERAND_ABSENT,
    )
    known = CheckRecord(
        check_id="number#required",
        field_path="number",
        kind=CheckKind.REQUIRED,
        outcome=Outcome.PASSED,
    )
    assert sort_key(stray, index) > sort_key(known, index)
