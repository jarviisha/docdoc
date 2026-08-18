"""T025 — conformance (EXT-15…EXT-18).

Nothing is repaired. Every case below is one a real model produces sooner or later,
and for each the required behaviour is an error naming the field rather than a
result that looks confident.
"""

from __future__ import annotations

import pathlib
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest

from docdoc.extraction import ExtractionError, load_schema
from docdoc.extraction.conform import conform
from docdoc.extraction.schema import Cardinality, FieldSpec, FieldType, Schema

SCHEMAS = pathlib.Path("schemas")


def sc(value: Any = None, claimed: str | None = None, confidence: float | None = None) -> dict:
    return {"value": value, "claimed_text": claimed, "confidence": confidence}


def _schema(*fields: FieldSpec) -> Schema:
    return Schema(name="probe", version=1, fields=fields)


def _scalar(name: str, kind: FieldType) -> FieldSpec:
    return FieldSpec(name=name, type=kind, description=f"a {kind}")


# -- type coercion -----------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "raw", "expected"),
    [
        (FieldType.STRING, "abc", "abc"),
        (FieldType.INTEGER, 7, 7),
        (FieldType.NUMBER, 1.5, 1.5),
        (FieldType.NUMBER, 2, 2.0),
        (FieldType.BOOLEAN, True, True),
        (FieldType.DECIMAL, "1240.00", Decimal("1240.00")),
        (FieldType.DATE, "2026-03-01", date(2026, 3, 1)),
        (FieldType.DATETIME, "2026-03-01T14:05:00", datetime(2026, 3, 1, 14, 5)),
    ],
)
def test_declared_types_parse(kind: FieldType, raw: Any, expected: Any) -> None:
    schema = _schema(_scalar("f", kind))
    report = conform({"f": sc(raw)}, schema)
    assert report.values["f"].value == expected
    assert type(report.values["f"].value) is type(expected)


@pytest.mark.parametrize(
    ("kind", "raw"),
    [
        (FieldType.STRING, 7),
        (FieldType.INTEGER, "7"),
        (FieldType.INTEGER, True),  # a bool is not an integer here, however Python feels
        (FieldType.INTEGER, 1.5),
        (FieldType.NUMBER, "1.5"),
        (FieldType.BOOLEAN, "true"),
        (FieldType.DECIMAL, 1240.0),  # a float would already have lost the printed value
        (FieldType.DECIMAL, "not a number"),
        (FieldType.DATE, "1 March 2026"),
        (FieldType.DATE, 20260301),
        (FieldType.DATETIME, "yesterday"),
    ],
)
def test_unparseable_values_name_the_field(kind: FieldType, raw: Any) -> None:
    """EXT-15 -- and the reason is `type`, distinguishable from a shape failure."""
    schema = _schema(_scalar("f", kind))
    with pytest.raises(ExtractionError) as caught:
        conform({"f": sc(raw)}, schema)
    assert caught.value.reason == "type"
    assert caught.value.field_path == "f"


# -- absence vs empty --------------------------------------------------------


def test_absence_and_empty_are_different_answers() -> None:
    """EXT-16 -- the distinction FR-005 turns on."""
    schema = _schema(_scalar("absent", FieldType.STRING), _scalar("empty", FieldType.STRING))
    report = conform({"absent": sc(None), "empty": sc("")}, schema)
    assert report.values["absent"].present is False
    assert report.values["absent"].value is None
    assert report.values["empty"].present is True
    assert report.values["empty"].value == ""


def test_an_omitted_key_is_a_failure_not_an_absence() -> None:
    """A model that did not answer is not the same as a document that lacks the field."""
    schema = _schema(_scalar("f", FieldType.STRING))
    with pytest.raises(ExtractionError) as caught:
        conform({}, schema)
    assert caught.value.reason == "missing_field"
    assert caught.value.field_path == "f"
    assert "reported as a null value, not as an absent key" in str(caught.value)


# -- claimed text ------------------------------------------------------------


@pytest.mark.parametrize(
    "claimed",
    [
        # Each entry is a character class a well-meaning `.strip()` or NFKC pass
        # would silently change. The `noqa`s are load-bearing: ruff is right that
        # these characters are ambiguous, and being ambiguous is what makes them
        # worth testing.
        "  1,240.00  ",
        "Total 1,240",  # noqa: RUF001 - a no-break space mid-string
        "ＴＯＴＡＬ",  # noqa: RUF001 - full-width latin
        "café",  # a combining acute, which NFC would recompose
        "رقم",  # right-to-left
        "line\nbreak",  # an embedded newline
    ],
)
def test_claimed_text_is_preserved_byte_for_byte(claimed: str) -> None:
    """EXT-18 -- Milestone 4 cannot locate text this layer normalised.

    The cases are chosen to be the ones a well-meaning `.strip()` or NFKC pass
    would silently change: leading whitespace, a non-breaking space, full-width
    characters, a combining form, right-to-left text, and an embedded newline.
    """
    schema = _schema(_scalar("f", FieldType.STRING))
    report = conform({"f": sc("x", claimed)}, schema)
    assert report.values["f"].claimed_text == claimed


def test_claimed_text_may_be_absent_when_the_value_was_not_read_from_text() -> None:
    schema = _schema(_scalar("f", FieldType.STRING))
    report = conform({"f": sc("x", None)}, schema)
    assert report.values["f"].value == "x"
    assert report.values["f"].claimed_text is None


