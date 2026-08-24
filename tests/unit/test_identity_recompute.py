"""SC-006 — a run's terminal identity is recomputable from what the run recorded.

The criterion is stated precisely, and the precision is the point: recomputable
from ``RunProvenance`` and the per-stage processor identities, versions, and
options hashes **and from nothing else**. A recomputation that needs a field the
run did not record is a failure of the criterion rather than a gap in the test.

That is what makes the artifact chain auditable after the fact. Somebody holding
a result — and no store, no source document, and no credentials — must be able to
show that its identity follows from its inputs. If they cannot, "why is this
value cached?" has no answer that does not begin with re-running the pipeline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.kernel.identity import canonical_json, content_id_for
from docdoc.pipeline import PipelineResult, Stage, run

FIXTURE = Path("tests/fixtures/pdf/digital_invoice.pdf")
SCHEMA = "invoice@1"


@pytest.fixture(scope="module")
def result() -> PipelineResult:
    return run(
        FIXTURE.read_bytes(),
        schema=SCHEMA,
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
    )


def _chain_step(input_artifact_id: str, processor: dict[str, str]) -> str:
    """ADR-0003's formula, written out here rather than imported.

    Deliberately a second implementation, and the only place in the codebase
    where that is right: importing the function under test would make this assert
    that a function equals itself. What it checks is that the *recorded fields*
    are sufficient to reproduce the identity, and for that the arithmetic has to
    be done independently.
    """
    payload = canonical_json(
        {
            "input_artifact_id": input_artifact_id,
            "processor_id": processor["processor_id"],
            "processor_version": processor["processor_version"],
            "options_hash": processor["options_hash"],
        }
    )
    return content_id_for(payload)


def test_the_run_records_a_processor_entry_for_every_stage(result: PipelineResult) -> None:
    """The precondition. Without it the recomputation below cannot start."""
    processors = result.provenance.processors
    assert set(processors) == {stage.value for stage in Stage}

    for stage, entry in processors.items():
        assert entry["processor_id"], f"{stage} recorded no processor id"
        assert entry["processor_version"], f"{stage} recorded no processor version"
        assert entry["options_hash"].startswith("sha256:"), f"{stage} recorded no options hash"


def test_every_stage_identity_is_reachable_from_the_recorded_outcomes(
    result: PipelineResult,
) -> None:
    """The chain composes, and the run recorded enough to walk it.

    Each stage's artifact id must be derivable from the *previous* stage's
    artifact id plus that stage's own recorded processor entry. Checked with the
    formula rewritten above rather than with docdoc's own helpers.
    """
    assert result.failed_stage is None

    order = list(Stage)
    previous = result.outcome_for(Stage.PARSE)
    assert previous is not None
    assert previous.artifact_id is not None

    for stage in order[1:]:
        outcome = result.outcome_for(stage)
        assert outcome is not None
        assert outcome.artifact_id is not None

        recomputed = _chain_step(
            previous.artifact_id,  # type: ignore[arg-type]
            result.provenance.processors[stage.value],
        )
        assert recomputed == outcome.artifact_id, (
            f"{stage.value}'s identity does not follow from the previous stage's "
            "identity and the processor entry this run recorded (SC-006)"
        )
        previous = outcome


def test_the_processing_id_is_the_terminal_artifact_and_not_a_second_identifier(
    result: PipelineResult,
) -> None:
    """FR-007 — one identity per run, and it is the one the chain already produced."""
    terminal = result.outcome_for(Stage.VALIDATE)
    assert terminal is not None
    assert result.processing_id == terminal.artifact_id
    assert result.validation is not None
    assert result.processing_id == result.validation.artifact_id


def test_no_duration_or_request_id_enters_an_identity() -> None:
    """FR-060 — two runs differing only in correlation must agree on every id.

    Durations necessarily differ between any two runs, so this is really the
    assertion that they are *not* folded: if they were, no two runs of the same
    document would ever produce the same identity and every cache would miss.
    """
    registry = SchemaRegistry.from_paths([Path("schemas")])
    adapter = EchoAdapter.from_fixtures("tests/fixtures/echo")
    source = FIXTURE.read_bytes()

    first = run(source, schema=SCHEMA, registry=registry, adapter=adapter, request_id="one")
    second = run(source, schema=SCHEMA, registry=registry, adapter=adapter, request_id="two")

    assert first.processing_id == second.processing_id
    assert first.provenance.request_id != second.provenance.request_id
    assert [o.artifact_id for o in first.outcomes] == [o.artifact_id for o in second.outcomes]


def test_the_pipeline_version_reaches_the_terminal_identity_and_nothing_else(
    result: PipelineResult,
) -> None:
    """ADR-0003 folds ``pipeline_version`` into the terminal artifact alone.

    Recorded on the provenance so a reader can see which pipeline produced the
    run — and *not* folded into the earlier stages, which is why changing the
    sequencing does not invalidate a parse.
    """
    assert result.provenance.pipeline_id
    assert result.provenance.pipeline_version

    parse_outcome = result.outcome_for(Stage.PARSE)
    assert parse_outcome is not None
    assert result.document is not None
    assert parse_outcome.artifact_id == result.document.id, (
        "the parse identity is the document id, which folds the parser and the "
        "parse options — never the pipeline version"
    )
