"""`_requested_callback` — unknown, malformed, and another tenant's are one answer.

The submission route turns anything but a real callback into a single `404`.
That is the same refusal `GET /v1/runs/{run_id}` makes, for the same reason: a
distinguishable answer would tell a caller which callback identifiers exist
under somebody else's key.

Untested, and the tenant scope is the part that matters. It is applied **in the
query** rather than checked after the fetch — a caller cannot forget a check
that does not exist — so the thing to verify is that the tenant reaches the
lookup at all, which is what a stub deliverer can say and a live one cannot.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from docdoc.api.app import _UNKNOWN_CALLBACK, _Deployment, _requested_callback


class _Deliverer:
    """Answers for one tenant's one callback, and records what it was asked."""

    def __init__(self, known: UUID, tenant_id: str = "acme"):
        self._known = known
        self._tenant = tenant_id
        self.asked: list[tuple[UUID, str]] = []

    def callback(self, callback_id: UUID, *, tenant_id: str):
        self.asked.append((callback_id, tenant_id))
        if callback_id == self._known and tenant_id == self._tenant:
            return object()
        return None


def _deployment(deliverer: object | None) -> _Deployment:
    return _Deployment(deliverer=deliverer)


def test_no_callback_requested_is_none_and_asks_nothing() -> None:
    """The Milestone 9 submission. It must not reach a deliverer at all."""
    deliverer = _Deliverer(uuid4())

    assert _requested_callback(_deployment(deliverer), None, tenant_id="acme") is None
    assert deliverer.asked == []


def test_a_registered_callback_comes_back_as_its_identity() -> None:
    known = uuid4()

    assert (
        _requested_callback(_deployment(_Deliverer(known)), str(known), tenant_id="acme") == known
    )


def test_another_tenants_callback_is_indistinguishable_from_an_unknown_one() -> None:
    """Tenant-scoped in the query. The two must produce the same value here, or
    the route can produce two different `404`s."""
    known = uuid4()
    deliverer = _Deliverer(known)

    theirs = _requested_callback(_deployment(deliverer), str(known), tenant_id="globex")
    absent = _requested_callback(_deployment(deliverer), str(uuid4()), tenant_id="acme")

    assert theirs is absent is _UNKNOWN_CALLBACK


def test_a_malformed_identifier_is_the_same_answer_and_asks_nothing() -> None:
    """Naming which identifiers are well-formed enough to exist is a smaller
    leak than the one above and the same kind."""
    deliverer = _Deliverer(uuid4())

    assert _requested_callback(_deployment(deliverer), "not-a-uuid", tenant_id="acme") is (
        _UNKNOWN_CALLBACK
    )
    assert deliverer.asked == []


def test_a_deployment_that_delivers_nothing_refuses_the_request() -> None:
    """FR-064. Accepting a `callback_id` here would create a run whose caller
    believes they will be notified and who never will be."""
    assert _requested_callback(_deployment(None), str(uuid4()), tenant_id="acme") is (
        _UNKNOWN_CALLBACK
    )
