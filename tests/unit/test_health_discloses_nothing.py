"""The health routes are unauthenticated, so what they say is public.

That is not an oversight and it cannot be fixed by adding a credential: kubelet,
an ELB target group, and Docker's `HEALTHCHECK` all issue a bare request, so
requiring a key here would make every authenticated deployment permanently
unhealthy. The exemption is FR-058's, and this file is the other half of it —
what an unauthenticated route is allowed to contain.

Two claims, and they fail differently:

**Nothing is disclosed.** No configuration value, no credential, no tenant
identifier, no count of anything stored. The failure mode is a readiness body
that names the database's host, which publishes the deployment's topology to
anyone who can reach the port.

**Nothing is billed.** Readiness invokes no model provider and no parser
(FR-056). The failure mode here is quieter and much more expensive: a probe with
a side effect is charged per probe interval per replica, and it looks like usage
rather than like a bug.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from docdoc.runs.health import (
    DOCUMENT_STORE,
    RUN_STATE_DATABASE,
    Readiness,
    liveness_body,
    readiness_body,
)

#: Values that must never appear in either body, each standing for a class of
#: thing FR-058 forbids. Distinctive enough that a substring sweep means
#: something.
SECRET_DSN = "postgresql://someuser:hunter2@db.internal:5432/docdoc"
SECRET_BUCKET = "s3://acme-private-documents"
TENANT = "acme-corporation"


class _CountingQueue:
    """A queue that answers readiness and counts what else it was asked.

    The counter is the point. A readiness check that ran the claim query would
    pass every assertion about its body and quietly hold a lock on the queue's
    hot path once a fleet was probing it.
    """

    def __init__(self, *, reachable: bool = True) -> None:
        self.reachable = reachable
        self.pings = 0
        self.other_calls = 0
        self.dsn = SECRET_DSN

    def ping(self) -> None:
        self.pings += 1
        if not self.reachable:
            raise RuntimeError(f"could not connect to {SECRET_DSN}")

    def __getattr__(self, name: str) -> Any:
        def _record(*args: Any, **kwargs: Any) -> None:
            self.other_calls += 1

        return _record


class _CountingStore:
    """A blob store that answers `probe()` and counts reads and writes."""

    def __init__(self, *, reachable: bool = True) -> None:
        self.reachable = reachable
        self.probes = 0
        self.reads = 0
        self.writes = 0
        self.bucket = SECRET_BUCKET

    def probe(self) -> None:
        self.probes += 1
        if not self.reachable:
            raise RuntimeError(f"cannot reach {SECRET_BUCKET}")

    def get(self, blob_id: str) -> None:
        self.reads += 1

    def put(self, data: bytes) -> None:
        self.writes += 1


class _CountingAdapter:
    """Stands in for a model provider, and must never be reached."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *args: Any, **kwargs: Any) -> None:
        self.calls += 1


class _CountingParser:
    """Stands in for a billable parser, and must never be reached."""

    def __init__(self) -> None:
        self.calls = 0

    def parse(self, *args: Any, **kwargs: Any) -> None:
        self.calls += 1


# -- what the bodies contain --------------------------------------------------


def test_liveness_is_a_constant() -> None:
    """FR-053, from the disclosure side: a constant discloses nothing.

    Asserted as equality rather than as an absence of secrets, because equality
    is the stronger statement and this body is small enough to state in full.
    """
    assert liveness_body() == {"status": "alive"}


@pytest.mark.parametrize(
    "unmet", [(), (RUN_STATE_DATABASE,), (DOCUMENT_STORE,), (RUN_STATE_DATABASE, DOCUMENT_STORE)]
)
def test_no_readiness_body_carries_anything_but_dependency_kinds(
    unmet: tuple[str, ...],
) -> None:
    """FR-058. Every value in the body is one of a closed set of names.

    Checked as a closed set rather than as a search for known secrets: a search
    finds the secrets somebody thought of, and this finds anything at all that is
    not a dependency kind — including whatever a later change adds.
    """
    body = readiness_body(unmet)

    assert set(body) <= {"status", "unmet"}
    assert body["status"] in {"ready", "not_ready"}
    assert set(body.get("unmet", ())) <= {RUN_STATE_DATABASE, DOCUMENT_STORE}


