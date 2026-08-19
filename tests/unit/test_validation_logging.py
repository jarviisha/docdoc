"""T071 — one event per run, and no value in it (FR-057, FR-058, SC-021).

The boundary this stage tests hardest: findings carry values *by design* — "total
is 1240.00, the lines sum to 1420.00" is what a finding is for — while the log
carries identities, versions, counts, and the verdict. Reading FR-057 as "values
never appear anywhere" would make findings useless; reading it as "logs are
countable, not quotable" is what it means.
"""

from __future__ import annotations

import logging

import pytest

from docdoc.validation import ValidationError, validate
from docdoc.validation.observe import EVENT_NAME
from tests.fixtures.validation import artifacts
from tests.fixtures.validation import rules as rule_fixtures
from tests.fixtures.validation.schemas import invoice_schema


def _payloads(caplog):
    return [record.docdoc for record in caplog.records if hasattr(record, "docdoc")]


@pytest.fixture
def busy():
    schema = invoice_schema(rules=rule_fixtures.every_kind())
    return artifacts.build(
        schema=schema,
        number="INV-1",
        currency="GBP",
        total="1000.00",
        total_claim="1420.00",
        tax_id=None,
    ), schema


def test_one_event_per_successful_run(caplog, busy) -> None:
    pair, schema = busy
    with caplog.at_level(logging.INFO, logger="docdoc.validation"):
        validate(pair.extraction, pair.grounding, schema)
    payloads = _payloads(caplog)
    assert len(payloads) == 1
    assert payloads[0]["event"] == EVENT_NAME
    assert payloads[0]["outcome"] == "ok"


def test_the_event_carries_identities_versions_counts_and_verdict(caplog, busy) -> None:
    pair, schema = busy
    with caplog.at_level(logging.INFO, logger="docdoc.validation"):
        result = validate(pair.extraction, pair.grounding, schema)
    payload = _payloads(caplog)[0]

    assert payload["document_id"] == pair.document.id
    assert payload["extraction_artifact_id"] == pair.extraction.artifact_id
    assert payload["grounding_artifact_id"] == pair.grounding.artifact_id
    assert payload["artifact_id"] == result.artifact_id
    assert payload["validator_version"] == result.provenance.validator_version
    assert payload["rule_vocabulary_version"] == "rule_vocabulary@1"
    assert payload["pattern_dialect_version"] == "pattern_dialect@1"
    assert payload["verdict"] == str(result.verdict)
    assert payload["checks_declared"] == result.counts.declared
    assert payload["checks_not_evaluated"] == result.counts.not_evaluated
    assert payload["duration_ms"] >= 0


def test_no_field_value_or_claim_reaches_the_log(caplog, busy) -> None:
    """Checked against the fixture's own strings rather than against a pattern."""
    pair, schema = busy
    with caplog.at_level(logging.INFO, logger="docdoc.validation"):
        validate(pair.extraction, pair.grounding, schema)
    text = " ".join(str(payload) for payload in _payloads(caplog))
    text += " ".join(record.getMessage() for record in caplog.records)

    for secret in (
        "ACME SUPPLIES LIMITED",
        "GB123456789",
        "Widget A",
        "INV-1",
        "1420.00",
        "1000.00",
        "GBP",
    ):
        assert secret not in text, f"{secret!r} reached the log output"


def test_no_document_text_reaches_the_log(caplog, busy) -> None:
    pair, schema = busy
    with caplog.at_level(logging.INFO, logger="docdoc.validation"):
        validate(pair.extraction, pair.grounding, schema)
    text = " ".join(str(payload) for payload in _payloads(caplog))
    for line in artifacts.DOCUMENT_TEXT.splitlines():
        if line.strip():
            assert line.strip() not in text


def test_the_findings_do_carry_the_values(busy) -> None:
    """The other side of the boundary, asserted so nobody 'fixes' it later."""
    pair, schema = busy
    result = validate(pair.extraction, pair.grounding, schema)
    quoted = " ".join(f"{f.expected} {f.actual} {f.message}" for f in result.findings)
    assert "1420.00" in quoted
    assert "GBP" in quoted


def test_a_refused_run_also_emits_exactly_one_event(caplog) -> None:
    mine = artifacts.build()
    theirs = artifacts.build(number="INV-2026-999")
    with (
        caplog.at_level(logging.INFO, logger="docdoc.validation"),
        pytest.raises(ValidationError),
    ):
        validate(mine.extraction, theirs.grounding, mine.schema)
    payloads = _payloads(caplog)
    assert len(payloads) == 1
    assert payloads[0]["outcome"] == "refused"
    assert payloads[0]["expected"]
    assert payloads[0]["actual"]
