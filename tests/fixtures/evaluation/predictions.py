"""T007 — prediction triples, produced offline by the real pipeline.

Every ``DocumentPrediction`` here is built by running **extract → ground →
validate** with Milestone 3's ``echo`` adapter over a document built from the
text in :mod:`.datasets`. Nothing is hand-assembled, and that is the point: a
hand-built ``GroundingResult`` would let the scorer pass against a shape the
grounding layer never produces, which is exactly the drift a fixture is supposed
to catch.

**No parser and no PDF dependency.** The documents are built with
``tests.support.make_document``, the same helper the grounding and validation
suites use. SC-022 says a contributor runs 100% of this feature's tests after
``uv sync --extra dev``, and a fixture that needed ``--extra pdf`` to build would
make that false for the whole suite rather than for one file.

The eight cases T007 asks for, and what each one exists to exercise:

============= =========================================================
``clean``     matches its labels exactly, and predicts one unlabeled path
``near-miss`` a wrong value, a missing one, and a spurious one at once
``keyed``     entries recorded in a different order than they are labelled
``receipt``   the second schema, so nothing is invoice-shaped (FR-013)
``failing``   stopped at ``GROUND``; labelled fields count MISSING (FR-037)
``silent``    absent from the set entirely -> ``UNEVALUATED`` (FR-005)
``mismatched`` recorded under ``invoice@2`` -- for the refusal tests (FR-004)
``restricted`` a restricted-tier prediction, for the redaction tests (FR-056)
============= =========================================================
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tests.fixtures.evaluation.datasets import (
    DOCUMENT_TEXT,
    INVOICE,
    RECEIPT,
    registry,
)

from docdoc.evaluation import DocumentPrediction, PredictionSet, Stage
from docdoc.extraction.adapters import EchoAdapter
from docdoc.extraction.extract import extract
from docdoc.grounding import ground
from docdoc.validation import validate

if TYPE_CHECKING:
    from docdoc.extraction.extract import ExtractionResult
    from docdoc.kernel import Document

# `tests.support` is imported through the package path above where possible; this
# keeps a direct `pytest tests/...` invocation working from the repository root
# regardless of how the suite was started.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tests.support import make_document

__all__ = [
    "MISMATCHED_SCHEMA",
    "prediction_for",
    "prediction_set",
]

#: The identity the refusal fixture records. A label written under ``invoice@1``
#: says nothing about a result produced under ``invoice@2`` (ADR-0008), and the
#: refusal that enforces it needs a fixture that genuinely was.
MISMATCHED_SCHEMA = "invoice@2"


def _echo(field: object, claimed: str | None = None) -> dict[str, Any]:
    """One field of an echo response: the value, and the text it claims to read it from."""
    return {"value": field, "claimed_text": claimed, "confidence": None}


def _absent() -> dict[str, Any]:
    return {"value": None, "claimed_text": None, "confidence": None}


#: The canned model responses, one per fixture document. These are the *model's*
#: answers, so they are JSON scalars: `conform` types them on the way in, exactly
#: as it would for a real provider.
RESPONSES: dict[str, dict[str, Any]] = {
    "clean": {
        "invoice_number": _echo("INV-001", "INV-001"),
        "issue_date": _echo("2026-03-01", "2026-03-01"),
        "due_date": _absent(),
        "currency": _echo("USD", "USD"),
        "total": _echo("1240.00", "1240.00"),
        "supplier": {"legal_name": _echo("ACME LTD", "ACME LTD"), "tax_id": _absent()},
        "line_items": [
            {
                "description": _echo("Widget, large", "Widget, large"),
                # Labelled nowhere, and predicted anyway. UNLABELED: neither right
                # nor wrong, and in no accuracy denominator (FR-036).
                "quantity": _echo(2.0, "2"),
                "unit_price": _echo("500.00", "500.00"),
                "amount": _echo("1000.00", "1000.00"),
            },
            {
                "description": _echo("Delivery", "Delivery"),
                "quantity": _echo(1.0, "1"),
                "unit_price": _echo("240.00", "240.00"),
                "amount": _echo("240.00", "240.00"),
            },
        ],
    },
    "near-miss": {
        "invoice_number": _echo("INV-002", "INV-002"),
        "issue_date": _echo("2026-03-02", "2026-03-02"),
        "due_date": _absent(),
        "currency": _echo("USD", "USD"),
        # The truth says 350.00. A wrong answer, not a blank one.
        "total": _echo("300.00", "300.00"),
        "supplier": {
            # The truth says ACME LTD. A blank, not a wrong answer.
            "legal_name": _absent(),
            # The truth says this is absent. An invention.
            "tax_id": _echo("GB-123", "GB-123"),
        },
        "line_items": [],
    },
    "keyed": {
        "invoice_number": _echo("INV-003", "INV-003"),
        "issue_date": _echo("2026-03-03", "2026-03-03"),
        "due_date": _absent(),
        "currency": _echo("EUR", "EUR"),
        "total": _echo("100.00", "100.00"),
        "supplier": {"legal_name": _echo("ACME LTD", "ACME LTD"), "tax_id": _absent()},
        # Recorded in printed order, which is the reverse of the labelled order.
        # Positionally every field mismatches; keyed by `description` every one
        # agrees. One fixture, two answers, decided by the alignment policy.
        "line_items": [
            {
                "description": _echo("SKU-B", "SKU-B"),
                "quantity": _echo(1.0, "1"),
                "unit_price": _echo("50.00", "50.00"),
                "amount": _echo("50.00", "50.00"),
            },
            {
                "description": _echo("SKU-A", "SKU-A"),
                "quantity": _echo(2.0, "2"),
                "unit_price": _echo("25.00", "25.00"),
                "amount": _echo("50.00", "50.00"),
            },
        ],
    },
    "receipt": {
        "merchant_name": _echo("CORNER STORE", "CORNER STORE"),
        "purchased_at": _echo("2026-03-02T14:05:00", "2026-03-02 14:05"),
        "total": _echo("12.50", "12.50"),
        "payment_method": _echo("card", "card"),
    },
    "failing": {
        "invoice_number": _echo("INV-004", "INV-004"),
        "issue_date": _echo("2026-03-04", "2026-03-04"),
        "due_date": _absent(),
        "currency": _echo("GBP", "GBP"),
        "total": _echo("999.00", "999.00"),
        "supplier": {"legal_name": _echo("ACME LTD", "ACME LTD"), "tax_id": _absent()},
        "line_items": [],
    },
}

#: Which schema each fixture document was recorded under.
SCHEMAS = {
    "clean": INVOICE,
    "near-miss": INVOICE,
    "keyed": INVOICE,
    "receipt": RECEIPT,
    "failing": INVOICE,
}


def document_for(name: str) -> Document:
    """A real ``Document`` over the fixture text, built without a parser."""
    return make_document(DOCUMENT_TEXT[name])


#: ``invoice@2`` declares everything ``invoice@1`` does plus a purchase-order
#: number. `conform` requires **every** declared field to be present -- a field
#: the document does not contain is a null value, not an absent key (EXT-16) --
#: so the mismatched-schema fixture answers the newer schema in full rather than
#: reusing the older answer and failing for the wrong reason.
RESPONSES[MISMATCHED_SCHEMA] = {**RESPONSES["clean"], "purchase_order_number": _absent()}


def _extract_for(name: str, *, schema: str | None = None) -> tuple[Document, ExtractionResult]:
    document = document_for(name)
    identity = schema or SCHEMAS[name]
    # Keyed by the schema when one is forced, so the response matches the fields
    # that schema declares.
    adapter = EchoAdapter.returning(identity, RESPONSES.get(identity, RESPONSES[name]))
    return document, extract(document, schema=identity, registry=registry(), adapter=adapter)


def prediction_for(name: str, *, failed_stage: Stage | None = None) -> DocumentPrediction:
    """One document's recorded triple, produced by running the real stages.

    ``failed_stage`` records a document that stopped part-way. Its extraction is
    kept and its grounding and validation are absent, which is what a real
    failure leaves behind -- and its labelled fields still count as ``MISSING``
    rather than leaving the denominator (FR-037, EVA-9a).
    """
    document, extraction = _extract_for(name)

    parser = document.provenance

    if failed_stage is not None:
        return DocumentPrediction(
            document_id=name,
            extraction=extraction,
            failed_stage=failed_stage,
            # The typed error's class name, never a value: this field travels
            # into reports and logs, where FR-057 forbids document content.
            failure_reason="GroundingError",
            parser_id=parser.parser_id,
            parser_version=parser.parser_version,
        )

    grounding = ground(document, extraction)
    validation = validate(extraction, grounding, registry().resolve(SCHEMAS[name]).schema)
    return DocumentPrediction(
        document_id=name,
        extraction=extraction,
        grounding=grounding,
        validation=validation,
        parser_id=parser.parser_id,
        parser_version=parser.parser_version,
    )


def mismatched_prediction(document_id: str = "clean") -> DocumentPrediction:
    """A prediction recorded under ``invoice@2`` — for the FR-004 refusal.

    Genuinely produced under the other schema rather than edited to claim it was,
    so the refusal is tested against the thing it will actually meet.
    """
    document, extraction = _extract_for("clean", schema=MISMATCHED_SCHEMA)
    grounding = ground(document, extraction)
    validation = validate(extraction, grounding, registry().resolve(MISMATCHED_SCHEMA).schema)
    return DocumentPrediction(
        document_id=document_id,
        extraction=extraction,
        grounding=grounding,
        validation=validation,
        parser_id=document.provenance.parser_id,
        parser_version=document.provenance.parser_version,
    )


def restricted_prediction() -> DocumentPrediction:
    """A restricted-tier prediction, whose values the report must not print."""
    return prediction_for("clean").model_copy(update={"document_id": "restricted-invoice"})


def degraded_prediction_set() -> PredictionSet:
    """The same documents with one deliberate degradation, recorded for real.

    ``clean``'s total is misread as 1249.00, and the claimed text no longer
    appears in the document -- so the value is both **incorrect** and
    **ungrounded**, which is the pair a regression usually arrives as.

    Produced by re-running extract -> ground -> validate rather than by editing a
    recorded result, and **recorded under a new model version**, because both
    halves are needed to make it a realistic regression.

    ``prediction_set_id`` folds one validation artifact id per document, since
    ADR-0003 makes that id transitively cover every earlier stage's *input*. An
    artifact id is over inputs, not outputs -- so two runs that claim the same
    document, schema, prompt, model, and options claim the same identity no
    matter what came back. Editing a recorded extraction in place therefore
    leaves the whole chain unmoved, and so does swapping the echo adapter's
    canned answer while leaving its version alone: both produce two prediction
    sets that assert they are the same measurement, and comparing them compares a
    report against itself.

    In reality a different answer comes from something identified having changed
    -- the model, the prompt, the document, the options. Here it is the model
    version, which is also what makes the comparison's ``provenance_differences``
    say something true about why the number moved.
    """
    degraded = {
        **RESPONSES["clean"],
        "total": _echo("1249.00", "1,249.00 (misread)"),
    }
    document = document_for("clean")
    adapter = EchoAdapter({INVOICE: degraded}, version="1.1.0", model_version="2")
    extraction = extract(document, schema=INVOICE, registry=registry(), adapter=adapter)
    grounding = ground(document, extraction)
    validation = validate(extraction, grounding, registry().resolve(INVOICE).schema)

    base = prediction_set()
    return base.model_copy(
        update={
            "predictions": {
                **base.predictions,
                "clean": DocumentPrediction(
                    document_id="clean",
                    extraction=extraction,
                    grounding=grounding,
                    validation=validation,
                    parser_id=document.provenance.parser_id,
                    parser_version=document.provenance.parser_version,
                ),
            }
        }
    )


def prediction_set(
    *,
    include_failing: bool = True,
    include_silent: bool = False,
    include_restricted: bool = False,
) -> PredictionSet:
    """The prediction set the US1 suite scores.

    ``silent`` is **excluded by default**, and that is the fixture's whole point:
    a golden-set document with no prediction must be reported ``UNEVALUATED`` and
    stay in every denominator. Adding it here would delete the case.
    """
    predictions = {
        "clean": prediction_for("clean"),
        "near-miss": prediction_for("near-miss"),
        "keyed": prediction_for("keyed"),
        "receipt": prediction_for("receipt"),
    }
    if include_failing:
        predictions["failing"] = prediction_for("failing", failed_stage=Stage.GROUND)
    if include_silent:
        predictions["silent"] = prediction_for("clean").model_copy(update={"document_id": "silent"})
    if include_restricted:
        predictions["restricted-invoice"] = restricted_prediction()

    return PredictionSet(
        predictions=predictions,
        recorder_id="fixtures",
        recorder_version="1.0.0",
    )
