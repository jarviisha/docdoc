"""How the pipeline fails, which matters more than how it succeeds.

Three claims, each of which the obvious implementation gets wrong:

**A failure keeps everything before it** (FR-004). Returning nothing is easier
and destroys the evidence of what went wrong at the moment somebody needs it.

**A failure is attributed to the layer that declared the error** (FR-005), not to
whatever was executing when it surfaced. A grounding error raised during
validation is a grounding error; calling it a validation error sends the reader
to the wrong code.

**No untyped exception escapes** (FR-051). A caller that has to catch
``Exception`` cannot tell a docdoc problem from a bug in their own code.
"""

from __future__ import annotations

from typing import Any

import pytest

from docdoc.extraction.adapters import EchoAdapter
from docdoc.extraction.errors import ExtractionError, SchemaError
from docdoc.grounding.errors import GroundingError
from docdoc.pipeline import Stage, StageStatus, run
from docdoc.validation.errors import ValidationError
from tests.fixtures.evaluation.datasets import registry
from tests.fixtures.evaluation.predictions import RESPONSES, document_for

DOCUMENT = document_for("clean")


def _run(**overrides: Any):  # type: ignore[no-untyped-def]
    kwargs: dict[str, Any] = {
        "schema": "invoice@1",
        "registry": registry(),
        "adapter": EchoAdapter.returning("invoice@1", RESPONSES["clean"]),
        "document": DOCUMENT,
    }
    kwargs.update(overrides)
    return run(b"", **kwargs)


def test_a_clean_run_reaches_validate_and_carries_a_processing_id() -> None:
    result = _run()
    assert result.failed_stage is None
    assert result.processing_id == result.validation.artifact_id  # type: ignore[union-attr]
    assert [outcome.stage for outcome in result.outcomes] == list(Stage)


def test_the_processing_id_is_present_exactly_when_validation_is() -> None:
    result = _run()
    assert (result.processing_id is not None) == (result.validation is not None)


# -- failure keeps what came before -----------------------------------------


def test_a_failure_at_extract_keeps_nothing_after_it_and_the_document_before_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _run(adapter=EchoAdapter.malformed())

    assert result.failed_stage is Stage.EXTRACT
    assert result.document is not None, "the parse survived and must be returned"
    assert result.extraction is None
    assert result.processing_id is None


@pytest.mark.parametrize(
    ("failing", "error"),
    [
        (Stage.GROUND, GroundingError("no")),
        (Stage.VALIDATE, ValidationError("no")),
    ],
)
def test_a_failure_late_in_the_run_keeps_every_earlier_result(
    monkeypatch: pytest.MonkeyPatch, failing: Stage, error: Exception
) -> None:
    """SC-012: a run that fails at stage n returns the results of all n-1 before it."""
    target = {Stage.GROUND: "_ground", Stage.VALIDATE: "_validate"}[failing]

    def _explode(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(f"docdoc.pipeline.runner.{target}", _explode)
    result = _run()

    assert result.failed_stage is failing
    assert result.document is not None
    assert result.extraction is not None
    if failing is Stage.VALIDATE:
        assert result.grounding is not None
    assert result.processing_id is None


def test_the_stages_after_a_failure_are_recorded_as_skipped_not_omitted() -> None:
    """A missing outcome and a skipped one mean different things."""
    result = _run(adapter=EchoAdapter.malformed())

    statuses = {outcome.stage: outcome.status for outcome in result.outcomes}
    assert statuses[Stage.EXTRACT] is StageStatus.FAILED
    assert statuses[Stage.GROUND] is StageStatus.SKIPPED
    assert statuses[Stage.VALIDATE] is StageStatus.SKIPPED


# -- attribution by declaring layer (FR-005) --------------------------------


def test_a_grounding_error_raised_during_validation_is_a_grounding_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rule this milestone inherits from the recorder.

    Attributing it to the stage that happened to be running would send whoever
    reads the report to the wrong code.
    """

    def _explode(*args: object, **kwargs: object) -> None:
        raise GroundingError("surfaced while validating")

    monkeypatch.setattr("docdoc.pipeline.runner._validate", _explode)
    result = _run()

    assert result.failed_stage is Stage.GROUND


# -- the message never travels (FR-043) -------------------------------------


def test_only_the_error_class_name_is_recorded_never_its_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception message can quote the content it choked on."""
    secret = "ACME-CONFIDENTIAL-INV-0001"

    def _explode(*args: object, **kwargs: object) -> None:
        raise ValidationError(f"could not check {secret}")

    monkeypatch.setattr("docdoc.pipeline.runner._validate", _explode)
    result = _run()

    outcome = result.outcome_for(Stage.VALIDATE)
    assert outcome is not None
    assert outcome.failure_class == "ValidationError"
    assert secret not in result.model_dump_json()


# -- no untyped exception escapes (FR-051, SC-011) --------------------------


@pytest.mark.parametrize(
    "error",
    [
        SchemaError("bad schema", identity="invoice@9"),
        ExtractionError("bad extraction", reason="shape"),
        GroundingError("bad grounding"),
        ValidationError("bad validation"),
        RuntimeError("something nobody typed"),
        ValueError("a bare value error"),
    ],
)
@pytest.mark.parametrize("target", ["_extract", "_ground", "_validate"])
def test_no_exception_escapes_the_run(
    monkeypatch: pytest.MonkeyPatch, error: Exception, target: str
) -> None:
    def _explode(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(f"docdoc.pipeline.runner.{target}", _explode)
    result = _run()  # must not raise

    assert result.failed_stage is not None
    assert result.processing_id is None


# -- retries (FR-010) -------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [ValidationError("no"), GroundingError("no"), SchemaError("no", identity="invoice@9")],
)
def test_deterministic_errors_are_never_retried(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """There is no transient failure mode in a deterministic computation.

    The pipeline adds no retry of its own; provider retry policy lives in the
    layer that makes provider calls, where it can distinguish a timeout from a
    verdict.
    """
    calls = {"count": 0}
    target = {
        "ValidationError": "_validate",
        "GroundingError": "_ground",
        "SchemaError": "_extract",
    }[type(error).__name__]

    def _count_then_explode(*args: object, **kwargs: object) -> None:
        calls["count"] += 1
        raise error

    monkeypatch.setattr(f"docdoc.pipeline.runner.{target}", _count_then_explode)
    _run()

    assert calls["count"] == 1, "a deterministic error was retried"
