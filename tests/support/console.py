"""What the console contract tests share: keys, an account, and a sign-in.

`test_console_session.py` and `test_console_runs.py` both run with
authentication on and a signed-in operator, and both need an admin Bearer key
to prove it opens nothing under `/v1/console`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar

from docdoc.runs.principal import ADMIN_SCOPE, Principal, digest_of
from tests.fixtures.console_accounts import InMemoryConsoleAccounts

H = {"X-Docdoc-Console": "1"}
ADMIN_KEY = "an-admin-key"
PLAIN_KEY = "a-plain-key"
USERNAME = "alice"
PASSWORD = "long enough pass"


class Ring:
    """Authentication on, with one admin-scoped key and one ordinary one.

    The file `KeyRing` builds principals with no scopes, so the admin key is
    held here — enough to head a `ChainedKeyStore`, which is all `_Deployment`
    asks of a ring once authentication is on.
    """

    enabled = True
    _by_digest: ClassVar[dict[str, Principal]] = {
        digest_of(ADMIN_KEY): Principal(tenant_id="ops", scopes=frozenset({ADMIN_SCOPE})),
        digest_of(PLAIN_KEY): Principal(tenant_id="acme"),
    }

    def resolve(self, digest: str) -> Principal | None:
        return self._by_digest.get(digest)

    def tenant_ids(self) -> frozenset[str]:
        return frozenset(p.tenant_id for p in self._by_digest.values())


def accounts_with_alice() -> InMemoryConsoleAccounts:
    store = InMemoryConsoleAccounts()
    store.add(USERNAME, PASSWORD, salt=b"\x01" * 16, now=datetime(2026, 10, 20, tzinfo=UTC))
    return store


def sign_in(client: Any) -> dict[str, str]:
    """Log in as alice; the session cookie as an explicit header.

    An explicit header rather than the client's cookie jar, which honours
    `Path=/v1/console` and `Max-Age=0` — relying on it would let a test pass
    even if the server opened `/v1/runs` to a session or kept a logged-out one
    alive.
    """
    r = client.post(
        "/v1/console/session", json={"username": USERNAME, "password": PASSWORD}, headers=H
    )
    assert r.status_code == 200
    return {"Cookie": f"docdoc_console={r.cookies['docdoc_console']}"}
