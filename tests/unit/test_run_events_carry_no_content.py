"""FR-092 and FR-093 — what a `run.transition` event may contain, and may not.

Two rules, and they exclude different things for different reasons.

**No content** (FR-093). No document text, no extracted value, no claimed text,
no prompt body, no credential, no provider message. The same rule every other
`observe.py` in this project follows, and the reason it is a rule rather than a
habit: a log line is the single most likely place for a customer's invoice total
to end up somewhere it was never meant to be.

**No summary** (FR-092, R10a). No duration, no token count, no cost, no stage
result. This one is not about disclosure at all — `pipeline/observe.py` refuses a
run-level event on the grounds that "a fifth event summarising the four would be
a second place where the cost of a run is stated, and the two would eventually
disagree". A transition event is not a summary and must not become one, and the
way it stays not-one is that the payload is a closed set of keys.

So the strongest assertion available is on the *shape*: the event's keys are
exactly seven, and anything else — however harmless it looks — fails here.
"""

from __future__ import annotations

import json
import logging
from uuid import uuid4

import pytest

from docdoc.runs.observe import EVENT_NAME, log_transition

#: Distinctive strings standing in for each category FR-093 forbids. A run seeded
#: with these makes the sweep a measurement rather than an inspection.
SEEDED = {
    "document text": "Acme Corporation Invoice INV-88213",
    "extracted value": "1420.00",
    "claimed text": "Total due: 1,420.00 EUR",
    "prompt body": "You are an extraction system. Return JSON matching",
    "credential": "sk-live-Zq7NEVERLOGTHIS",
    "provider message": "InvalidArgument: request contains 'Acme Corporation'",
}

#: FR-092's payload, exactly. `event` is the name; the other seven are the fields
#: the requirement lists. Written out so a field added later fails here and is
#: therefore a decision.
PERMITTED_KEYS = {
    "event",
    "run_id",
    "tenant_id",
    "from_state",
    "to_state",
    "attempts",
    "worker_id",
    "reason",
}


def _emit(caplog: pytest.LogCaptureFixture, **overrides: object) -> dict:
    fields: dict = {
        "run_id": uuid4(),
        "tenant_id": "acme",
        "from_state": "running",
        "to_state": "failed",
        "attempts": 2,
        "worker_id": "host-7:4213",
        "reason": "ProviderError",
    }
    fields.update(overrides)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="docdoc.runs"):
        log_transition(**fields)  # type: ignore[arg-type]
    assert caplog.records, "no event was emitted"
    return dict(json.loads(caplog.records[-1].getMessage()))


