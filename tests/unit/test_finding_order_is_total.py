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
    """A missing key must not sort at position zero, which is what `.get` would give.

    **This test used to pass for the wrong reason**, and a mutation run found it.
    It compared the stray record against `number#required` — the *first* field in
    the walk, at position 0. Setting the missing-path position to 0 as well made
    the two tie, the comparison fell through to `check_id`, and
    `"rule:ghost@nowhere"` happens to sort after `"number#required"`
    alphabetically. The assertion succeeded with the bug in place.

    The fix is to compare against a record from the **middle** of the walk, and in
    both directions, so that position is the only term that can decide.
    """
    pair, schema = _busy()
    from docdoc.validation.enumerate import walk

    index = walk(schema, pair.extraction.values)
    stray = CheckRecord(
        # Deliberately alphabetically *early*, so a fall-through to `check_id`
        # would order it before the known record rather than after it.
        check_id="aaa:ghost@nowhere",
        field_path="nowhere",
        kind=CheckKind.RULE,
        outcome=Outcome.NOT_EVALUATED,
        reason=ReasonCode.OPERAND_ABSENT,
    )
    middle_path = list(index.order)[len(index.order) // 2]
    assert index.order[middle_path] > 0, "the probe must not sit at position zero"
    middle = CheckRecord(
        check_id=f"{middle_path}#required",
        field_path=middle_path,
        kind=CheckKind.REQUIRED,
        outcome=Outcome.PASSED,
    )

    assert sort_key(stray, index) > sort_key(middle, index)
    assert sort_key(middle, index) < sort_key(stray, index)

    # And against the very first field too: the stray must come after everything
    # the walk produced, not merely after most of it.
    first_path = next(iter(index.order))
    first = CheckRecord(
        check_id=f"{first_path}#required",
        field_path=first_path,
        kind=CheckKind.REQUIRED,
        outcome=Outcome.PASSED,
    )
    assert sort_key(stray, index) > sort_key(first, index)


def test_entry_indices_order_anchors_the_walk_never_produced() -> None:
    """T110 — the one case where the middle term of the sort key decides (FR-043).

    Every anchor the four rule kinds produce is a scalar path the walk emitted, so
    position orders it and the index is never reached. A mutation run made that
    concrete: dropping the index term broke nothing, which left the term looking
    like dead code.

    It is not dead — it is unreached *by the current vocabulary*. A rule kind
    anchored at `line_items[2]` itself, rather than at a field inside it, would
    produce records that share the fallback position, and `check_id` alone would
    then sort entry 10 before entry 2. This pins that, so the next kind added
    inherits the ordering instead of discovering it.
    """
    pair, schema = _busy()
    from docdoc.validation.enumerate import walk

    index = walk(schema, pair.extraction.values)

    def anchored(entry: int) -> CheckRecord:
        # A group-level anchor: `line_items` and `line_items[0].amount` are in the
        # walk, `line_items[0]` is not.
        return CheckRecord(
            check_id=f"rule:per_entry@line_items[{entry}]",
            field_path=f"line_items[{entry}]",
            kind=CheckKind.RULE,
            outcome=Outcome.NOT_EVALUATED,
            reason=ReasonCode.OPERAND_ABSENT,
        )

    second, tenth = anchored(2), anchored(10)
    assert second.field_path not in index.order
    assert tenth.field_path not in index.order

    # Both fall back to the same position, so only the index can order them —
    # and lexicographically `[10]` precedes `[2]`, which is what this forbids.
    assert sort_key(second, index) < sort_key(tenth, index)
    assert second.check_id > tenth.check_id, "the check ids alone would sort the other way"
