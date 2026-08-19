"""T075 — three ways two artifacts fail to belong together, and none is validated anyway.

Every one of these produces, if allowed through, a verdict that is *structurally
valid and about the wrong thing*: the fields line up, the checks run, the counts
reconcile, and the answer describes a document nobody examined. That is worse
than an error, because it is signed (FR-002, SC-015).
"""

from __future__ import annotations

import logging

import pytest

from docdoc.extraction.schema import Schema
from docdoc.validation import ValidationError, validate
from tests.fixtures.validation import artifacts
from tests.fixtures.validation.schemas import invoice_schema


def test_a_grounding_of_another_extraction_is_refused() -> None:
    mine = artifacts.build()
    theirs = artifacts.build(number="INV-2026-999")
    with pytest.raises(ValidationError) as caught:
        validate(mine.extraction, theirs.grounding, mine.schema)

    assert caught.value.expected == mine.extraction.artifact_id
    assert caught.value.actual == theirs.extraction.artifact_id
    assert mine.extraction.artifact_id in str(caught.value)
    assert theirs.extraction.artifact_id in str(caught.value)


def test_a_result_extracted_under_another_schema_is_refused() -> None:
    pair = artifacts.build()
    other = Schema(name="other_schema", version=1, fields=invoice_schema().fields)
    with pytest.raises(ValidationError) as caught:
        validate(pair.extraction, pair.grounding, other)
    assert caught.value.expected == "other_schema@1"
    assert caught.value.actual == "probe_invoice@1"


def test_a_schema_edited_since_extraction_is_refused() -> None:
    """The version did not move, so only the hash can tell the two apart (ADR-0008)."""
    schema = invoice_schema()
    pair = artifacts.build(schema=schema, schema_hash="sha256:" + "0" * 64)
    with pytest.raises(ValidationError, match="edited since"):
        validate(pair.extraction, pair.grounding, schema)


def test_a_refusal_produces_no_verdict_at_all() -> None:
    """The error carries the two identities and nothing that could be read as a verdict."""
    mine = artifacts.build()
    theirs = artifacts.build(number="INV-2026-999")
    with pytest.raises(ValidationError) as caught:
        validate(mine.extraction, theirs.grounding, mine.schema)
    assert not hasattr(caught.value, "verdict")
    assert not hasattr(caught.value, "findings")


def test_a_refusal_emits_the_log_event(caplog) -> None:
    mine = artifacts.build()
    theirs = artifacts.build(number="INV-2026-999")
    with (
        caplog.at_level(logging.WARNING, logger="docdoc.validation"),
        pytest.raises(ValidationError),
    ):
        validate(mine.extraction, theirs.grounding, mine.schema)

    payloads = [record.docdoc for record in caplog.records if hasattr(record, "docdoc")]
    assert payloads
    assert payloads[-1]["outcome"] == "refused"
    assert payloads[-1]["reason"] == "grounding_is_of_another_extraction"


def test_a_value_tree_that_does_not_fit_the_schema_is_refused() -> None:
    pair = artifacts.build()
    broken = {k: v for k, v in pair.extraction.values.items() if k != "currency"}
    with pytest.raises(ValidationError, match="currency"):
        validate(pair.extraction.model_copy(update={"values": broken}), pair.grounding, pair.schema)


def test_the_matching_pair_is_not_refused() -> None:
    """The other half of a refusal test: it must not refuse everything."""
    pair = artifacts.build()
    assert validate(pair.extraction, pair.grounding, pair.schema)
