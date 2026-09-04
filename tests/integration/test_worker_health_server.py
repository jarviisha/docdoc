"""US4/AC3 — the worker answers a probe, on the same terms as the API.

"**Given** a worker process, **When** it is probed, **Then** it answers on both
the same terms as the API, so one orchestrator configuration covers both process
types." That is FR-053 and FR-054's "both process types", and until this file
existed it was verified for one of them: every health assertion covered the API's
routes or the shared `Readiness` object, and nothing in the suite so much as
mentioned `_HealthServer`.

That is fifty lines of standard-library HTTP handling that `compose.yml` runs with
`--health-port 8000` and that the container healthcheck probes, with no test. The
failure modes are the expensive kind and both are silent: an orchestrator killing
a healthy worker, or leaving a dead one in rotation.

**The assertion that matters is byte-identity with the API**, not "it returns
200". "One orchestrator configuration covers both" is a claim about the *bodies*
and the *statuses* being the same, and comparing them is the only check that
tests it. Two servers that each answer sensibly in their own way would satisfy
every weaker assertion and fail the requirement.

The server binds port `0` — any free one — and reports back through
`bound_port`. Hard-coding a port would race whatever else on the machine wanted
it, which is a flake that arrives on a busy CI runner and nowhere else.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest

pytest.importorskip("fastapi", reason="the API half of the comparison needs docdoc[api]")

from fastapi.testclient import TestClient

from docdoc.api.app import _Deployment, build_app
from docdoc.runs.health import LIVENESS_PATH, READINESS_PATH, Readiness
from docdoc.runs.worker import _HealthServer

TIMEOUT_S = 5


class _Queue:
    """A run store that is reachable, or is not, on request."""

    def __init__(self, *, reachable: bool = True) -> None:
        self.reachable = reachable

    def ping(self) -> None:
        if not self.reachable:
            raise RuntimeError("the database is down")


def _serve(readiness: Readiness) -> Iterator[_HealthServer]:
    server = _HealthServer(readiness, port=0, host="127.0.0.1")
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def healthy() -> Iterator[_HealthServer]:
    """A worker whose dependencies all answer."""
    yield from _serve(Readiness(runs=_Queue()))


@pytest.fixture
def unhealthy() -> Iterator[_HealthServer]:
    """A worker that cannot reach its run-state database."""
    yield from _serve(Readiness(runs=_Queue(reachable=False)))


def _get(server: _HealthServer, path: str) -> tuple[int, str]:
    """One request against the worker, returning ``(status, body)``.

    `urllib` rather than a client package: the worker runs on a base install plus
    `docdoc[postgres]`, and a test that needed something else installed to probe
    it would be testing a configuration nobody deploys.
    """
    url = f"http://127.0.0.1:{server.bound_port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_S) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as refused:
        return refused.code, refused.read().decode("utf-8")


def _api(*, reachable: bool) -> TestClient:
    """The same two routes, served by the other process type."""
    return TestClient(build_app(_Deployment(runs=_Queue(reachable=reachable))))


# -- it answers at all ---------------------------------------------------------


def test_the_worker_answers_liveness(healthy: _HealthServer) -> None:
    """FR-053. The route that touches nothing, on the process that has no FastAPI."""
    status, body = _get(healthy, LIVENESS_PATH)

    assert status == 200
    assert json.loads(body) == {"status": "alive"}


def test_the_worker_answers_readiness(healthy: _HealthServer) -> None:
    status, body = _get(healthy, READINESS_PATH)

    assert status == 200
    assert json.loads(body) == {"status": "ready"}


def test_the_worker_reports_not_ready_and_names_the_dependency(
    unhealthy: _HealthServer,
) -> None:
    """FR-055 on the worker: a failure that does not say what is unmet is wasted."""
    status, body = _get(unhealthy, READINESS_PATH)

    assert status == 503
    assert json.loads(body) == {"status": "not_ready", "unmet": ["run-state-database"]}


def test_liveness_still_answers_when_readiness_does_not(
    unhealthy: _HealthServer,
) -> None:
    """The distinction that keeps a dependency outage from becoming a restart loop.

    The container runtime probes liveness and the orchestrator probes readiness.
    If both failed together, a database outage would restart every worker in the
    fleet for a fault none of them has.
    """
    assert _get(unhealthy, LIVENESS_PATH)[0] == 200
    assert _get(unhealthy, READINESS_PATH)[0] == 503


# -- on the same terms as the API ----------------------------------------------


@pytest.mark.parametrize("path", [LIVENESS_PATH, READINESS_PATH])
def test_the_two_process_types_answer_identically_when_healthy(
    healthy: _HealthServer, path: str
) -> None:
    """US4/AC3, in the only form that actually tests it.

    Byte-identical bodies and equal statuses. "One orchestrator configuration
    covers both process types" is a claim about these two answers being the same
    answer — two servers each replying sensibly in their own dialect would pass
    every weaker assertion and fail the requirement.
    """
    worker_status, worker_body = _get(healthy, path)
    api = _api(reachable=True).get(path)

    assert worker_status == api.status_code
    assert json.loads(worker_body) == api.json()


@pytest.mark.parametrize("path", [LIVENESS_PATH, READINESS_PATH])
def test_the_two_process_types_answer_identically_when_the_database_is_down(
    unhealthy: _HealthServer, path: str
) -> None:
    """The same, for the case an orchestrator actually acts on.

    Agreeing while everything works is easy. The answer that matters is the one
    given during an outage, because that is the one that takes a node out of
    rotation.
    """
    worker_status, worker_body = _get(unhealthy, path)
    api = _api(reachable=False).get(path)

    assert worker_status == api.status_code
    assert json.loads(worker_body) == api.json()


# -- and nothing else ----------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/", "/v1/schemas", "/v1/documents", "/metrics", "/healthz/", "/../etc/passwd"]
)
def test_the_worker_serves_nothing_but_the_two_probes(healthy: _HealthServer, path: str) -> None:
    """A worker serves no API, and this port is reachable by whoever can reach it.

    `/healthz/` with a trailing slash is in the list on purpose: it is the
    likeliest near-miss, and answering it would mean the exemption set is a prefix
    match rather than the exact one `docdoc.api.app` also relies on.
    """
    status, body = _get(healthy, path)

    assert status == 404
    assert json.loads(body) == {"status": "not_found"}, (
        "the 404 body describes something. This port is reachable by anyone who "
        "can reach the worker and should name nothing that exists"
    )


def test_the_worker_discloses_no_configuration(unhealthy: _HealthServer) -> None:
    """FR-058 on the worker, which is the process holding the database DSN.

    The API's version of this is asserted in `test_health_discloses_nothing.py`
    against the shared `Readiness`. This asserts it of the *transport*: whatever
    the worker serialises must carry no host, no credential, and no path.
    """
    _, body = _get(unhealthy, READINESS_PATH)

    for leaked in ("postgres", "password", "@", "://", "/var", "docdoc:"):
        assert leaked not in body, f"the worker's readiness body carries {leaked!r}"


# -- the lifecycle -------------------------------------------------------------


def test_the_server_stops_when_the_worker_does() -> None:
    """A daemon thread that outlived its worker would hold the port on restart.

    Asserted by connecting after `stop()`: the socket must be gone, not merely
    unreferenced.
    """
    server = _HealthServer(Readiness(runs=_Queue()), port=0, host="127.0.0.1")
    server.start()
    port = server.bound_port
    assert _get(server, LIVENESS_PATH)[0] == 200

    server.stop()

    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(f"http://127.0.0.1:{port}{LIVENESS_PATH}", timeout=TIMEOUT_S)


def test_a_worker_given_no_port_serves_nothing(tmp_path: Any) -> None:
    """Off unless asked for, which is the honest default.

    A worker behind no load balancer has nobody to answer, and binding a port
    nobody requested is how a host network collides.
    """
    from tests.fixtures.run_queue import InMemoryRunQueue

    from docdoc.runs.worker import Worker

    worker = Worker(
        queue=InMemoryRunQueue(),
        blobs=None,  # type: ignore[arg-type]
        store=None,  # type: ignore[arg-type]
        registry=None,
        adapter=None,
        worker_id="w1",
    )
    worker.stop()  # so `run_forever` returns immediately
    worker.run_forever()

    assert worker._health is None, (
        "a worker with no --health-port bound one anyway; the flag's absence is "
        "supposed to mean the server does not exist"
    )
