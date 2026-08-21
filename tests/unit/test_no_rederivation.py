"""T060 — evaluation reads recorded facts and recomputes none of them (FR-002, FR-044).

A stage that quietly re-derived one of its inputs would be reporting on a
pipeline nobody ran. The report would name a ``prompt_hash`` and a ``model_id``
from the recorded provenance while the values it scored came from a fresh
extraction under today's code -- so the numbers would describe a run that never
happened, attributed to versions that did not produce them.

The failure is not exotic. Re-grounding is the tempting one: the recorded
``GroundingResult`` might be stale, and grounding is deterministic and cheap, so
recomputing it "cannot hurt". It changes the meaning of the report completely,
and nothing in the output would say so.

The second half of this file is FR-044: re-evaluating produces a **new** report
and leaves the previous one untouched. Two runs, two artifacts, each with its own
provenance -- which is what makes a comparison between them meaningful.
"""

from __future__ import annotations

import pytest

from docdoc.evaluation import evaluate
from tests.fixtures.evaluation.datasets import facts_for_fixtures, golden_set
from tests.fixtures.evaluation.predictions import prediction_set


@pytest.fixture
def exploding_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every upstream stage fatal. Scoring must not notice."""
    import importlib

    def refuse(stage: str):  # type: ignore[no-untyped-def]
        def explode(*args: object, **kwargs: object) -> None:
            raise AssertionError(
                f"evaluation called {stage}(). It reads recorded facts; a stage that "
                "recomputed one would be reporting on a pipeline nobody ran (FR-002)"
            )

        return explode

    # Reached through `import_module` rather than attribute access: each package
    # re-exports its entry point under the submodule's own name, so
    # `docdoc.extraction.extract` is the *function*, not the module holding it.
    for module_name, attribute in (
        ("docdoc.extraction.extract", "extract"),
        ("docdoc.grounding.ground", "ground"),
        ("docdoc.validation", "validate"),
    ):
        monkeypatch.setattr(importlib.import_module(module_name), attribute, refuse(attribute))


def test_scoring_calls_none_of_the_upstream_stages(exploding_stages: None) -> None:
    """The assertion, with all three patched to raise."""
    report = evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())

    assert report.metrics.micro["field_accuracy"].numerator == 25


def test_the_recorded_grounding_is_reported_rather_than_recomputed() -> None:
    """A grounding status the pipeline could not have produced still reaches the report.

    The status below is *wrong* for this document -- a fresh grounding run would
    never return it. It survives into the outcome, which is the proof that the
    outcome is a recorded fact rather than a recomputed one.
    """
    from docdoc.grounding.result import GroundingStatus

    predictions = prediction_set()
    clean = predictions.for_document("clean")
    assert clean is not None
    assert clean.grounding is not None

    outcome = clean.grounding.outcomes["invoice_number"]
    assert outcome.status is GroundingStatus.EXACT, "the fixture's real status"

    tampered_outcomes = {
        **clean.grounding.outcomes,
        "invoice_number": outcome.model_copy(
            update={"status": GroundingStatus.FUZZY, "score": 0.42}
        ),
    }
    tampered = predictions.model_copy(
        update={
            "predictions": {
                **predictions.predictions,
                "clean": clean.model_copy(
                    update={
                        "grounding": clean.grounding.model_copy(
                            update={"outcomes": tampered_outcomes}
                        )
                    }
                ),
            }
        }
    )

    report = evaluate(golden_set(), tampered, facts=facts_for_fixtures())
    reported = next(
        o for o in report.outcomes if o.document_id == "clean" and o.field_path == "invoice_number"
    )

    assert reported.grounding_status is GroundingStatus.FUZZY
    assert reported.grounding_score == 0.42


def test_the_scorer_opens_no_document(monkeypatch: pytest.MonkeyPatch) -> None:
    """It compares labels against recorded predictions, so it has no need of one.

    This is also why scoring needs no parser, no credentials, and no network
    (FR-007) -- the three follow from having nothing to open.
    """
    import docdoc.ingest

    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("evaluation called parse(); it has no document to open")

    monkeypatch.setattr(docdoc.ingest, "parse", explode)

    evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())


# -- FR-044: a second run is a second artifact -------------------------------


def test_re_evaluating_leaves_the_prior_report_untouched() -> None:
    """Frozen models give this structurally.

    Asserted anyway, because a ``model_copy`` is one review away.
    """
    golden = golden_set()
    predictions = prediction_set()
    facts = facts_for_fixtures()

    first = evaluate(golden, predictions, facts=facts)
    snapshot = first.model_dump_json()

    second = evaluate(golden, predictions, facts=facts)

    assert first.model_dump_json() == snapshot
    assert second is not first


def test_the_second_report_carries_its_own_provenance() -> None:
    """Two runs, two artifacts. Identical inputs make them identical, not shared."""
    golden = golden_set()
    predictions = prediction_set()

    first = evaluate(golden, predictions, facts=facts_for_fixtures(), repo_revision="aaa")
    second = evaluate(golden, predictions, facts=facts_for_fixtures(), repo_revision="bbb")

    assert first.provenance.repo_revision == "aaa"
    assert second.provenance.repo_revision == "bbb"
    assert first.provenance is not second.provenance
    assert first.report_id == second.report_id, (
        "the repo revision is recorded and does not move the identity: it cannot "
        "change a number, and an id that moved on it would refuse comparisons "
        "between two checkouts of the same code"
    )


def test_a_report_is_immutable() -> None:
    """Nothing downstream can edit a metric after the fact."""
    import pydantic

    report = evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())

    with pytest.raises(pydantic.ValidationError):
        report.report_id = "sha256:tampered"  # type: ignore[misc]
