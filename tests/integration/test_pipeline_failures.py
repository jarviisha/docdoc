"""A failure at each of the four stages, end to end (SC-012).

`tests/unit/test_pipeline_errors.py` injects failures into the pipeline's own
call sites. This one causes them the way a document would — a schema that does
not resolve, a model answer that is not the requested shape, an extraction that
does not belong to the document it is being grounded against — so the claim being
tested is that *real* failures behave this way, not that a monkeypatched one does.
"""

from __future__ import annotations

from typing import Any

from tests.fixtures.evaluation.datasets import registry
from tests.fixtures.evaluation.predictions import RESPONSES, document_for

from docdoc.extraction.adapters import EchoAdapter
from docdoc.pipeline import Stage, StageStatus, run

CLEAN = document_for("clean")


def _run(**overrides: Any):  # type: ignore[no-untyped-def]
    kwargs: dict[str, Any] = {
        "schema": "invoice@1",
        "registry": registry(),
        "adapter": EchoAdapter.returning("invoice@1", RESPONSES["clean"]),
        "document": CLEAN,
    }
    kwargs.update(overrides)
    return run(b"", **kwargs)


def test_an_unresolvable_schema_fails_at_extract_and_keeps_the_parse() -> None:
    result = _run(schema="invoice@99")

    assert result.failed_stage is Stage.EXTRACT
    assert result.failure_of(Stage.EXTRACT) == "SchemaError"
    assert result.document is CLEAN
    assert result.extraction is None


def test_a_malformed_model_answer_fails_at_extract() -> None:
    result = _run(adapter=EchoAdapter.malformed())

    assert result.failed_stage is Stage.EXTRACT
    assert result.document is not None
    assert result.processing_id is None


def test_a_stage_error_surfaces_as_that_stage_and_stops_the_run() -> None:
    """The pipeline reports the stage; the stage decides what is an error.

    Grounding's own refusal to resolve one parse's claims against another is
    Milestone 4's guarantee and is tested there. What belongs here is that when a
    stage does refuse, the run stops at it, names it, and keeps what came before.

    (The obvious way to provoke that from this file does not work: the shared
    evaluation fixtures build every document through one helper that gives them
    all the same blob identity, so two different fixture texts are one document
    as far as ADR-0002 is concerned. Worth knowing before writing a test that
    seems to prove grounding accepts a foreign extraction.)
    """
    result = _run(schema="invoice@99")

    assert result.failed_stage is Stage.EXTRACT
    assert result.outcome_for(Stage.EXTRACT).status is StageStatus.FAILED  # type: ignore[union-attr]
    assert result.outcome_for(Stage.GROUND).status is StageStatus.SKIPPED  # type: ignore[union-attr]
    assert result.document is not None


def test_every_run_records_an_outcome_for_all_four_stages() -> None:
    """Whether it succeeded, failed, or was never reached.

    An omitted outcome and a skipped one read the same and mean different
    things: "we did not get there" versus "nobody asked".
    """
    for result in (_run(), _run(schema="invoice@99"), _run(adapter=EchoAdapter.malformed())):
        assert [outcome.stage for outcome in result.outcomes] == list(Stage)


def test_a_successful_run_executes_each_remaining_stage_exactly_once() -> None:
    """Three executed, one reused — the parse is supplied by the fixture.

    Stated as counts rather than as "all four ran", because the counts are what
    every reuse claim in this milestone is measured with, and a test that could
    not tell three from four would not notice a stage running twice.
    """
    result = _run()
    assert result.executed_count == 3
    assert result.reused_count == 1
    assert all(
        outcome.status is StageStatus.EXECUTED
        for outcome in result.outcomes
        if outcome.stage is not Stage.PARSE
    )


def test_the_cost_of_a_run_is_readable_off_the_run(  # SC-004
) -> None:
    summary = _run().cost_summary()
    assert summary["executed"] + summary["reused"] == 4
    assert set(summary["stages"]) == {stage.value for stage in Stage}


def test_a_supplied_document_is_recorded_as_work_this_run_did_not_do() -> None:
    """Supplying a parsed document skips the parse, and the result says so."""
    outcome = _run().outcome_for(Stage.PARSE)
    assert outcome is not None
    assert outcome.status is StageStatus.REUSED
    assert outcome.artifact_id == CLEAN.id