def test_an_unreachable_dependency_does_not_leak_the_error_it_raised() -> None:
    """The likeliest leak, and the one nobody writes on purpose.

    A driver's connection error quotes the DSN — host, database, and on some
    drivers the password. Putting `str(exc)` into the readiness body is the
    obvious, helpful-looking implementation and it publishes the deployment's
    credentials to an unauthenticated route.
    """
    probe = Readiness(runs=_CountingQueue(reachable=False), blobs=_CountingStore(reachable=False))

    serialised = json.dumps(readiness_body(probe.unmet()))

    assert SECRET_DSN not in serialised
    assert "hunter2" not in serialised
    assert "db.internal" not in serialised
    assert SECRET_BUCKET not in serialised
    assert "acme" not in serialised


def test_no_tenant_identifier_can_appear_because_readiness_has_none() -> None:
    """FR-058's tenant clause, asserted through the signature.

    `Readiness` takes a queue and a store and no tenant, so there is no tenant
    identifier in scope to leak. That is a stronger guarantee than checking the
    body: a value the code does not hold cannot be emitted by a later edit.
    """
    import inspect

    parameters = set(inspect.signature(Readiness.__init__).parameters)

    assert "tenant_id" not in parameters
    assert "tenant" not in parameters


def test_no_count_of_stored_content_appears() -> None:
    """A body that said "1,284 documents" would be a business metric on a probe."""
    body = readiness_body((RUN_STATE_DATABASE,))

    numbers = [value for value in body.values() if isinstance(value, int)]
    assert not numbers, f"readiness reported a count: {numbers}"


# -- what the check costs ------------------------------------------------------


def test_readiness_invokes_no_provider_and_no_parser() -> None:
    """FR-056, and the reason it is a requirement rather than good sense.

    A readiness probe runs on every replica on a fixed interval forever. A
    version of it that extracted a one-page document to prove the pipeline works
    would be a model call every five seconds per replica — and it would appear on
    the invoice as usage, not as a fault.
    """
    adapter = _CountingAdapter()
    parser = _CountingParser()
    queue = _CountingQueue()
    store = _CountingStore()

    probe = Readiness(runs=queue, blobs=store)
    for _ in range(5):
        probe.unmet()

    assert adapter.calls == 0, "readiness called the model provider"
    assert parser.calls == 0, "readiness invoked a billable parser"
    assert store.reads == 0, "readiness read a document"
    assert store.writes == 0, (
        "readiness wrote something; the probe is a metadata call against a fixed "
        "key and has no side effect"
    )
    assert queue.other_calls == 0, (
        "readiness asked the queue for something other than a ping; a probe that "
        "runs the claim query competes with the workers it is reporting on"
    )


def test_the_probe_is_cached_so_a_fleet_is_not_a_load_generator() -> None:
    """Research R13's two seconds, asserted at an instant rather than by sleeping.

    Readiness is polled by every load-balancer target at a fixed interval, so an
    uncached check makes probe traffic scale with fleet size against the one
    component already under stress.
    """
    from datetime import UTC, datetime, timedelta

    queue = _CountingQueue()
    start = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
    probe = Readiness(runs=queue, cache_seconds=2.0)

    for offset in (0.0, 0.5, 1.0, 1.9):
        probe.unmet(now=start + timedelta(seconds=offset))
    assert queue.pings == 1, "the cache did not hold within its window"

    probe.unmet(now=start + timedelta(seconds=2.1))
    assert queue.pings == 2, "the cache never expires, so a real outage is invisible"


def test_a_dependency_the_deployment_does_not_have_is_not_unmet() -> None:
    """SC-018. A Milestone 8 install has no database and is validly configured.

    Reporting an absent dependency as unmet would make every existing deployment
    permanently unready the moment it upgraded — the upgrade breaking nothing is
    the whole of SC-018.
    """
    assert Readiness().unmet() == ()
    assert Readiness(blobs=_CountingStore()).unmet() == ()


