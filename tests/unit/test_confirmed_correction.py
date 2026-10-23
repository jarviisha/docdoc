"""A check can say "correct": a Correction that confirms the predicted value."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from docdoc.evaluation.corrections import Correction
from docdoc.evaluation.labels import Expectation, ExpectedLocation

NOW = datetime(2026, 1, 1, tzinfo=UTC)
BASE = {
    "report_id": "sha256:" + "a" * 64,
    "document_id": "d",
    "field_path": "total",
    "predicted_value": "228.00",
    "annotator": "ops",
    "timestamp": NOW,
}


def test_a_confirmed_check_becomes_a_label_of_the_predicted_value():
    c = Correction(**BASE, confirmed=True, reason="", location=ExpectedLocation(page=0))
    label = c.as_label()
    assert (label.expectation, label.value, label.location.page) == (Expectation.VALUE, "228.00", 0)
    assert c.reason == "confirmed correct"


def test_confirmed_with_a_corrected_value_is_refused():
    with pytest.raises(ValidationError):
        Correction(**BASE, confirmed=True, reason="", corrected_value="1")


def test_confirmed_with_corrected_absence_is_refused():
    with pytest.raises(ValidationError):
        Correction(**BASE, confirmed=True, reason="", corrected_absence=True)


def test_an_unconfirmed_check_still_needs_a_value_or_absence():
    with pytest.raises(ValidationError):
        Correction(**BASE, confirmed=False, reason="x")


def test_an_unconfirmed_check_still_needs_a_reason():
    with pytest.raises(ValidationError):
        Correction(**BASE, corrected_value="1", reason=" ")


def test_confirming_a_value_docdoc_did_not_find_is_an_absence():
    c = Correction(**{**BASE, "predicted_value": None}, confirmed=True, reason="")
    label = c.as_label()
    assert (label.expectation, label.value, label.location) == (Expectation.ABSENT, None, None)
