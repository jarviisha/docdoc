"""T066 — a finding is machine-readable, and its prose carries nothing extra.

The test worth reading is the last one: removing `message` from a finding must
lose no information a machine needed. If it ever does, the prose has become the
only place some fact lives, and a consumer would have to parse English to act on
a verdict (FR-039, VAL-21).
"""

from __future__ import annotations

import re

from docdoc.validation import ReasonCode, Severity, validate
from tests.fixtures.validation import artifacts
from tests.fixtures.validation import rules as rule_fixtures
from tests.fixtures.validation.schemas import invoice_schema

_INDEXED = re.compile(r"^([a-z_]+)(\[(\d+)\])?(\.[a-z_]+)*$")


def _busy_result():
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
    return pair, validate(pair.extraction, pair.grounding, schema)


def test_every_finding_addresses_a_path_that_exists() -> None:
    _pair, result = _busy_result()
    known = {check.field_path for check in result.checks}
    assert result.findings
    for finding in result.findings:
        assert finding.field_path in known
        assert _INDEXED.match(finding.field_path), finding.field_path


def test_a_finding_inside_a_repeating_group_carries_its_entry_index() -> None:
    schema = invoice_schema(rules=(rule_fixtures.product_rule(),))
    pair = artifacts.build(schema=schema)
    lines = list(pair.extraction.values["line_items"])
    lines[1] = dict(lines[1])
    from tests.support import make_extracted

    lines[1]["quantity"] = make_extracted("line_items[1].quantity", value=9, claimed_text="1")
    values = dict(pair.extraction.values)
    values["line_items"] = tuple(lines)
    result = validate(pair.extraction.model_copy(update={"values": values}), pair.grounding, schema)
    finding = next(f for f in result.findings if f.reason is ReasonCode.PRODUCT_MISMATCH)
    assert finding.field_path == "line_items[1].quantity"


def test_every_rule_finding_lists_all_its_participants() -> None:
    _, result = _busy_result()
    for finding in result.findings:
        if finding.check_id.startswith("rule:"):
            assert len(finding.participants) >= 2
            assert finding.field_path in finding.participants


def test_every_finding_carries_a_closed_reason_code() -> None:
    _, result = _busy_result()
    for finding in result.findings:
        assert isinstance(finding.reason, ReasonCode)
        assert isinstance(finding.severity, Severity)


def test_expected_and_actual_are_present_for_a_comparison() -> None:
    _, result = _busy_result()
    for finding in result.findings:
        if finding.reason in (ReasonCode.SUM_MISMATCH, ReasonCode.NOT_IN_ENUM):
            assert finding.expected
            assert finding.actual


def test_removing_the_prose_loses_nothing_a_machine_needed() -> None:
    """VAL-21 — the message is redundant by construction.

    Every fact the message states appears in a structured field: the path, the
    check, the reason, the severity, the expectation, the actual value, and the
    participants. The test is not that the prose is short; it is that a consumer
    never has to read it.
    """
    _, result = _busy_result()
    for finding in result.findings:
        stripped = finding.model_copy(update={"message": ""})
        assert stripped.field_path
        assert stripped.check_id
        assert stripped.reason is finding.reason
        assert stripped.severity is finding.severity
        assert stripped.participants == finding.participants
        # Numbers quoted in the prose are also carried structurally.
        for number in re.findall(r"\d+\.\d+", finding.message):
            assert number in f"{finding.expected} {finding.actual}", (
                f"{finding.check_id}: {number!r} appears only in the prose"
            )


def test_findings_and_checks_never_disagree() -> None:
    _, result = _busy_result()
    failing = {c.check_id for c in result.checks if c.outcome.value != "passed"}
    assert {f.check_id for f in result.findings} == failing