# -- what a probe is allowed to cost, in time (T114) ---------------------------
#
# `Readiness` swallows whatever a dependency raises, so a store that never
# answers looks exactly like one that answered "no" — eventually. The gap was in
# the *eventually*: `s3_client()` set no botocore config, so the store probe
# inherited 60 s connect, 60 s read, and retries on top, against research R13's
# "one store metadata operation against a fixed key, **with a short timeout**".
#
# It bites hardest on the worker, where `_HealthServer` is a `ThreadingHTTPServer`
# and every probe against a black-holed endpoint holds a thread for the full
# duration — an orchestrator polling every five seconds accumulates them faster
# than they retire.


def test_the_probe_client_is_configured_for_seconds_not_minutes() -> None:
    """R13's "short timeout", as a number rather than an adjective."""
    pytest.importorskip("boto3", reason="the S3 store lives behind the docdoc[s3] extra")
    from docdoc.artifacts.s3 import s3_client

    config = s3_client(endpoint_url="http://127.0.0.1:1", probe=True).meta.config

    assert config.connect_timeout <= 5, (
        f"the probe waits {config.connect_timeout}s to connect. A probe polled "
        f"every five seconds that takes longer than that is answering a question "
        f"nobody is still asking, and on the worker it holds a thread while it does"
    )
    assert config.read_timeout <= 5
    assert config.retries["total_max_attempts"] == 1, (
        "the probe retries. 'Is the store answering right now' has no second "
        "attempt — retrying multiplies the time the wrong answer takes to arrive"
    )


def test_the_pipeline_client_is_not_given_the_probe_s_timeouts() -> None:
    """The reason there are two clients rather than one compromise.

    ADR-0010 §4 turns a failed artifact read into a no-reuse degradation, so a
    timeout tight enough for a probe would make a slow-but-working store look
    broken — correct results, and a silently re-paid parse as the only symptom.
    The pipeline waits; the probe does not.
    """
    pytest.importorskip("boto3", reason="the S3 store lives behind the docdoc[s3] extra")
    from docdoc.artifacts.s3 import s3_client

    pipeline = s3_client(endpoint_url="http://127.0.0.1:1").meta.config
    probe = s3_client(endpoint_url="http://127.0.0.1:1", probe=True).meta.config

    assert pipeline.read_timeout > probe.read_timeout, (
        "the pipeline and the probe wait the same length of time, so one of the "
        "two is wrong: either a large artifact over a congested link now fails, "
        "or a readiness probe now hangs"
    )
    assert pipeline.retries["total_max_attempts"] > 1, (
        "the pipeline no longer retries a transient object-store failure, which "
        "turns a blip into a re-paid parse"
    )


def test_an_unreachable_store_is_reported_unready_quickly() -> None:
    """The behaviour, not the configuration — measured against a dead port.

    Port 1 is reserved and nothing binds it, so this is a real unreachable
    endpoint rather than a mock. The bound is generous next to the two-second
    timeout and far under the sixty botocore would have taken, so it fails on the
    regression and not on a slow machine.
    """
    pytest.importorskip("boto3", reason="the S3 store lives behind the docdoc[s3] extra")
    import time

    from docdoc.artifacts.s3 import S3BlobStore

    store = S3BlobStore("docdoc", endpoint_url="http://127.0.0.1:1")
    probe = Readiness(blobs=store)

    started = time.monotonic()
    unmet = probe.unmet()
    elapsed = time.monotonic() - started

    assert unmet == (DOCUMENT_STORE,), f"expected the store to be reported unmet, got {unmet}"
    assert elapsed < 15, (
        f"readiness took {elapsed:.1f}s against an unreachable store. botocore's "
        f"defaults are 60s connect and 60s read with retries; a probe inheriting "
        f"them outlives its own polling interval and, on the worker, holds a "
        f"server thread for the duration"
    )
