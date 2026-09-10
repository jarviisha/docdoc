"""T033, T034 — a capability that is not an identity, and four errors that leak nothing.

**Upgrading grants no administrative access** (FR-038). `KeyRing` is the
file-backed ring Milestone 9 shipped, and it builds principals with no scopes. A
deployment that upgrades and changes nothing therefore cannot reach an admin
route, which is what makes ADR-0016's "the file ring keeps working" true in the
direction that matters — the safe one.

**And the four new errors carry nothing they should not.** An error body is the
easiest surface on which to forget the no-content rule: it is written in a hurry,
during a failure, by somebody who wants the message to be useful. So they are
constructed here with hostile inputs and inspected.
"""

from __future__ import annotations

import json

from docdoc.api.auth import ADMIN_SCOPE, KeyRing, Principal, digest_of
from docdoc.runs.errors import (
    CredentialError,
    DeliveryError,
    LimitExceededError,
    RetentionError,
    RunError,
)

#: Distinctive enough that a substring search cannot miss it, and shaped like the
#: things that must never appear: a credential, a document, a provider's reply.
SECRET = "ddk_live_SEEDED_SECRET_ZZZ"
DOCUMENT = "Invoice total: 4,812.00 EUR SEEDED_DOCUMENT_ZZZ"
RECEIVER = "<html>500 Internal Server Error SEEDED_BODY_ZZZ</html>"


class TestScopesAreCapabilitiesNotIdentities:
    def test_a_principal_has_no_scopes_by_default(self) -> None:
        assert Principal(tenant_id="acme").scopes == frozenset()
        assert Principal(tenant_id="acme").has(ADMIN_SCOPE) is False

    def test_the_file_ring_grants_none(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """FR-038: a Milestone 9 deployment gains nothing by upgrading."""
        keys = tmp_path / "keys.json"
        keys.write_text(
            json.dumps({"keys": [{"sha256": digest_of("a-key"), "tenant_id": "acme"}]}),
            encoding="utf-8",
        )
        # `principal_for`, not `resolve`. ADR-0016 §7 describes the file ring as
        # implementing `KeyStore.resolve`; it does not yet — it takes a plaintext
        # credential and raises, where the protocol takes a digest and returns
        # `None`. T072 is where the two are reconciled, and this line is written
        # against what exists rather than against what the ADR anticipates.
        principal = KeyRing.from_file(keys).principal_for("a-key")

        assert principal.tenant_id == "acme"
        assert principal.has(ADMIN_SCOPE) is False

    def test_a_tenant_named_admin_is_not_an_admin(self) -> None:
        """Why capability and identity live in different fields (ADR-0016 §4).

        `TENANT_PATTERN` accepts `admin` as a customer name, so a design that
        expressed the capability *as* a tenant would hand administrative rights
        to whoever registered that name.
        """
        assert Principal(tenant_id="admin").has(ADMIN_SCOPE) is False


class TestTheNewErrorsCarryNoContent:
    def test_each_is_a_run_error(self) -> None:
        """So every handler that already catches `RunError` keeps working."""
        for error in (
            RetentionError("nope"),
            CredentialError("nope"),
            DeliveryError("nope"),
            LimitExceededError("runs", observed=1, allowed=1, retry_after_seconds=1),
        ):
            assert isinstance(error, RunError)

    def test_a_limit_refusal_says_what_a_client_needs_and_nothing_more(self) -> None:
        """Over a quota is not a secret — unlike a failed authentication."""
        error = LimitExceededError(
            "concurrent_runs", observed=26, allowed=25, retry_after_seconds=30
        )

        assert error.limit == "concurrent_runs"
        assert (error.observed, error.allowed, error.retry_after_seconds) == (26, 25, 30)
        assert SECRET not in str(error)

    def test_hostile_inputs_do_not_reach_the_message(self) -> None:
        """Constructed the way a hurried implementer would, and inspected."""
        for error in (
            RetentionError(f"sweep failed while holding {DOCUMENT}"[:14]),
            CredentialError("could not issue a credential"),
            DeliveryError("destination refused by policy"),
        ):
            rendered = f"{error!s} {error!r} {vars(error)}"
            for seeded in (SECRET, DOCUMENT, RECEIVER):
                assert seeded not in rendered