def test_the_payload_is_exactly_the_seven_fields_and_the_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FR-092 as a closed set, which is the only form that catches an addition."""
    payload = _emit(caplog)

    assert set(payload) == PERMITTED_KEYS, (
        f"unexpected: {sorted(set(payload) - PERMITTED_KEYS)}; "
        f"missing: {sorted(PERMITTED_KEYS - set(payload))}"
    )
    assert payload["event"] == EVENT_NAME


def test_no_seeded_content_reaches_the_event(caplog: pytest.LogCaptureFixture) -> None:
    """FR-093, over a run seeded with a distinctive string of each kind.

    Every seeded value is offered to the emitter as a `reason`, which is the one
    free-text-ish field and therefore the plausible route in. It travels, because
    `reason` is a class name by contract — so the assertion is that the *other*
    categories never appear, whatever a caller does with that one.
    """
    payload = _emit(caplog)
    serialised = json.dumps(payload)

    for category, value in SEEDED.items():
        assert value not in serialised, f"the event carried {category}: {value!r}"


def test_the_reason_field_cannot_smuggle_a_document(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The honest limit of the rule, stated rather than assumed.

    `reason` is a class name or a short constant *by contract* — the emitter
    cannot enforce that, and pretending otherwise would be a false guarantee. So
    this pins the contract at the call site that carries a value from outside:
    the terminal transition passes `outcome.error_class`, which `PipelineResult`
    has already reduced to a class name, or the constant `"completed"`.

    **It moved once and this test is why it was noticed.** The emission used to
    live in `worker._finish`; convergence moved it into the queue, where the
    transition actually happens, and this assertion failed on the old path
    immediately.

    **It moved a second time, and the assertion needed re-aiming rather than
    reverting.** This searched both queues' source for the literal text
    ``reason=outcome.error_class or``, which was the right check while that
    expression was duplicated in two modules. It is now one function,
    ``observe.reason_for``, and both queues call it — so the property is
    checkable directly instead of by grep, and a grep for a spelling would have
    failed on a refactor that made the thing it guards *more* true.

    Asserted over every terminal outcome shape, because the case that motivated
    the extraction was one this grep could never have caught: a cancelled run
    carries no ``error_class``, so ``error_class or "completed"`` labelled it a
    completion, and the literal text was present and correct throughout.
    """
    from docdoc.runs.model import RunOutcome, RunStatus
    from docdoc.runs.observe import REASONS, reason_for
    from tests.fixtures import run_queue

    # A message, of the kind that can quote a document. If one of these ever
    # reaches `reason`, FR-037's rule is broken.
    smuggled = "ValueError: could not parse 'Acme Corp invoice 4471'"

    succeeded = RunOutcome(status=RunStatus.SUCCEEDED, processing_id="sha256:" + "a" * 64)
    assert reason_for(succeeded) in REASONS
    assert reason_for(RunOutcome(status=RunStatus.CANCELLED)) in REASONS
    assert reason_for(RunOutcome(status=RunStatus.CANCELLED)) != "completed", (
        "a cancellation is reported as a completion. Nothing refused anything, "
        "so there is no `error_class` to name, and `error_class or 'completed'` "
        "made deliberate stops indistinguishable from successes in the one place "
        "built to make a run's history legible"
    )

    # A failure names its class and nothing else. The value passed through is
    # whatever `PipelineResult` reduced to a class name; this pins that the
    # function does not decorate, prefix, or explain it.
    named = reason_for(RunOutcome(status=RunStatus.FAILED, error_class="SchemaNotFound"))
    assert named == "SchemaNotFound"
    assert smuggled not in named

    # Both implementations route through it, which is what stops the fake from
    # describing a transition differently from the real queue.
    for module in ("docdoc.runs.postgres", run_queue.__name__):
        import importlib
        import inspect

        source = inspect.getsource(importlib.import_module(module))
        assert "reason=reason_for(outcome)" in source, (
            f"{module} no longer routes `reason` through `observe.reason_for`. "
            f"Whatever it passes instead is now the thing that reaches a log "
            f"line, and FR-037's rule — the class name, never the message — has "
            f"to be re-argued for it"
        )


def test_no_duration_token_count_or_cost_appears(caplog: pytest.LogCaptureFixture) -> None:
    """FR-092's other half, and the one that is not about privacy.

    `pipeline/observe.py` already states each run's cost, exactly once. A second
    statement of it here would be a second source of truth for the same number,
    and the two would drift — which is the reason that module gives for refusing
    a run-level event in the first place (R10a).
    """
    payload = _emit(caplog)

    for forbidden in ("duration_ms", "duration", "tokens", "input_tokens", "cost", "usage"):
        assert forbidden not in payload, f"the event summarises the run: {forbidden}"


def test_no_stage_result_appears(caplog: pytest.LogCaptureFixture) -> None:
    """A transition is about the run's state, never about what a stage produced."""
    payload = _emit(caplog)

    for forbidden in ("stage", "stage_outcomes", "processing_id", "extraction", "validation"):
        assert forbidden not in payload


