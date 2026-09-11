"""Credentials with a lifetime: issue, list, revoke, and a bounded cache.

Milestone 9 shipped authentication and left a defect in it, documented rather
than fixed: ``api/auth.py`` reads a key file **once, at startup**, so an operator
who deletes a compromised key sees no error, no warning, and no change — the
revoked key keeps working until the process restarts.

That was recorded as a limitation because `specs/009` FR-061 forbade a mutable
mapping *in that milestone*, and ``auth.py``'s docstring drew the consequence:
"a table invites exactly the endpoint the requirement forbids". This is the
milestone that wants the endpoint (ADR-0016).

**Three properties do the work here.**

*The plaintext exists once.* ``issue`` returns it as the first element of a pair
and nothing stores it; what goes in the table is ``sha256(key)``, the same
derivation ``auth.digest_of`` already uses. There is no route that returns it a
second time and none can be added without changing what "stored as a digest"
means.

*Revocation is an ``UPDATE``.* The row stays, the identifier is never reused, and
a revoked credential is never reissuable — "this key was revoked on the 4th" is
what an incident review needs and a missing row cannot say it.

*Propagation is a bounded cache lifetime, and the bound is the documentation.*
``CachedKeyStore`` holds resolutions for a configured number of seconds, so every
process observes a revocation within one lifetime with no restart, no file, and
no signal. A query per request would make every route depend on database
availability; ``LISTEN/NOTIFY`` is a TTL arrived at the long way, with a
connection per process and a reconnect path (research R11).

**Nothing here reads a clock.** ``now`` is a parameter and the cache takes its
monotonic source from `identity`, because that module is the only one in this
package permitted to reach an ambient one (FR-096a).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from docdoc.runs.errors import CredentialError
from docdoc.runs.principal import (
    ADMIN_SCOPE,
    AuthenticationError,
    Principal,
    digest_of,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from uuid import UUID

__all__ = [
    "DEFAULT_TTL_SECONDS",
    "CachedKeyStore",
    "ChainedKeyStore",
    "Credential",
    "KeyStore",
    "PostgresKeyStore",
]

#: How long a resolution is cached, and therefore **the propagation bound for a
#: revocation** (FR-028). Thirty seconds is short enough that "revoke and it
#: stops working" is true in the sense an operator means, and long enough that a
#: busy process is not querying per request.
#:
#: The operator documentation states this as a number rather than as
#: "immediately", because "immediately" is not a property anybody can test.
DEFAULT_TTL_SECONDS = 30


@dataclass(frozen=True)
class Credential:
    """An issued credential, as everything except the credential.

    No key and **no digest** (FR-030). A listing exists so an operator can see
    what they have issued and revoke one of them; either field would make that
    listing a way to obtain what it describes.
    """

    credential_id: UUID
    tenant_id: str
    scopes: frozenset[str] = frozenset()
    label: str | None = None
    created_at: datetime | None = None
    #: Best-effort and explicitly **not** transactional with the request it
    #: describes (ADR-0016). Exactness would put a write on every authenticated
    #: request; this exists for the listing, and the listing says so.
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None


class KeyStore(Protocol):
    """Resolution, and the three operations `KeyRing` cannot perform."""

    def resolve(self, digest: str) -> Principal | None:
        """The principal behind a digest, or ``None``.

        Takes a **digest** rather than a credential, so a store never holds a
        plaintext key even for the duration of a lookup, and returns ``None``
        rather than raising: an absent credential and a revoked one are the same
        answer here, and the single indistinguishable refusal is assembled one
        layer up by `AuthenticationError`.
        """
        ...

    def issue(
        self,
        *,
        tenant_id: str,
        scopes: frozenset[str],
        label: str | None,
        now: datetime,
    ) -> tuple[str, Credential]:
        """``(plaintext, credential)``. The plaintext exists exactly here."""
        ...

    def revoke(self, credential_id: UUID, *, now: datetime) -> bool:
        """``True`` if it was active. Idempotent: revoking twice is ``False``."""
        ...

    def list_for(self, tenant_id: str) -> tuple[Credential, ...]:
        """Every credential issued to a tenant, revoked ones included."""
        ...


@dataclass
class PostgresKeyStore:
    """The table, and the only place a plaintext credential is ever produced."""

    execute: Callable[..., Any]

    def resolve(self, digest: str) -> Principal | None:
        row = self.execute(
            "SELECT tenant_id, scopes FROM credentials WHERE digest = %s AND revoked_at IS NULL",
            (digest,),
            fetch="one",
        )
        if row is None:
            return None
        return Principal(tenant_id=row["tenant_id"], scopes=frozenset(row["scopes"] or ()))

    def issue(
        self,
        *,
        tenant_id: str,
        scopes: frozenset[str],
        label: str | None,
        now: datetime,
    ) -> tuple[str, Credential]:
        """Generate, store the digest, and hand the plaintext back once."""
        from docdoc.runs.identity import new_credential_id, new_key_secret

        secret = new_key_secret()
        credential_id = new_credential_id()
        self.execute(
            "INSERT INTO credentials "
            "(credential_id, tenant_id, digest, scopes, label, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (credential_id, tenant_id, digest_of(secret), sorted(scopes), label, now),
        )
        return secret, Credential(
            credential_id=credential_id,
            tenant_id=tenant_id,
            scopes=scopes,
            label=label,
            created_at=now,
        )

    def revoke(self, credential_id: UUID, *, now: datetime) -> bool:
        """An ``UPDATE``, never a ``DELETE`` (ADR-0016 §2)."""
        rows = self.execute(
            "UPDATE credentials SET revoked_at = %s "
            "WHERE credential_id = %s AND revoked_at IS NULL "
            "RETURNING credential_id",
            (now, credential_id),
            fetch="all",
        )
        return bool(rows)

    def list_for(self, tenant_id: str) -> tuple[Credential, ...]:
        rows = self.execute(
            "SELECT credential_id, tenant_id, scopes, label, created_at, "
            "last_used_at, revoked_at FROM credentials "
            "WHERE tenant_id = %s ORDER BY created_at",
            (tenant_id,),
            fetch="all",
        )
        return tuple(
            Credential(
                credential_id=row["credential_id"],
                tenant_id=row["tenant_id"],
                scopes=frozenset(row["scopes"] or ()),
                label=row["label"],
                created_at=row["created_at"],
                last_used_at=row["last_used_at"],
                revoked_at=row["revoked_at"],
            )
            for row in rows or ()
        )

    def touch(self, digest: str, *, now: datetime) -> None:
        """Record use, best-effort and outside the request's transaction.

        Swallows everything. A failure to record *when* a key was last used must
        never refuse a request that authenticated correctly.
        """
        try:
            self.execute(
                "UPDATE credentials SET last_used_at = %s WHERE digest = %s", (now, digest)
            )
        except Exception:  # a bookkeeping write may not fail a request
            return


@dataclass
class CachedKeyStore:
    """Resolution cached for a bounded lifetime. The bound is FR-028's promise.

    **A hit is cached; a miss is not**, and the asymmetry is deliberate. Issuance
    therefore takes effect immediately, while revocation takes up to the
    lifetime — safe in exactly one direction, and the direction is stated rather
    than discovered. Caching misses would make a newly issued key fail for up to
    a lifetime, which is the same defect this class exists to remove, pointing
    the other way.

    The cache holds digests and principals. It never holds a key.
    """

    inner: KeyStore
    ttl_seconds: int = DEFAULT_TTL_SECONDS
    monotonic_ms: Callable[[], float] | None = None
    #: For `last_used_at`, which is the only thing here that needs a wall clock.
    now: Callable[[], Any] | None = None
    _entries: dict[str, tuple[float, Principal]] = field(default_factory=dict)

    def _clock(self) -> float:
        from docdoc.runs.identity import monotonic_ms as default_source

        return (self.monotonic_ms or default_source)()

    def resolve(self, digest: str) -> Principal | None:
        now = self._clock()
        cached = self._entries.get(digest)
        if cached is not None:
            expires_at, principal = cached
            if now < expires_at:
                return principal
            del self._entries[digest]

        resolved = self.inner.resolve(digest)
        if resolved is not None:
            self._entries[digest] = (now + self.ttl_seconds * 1000, resolved)
            self._touch(digest)
        return resolved

    def _touch(self, digest: str) -> None:
        """Record use, **on a cache miss only** (FR-030, ADR-0016).

        Not per request, which is the whole point of the cache: a write on every
        authenticated request is the cost this class exists to avoid, and doing
        it here would trade a read per request for a write per request.

        The consequence is stated rather than hidden: `last_used_at` is accurate
        to within one cache lifetime per process, not to the request. It exists
        so an operator can see which of their issued credentials are still in
        use before revoking one — a question a 30-second granularity answers
        perfectly well.

        Best-effort and swallowing: a bookkeeping write must never refuse a
        request that authenticated correctly.
        """
        touch = getattr(self.inner, "touch", None)
        if touch is None:
            return
        from docdoc.runs.identity import now as default_now

        try:
            touch(digest, now=(self.now or default_now)())
        except Exception:  # bookkeeping may not fail authentication
            return

    def forget(self, digest: str | None = None) -> None:
        """Drop one entry, or all of them.

        Used by the revocation route in the process that served it, so an
        operator who revokes and immediately retries sees the refusal at once
        **on that process**. The other processes still take up to the lifetime,
        and that is the number the documentation states — this is a courtesy, not
        the mechanism.
        """
        if digest is None:
            self._entries.clear()
        else:
            self._entries.pop(digest, None)

    def issue(
        self,
        *,
        tenant_id: str,
        scopes: frozenset[str],
        label: str | None,
        now: datetime,
    ) -> tuple[str, Credential]:
        return self.inner.issue(tenant_id=tenant_id, scopes=scopes, label=label, now=now)

    def revoke(self, credential_id: UUID, *, now: datetime) -> bool:
        revoked = self.inner.revoke(credential_id, now=now)
        # Not `forget(digest)`: this store never learned the digest of the
        # credential it just revoked, and looking one up to evict it would mean
        # holding a mapping from identifier to digest that nothing else needs.
        self.forget()
        return revoked

    def list_for(self, tenant_id: str) -> tuple[Credential, ...]:
        return self.inner.list_for(tenant_id)


@dataclass
class ChainedKeyStore:
    """The file ring first, the table second (ADR-0016 §7).

    A deployment migrating keeps its file keys working while table-issued keys
    begin to, and **no key silently stops working because a table appeared**.

    Mutation goes to the table alone. The file ring raises `CredentialError` for
    all three, which is the honest answer: a file cannot issue.
    """

    ring: Any
    store: KeyStore | None = None

    def resolve(self, digest: str) -> Principal | None:
        found: Principal | None = self.ring.resolve(digest)
        if found is not None:
            return found
        return self.store.resolve(digest) if self.store is not None else None

    @property
    def enabled(self) -> bool:
        """Whether authentication is on, which only the ring can answer.

        A key store holding rows does not turn authentication on: a deployment
        with a database and no key file is still Milestone 8's deployment, and
        FR-088 requires it to stay that way until an operator says otherwise.
        """
        return bool(self.ring.enabled)

    def principal_for(self, credential: str | None) -> Principal:
        """What the HTTP layer calls, resolved through **both** sources.

        Not the ring alone. A table-issued credential arriving at a route has to
        authenticate, and the one thing that would make this milestone's whole
        feature inert is a chain assembled for resolution and then bypassed here.

        The four causes -- absent, malformed, unknown, revoked -- still produce
        one indistinguishable refusal (FR-034), because `resolve` answers `None`
        for the last two and this raises the same constant error for all four.
        """
        if not self.enabled:
            # Authentication off: one implicit tenant, and a credential presented
            # anyway is ignored rather than rejected (FR-088).
            return self.ring.principal_for(credential)  # type: ignore[no-any-return]

        if not credential:
            raise AuthenticationError

        found = self.resolve(digest_of(credential))
        if found is None:
            raise AuthenticationError
        return found

    def _mutable(self) -> KeyStore:
        if self.store is None:
            raise CredentialError(
                "no run-state database is configured, so credentials cannot be "
                "issued, revoked, or listed. The key file still authenticates, "
                "and a key removed from it takes effect on restart"
            )
        return self.store

    def issue(
        self,
        *,
        tenant_id: str,
        scopes: frozenset[str],
        label: str | None,
        now: datetime,
    ) -> tuple[str, Credential]:
        return self._mutable().issue(tenant_id=tenant_id, scopes=scopes, label=label, now=now)

    def revoke(self, credential_id: UUID, *, now: datetime) -> bool:
        return self._mutable().revoke(credential_id, now=now)

    def list_for(self, tenant_id: str) -> tuple[Credential, ...]:
        return self._mutable().list_for(tenant_id)


#: Re-exported so a caller checking a capability does not import two modules.
__all__ += ["ADMIN_SCOPE"]
