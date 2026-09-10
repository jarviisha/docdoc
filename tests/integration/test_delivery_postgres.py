"""T120-T123 — delivery against a real receiver and a real database.

**Four tasks in one file, deliberately**, and the same consolidation Phases 3-6
already made: every one of these needs the same fixture — a migrated database, a
registered callback, a finished run, and an HTTP server on a real port — and four
files would be four copies of it, drifting apart at the first change to any of
them. The task each block answers is named on the block.

Nothing here mocks the transport. `tests/support/webhook_receiver.py` is
`http.server` on a loopback port, because what is under test is what happens on
the wire: which bytes were signed, whether a redirect is followed, and whether a
receiver that never answers costs anything but its own timeout.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from tests.infra import require_database
from tests.support.webhook_receiver import WebhookReceiver

from docdoc.runs import migrations
from docdoc.runs.delivery import DeliveryState, PostgresDeliverer, SecretBook
from docdoc.runs.identity import new_credential_id, new_run_id
from docdoc.runs.model import RunOutcome, RunStatus
from docdoc.runs.postgres import PostgresRunQueue
from docdoc.runs.principal import digest_of

pytestmark = pytest.mark.postgres

SECRET = "whsec_the-one-the-operator-configured"
TENANT = "acme"
OTHER = "globex"


@dataclass
class Spec:
    tenant_id: str = TENANT
    blob_id: str = "sha256:" + "a" * 64
    schema_identity: str = "invoice@1"
    request_id: str | None = None
    idempotency_key: str | None = None
    priority: int = 0
    callback_id: object = None


@pytest.fixture
def queue() -> PostgresRunQueue:
    psycopg = pytest.importorskip("psycopg")
    dsn = require_database()
    with psycopg.connect(dsn, autocommit=True) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        connection.execute("TRUNCATE runs, deliveries, callbacks")
    return PostgresRunQueue(lambda: psycopg.connect(dsn))


@pytest.fixture
def deliverer(queue: PostgresRunQueue) -> PostgresDeliverer:
    return PostgresDeliverer(
        execute=queue._execute,
        secrets=SecretBook({digest_of(SECRET): SECRET}),
        max_attempts=3,
        timeout_seconds=5,
        backoff_seconds=30,
        # The receiver is `http` on loopback, which is precisely what the
        # destination policy refuses. Setting this here rather than reaching past
        # the policy is the point: a test that bypassed the guard would prove
        # nothing about the guard.
        allow_private=True,
    )


def _finished_run(
    queue: PostgresRunQueue,
    deliverer: PostgresDeliverer,
    url: str,
    *,
    tenant_id: str = TENANT,
    status: RunStatus = RunStatus.SUCCEEDED,
):
    """Register a callback, submit against it, and finish — the whole cycle."""
    now = datetime.now(UTC)
    callback = deliverer.register(
        tenant_id=tenant_id,
        url=url,
        secret=SECRET,
        at=now,
        callback_id=new_credential_id(),
    )
    run = queue.submit(
        Spec(tenant_id=tenant_id, callback_id=callback.callback_id),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=now,
        expires_at=now + timedelta(days=30),
    )
    outcome = (
        RunOutcome(status=RunStatus.SUCCEEDED, processing_id="sha256:" + "b" * 64)
        if status is RunStatus.SUCCEEDED
        else RunOutcome(status=status, failed_stage="extraction", error_class="ProviderError")
    )
    queue.finish(run.run_id, outcome, now=now)
    return run, callback


# -- T120: signed, retried, and one identity across every attempt -------------


def test_one_delivery_is_signed_and_the_receiver_can_verify_it(
    queue: PostgresRunQueue, deliverer: PostgresDeliverer
) -> None:
    """SC-011. Verified by the receiver's own implementation of the documented rule."""
    with WebhookReceiver() as receiver:
        run, _ = _finished_run(queue, deliverer, receiver.url)

        due = deliverer.due(at=datetime.now(UTC), limit=10)
        assert len(due) == 1, "finishing a run with a callback enqueues exactly one delivery"

        result = deliverer.attempt(due[0], at=datetime.now(UTC))

    assert result.state is DeliveryState.DELIVERED
    assert result.last_status == 200
    assert len(receiver.received) == 1

    got = receiver.received[0]
    assert got.verifies(SECRET), "the receiver could not verify what docdoc signed"
    assert got.payload["run_id"] == str(run.run_id)
    assert got.payload["status"] == "succeeded"


