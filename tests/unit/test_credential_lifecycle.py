"""T080 to T086 — the Milestone 9 defect, closed and asserted.

`specs/009` shipped authentication and documented what it could not do:

    "an operator who deletes a compromised key from the file sees no error, no
    warning, and no change -- the revoked key keeps working until the process
    restarts."

That sentence is in Milestone 9's task T117 because it was too important to leave
in a docstring. This file is the assertion that it is no longer true.

The tests run against a fake key store rather than Postgres, because what is
worth checking here is the **policy**: what the cache does with a hit and with a
miss, what the chain does when the ring says no, what a refusal discloses. None of
that needs a database, and a test that did would be skipped on every machine
without one — which is where this defect would come back.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest

from docdoc.api.auth import KeyRing
from docdoc.runs.errors import CredentialError
from docdoc.runs.keys import (
    ADMIN_SCOPE,
    CachedKeyStore,
    ChainedKeyStore,
    Credential,
    PostgresKeyStore,
)
from docdoc.runs.principal import AuthenticationError, Principal, digest_of

if TYPE_CHECKING:
    import pathlib

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


class FakeStore:
    """A key store in a dictionary, with the same three verbs."""

    def __init__(self) -> None:
        self.rows: dict[str, tuple[UUID, str, frozenset[str], bool]] = {}
        self.resolves = 0
        self.touched: list[str] = []

    def resolve(self, digest: str) -> Principal | None:
        self.resolves += 1
        row = self.rows.get(digest)
        if row is None or row[3]:
            return None
        return Principal(tenant_id=row[1], scopes=row[2])

    def add(self, key: str, tenant_id: str, scopes: frozenset[str] = frozenset()) -> UUID:
        identity = uuid4()
        self.rows[digest_of(key)] = (identity, tenant_id, scopes, False)
        return identity

    def revoke(self, credential_id: UUID, *, now: datetime) -> bool:
        for digest, (identity, tenant, scopes, revoked) in self.rows.items():
            if identity == credential_id and not revoked:
                self.rows[digest] = (identity, tenant, scopes, True)
                return True
        return False

    def touch(self, digest: str, *, now: datetime) -> None:
        self.touched.append(digest)

    def issue(self, **_: Any) -> tuple[str, Credential]:  # pragma: no cover
        raise NotImplementedError

    def list_for(self, tenant_id: str) -> tuple[Credential, ...]:  # pragma: no cover
        return ()


def _ticking(values: list[float]) -> Any:
    """A monotonic source the test drives, so nothing sleeps."""
    return lambda: values[0]


class TestRevocationWithoutARestart:
    """T080, SC-006 — the defect Milestone 9 documented and did not fix."""

    def test_a_revoked_credential_stops_working_within_one_lifetime(self) -> None:
        clock = [0.0]
        store = FakeStore()
        credential_id = store.add("a-key", "acme")
        cached = CachedKeyStore(inner=store, ttl_seconds=30, monotonic_ms=_ticking(clock))

        assert cached.resolve(digest_of("a-key")) == Principal("acme")

        store.revoke(credential_id, now=NOW)
        # Still cached: the bound is a real bound, not a hope.
        clock[0] = 29_000
        assert cached.resolve(digest_of("a-key")) == Principal("acme")

        # One lifetime later, on the same object, with nothing restarted.
        clock[0] = 30_001
        assert cached.resolve(digest_of("a-key")) is None

    def test_issuance_takes_effect_immediately(self) -> None:
        """A miss is not cached, and the asymmetry is the safe one.

        Caching misses would make a newly issued key fail for up to a lifetime,
        which is the same defect this class removes, pointing the other way.
        """
        clock = [0.0]
        store = FakeStore()
        cached = CachedKeyStore(inner=store, ttl_seconds=30, monotonic_ms=_ticking(clock))

        assert cached.resolve(digest_of("later")) is None
        store.add("later", "acme")
        assert cached.resolve(digest_of("later")) == Principal("acme")

    def test_a_hit_is_served_without_touching_the_store(self) -> None:
        clock = [0.0]
        store = FakeStore()
        store.add("a-key", "acme")
        cached = CachedKeyStore(inner=store, ttl_seconds=30, monotonic_ms=_ticking(clock))

        cached.resolve(digest_of("a-key"))
        cached.resolve(digest_of("a-key"))
        cached.resolve(digest_of("a-key"))

        assert store.resolves == 1


class TestRotationHasNoGap:
    """T081, SC-008 — two active credentials, zero refusals."""

    def test_both_authenticate_during_the_overlap(self) -> None:
        store = FakeStore()
        first = store.add("old-key", "acme")
        store.add("new-key", "acme")
        chain = ChainedKeyStore(ring=KeyRing.disabled(), store=store)

        assert chain.resolve(digest_of("old-key")) == Principal("acme")
        assert chain.resolve(digest_of("new-key")) == Principal("acme")

        store.revoke(first, now=NOW)

        assert chain.resolve(digest_of("old-key")) is None
        assert chain.resolve(digest_of("new-key")) == Principal("acme")


class TestLastUsedIsBestEffort:
    """T077 — recorded on a miss, not on a request."""

    def test_it_is_not_written_per_request(self) -> None:
        clock = [0.0]
        store = FakeStore()
        store.add("a-key", "acme")
        cached = CachedKeyStore(inner=store, ttl_seconds=30, monotonic_ms=_ticking(clock))

        for _ in range(5):
            cached.resolve(digest_of("a-key"))

        assert store.touched == [digest_of("a-key")], (
            "last_used_at was written per request. That is the cost the cache "
            "exists to avoid, traded from a read to a write."
        )

    def test_a_failing_touch_does_not_refuse_the_request(self) -> None:
        class Hostile(FakeStore):
            def touch(self, digest: str, *, now: datetime) -> None:
                raise RuntimeError("the bookkeeping write failed")

        store = Hostile()
        store.add("a-key", "acme")
        cached = CachedKeyStore(inner=store, ttl_seconds=30, monotonic_ms=_ticking([0.0]))

        assert cached.resolve(digest_of("a-key")) == Principal("acme")


class TestTheChainConsultsBothSources:
    """T073, FR-038 — the file ring first, and it keeps working."""

    def _ring(self, tmp_path: pathlib.Path, key: str, tenant_id: str) -> KeyRing:
        path = tmp_path / "keys.json"
        path.write_text(
            json.dumps({"keys": [{"sha256": digest_of(key), "tenant_id": tenant_id}]}),
            encoding="utf-8",
        )
        return KeyRing.from_file(path)

    def test_a_file_key_still_authenticates(self, tmp_path: pathlib.Path) -> None:
        chain = ChainedKeyStore(ring=self._ring(tmp_path, "file-key", "acme"), store=FakeStore())

        assert chain.principal_for("file-key") == Principal("acme")

    def test_a_table_key_authenticates_too(self, tmp_path: pathlib.Path) -> None:
        """The half that would make this milestone inert if it were missing."""
        store = FakeStore()
        store.add("table-key", "globex")
        chain = ChainedKeyStore(ring=self._ring(tmp_path, "file-key", "acme"), store=store)

        assert chain.principal_for("table-key") == Principal("globex")

    def test_the_file_ring_is_consulted_first(self, tmp_path: pathlib.Path) -> None:
        """So a table appearing cannot silently change what a key resolves to."""
        store = FakeStore()
        store.add("shared", "globex")
        chain = ChainedKeyStore(ring=self._ring(tmp_path, "shared", "acme"), store=store)

        assert chain.principal_for("shared") == Principal("acme")
        assert store.resolves == 0

    def test_with_authentication_off_everyone_is_the_default_tenant(self) -> None:
        """FR-088: a database holding rows does not turn authentication on."""
        store = FakeStore()
        store.add("a-key", "acme")
        chain = ChainedKeyStore(ring=KeyRing.disabled(), store=store)

        assert chain.enabled is False
        assert chain.principal_for(None).tenant_id == "default"
        assert chain.principal_for("a-key").tenant_id == "default"


class TestOneRefusalForFourCauses:
    """T085, FR-034 — and the fourth cause is new."""

    @pytest.mark.parametrize(
        "credential",
        [None, "", "not-a-key", "revoked-key"],
        ids=["absent", "empty", "unknown", "revoked"],
    )
    def test_every_cause_produces_the_same_error(
        self, tmp_path: pathlib.Path, credential: str | None
    ) -> None:
        store = FakeStore()
        revoked = store.add("revoked-key", "acme")
        store.revoke(revoked, now=NOW)
        path = tmp_path / "keys.json"
        path.write_text(
            json.dumps({"keys": [{"sha256": digest_of("real"), "tenant_id": "acme"}]}),
            encoding="utf-8",
        )
        chain = ChainedKeyStore(ring=KeyRing.from_file(path), store=store)

        with pytest.raises(AuthenticationError) as caught:
            chain.principal_for(credential)

        assert str(caught.value) == "a valid credential is required"

    def test_one_tenant_and_a_capability_that_is_not_one(self) -> None:
        """FR-037, FR-060 — `admin` is a scope, never a second tenant."""
        store = FakeStore()
        store.add("admin-key", "ops", frozenset({ADMIN_SCOPE}))
        chain = ChainedKeyStore(ring=KeyRing.disabled(), store=store)

        principal = chain.resolve(digest_of("admin-key"))

        assert principal is not None
        assert principal.tenant_id == "ops"
        assert principal.has(ADMIN_SCOPE) is True
        assert Principal(tenant_id="admin").has(ADMIN_SCOPE) is False


class TestAFileRingCannotMutate:
    """T072 — the honest answer, not a silent no-op."""

    def test_the_three_verbs_refuse_when_there_is_no_store(self) -> None:
        chain = ChainedKeyStore(ring=KeyRing.disabled(), store=None)

        for call in (
            lambda: chain.issue(tenant_id="acme", scopes=frozenset(), label=None, now=NOW),
            lambda: chain.revoke(uuid4(), now=NOW),
            lambda: chain.list_for("acme"),
        ):
            with pytest.raises(CredentialError):
                call()


class TestNothingLeaksTheCredential:
    """T083, SC-007 — over the surfaces this milestone adds."""

    def test_a_listing_carries_no_key_and_no_digest(self) -> None:
        credential = Credential(credential_id=uuid4(), tenant_id="acme", label="ci")

        rendered = f"{credential!r} {vars(credential)}"

        assert "digest" not in rendered
        assert "key" not in rendered.replace("keys", "")

    def test_the_cache_holds_digests_and_never_a_key(self) -> None:
        store = FakeStore()
        store.add("a-key", "acme")
        cached = CachedKeyStore(inner=store, ttl_seconds=30, monotonic_ms=_ticking([0.0]))

        cached.resolve(digest_of("a-key"))

        assert "a-key" not in repr(cached._entries)
        assert digest_of("a-key") in cached._entries


class TestIssuanceProducesThePlaintextOnce:
    """T069, FR-025 — and the table holds only its digest."""

    def test_the_store_records_a_digest_and_returns_a_key(self) -> None:
        statements: list[tuple[str, tuple[Any, ...]]] = []

        def execute(sql: str, params: tuple[Any, ...] = (), *, fetch: str | None = None) -> Any:
            statements.append((sql, params))
            return None

        secret, credential = PostgresKeyStore(execute=execute).issue(
            tenant_id="acme", scopes=frozenset(), label="ci", now=NOW
        )

        (sql, params) = statements[0]
        assert "INSERT INTO credentials" in sql
        assert secret not in params, "the plaintext reached the database"
        assert digest_of(secret) in params
        assert secret.startswith("ddk_")
        assert credential.tenant_id == "acme"

    def test_revocation_is_an_update_and_never_a_delete(self) -> None:
        statements: list[str] = []

        def execute(sql: str, params: tuple[Any, ...] = (), *, fetch: str | None = None) -> Any:
            statements.append(sql)
            return [{"credential_id": uuid4()}]

        PostgresKeyStore(execute=execute).revoke(uuid4(), now=NOW)

        assert "UPDATE credentials" in statements[0]
        assert "DELETE" not in statements[0], (
            "revocation removed the row. 'This key was revoked on the 4th' is "
            "what an incident review needs, and a missing row cannot say it."
        )
