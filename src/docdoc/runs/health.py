"""Liveness and readiness, as facts rather than as routes.

Both process types answer these, on the same terms (FR-053, FR-054), and only one
of them has FastAPI. So the *decision* lives here, below both front ends:
`docdoc.api.health` wraps it in two routes and `docdoc.runs.worker` serves it
from a standard-library HTTP server. Two transports, one answer — which is what
"on the same terms" has to mean if an orchestrator is to use one configuration
for both.

**Liveness touches nothing.** Not a simplification: a liveness probe that checks
a dependency turns a dependency outage into a restart loop, which is the standard
way to convert a degradation into an outage. It returns a constant, and the
constant is the whole implementation (FR-053).

**Readiness is strict, and that withdraws working capacity on purpose.** A
process that cannot reach the run-state database reports not ready even though
the synchronous routes would still serve every request correctly (FR-087). The
alternative — a per-capability readiness signal — asks a load balancer to route
by capability, which no orchestrator's readiness probe can express. One binary
signal that is sometimes pessimistic beats a richer one nothing can consume.

**A dependency this deployment does not have is not unmet.** A Milestone 8
install has no run-state database, and reporting it as missing would make every
existing deployment permanently unready on upgrade (SC-018). Only what was
configured is probed.

**Nothing is disclosed** (FR-058): no configuration value, no credential, no
tenant identifier, no count of anything stored. The failure body names the
*kind* of dependency — `run-state-database` — and never where it is or who
could not reach it.

**No provider and no billable parser is invoked** (FR-056). A readiness check
that spends money is worse than no readiness check, because it is charged per
probe interval per replica.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docdoc.artifacts.paths import PROBE_BLOB_ID
from docdoc.runs.identity import now as _now

if TYPE_CHECKING:
    from datetime import datetime

__all__ = [
    "CACHE_SECONDS",
    "DOCUMENT_STORE",
    "LIVENESS_PATH",
    "PROBE_BLOB_ID",
    "READINESS_PATH",
    "RUN_STATE_DATABASE",
    "Readiness",
    "liveness_body",
    "readiness_body",
]

LIVENESS_PATH = "/healthz"
READINESS_PATH = "/readyz"

#: The names a readiness failure may use. Kinds, not locations: an operator
#: reading `run-state-database` knows which component to look at, and a caller
#: who should not know the deployment's topology learns nothing they could not
#: have guessed from the documentation.
RUN_STATE_DATABASE = "run-state-database"
DOCUMENT_STORE = "document-store"

#: How long an outcome is reused. Readiness is polled by every load-balancer
#: target at a fixed interval, so an uncached check makes probe traffic scale
#: with fleet size against the one component already under stress (research R13).
#:
#: Two seconds, because that is short enough that a probe interval of five or ten
#: seconds still observes a real transition promptly, and long enough that a
#: fleet of thirty replicas is not thirty round trips a second.
CACHE_SECONDS = 2.0

#: Re-exported from `docdoc.artifacts.paths`, which owns it because the store is
#: what uses it as a key. Named here so a reader of the readiness check can see
#: what it probes with without leaving the module.


def liveness_body() -> dict[str, str]:
    """The constant. Both process types return exactly this."""
    return {"status": "alive"}


def readiness_body(unmet: tuple[str, ...]) -> dict[str, Any]:
    """Ready, or not ready with the dependency named (FR-055)."""
    if not unmet:
        return {"status": "ready"}
    return {"status": "not_ready", "unmet": list(unmet)}


class Readiness:
    """Whether this process can reach what it needs, cached briefly.

    Holds the queue and the blob store rather than re-resolving them, so the
    probe answers about the objects the process is actually using. A readiness
    check that built its own connection would be checking a different thing than
    the one serving requests, and would pass while the real one was broken.

    `None` for either dependency means "this deployment does not have one", not
    "it is broken".
    """

    def __init__(
        self,
        *,
        runs: Any = None,
        blobs: Any = None,
        cache_seconds: float = CACHE_SECONDS,
    ) -> None:
        self._runs = runs
        self._blobs = blobs
        self._cache_seconds = cache_seconds
        self._at: datetime | None = None
        self._unmet: tuple[str, ...] = ()

    def unmet(self, *, now: datetime | None = None) -> tuple[str, ...]:
        """The dependencies this process cannot reach, or an empty tuple.

        `now` is a parameter for the reason every other instant in this layer is
        one: it makes the cache's behaviour testable without sleeping. Unlike the
        queue's methods this one has a default, because a probe has no caller who
        already holds an instant to pass down.
        """
        instant = _now() if now is None else now
        if self._at is not None and (instant - self._at).total_seconds() < self._cache_seconds:
            return self._unmet

        self._unmet = self._measure()
        self._at = instant
        return self._unmet

    @property
    def is_ready(self) -> bool:
        return not self.unmet()

    def _measure(self) -> tuple[str, ...]:
        """One round trip per configured dependency, and nothing else.

        Deliberately not concurrent. Two probes on two threads to save a few
        milliseconds on a call that is cached for two seconds would add the only
        thread this process did not already need.
        """
        unmet: list[str] = []
        if self._runs is not None and not _reaches(self._runs.ping):
            unmet.append(RUN_STATE_DATABASE)
        if self._blobs is not None and not _reaches(lambda: self._blobs.probe()):
            unmet.append(DOCUMENT_STORE)
        return tuple(unmet)


def _reaches(probe: Any) -> bool:
    """Whether one dependency answered, swallowing whatever it raised.

    Broad on purpose, and for the same reason `PostgresRunQueue._execute` is:
    every way a dependency can be unreachable is a way this must answer "no",
    and enumerating them here would make this module wrong on a driver's next
    release. The exception is not re-raised and not logged with its message —
    a driver's message can name a host, a database, and sometimes a password.
    """
    try:
        probe()
    except Exception:
        return False
    return True