def test_the_attempt_count_is_a_number_and_not_a_history(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A list of previous attempts would be a summary wearing a counter's clothes."""
    payload = _emit(caplog, attempts=3)

    assert payload["attempts"] == 3
    assert isinstance(payload["attempts"], int)


def test_the_sweep_can_actually_fail() -> None:
    """Guards the guard: a substring search over a payload that never grows."""
    serialised = json.dumps({"reason": SEEDED["extracted value"]})

    assert any(value in serialised for value in SEEDED.values())


# -- coverage: every transition produces an event (T094) -----------------------
#
# The tests above pin what an event *contains*. None of them pins that a
# transition *produces* one, which is why T044a could be marked done with a
# single call site in the worker's terminal path — a claim, a lease expiry, an
# abandonment and a cancellation each changed a run's state and said nothing.
#
# These drive the in-memory queue, which mirrors the Postgres implementation's
# emissions exactly. Asserting against the fake is the point rather than a
# compromise: FR-092 is a claim about the *rule*, and a rule checked at arbitrary
# instants with no database is a rule every contributor can check.

from dataclasses import dataclass  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402

from docdoc.runs.identity import DEFAULT_LEASE, new_run_id  # noqa: E402
from docdoc.runs.model import DEFAULT_TENANT, RunOutcome, RunStatus  # noqa: E402
from tests.fixtures.run_queue import InMemoryRunQueue  # noqa: E402

AT = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


@dataclass
class _Spec:
    blob_id: str = "sha256:" + "d" * 64
    tenant_id: str = DEFAULT_TENANT
    schema_identity: str = "invoice@1"
    request_id: str | None = None
    idempotency_key: str | None = None


def _events(caplog: pytest.LogCaptureFixture) -> list[dict]:
    """Every `run.transition` payload emitted so far, decoded."""
    found = []
    for record in caplog.records:
        message = record.getMessage()
        if EVENT_NAME not in message:
            continue
        found.append(json.loads(message))
    return found


def _submit(queue: InMemoryRunQueue, **fields: object):
    return queue.submit(
        _Spec(**fields),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=AT,
        expires_at=AT + timedelta(days=30),
    )


def test_submission_emits_an_event_with_no_previous_state(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A run coming into existence is the one event with no `from_state`.

    `None` rather than a string like "absent", because there *was* no previous
    state — as against a previous state that happened to be called absent.
    """
    with caplog.at_level(logging.INFO, logger="docdoc.runs"):
        _submit(InMemoryRunQueue())

    (event,) = _events(caplog)
    assert event["from_state"] is None
    assert event["to_state"] == "queued"
    assert event["reason"] == "submitted"


def test_a_claim_emits_an_event_naming_the_state_it_came_from(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The transition that was silent, and the one an operator most needs."""
    queue = InMemoryRunQueue()
    _submit(queue)

    with caplog.at_level(logging.INFO, logger="docdoc.runs"):
        queue.claim(worker_id="w1", now=AT, lease=DEFAULT_LEASE, max_attempts=3)

    (event,) = _events(caplog)
    assert event["from_state"] == "queued"
    assert event["to_state"] == "running"
    assert event["reason"] == "claimed"
    assert event["worker_id"] == "w1"
    assert event["attempts"] == 1


def test_a_redelivery_is_distinguishable_from_a_first_claim(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`running → running` after a lapsed lease, and it must not read as `claimed`.

    Lease handoff between workers is the hardest thing in a four-process topology
    to debug. A log that reported a redelivery and a first claim identically
    would leave "why is this document being processed twice" unanswerable.
    """
    queue = InMemoryRunQueue()
    _submit(queue)
    queue.claim(worker_id="w1", now=AT, lease=DEFAULT_LEASE, max_attempts=3)
    later = AT + DEFAULT_LEASE + timedelta(seconds=1)

    with caplog.at_level(logging.INFO, logger="docdoc.runs"):
        queue.claim(worker_id="w2", now=later, lease=DEFAULT_LEASE, max_attempts=3)

    (event,) = _events(caplog)
    assert event["from_state"] == "running"
    assert event["to_state"] == "running"
    assert event["reason"] == "redelivered"
    assert event["worker_id"] == "w2"
    assert event["attempts"] == 2


def test_abandonment_emits_one_event_per_run(caplog: pytest.LogCaptureFixture) -> None:
    """Set-based in Postgres, and still one event each.

    A sweep that abandoned four runs abandoned four runs; an operator reading
    "a document is killing workers" needs to know which.
    """
    queue = InMemoryRunQueue()
    for _ in range(2):
        _submit(queue)

    at = AT
    for _ in range(3):
        while queue.claim(worker_id="w", now=at, lease=DEFAULT_LEASE, max_attempts=3):
            pass
        at = at + DEFAULT_LEASE + timedelta(seconds=1)

    with caplog.at_level(logging.INFO, logger="docdoc.runs"):
        queue.claim(worker_id="w", now=at, lease=DEFAULT_LEASE, max_attempts=3)

    abandoned = [e for e in _events(caplog) if e["reason"] == "RunAbandonedError"]
    assert len(abandoned) == 2, f"expected one event per abandoned run, got {len(abandoned)}"
    for event in abandoned:
        assert event["from_state"] == "running"
        assert event["to_state"] == "failed"


def test_release_emits_an_event_naming_the_worker_that_let_go(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FR-043's transition. `worker_id` is captured before it is cleared."""
    queue = InMemoryRunQueue()
    run = _submit(queue)
    queue.claim(worker_id="w1", now=AT, lease=DEFAULT_LEASE, max_attempts=3)

    with caplog.at_level(logging.INFO, logger="docdoc.runs"):
        queue.release(run.run_id, now=AT)

    (event,) = _events(caplog)
    assert event["from_state"] == "running"
    assert event["to_state"] == "queued"
    assert event["reason"] == "released"
    assert event["worker_id"] == "w1", (
        "the event named no worker, so 'which replica let this go' is unanswerable"
    )


def test_cancelling_a_queued_run_emits_exactly_one_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One transition, one event — not one for the request and one for the change."""
    queue = InMemoryRunQueue()
    run = _submit(queue)

    with caplog.at_level(logging.INFO, logger="docdoc.runs"):
        queue.cancel(run.run_id, DEFAULT_TENANT, now=AT)

    (event,) = _events(caplog)
    assert event["from_state"] == "queued"
    assert event["to_state"] == "cancelled"


def test_cancelling_a_running_run_is_logged_as_a_request_and_not_a_change(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FR-029 in the log, matching what the route reports.

    The run keeps reading `running` until the worker reaches a stage boundary, so
    the event says `running → running` with reason `cancel_requested`. An event
    claiming `cancelled` here would contradict both the response body and the
    run's own state.
    """
    queue = InMemoryRunQueue()
    run = _submit(queue)
    queue.claim(worker_id="w1", now=AT, lease=DEFAULT_LEASE, max_attempts=3)

    with caplog.at_level(logging.INFO, logger="docdoc.runs"):
        queue.cancel(run.run_id, DEFAULT_TENANT, now=AT)

    (event,) = _events(caplog)
    assert event["from_state"] == event["to_state"] == "running"
    assert event["reason"] == "cancel_requested"


def test_a_terminal_run_finishing_again_emits_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The redelivered attempt that loses the race must not look like a second
    conclusion. `finish` is a no-op on a terminal run, and a no-op transitions
    nothing."""
    queue = InMemoryRunQueue()
    run = _submit(queue)
    queue.claim(worker_id="w1", now=AT, lease=DEFAULT_LEASE, max_attempts=3)
    queue.finish(run.run_id, RunOutcome(status=RunStatus.FAILED, error_class="X"), now=AT)

    with caplog.at_level(logging.INFO, logger="docdoc.runs"):
        queue.finish(run.run_id, RunOutcome(status=RunStatus.FAILED, error_class="Y"), now=AT)

    assert _events(caplog) == []


def test_an_idempotent_replay_emits_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """A retrying client is not a queue filling up."""
    queue = InMemoryRunQueue()
    _submit(queue, idempotency_key="retry-1")

    with caplog.at_level(logging.INFO, logger="docdoc.runs"):
        _submit(queue, idempotency_key="retry-1")

    assert _events(caplog) == []


def test_every_reason_is_a_class_name_or_a_named_constant(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`reason` is the one free-form field, so its vocabulary is closed.

    Either one of `REASONS` or an error class name — never a sentence, because a
    sentence can quote the document (FR-093).
    """
    from docdoc.runs.observe import REASONS

    queue = InMemoryRunQueue()
    with caplog.at_level(logging.INFO, logger="docdoc.runs"):
        run = _submit(queue)
        queue.claim(worker_id="w1", now=AT, lease=DEFAULT_LEASE, max_attempts=3)
        queue.release(run.run_id, now=AT)
        queue.claim(worker_id="w1", now=AT, lease=DEFAULT_LEASE, max_attempts=3)
        queue.finish(
            run.run_id, RunOutcome(status=RunStatus.FAILED, error_class="ProviderError"), now=AT
        )

    reasons = [e["reason"] for e in _events(caplog)]
    assert reasons, "no events emitted; this check is vacuous"
    for reason in reasons:
        assert reason in REASONS or reason.isidentifier(), (
            f"{reason!r} is neither one of REASONS nor a class name. A free-form "
            f"reason is a sentence, and a sentence can quote a document"
        )
    assert " " not in "".join(reasons)