def test_a_failing_receiver_backs_off_and_comes_to_rest(
    queue: PostgresRunQueue, deliverer: PostgresDeliverer
) -> None:
    """SC-012, FR-057 — and the `delivery_id` is identical on every attempt.

    That last assertion is the one worth having. `delivery_id` is the value a
    receiver deduplicates on (FR-056), so an id regenerated per attempt would
    turn at-least-once *delivery* into at-least-once *processing* on their side —
    the one thing at-least-once is supposed to let them avoid.
    """
    with WebhookReceiver(status=503) as receiver:
        _finished_run(queue, deliverer, receiver.url)

        at = datetime.now(UTC)
        identities = set()
        states = []
        for _ in range(3):
            due = deliverer.due(at=at, limit=10)
            assert due, "a pending delivery whose next attempt has come is due"
            result = deliverer.attempt(due[0], at=at)
            identities.add(result.delivery_id)
            states.append(result.state)
            # Advance past the backoff the attempt just scheduled, rather than
            # sleeping: the schedule is arithmetic on an instant and this is what
            # `identity.backoff` taking `at` as a parameter buys.
            at = (result.next_attempt_at or at) + timedelta(seconds=1)

    assert len(identities) == 1, "the delivery identity moved between attempts"
    assert states[:-1] == [DeliveryState.PENDING] * (len(states) - 1)
    assert states[-1] is DeliveryState.FAILED, "it came to rest at the attempt limit"
    assert len(receiver.received) == 3, "one request per attempt and no more"


def test_the_backoff_grows(queue: PostgresRunQueue, deliverer: PostgresDeliverer) -> None:
    """A retry schedule that did not back off would be a receiver's outage
    amplified into a load test against them."""
    with WebhookReceiver(status=500) as receiver:
        _finished_run(queue, deliverer, receiver.url)

        at = datetime.now(UTC)
        first = deliverer.attempt(deliverer.due(at=at, limit=10)[0], at=at)
        second_at = (first.next_attempt_at or at) + timedelta(seconds=1)
        second = deliverer.attempt(deliverer.due(at=second_at, limit=10)[0], at=second_at)

    assert first.next_attempt_at is not None
    assert second.next_attempt_at is not None
    assert (second.next_attempt_at - second_at) > (first.next_attempt_at - at)


def test_a_redirect_is_not_followed(queue: PostgresRunQueue, deliverer: PostgresDeliverer) -> None:
    """FR-061. A public URL answering `302 → 169.254.169.254` is the other half
    of the attack the destination policy exists to stop."""
    with WebhookReceiver(status=302, location="http://169.254.169.254/latest/meta-data/") as r:
        _finished_run(queue, deliverer, r.url)

        at = datetime.now(UTC)
        result = deliverer.attempt(deliverer.due(at=at, limit=10)[0], at=at)

    assert result.state is DeliveryState.PENDING, "a 302 is not a delivery"
    assert result.last_status == 302
    assert len(r.received) == 1, "exactly one request; the Location was not followed"


# -- T121: a failing receiver touches no run and nobody else's delivery -------


def test_a_failed_delivery_changes_no_run_state(
    queue: PostgresRunQueue, deliverer: PostgresDeliverer
) -> None:
    """FR-063. The run succeeded; whether anybody was told is a separate fact."""
    with WebhookReceiver(status=500) as receiver:
        run, _ = _finished_run(queue, deliverer, receiver.url)

        at = datetime.now(UTC)
        for _ in range(3):
            due = deliverer.due(at=at, limit=10)
            if not due:
                break
            result = deliverer.attempt(due[0], at=at)
            at = (result.next_attempt_at or at) + timedelta(seconds=1)

    assert queue.get(run.run_id, TENANT).status is RunStatus.SUCCEEDED
    assert deliverer.for_run(run_id=run.run_id, tenant_id=TENANT).state is DeliveryState.FAILED


