"""T119, FR-054 — the one observer that leaves the deployment.

Every other observer in this project keeps document content out of what it emits
and argues its own reason. This is the only one whose output crosses a network to
somebody else's server, so the same rule is checked here with the same technique
Milestone 9 used for run records: seed a run with distinctive strings and require
zero occurrences.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from docdoc.runs.delivery import payload_for
from docdoc.runs.model import Run, RunStatus, StageOutcomeRecord

#: Strings that could only appear by having been copied out of a document, a
#: provider's answer, or a credential.
SEEDED = (
    "ACME-INVOICE-CONFIDENTIAL",
    "1,234.56",
    "ddk_live_notarealkeybutlooksliketone",
    "the model refused because the document said",
)

AT = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def _run(**overrides: object) -> Run:
    fields: dict = {
        "run_id": uuid4(),
        "tenant_id": "acme",
        # A blob id is a hash of the document, not the document.
        "blob_id": "sha256:" + "a" * 64,
        "schema_identity": "invoice@3",
        "status": RunStatus.SUCCEEDED,
        "processing_id": "sha256:" + "b" * 64,
        "stage_outcomes": (
            StageOutcomeRecord(
                stage="extraction",
                status="executed",
                artifact_id="sha256:" + "c" * 64,
                duration_ms=12,
            ),
        ),
        "created_at": AT,
        "updated_at": AT,
        "expires_at": AT,
    }
    fields.update(overrides)
    return Run(**fields)


def test_a_succeeded_run_reports_identities_and_nothing_else() -> None:
    run = _run()

    payload = payload_for(run, delivery_id=uuid4())

    assert payload["run_id"] == str(run.run_id)
    assert payload["status"] == "succeeded"
    assert payload["processing_id"] == run.processing_id
    # Not the tenant. It is not secret to its owner, but a delivery arrives at a
    # server the deployment does not control, and `Run.dump_public` already
    # excludes it for the same reason.
    assert "tenant_id" not in payload
    assert "stage_outcomes" not in payload
    assert "blob_id" not in payload


def test_a_failed_run_reports_the_stage_and_the_error_class() -> None:
    run = _run(
        status=RunStatus.FAILED,
        processing_id=None,
        failed_stage="extraction",
        error_class="ProviderRefusedError",
    )

    payload = payload_for(run, delivery_id=uuid4())

    assert payload["failed_stage"] == "extraction"
    assert payload["error_class"] == "ProviderRefusedError"
    assert "processing_id" not in payload, "absent rather than null where it does not apply"


def test_nothing_seeded_into_the_run_reaches_the_payload() -> None:
    """The whole requirement, as one assertion over the serialised bytes."""
    run = _run(
        status=RunStatus.FAILED,
        processing_id=None,
        failed_stage="extraction",
        # The worst case: a class name is the one free-text-ish field, and a
        # careless implementation upstream could put a message in it.
        error_class="ProviderRefusedError",
        request_id=SEEDED[0],
        idempotency_key=SEEDED[1],
    )

    body = json.dumps(payload_for(run, delivery_id=uuid4()))

    for seeded in SEEDED:
        assert seeded not in body, f"{seeded!r} left the deployment in a delivery payload"


def test_the_delivery_id_is_in_the_payload_and_is_what_was_passed() -> None:
    """FR-056: the receiver deduplicates on this, so it has to be *in* the body.

    A receiver that could only read it from a header would lose it the moment
    anybody put a proxy in front of them.
    """
    delivery_id = uuid4()

    payload = payload_for(_run(), delivery_id=delivery_id)

    assert payload["delivery_id"] == str(delivery_id)


def test_the_seed_check_can_actually_fail() -> None:
    """Guards the guard: a serialisation that stopped including anything would
    pass the assertion above vacuously."""
    body = json.dumps(payload_for(_run(), delivery_id=uuid4()))

    assert "succeeded" in body, "the payload is not empty, so the sweep above means something"