def test_a_non_string_claimed_text_is_a_shape_failure() -> None:
    schema = _schema(_scalar("f", FieldType.STRING))
    with pytest.raises(ExtractionError) as caught:
        conform({"f": {"value": "x", "claimed_text": 7, "confidence": None}}, schema)
    assert caught.value.reason == "shape"


# -- undeclared fields -------------------------------------------------------


def test_undeclared_fields_are_discarded_and_recorded() -> None:
    """EXT-17 -- never merged, and never silently dropped either."""
    schema = _schema(_scalar("f", FieldType.STRING))
    report = conform({"f": sc("x"), "extra": sc("y"), "also": 1}, schema)
    assert set(report.values) == {"f"}
    assert sorted(report.discarded) == ["also", "extra"]


def test_undeclared_fields_inside_a_group_are_recorded_with_their_path() -> None:
    schema = _schema(
        FieldSpec(
            name="g",
            cardinality=Cardinality.GROUP,
            description="a group",
            fields=(_scalar("kept", FieldType.STRING),),
        )
    )
    report = conform({"g": {"kept": sc("x"), "stray": sc("y")}}, schema)
    assert report.discarded == ("g.stray",)


# -- cardinality -------------------------------------------------------------


def _repeating() -> Schema:
    return _schema(
        FieldSpec(
            name="items",
            cardinality=Cardinality.REPEATING_GROUP,
            description="lines",
            fields=(_scalar("amount", FieldType.DECIMAL),),
        )
    )


def test_a_repeating_group_with_zero_occurrences_is_legal() -> None:
    report = conform({"items": []}, _repeating())
    assert report.values["items"] == ()


def test_a_repeating_group_indexes_its_error_paths() -> None:
    """ "Somewhere in the line items" is not an actionable message."""
    with pytest.raises(ExtractionError) as caught:
        conform({"items": [{"amount": sc("1.00")}, {"amount": sc(2.0)}]}, _repeating())
    assert caught.value.field_path == "items[1].amount"


def test_a_scalar_where_a_repeating_group_was_asked_for() -> None:
    with pytest.raises(ExtractionError) as caught:
        conform({"items": sc("nope")}, _repeating())
    assert caught.value.reason == "shape"
    assert caught.value.field_path == "items"


def test_a_list_where_a_scalar_was_asked_for() -> None:
    schema = _schema(_scalar("f", FieldType.STRING))
    with pytest.raises(ExtractionError) as caught:
        conform({"f": [sc("a"), sc("b")]}, schema)
    assert caught.value.reason == "shape"


def test_a_scalar_where_a_group_was_asked_for() -> None:
    schema = _schema(
        FieldSpec(
            name="g",
            cardinality=Cardinality.GROUP,
            description="g",
            fields=(_scalar("inner", FieldType.STRING),),
        )
    )
    with pytest.raises(ExtractionError) as caught:
        conform({"g": sc("nope")}, schema)
    assert caught.value.reason == "shape"
    assert caught.value.field_path == "g"


def test_a_non_object_response_fails_at_the_root() -> None:
    with pytest.raises(ExtractionError) as caught:
        conform(["not", "an", "object"], _schema(_scalar("f", FieldType.STRING)))
    assert caught.value.reason == "shape"
    assert "<root>" in str(caught.value)


# -- confidence and grounding ------------------------------------------------


def test_model_confidence_is_carried_verbatim_and_grounding_is_not_set() -> None:
    """ADR-0004 and EXT-24 in one assertion, because they are one boundary."""
    schema = _schema(_scalar("f", FieldType.STRING))
    value = conform({"f": sc("x", "x", 0.42)}, schema).values["f"]
    assert value.model_confidence == 0.42
    assert value.grounding is None
    assert value.grounding_score is None
    assert value.calibrated_confidence is None
    assert value.calibrator_version is None
    assert value.grounded is False


def test_a_non_numeric_confidence_is_a_shape_failure() -> None:
    schema = _schema(_scalar("f", FieldType.STRING))
    with pytest.raises(ExtractionError) as caught:
        conform({"f": {"value": "x", "claimed_text": None, "confidence": "high"}}, schema)
    assert caught.value.reason == "shape"


def test_a_missing_confidence_key_is_tolerated() -> None:
    """Only `value` and `claimed_text` are load-bearing; confidence is untrusted anyway."""
    schema = _schema(_scalar("f", FieldType.STRING))
    value = conform({"f": {"value": "x", "claimed_text": "x"}}, schema).values["f"]
    assert value.model_confidence is None


# -- error context -----------------------------------------------------------


def test_every_failure_names_the_document_schema_and_adapter() -> None:
    """SC-012 -- attributes, so a caller routes on them rather than parsing prose."""
    schema = _schema(_scalar("f", FieldType.STRING))
    with pytest.raises(ExtractionError) as caught:
        conform({}, schema, document_id="sha256:doc", adapter_id="echo")
    assert caught.value.document_id == "sha256:doc"
    assert caught.value.schema_identity == "probe@1"
    assert caught.value.adapter_id == "echo"


def test_the_real_invoice_schema_conforms_end_to_end() -> None:
    schema = load_schema(SCHEMAS / "invoice@1.json")
    payload = {
        "invoice_number": sc("INV-1", "INV-1"),
        "issue_date": sc("2026-03-01", "1 Mar 2026"),
        "due_date": sc(None, None),
        "currency": sc("USD", "$"),
        "total": sc("10.00", "10.00"),
        "supplier": {"legal_name": sc("A", "A"), "tax_id": sc(None, None)},
        "line_items": [],
    }
    report = conform(payload, schema)
    assert report.discarded == ()
    assert report.values["line_items"] == ()
