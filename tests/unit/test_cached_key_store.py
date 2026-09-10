"""`CachedKeyStore` — a hit is cached, a miss is not, and that is the promise.

**The asymmetry is the whole design and none of it was tested.** Issuance takes
effect immediately; revocation takes up to the cache lifetime. That is safe in
exactly one direction, and FR-028 states the direction rather than leaving it to
be discovered — but nothing checked it, so a version that also cached misses
would have passed every test in the repository while making a newly issued key
fail for up to a lifetime.

The three delegating verbs are here for a smaller reason and a real one:
`revoke` clears the *whole* cache, because this store never learned the digest
of the credential it just revoked. A version that dropped one entry would look
correct and evict nothing.

`monotonic_ms` is a parameter, so none of this waits on a real clock.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from docdoc.runs.keys import CachedKeyStore
from docdoc.runs.principal import Principal

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)

ACME = Principal(tenant_id="acme", scopes=frozenset())


class _Inner:
    """A key store that counts every question it was asked."""

    def __init__(self, principal: Principal | None = ACME):
        self._principal = principal
        self.resolutions = 0
        self.touched: list[str] = []
        self.issued: list[str] = []
        self.revoked: list[UUID] = []
        self.listed: list[str] = []

    def resolve(self, digest: str) -> Principal | None:
        self.resolutions += 1
        return self._principal

    def touch(self, digest: str, *, now: datetime) -> None:
        self.touched.append(digest)

    def issue(self, *, tenant_id: str, scopes: frozenset[str], label: str | None, now: datetime):
        self.issued.append(tenant_id)
        return "plaintext", object()

    def revoke(self, credential_id: UUID, *, now: datetime) -> bool:
        self.revoked.append(credential_id)
        return True

    def list_for(self, tenant_id: str):
        self.listed.append(tenant_id)
        return ()


def _clock(readings: list[float]):
    return lambda: readings.pop(0) if len(readings) > 1 else readings[0]


def _store(inner: Any, *, readings: list[float] | None = None, ttl: int = 30) -> CachedKeyStore:
    return CachedKeyStore(
        inner=inner,
        ttl_seconds=ttl,
        monotonic_ms=_clock(readings or [0.0]),
        now=lambda: NOW,
    )


# -- the asymmetry -----------------------------------------------------------


def test_a_hit_is_answered_from_the_cache() -> None:
    inner = _Inner()
    store = _store(inner)

    assert store.resolve("d") == ACME
    assert store.resolve("d") == ACME

    assert inner.resolutions == 1, "the second request went to the database"


def test_a_miss_is_not_cached_so_a_new_credential_works_at_once() -> None:
    """The direction that must be safe.

    Caching misses would make a key issued a second ago fail for up to a
    lifetime — the same defect this class exists to remove, pointing the other
    way.
    """
    inner = _Inner(principal=None)
    store = _store(inner)

    assert store.resolve("d") is None
    assert store.resolve("d") is None

    assert inner.resolutions == 2


def test_an_entry_is_asked_again_once_its_lifetime_is_up() -> None:
    """FR-028's bound. Revocation takes *up to* this and never longer."""
    inner = _Inner()
    store = _store(inner, readings=[0.0, 31_000.0], ttl=30)

    store.resolve("d")
    store.resolve("d")

    assert inner.resolutions == 2


# -- last_used_at ------------------------------------------------------------


def test_use_is_recorded_on_a_miss_and_not_on_every_request() -> None:
    """Otherwise the cache trades a read per request for a write per request,
    which is the cost it exists to avoid.

    The consequence is stated rather than hidden: `last_used_at` is accurate to
    within one lifetime per process. It answers "is this credential still in
    use before I revoke it", which a 30-second granularity answers perfectly.
    """
    inner = _Inner()
    store = _store(inner)

    store.resolve("d")
    store.resolve("d")

    assert inner.touched == ["d"]


def test_an_inner_store_that_cannot_record_use_is_not_required_to() -> None:
    """`touch` is optional, and a store without one still authenticates.

    `KeyRing` has no such method — it reads a file — so this is the shape the
    chained store actually resolves through on a deployment with no database.
    """

    class _NoTouch:
        def resolve(self, digest: str) -> Principal | None:
            return ACME

    assert _store(_NoTouch()).resolve("d") == ACME


def test_a_failed_bookkeeping_write_does_not_refuse_the_request() -> None:
    """A failure to record *when* a key was last used must never refuse a
    request that authenticated correctly."""

    class _Broken(_Inner):
        def touch(self, digest: str, *, now: datetime) -> None:
            raise RuntimeError("the write failed")

    assert _store(_Broken()).resolve("d") == ACME


# -- the three delegating verbs ----------------------------------------------


def test_revoking_clears_the_whole_cache() -> None:
    """Not one entry.

    This store never learned the digest of the credential it just revoked, and
    looking one up to evict it would mean holding an identifier-to-digest map
    that nothing else needs. So it drops everything, which is correct and cheap.
    """
    inner = _Inner()
    store = _store(inner)
    store.resolve("d")
    identity = uuid4()

    assert store.revoke(identity, now=NOW) is True
    assert inner.revoked == [identity]

    store.resolve("d")
    assert inner.resolutions == 2, "a revocation left a stale entry behind"


def test_forgetting_one_digest_leaves_the_others() -> None:
    """The courtesy the revocation route uses in the process that served it."""
    inner = _Inner()
    store = _store(inner)
    store.resolve("a")
    store.resolve("b")

    store.forget("a")
    store.resolve("a")
    store.resolve("b")

    assert inner.resolutions == 3


def test_issuing_and_listing_pass_straight_through() -> None:
    """Neither is cached: one creates a credential and the other is an operator
    reading a table, and a stale answer to either would be a wrong answer."""
    inner = _Inner()
    store = _store(inner)

    plaintext, _ = store.issue(tenant_id="acme", scopes=frozenset(), label=None, now=NOW)
    store.list_for("acme")

    assert plaintext == "plaintext"
    assert inner.issued == ["acme"]
    assert inner.listed == ["acme"]


def test_the_cache_holds_digests_and_principals_and_never_a_key() -> None:
    """Asserted on the structure rather than argued in a comment."""
    store = _store(_Inner())
    store.resolve("digest-only")

    assert list(store._entries) == ["digest-only"]
    assert all(isinstance(value[1], Principal) for value in store._entries.values())


@pytest.mark.parametrize("ttl", [1, 3600])
def test_the_lifetime_is_what_was_configured(ttl: int) -> None:
    inner = _Inner()
    store = _store(inner, readings=[0.0, ttl * 1000 - 1], ttl=ttl)

    store.resolve("d")
    store.resolve("d")

    assert inner.resolutions == 1
