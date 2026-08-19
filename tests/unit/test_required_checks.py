"""T034 — requiredness: what absence means, and where it is reported.

The distinction Milestone 3 preserved is the one this file is mostly about:
`present=False` means the document does not contain the field, while
`present=True, value=""` means it contains an empty one. A validator that read
content instead of presence would collapse them, and would report a document
that legitimately carries an empty field as one that carries nothing.
"""

from __future__ import annotations

import pytest

from docdoc.validation import ReasonCode, Verdict, validate
from docdoc.validation.result import Outcome
from tests.fixtures.validation import artifacts
from tests.support import make_extracted


def _validate(**kwargs):
    pair = artifacts.build(**kwargs)
    return validate(pair.extraction, pair.grounding, pair.schema)


def _reasons(result, path: str) -> list[ReasonCode]:
    return [f.reason for f in result.findings if f.field_path == path]


def test_an_absent_required_field_produces_one_finding() -> None:
    result = _validate(extraction_overrides={"total": make_extracted("total", present=False)})
    assert _reasons(result, "total") == [ReasonCode.REQUIRED_VALUE_MISSING]
    assert result.verdict is Verdict.INVALID


def test_an_absent_optional_field_produces_none() -> None:
    """`notes` is absent in every default fixture, and that is not a defect."""
    result = _validate()
    assert _reasons(result, "notes") == []
    assert result.verdict is Verdict.VALID


def test_a_present_empty_string_satisfies_requiredness() -> None:
    """FR-015 — requiredness reads presence, never content."""
    result = _validate(
        extraction_overrides={"number": make_extracted("number", value="", claimed_text="")}
    )
    assert ReasonCode.REQUIRED_VALUE_MISSING not in _reasons(result, "number")


def test_an_optional_field_absent_leaves_the_verdict_valid() -> None:
    """An absent optional field creates no obligation, so nothing goes unchecked."""
    result = _validate(due=None)
    assert result.verdict is Verdict.VALID
    assert result.counts.not_evaluated == 0


def test_a_required_field_inside_an_entry_is_checked_per_entry() -> None:
    pair = artifacts.build()
    lines = list(pair.extraction.values["line_items"])
    lines[1] = dict(lines[1])
    lines[1]["amount"] = make_extracted("line_items[1].amount", present=False)
    values = dict(pair.extraction.values)
    values["line_items"] = tuple(lines)
    extraction = pair.extraction.model_copy(update={"values": values})
    result = validate(extraction, pair.grounding, pair.schema)

    assert _reasons(result, "line_items[1].amount") == [ReasonCode.REQUIRED_VALUE_MISSING]
    assert _reasons(result, "line_items[0].amount") == []


def test_an_absent_required_group_produces_one_finding_not_one_per_field() -> None:
    """FR-017 — the group is the thing that is missing, and it says so once."""
    from docdoc.extraction.schema import Schema

    base = artifacts.build().schema
    required_supplier = tuple(
        field.model_copy(update={"required": True}) if field.name == "supplier" else field
        for field in base.fields
    )
    schema = Schema(name=base.name, version=base.version, fields=required_supplier)
    pair = artifacts.build(schema=schema, tax_id=None)

    empty_supplier = {
        "name": make_extracted("supplier.name", present=False),
        "tax_id": make_extracted("supplier.tax_id", present=False),
    }
    values = dict(pair.extraction.values)
    values["supplier"] = empty_supplier
    extraction = pair.extraction.model_copy(update={"values": values})
    result = validate(extraction, pair.grounding, schema)

    missing = [
        finding
        for finding in result.findings
        if finding.reason is ReasonCode.REQUIRED_VALUE_MISSING
    ]
    assert [finding.field_path for finding in missing] == ["supplier"]


def test_a_required_check_that_passes_is_still_recorded() -> None:
    """FR-011 — "did this run?" is answerable months later."""
    result = _validate()
    check = result.check("number#required")
    assert check is not None
    assert check.outcome is Outcome.PASSED


@pytest.mark.parametrize("entries", [0, 1, 3])
def test_a_repeating_group_of_any_size_checks_each_entry(entries: int) -> None:
    pair = artifacts.build()
    line = pair.extraction.values["line_items"][0]
    values = dict(pair.extraction.values)
    values["line_items"] = tuple(line for _ in range(entries))
    extraction = pair.extraction.model_copy(update={"values": values})
    result = validate(extraction, pair.grounding, pair.schema)
    per_entry = [
        check
        for check in result.checks
        if check.check_id.endswith("#required") and check.field_path.startswith("line_items[")
    ]
    # description and amount are the two required fields inside an entry.
    assert len(per_entry) == entries * 2
