"""T013 — every authoring error is refused at load, naming the offender (FR-014, SC-021).

The timing is the requirement. An authoring error that survives into a scoring
run does not announce itself as an error: it becomes a document that permanently
scores zero, and the number it produces looks exactly like a real measurement of
a pipeline that is working fine.

Naming the offender is the second half, and it is not politeness. A dataset with
fifty documents and one bad label is not debuggable by a message that says
"invalid golden set" -- the maintainer has to bisect the data by hand, which is
the step that gets skipped in favour of deleting the check.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from docdoc.evaluation import EvaluationError, load_golden_set
from docdoc.evaluation.values import carry
from tests.fixtures.evaluation.authoring_errors import CASES, write_dataset
from tests.fixtures.evaluation.datasets import facts_for_fixtures, golden_set

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(("name", "build", "offender"), CASES, ids=[case[0] for case in CASES])
def test_the_authoring_error_is_refused_at_load(
    name: str, build: object, offender: str, tmp_path: Path
) -> None:
    """Each of the seven, refused before a single metric is computed."""
    manifest = write_dataset(tmp_path, build())  # type: ignore[operator]

    with pytest.raises(EvaluationError) as raised:
        load_golden_set(manifest, facts=facts_for_fixtures())

    assert offender in str(raised.value), (
        f"{name} was refused, but the message does not name {offender!r}: "
        f"{raised.value}. A refusal that does not say which document or field is "
        "wrong sends the maintainer to bisect the dataset by hand"
    )


@pytest.mark.parametrize(("name", "build", "offender"), CASES, ids=[case[0] for case in CASES])
def test_the_refusal_carries_the_offender_as_an_attribute(
    name: str, build: object, offender: str, tmp_path: Path
) -> None:
    """Prose is for humans; a caller that has to parse it will not (contracts §8)."""
    manifest = write_dataset(tmp_path, build())  # type: ignore[operator]

    with pytest.raises(EvaluationError) as raised:
        load_golden_set(manifest, facts=facts_for_fixtures())

    error = raised.value
    located = [error.document_id, error.field_path, error.expected, error.actual]
    assert any(value for value in located), (
        f"{name} raised an EvaluationError carrying no structured location at all; "
        "every attribute was None, so the only machine-readable fact is the type"
    )

    # FR-060 names three things an error must carry: the dataset, the document,
    # and the field. `dataset` was declared and populated by no raise site at all,
    # which is worse than an absent field -- a caller reading it got a confident
    # `None` meaning "unknown" for a dataset that was perfectly well known.
    #
    # At load there is no identity yet: it is computed *from* the labels being
    # checked, so it cannot exist before they are known good. The manifest path is
    # the honest answer, and it is the one a maintainer can act on.
    assert error.dataset is not None, f"{name} does not name the dataset at fault (FR-060)"
    assert error.dataset.endswith("manifest.json"), error.dataset


def test_the_well_formed_set_loads_unchanged(tmp_path: Path) -> None:
    """The guard on the guard.

    A refusal suite that refused everything would pass every test above while
    making the loader useless. This is the case that says the checks discriminate.
    """
    golden = golden_set()
    documents = [d.document_id for d in golden.documents]

    assert len(documents) == len(set(documents)), "the fixture itself must be well formed"
    assert golden.labels_for("clean"), "the fixture must carry labels to be worth loading"

    # Every public document declares exactly the number of labels it carries, and
    # the restricted one declares a count while carrying none -- which is the
    # asymmetry EVA-5a exists for.
    for document in golden.documents:
        supplied = len(golden.labels_for(document.document_id))
        if supplied:
            assert supplied == document.declared_label_count, document.document_id
        else:
            assert document.declared_label_count > 0, (
                f"{document.document_id} declares no labels and supplies none, so a "
                "partial report covering it could not state a covered fraction"
            )


def test_a_dataset_with_no_schema_facts_still_loads(tmp_path: Path) -> None:
    """`facts` is optional, and the checks it powers are the ones that need it.

    A caller holding no schema registry gets a dataset; they just get no type
    checking with it. This asserts the optionality is real rather than a
    parameter nobody may omit -- and, by contrast with the parametrized cases
    above, that the type-dependent refusals are the ones that go quiet.
    """
    manifest = write_dataset(tmp_path, CASES[2][1]())  # value the type cannot carry

    loaded = load_golden_set(manifest)

    assert loaded.labels_for("mistyped")[0].value == 1240.00, (
        "without schema facts the value is taken as authored; the refusal is the "
        "thing that needs the schema, not the load"
    )


# -- the typing rule behind EVA-5b's fourth clause ---------------------------
#
# `carry` decides what a declared FieldType can hold, for a label and for a
# replayed prediction alike. It is the reason a golden set authored in JSON can
# be compared against a pipeline that produces `Decimal`, `date`, and `datetime`
# -- and if the two coercions ever disagreed, every decimal in the dataset would
# read as an extraction error while the pipeline was working perfectly.
#
# Tested directly as well as through the loader, because the loader only ever
# exercises the types the fixture happens to use.


@pytest.mark.parametrize(
    ("field_type", "raw", "expected"),
    [
        ("string", "INV-001", "INV-001"),
        ("boolean", True, True),
        ("integer", 7, 7),
        ("number", 7, 7.0),
        ("number", 7.5, 7.5),
        ("decimal", "1240.00", Decimal("1240.00")),
        ("date", "2026-03-01", date(2026, 3, 1)),
        ("datetime", "2026-03-02T14:05:00", datetime(2026, 3, 2, 14, 5)),
    ],
)
def test_a_json_scalar_becomes_the_type_the_schema_declares(
    field_type: str, raw: object, expected: object
) -> None:
    """The coercion, per type. ``number`` widens to float; nothing else changes shape."""
    carried = carry(raw, field_type)

    assert carried == expected
    assert type(carried) is type(expected)


@pytest.mark.parametrize(
    ("field_type", "raw", "why"),
    [
        ("string", 1, "an integer is not a string"),
        ("boolean", 1, "and 1 is not True, which is the whole point of EVA-12a"),
        ("boolean", "true", "nor is the word"),
        ("integer", True, "bool subclasses int, so this must be rejected explicitly"),
        ("integer", "7", "the string form is not the value"),
        ("integer", 7.0, "and neither is the float form"),
        ("number", True, "same trap, one type along"),
        ("decimal", 1240.00, "a decimal travels as a string; the float is already lossy"),
        ("decimal", "not-a-number", "and the string has to parse"),
        ("date", "2026-13-01", "month 13"),
        ("date", 20260301, "an integer date is not a date"),
        ("datetime", "yesterday", "prose is not ISO-8601"),
    ],
)
def test_a_value_the_declared_type_cannot_carry_is_refused(
    field_type: str, raw: object, why: str
) -> None:
    """Each rejection names the type and what arrived.

    "Invalid label" would send the author back to guess which of the two is
    wrong, on a file they may not have written.
    """
    with pytest.raises(ValueError, match=field_type) as raised:
        carry(raw, field_type)

    assert repr(raw) in str(raised.value), why


def test_an_unknown_field_type_is_refused_rather_than_passed_through() -> None:
    """A typo in a schema description would otherwise disable checking for one field.

    That is the least visible way for this to fail: every other field stays
    typed, so nothing looks wrong, and the one field silently accepts anything.
    """
    with pytest.raises(ValueError, match="unknown field type"):
        carry("anything", "strng")


def test_carry_is_idempotent() -> None:
    """Load-bearing, not a convenience.

    The loader coerces on the way in and ``validate_golden_set`` re-checks the
    set it just built. A ``carry`` that only accepted the JSON form would refuse
    its own output, so every dataset with a date in it would fail to load.
    """
    for field_type, value in (
        ("decimal", Decimal("1240.00")),
        ("date", date(2026, 3, 1)),
        ("datetime", datetime(2026, 3, 2, 14, 5)),
        ("number", 7.5),
        ("boolean", False),
    ):
        assert carry(value, field_type) is value


def test_idempotence_does_not_wave_a_subclass_through() -> None:
    """``datetime`` subclasses ``date`` and ``bool`` subclasses ``int``.

    An ``isinstance`` check here would accept a datetime as a date label, and the
    label would then never match a correctly typed prediction -- the same failure
    `comparators@1`'s type gate exists to prevent, arriving one layer earlier.
    """
    with pytest.raises(ValueError, match="date"):
        carry(datetime(2026, 3, 1, 12, 0), "date")

    with pytest.raises(ValueError, match="integer"):
        carry(True, "integer")


def test_no_type_declared_means_no_coercion_and_no_refusal() -> None:
    """A caller holding no schema registry still gets a dataset."""
    assert carry("1240.00", None) == "1240.00"
    assert carry(None, "decimal") is None