def test_one_tenants_broken_receiver_does_not_delay_anothers(
    queue: PostgresRunQueue, deliverer: PostgresDeliverer
) -> None:
    """FR-063's second half, measured as an outcome rather than as elapsed time.

    A stopwatch would answer a different question and would be flaky besides.
    What matters is that the working tenant's delivery *lands* in the same batch
    the broken one fails in — one bad receiver costs its own attempt and nothing
    else's.
    """
    with WebhookReceiver(status=500) as broken, WebhookReceiver() as working:
        _finished_run(queue, deliverer, broken.url, tenant_id=TENANT)
        good_run, _ = _finished_run(queue, deliverer, working.url, tenant_id=OTHER)

        at = datetime.now(UTC)
        for delivery in deliverer.due(at=at, limit=10):
            deliverer.attempt(delivery, at=at)

    assert deliverer.for_run(run_id=good_run.run_id, tenant_id=OTHER).state is (
        DeliveryState.DELIVERED
    )
    assert len(working.received) == 1


# -- T122: one delivery per run, by a constraint -----------------------------


def test_a_run_cannot_have_two_deliveries(
    queue: PostgresRunQueue, deliverer: PostgresDeliverer
) -> None:
    """FR-058 as a **constraint** rather than as worker discipline.

    `finish` is idempotent for a run already terminal, so the second call inserts
    nothing anyway; `ON CONFLICT (run_id) DO NOTHING` is what covers the case the
    idempotence does not — two workers concluding one run at the same instant.
    Asserted through `enqueue`, which is that insert with nothing else around it.
    """
    with WebhookReceiver() as receiver:
        run, callback = _finished_run(queue, deliverer, receiver.url)

        first = deliverer.for_run(run_id=run.run_id, tenant_id=TENANT)
        assert first is not None

        stored = queue.get(run.run_id, TENANT)
        again = deliverer.enqueue(stored, callback, at=datetime.now(UTC))

    assert again.delivery_id == first.delivery_id, "a second enqueue is the same delivery"

    rows = queue._execute(
        "SELECT count(*) AS n FROM deliveries WHERE run_id = %s", (run.run_id,), fetch="one"
    )
    assert rows["n"] == 1


# -- T123: polling remains the record ----------------------------------------


def test_a_lost_delivery_loses_no_information(
    queue: PostgresRunQueue, deliverer: PostgresDeliverer
) -> None:
    """FR-065, SC-012's other half.

    Delivery is a convenience, not a channel that carries anything polling
    cannot. A client whose receiver was down for a day reads exactly the same
    terminal state, the same `processing_id`, and the same failure class from
    `GET /v1/runs/{run_id}` afterwards.
    """
    with WebhookReceiver(status=500) as receiver:
        run, _ = _finished_run(queue, deliverer, receiver.url)

        at = datetime.now(UTC)
        for _ in range(3):
            due = deliverer.due(at=at, limit=10)
            if not due:
                break
            result = deliverer.attempt(due[0], at=at)
            at = (result.next_attempt_at or at) + timedelta(seconds=1)

    polled = queue.get(run.run_id, TENANT)
    assert polled.status is RunStatus.SUCCEEDED
    assert polled.processing_id == "sha256:" + "b" * 64

    delivery = deliverer.for_run(run_id=run.run_id, tenant_id=TENANT)
    assert delivery.state is DeliveryState.FAILED
    # A class name, never the receiver's body.
    assert delivery.last_error == "HTTPError"


def test_a_run_with_no_callback_enqueues_nothing(queue: PostgresRunQueue) -> None:
    """FR-064 at the database: no callback, no row, nothing for a tick to find."""
    now = datetime.now(UTC)
    run = queue.submit(
        Spec(),
        run_id=new_run_id(),
        now=now,
        expires_at=now + timedelta(days=30),  # type: ignore[arg-type]
    )
    queue.finish(
        run.run_id,
        RunOutcome(status=RunStatus.SUCCEEDED, processing_id="sha256:" + "c" * 64),
        now=now,
    )

    rows = queue._execute("SELECT count(*) AS n FROM deliveries", (), fetch="one")
    assert rows["n"] == 0


def test_a_registration_with_an_unconfigured_secret_is_refused(
    deliverer: PostgresDeliverer,
) -> None:
    """FR-055 is only true if every registration can be signed for.

    Refusing here is what prevents a callback that registers successfully, never
    delivers, and reports a configuration problem as six failed attempts per run.
    """
    from docdoc.runs.errors import DeliveryError

    with WebhookReceiver() as receiver, pytest.raises(DeliveryError):
        deliverer.register(
            tenant_id=TENANT,
            url=receiver.url,
            secret="whsec_never-configured-anywhere",
            at=datetime.now(UTC),
            callback_id=uuid4(),
        )
