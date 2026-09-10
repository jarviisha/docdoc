"""Webhook delivery: at-least-once, signed, and pinned to a validated address.

This is the only module in ``docdoc.runs`` permitted to open a socket, and the
``forbidden`` contract in ``pyproject.toml`` is what makes that true rather than
intended (FR-099).

**No dependency** (research R6). ``http.client``, ``socket``, ``ssl``, ``hmac``,
and ``hashlib``, because the destination policy needs to connect to an address it
has *already validated* while presenting the original hostname, and both
high-level HTTP clients hide that behind a transport that would have to be
subclassed. The standard library gives it directly.

**The attack this module is written against is server-side request forgery.** A
callback URL is chosen by a caller and fetched by the deployment, from inside
whatever network the deployment sits in. So:

1. resolve the host,
2. refuse if **any** resolved address is loopback, link-local, private,
   multicast, reserved, or unspecified — every address, because a host resolving
   to one public and one private address is the attack,
3. connect **to the validated address**, presenting the original hostname in
   ``Host`` and in TLS SNI,
4. do not follow redirects — a public URL answering ``302 → 169.254.169.254`` is
   the other half of the same attack.

Steps 1 and 2 run at registration **and again before every attempt**. Validating
once at registration is defeated by DNS rebinding: the name that resolved
publicly an hour ago resolves to the metadata service now.

**The signing secret is never in the database** (FR-066). ``callbacks`` holds
``sha256(secret)``, which is enough to tell two registrations apart and not
enough to sign with; the secret itself comes from a file the operator configures,
and `SecretBook` matches one to a registration by digest. A route therefore
cannot return it, because no process serving routes needs to hold it.

**Nothing here reads a clock.** ``at`` is a parameter and the backoff schedule
lives in `identity`, which is the only module in this package permitted to import
``datetime`` (FR-096a).
"""

from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import json
import logging
import socket
import ssl
from dataclasses import dataclass
from dataclasses import replace as _replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlsplit

from docdoc.runs.errors import DeliveryError

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from pathlib import Path
    from uuid import UUID

    from docdoc.runs.model import Run

__all__ = [
    "SIGNATURE_HEADER",
    "Callback",
    "Deliverer",
    "Delivery",
    "DeliveryState",
    "Destination",
    "PostgresDeliverer",
    "SecretBook",
    "payload_for",
    "signature",
    "validate_destination",
]

_logger = logging.getLogger("docdoc.runs")

#: ``t=<unix>,v1=<hex>`` — the timestamp is inside the signed material, so a
#: captured delivery is not valid for ever (R7, FR-055).
SIGNATURE_HEADER = "X-Docdoc-Signature"


class DeliveryState(StrEnum):
    """Three states, and they are a **delivery's** and never a run's.

    A failed delivery changes no run state (FR-063) and polling remains the
    record (FR-065). The migration's ``CHECK`` holds the same three.
    """

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


@dataclass(frozen=True)
class Callback:
    """A registered destination, as everything except the secret.

    ``secret_digest`` and no secret, for the reason `Credential` carries neither
    key nor digest: what a listing can show must not be what a listing can be
    used to obtain.
    """

    callback_id: UUID
    tenant_id: str
    url: str
    secret_digest: str
    created_at: datetime | None = None
    revoked_at: datetime | None = None


@dataclass(frozen=True)
class Delivery:
    """One notification, and every attempt at it.

    ``delivery_id`` is allocated once and is stable across retries, because it is
    the value a receiver deduplicates on (FR-056). One per run, by a ``UNIQUE``
    constraint rather than by worker discipline (FR-058).
    """

    delivery_id: UUID
    tenant_id: str
    run_id: UUID
    callback_id: UUID
    state: DeliveryState = DeliveryState.PENDING
    attempts: int = 0
    next_attempt_at: datetime | None = None
    last_status: int | None = None
    #: A **class name**, never the receiver's response body. The same rule
    #: ``Run.error_class`` follows: a body is somebody else's content, it can
    #: quote the request that produced it, and docdoc has no business storing it.
    last_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


# -- the destination policy (R8, FR-060, FR-061) -----------------------------


@dataclass(frozen=True)
class Destination:
    """A URL that passed the policy, and the address it passed it *at*.

    Holding the address is the point. Validating a hostname and then handing the
    hostname to a connection re-resolves it, and the second resolution is the one
    an attacker controls.
    """

    scheme: str
    hostname: str
    port: int
    path: str
    #: The one address this delivery may connect to.
    address: str


def validate_destination(url: str, *, allow_private: bool = False) -> Destination:
    """Resolve, refuse, and pin. Raises `DeliveryError` naming the class only.

    The refusal says *what kind* of destination was refused and **never the
    addresses it resolved to** — a 422 that reported them would make the
    registration route a name resolver for an unauthenticated caller, which is a
    capability nobody asked docdoc for.
    """
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise DeliveryError("a callback destination must be an http or https URL")
    if scheme == "http" and not allow_private:
        raise DeliveryError(
            "a callback destination must be https; a delivery carries a run's "
            "identity and its terminal state"
        )

    hostname = parts.hostname or ""
    if not hostname:
        raise DeliveryError("a callback destination must name a host")
    port = parts.port or (443 if scheme == "https" else 80)

    resolved = _resolve(hostname, port)
    if not allow_private:
        for address in resolved:
            _refuse_unroutable(address)

    return Destination(
        scheme=scheme,
        hostname=hostname,
        port=port,
        # A request-URI is never empty, and an empty path is the root.
        path=parts.path or "/",
        # The first, having checked **all** of them. Checking only the one we go
        # on to use would pass a host that resolves to a public address first and
        # a private one second, which is precisely the attack: the receiver
        # controls the order.
        address=resolved[0],
    )


def _resolve(hostname: str, port: int) -> list[str]:
    """Every address this name has, or a refusal.

    An unresolvable host is a `DeliveryError` and not a retry: a name that does
    not resolve at registration is a typo, and one that stops resolving later is
    a destination the operator has taken away.
    """
    try:
        found = socket.getaddrinfo(hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as error:
        raise DeliveryError("a callback destination must resolve") from error
    addresses = [str(entry[4][0]) for entry in found]
    if not addresses:
        raise DeliveryError("a callback destination must resolve")
    return addresses


def _refuse_unroutable(address: str) -> None:
    """The whole policy, on one address (FR-060).

    ``is_global`` would be shorter and is not equivalent: it is false for a
    handful of ranges that are routable, and the failure mode of getting this
    wrong in that direction is a destination the operator cannot register with no
    way to find out why. The categories are named instead, so a refusal can say
    which one fired.
    """
    parsed = ipaddress.ip_address(address)
    refused = (
        parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_private
        or parsed.is_multicast
        or parsed.is_reserved
        or parsed.is_unspecified
    )
    if refused:
        raise DeliveryError(
            "a callback destination must not resolve to a loopback, link-local, "
            "private, multicast, reserved, or unspecified address"
        )


# -- signing (R7, FR-055) ----------------------------------------------------


def signature(secret: str, body: bytes, *, timestamp: int) -> str:
    """The header value: ``t=<unix>,v1=<hex>`` over ``f"{t}.{body}"``.

    **The timestamp is inside the signed material.** Signing the body alone
    produces a value that stays valid for as long as the secret does, so a
    captured delivery can be replayed at any point in the future and verify. A
    receiver compares the ``t`` against its own clock and rejects what is too
    old — which it can only do if moving ``t`` invalidates the signature.
    """
    signed = f"{timestamp}.".encode() + body
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


@dataclass(frozen=True)
class SecretBook:
    """The signing secrets, by digest, read from a file the operator configures.

    **The database holds digests and this holds secrets**, and the join between
    them is `sha256`. That is what lets `callbacks` record which secret a
    registration was made with — so a rotation is detectable and two registrations
    are distinguishable — while no row, and no process serving a route, holds
    anything that can sign (FR-066).
    """

    by_digest: dict[str, str]

    @classmethod
    def from_environment(cls) -> SecretBook:
        """Read ``DOCDOC_DELIVERY_SECRETS_FILE``, or hold nothing.

        Empty is a valid configuration: it is what a deployment that registers no
        callbacks has, and it performs no outbound request (FR-064).
        """
        import os

        from docdoc.runs.identity import DELIVERY_SECRETS_FILE_ENV

        configured = os.environ.get(DELIVERY_SECRETS_FILE_ENV, "").strip()
        if not configured:
            return cls({})

        from pathlib import Path

        return cls.from_file(Path(configured))

    @classmethod
    def from_file(cls, path: Path) -> SecretBook:
        """Load ``{"secrets": ["…", …]}``, refusing what it cannot read.

        Strict for the reason `KeyRing.from_file` is: every failure here is a
        deployment that believes it can sign and cannot, and the quiet version —
        skipping an unreadable entry and starting anyway — produces a callback
        that registers successfully and never delivers.
        """
        from docdoc.runs.principal import digest_of

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise DeliveryError(
                f"the delivery secrets file {path} cannot be read: {type(error).__name__}"
            ) from error
        except json.JSONDecodeError as error:
            # The path and never the contents: a parse error from a secrets file
            # can quote the line it choked on.
            raise DeliveryError(f"{path} is not valid JSON") from error

        entries = raw.get("secrets") if isinstance(raw, dict) else None
        if not isinstance(entries, list) or not entries:
            raise DeliveryError(
                f"{path} must be an object with a non-empty `secrets` array. An "
                "empty file configures signing that nothing can verify"
            )
        return cls({digest_of(str(entry)): str(entry) for entry in entries})

    def secret_for(self, digest: str) -> str | None:
        """The secret a registration was made with, or ``None``."""
        return self.by_digest.get(digest)

    def knows(self, secret: str) -> bool:
        """Whether this secret is configured, which registration checks."""
        from docdoc.runs.principal import digest_of

        return digest_of(secret) in self.by_digest


# -- the payload (FR-054) ----------------------------------------------------


def payload_for(run: Run, *, delivery_id: UUID, routing: Any = None) -> dict[str, Any]:
    """What a receiver is told, and it is a notification and not a result.

    Identities, a terminal state, a failing stage, an error **class**, and the
    routing outcome where a policy is configured. No document text, no extracted
    value, no prompt body, no credential, and no provider message — the same rule
    every observer in this project follows, applied to the one of them that
    leaves the deployment.

    Absent rather than null where a field does not apply, so a receiver can tell
    "not applicable" from "not yet" without a convention nobody documented.
    """
    payload: dict[str, Any] = {
        "delivery_id": str(delivery_id),
        "run_id": str(run.run_id),
        "status": str(run.status),
    }
    if run.processing_id is not None:
        payload["processing_id"] = run.processing_id
    if run.failed_stage is not None:
        payload["failed_stage"] = run.failed_stage
    if run.error_class is not None:
        payload["error_class"] = run.error_class
    if routing is not None:
        payload["routing"] = {"outcome": str(routing.outcome)}
    return payload


# -- the transport -----------------------------------------------------------


class _PinnedHTTPS(http.client.HTTPSConnection):
    """TLS to a validated address, with the original hostname in SNI.

    The two have to differ, and that is the whole class. ``http.client`` resolves
    ``self.host`` at connect time, which would be a *second* resolution and the
    one an attacker times. So the socket goes to the address the policy passed,
    and the certificate is still verified against the name the operator
    registered — a delivery to an impostor at a validated address fails the
    handshake rather than succeeding quietly.
    """

    def __init__(self, destination: Destination, *, timeout: int) -> None:
        super().__init__(
            destination.hostname,
            destination.port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self._address = destination.address

    def connect(self) -> None:
        sock = socket.create_connection((self._address, self.port), self.timeout)
        # `server_hostname` and `Host` both stay the registered name: the address
        # is where we go, the name is who we expect to find.
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTP(http.client.HTTPConnection):
    """The same pinning without TLS, reachable only via `allow_private`."""

    def __init__(self, destination: Destination, *, timeout: int) -> None:
        super().__init__(destination.hostname, destination.port, timeout=timeout)
        self._address = destination.address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


def _post(destination: Destination, body: bytes, headers: dict[str, str], *, timeout: int) -> int:
    """One POST, and the status it answered. **Redirects are not followed.**

    ``http.client`` follows none by construction, which is why it is what this
    module uses: there is no flag to forget. A ``3xx`` is reported as the status
    it is and counts as a failed attempt — a receiver that wants delivery
    elsewhere registers elsewhere (FR-061).
    """
    connection: http.client.HTTPConnection = (
        _PinnedHTTPS(destination, timeout=timeout)
        if destination.scheme == "https"
        else _PinnedHTTP(destination, timeout=timeout)
    )
    try:
        connection.request("POST", destination.path, body=body, headers=headers)
        response = connection.getresponse()
        # Read and discard. Not stored, not logged, not surfaced: a receiver's
        # body is their content. Read at all because leaving it unread on a
        # keep-alive socket is how the next attempt gets the previous response.
        response.read()
        return int(response.status)
    finally:
        connection.close()


class Deliverer(Protocol):
    """Enqueue one, find what is due, attempt one."""

    def enqueue(self, run: Run, callback: Callback, *, at: datetime) -> Delivery:
        """Record the intention to notify. One row per run (FR-058)."""
        ...

    def due(self, *, at: datetime, limit: int) -> tuple[Delivery, ...]:
        """Pending deliveries whose next attempt has come."""
        ...

    def attempt(self, delivery: Delivery, *, at: datetime) -> Delivery:
        """Try once, and record what happened. Never raises into a caller."""
        ...


@dataclass
class PostgresDeliverer:
    """The two tables, and the one place an outbound request is made.

    ``execute`` is `PostgresRunQueue`'s, passed in rather than reached for, so
    this module opens no database connection of its own and inherits that class's
    rule that no driver exception escapes.
    """

    execute: Callable[..., Any]
    secrets: SecretBook
    max_attempts: int = 6
    timeout_seconds: int = 10
    backoff_seconds: int = 30
    allow_private: bool = False
    #: Substitutable so a test can drive the schedule without a socket. The real
    #: one is `_post`, and `tests/support/webhook_receiver.py` exercises *that*
    #: — mocking the transport would test nothing about the destination policy.
    transport: Callable[..., int] | None = None

    # -- callbacks ----------------------------------------------------------

    def register(
        self, *, tenant_id: str, url: str, secret: str, at: datetime, callback_id: UUID
    ) -> Callback:
        """Validate the destination, then record it (FR-060).

        Validated **here as well as** before every attempt. Registration-time
        validation is what gives the operator a 422 while they are looking at
        the response; attempt-time validation is what closes DNS rebinding. Only
        the second is load-bearing for security, and only the first is usable.
        """
        from docdoc.runs.principal import digest_of

        validate_destination(url, allow_private=self.allow_private)
        if not self.secrets.knows(secret):
            raise DeliveryError(
                "this signing secret is not configured on the deployment, so a "
                "delivery to this destination could not be signed"
            )

        digest = digest_of(secret)
        self.execute(
            "INSERT INTO callbacks (callback_id, tenant_id, url, secret_digest, created_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (callback_id, tenant_id, url, digest, at),
        )
        return Callback(
            callback_id=callback_id,
            tenant_id=tenant_id,
            url=url,
            secret_digest=digest,
            created_at=at,
        )

    def callback(self, callback_id: UUID, *, tenant_id: str | None = None) -> Callback | None:
        """One registration, tenant-scoped **in the query** (FR-079's rule).

        ``tenant_id`` is optional for exactly one caller: the deliverer itself,
        which has already established ownership by reading the delivery row. A
        route always passes it.
        """
        sql = (
            "SELECT callback_id, tenant_id, url, secret_digest, created_at, revoked_at "
            "FROM callbacks WHERE callback_id = %s AND revoked_at IS NULL"
        )
        params: tuple[Any, ...] = (callback_id,)
        if tenant_id is not None:
            sql += " AND tenant_id = %s"
            params += (tenant_id,)
        row = self.execute(sql, params, fetch="one")
        return None if row is None else _row_to_callback(row)

    def revoke(self, callback_id: UUID, *, tenant_id: str, at: datetime) -> bool:
        """An ``UPDATE``, for the reason credential revocation is one."""
        rows = self.execute(
            "UPDATE callbacks SET revoked_at = %s "
            "WHERE callback_id = %s AND tenant_id = %s AND revoked_at IS NULL "
            "RETURNING callback_id",
            (at, callback_id, tenant_id),
            fetch="all",
        )
        return bool(rows)

    # -- deliveries ---------------------------------------------------------

    def enqueue(self, run: Run, callback: Callback, *, at: datetime) -> Delivery:
        """Record the notification. Due immediately, attempted by the next tick.

        ``ON CONFLICT (run_id) DO NOTHING`` and then a read, rather than a read
        and then an insert: the constraint is what makes "one delivery per run"
        true, and a check-then-write would be a race that the constraint exists
        to remove.
        """
        from docdoc.runs.identity import new_delivery_id

        self.execute(
            "INSERT INTO deliveries (delivery_id, tenant_id, run_id, callback_id, state, "
            "attempts, next_attempt_at, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, 'pending', 0, %s, %s, %s) "
            "ON CONFLICT (run_id) DO NOTHING",
            (new_delivery_id(), run.tenant_id, run.run_id, callback.callback_id, at, at, at),
        )
        existing = self.for_run(run_id=run.run_id, tenant_id=run.tenant_id)
        if existing is None:  # pragma: no cover - the insert above just ran
            raise DeliveryError("the delivery could not be recorded")
        return existing

    def for_run(self, *, run_id: UUID, tenant_id: str) -> Delivery | None:
        """The delivery for one run, tenant-scoped in the query."""
        row = self.execute(
            f"SELECT {_DELIVERY_COLUMNS} FROM deliveries WHERE run_id = %s AND tenant_id = %s",
            (run_id, tenant_id),
            fetch="one",
        )
        return None if row is None else _row_to_delivery(row)

    def due(self, *, at: datetime, limit: int) -> tuple[Delivery, ...]:
        """What the maintenance tick attempts, oldest first.

        ``FOR UPDATE SKIP LOCKED``, exactly as the claim query does and for the
        same reason: two workers ticking at once must not both attempt one
        delivery, and skipping is how the second finds other work rather than
        waiting for the first.
        """
        rows = self.execute(
            f"SELECT {_DELIVERY_COLUMNS} FROM deliveries "
            "WHERE state = 'pending' AND next_attempt_at <= %s "
            "ORDER BY next_attempt_at LIMIT %s FOR UPDATE SKIP LOCKED",
            (at, limit),
            fetch="all",
        )
        return tuple(_row_to_delivery(row) for row in rows or ())

    def attempt(self, delivery: Delivery, *, at: datetime) -> Delivery:
        """One attempt, and the row that records it.

        **Never raises.** Every failure — a refused destination, an unresolvable
        host, a missing secret, a timeout, a 500 — is an attempt that did not
        succeed, and the difference between them belongs in ``last_error`` as a
        class name rather than in an exception that would stop a maintenance
        tick attempting the next delivery.
        """
        try:
            status = self._send(delivery, at=at)
        except DeliveryError as error:
            return self._record(delivery, at=at, status=None, error=type(error).__name__)
        except OSError as error:
            # Every transport failure: a timeout, a refused connection, a
            # handshake that did not verify. The class, never the message — a
            # TLS error message quotes the certificate it rejected.
            return self._record(delivery, at=at, status=None, error=type(error).__name__)

        if 200 <= status < 300:
            self.execute(
                "UPDATE deliveries SET state = 'delivered', attempts = attempts + 1, "
                "last_status = %s, last_error = NULL, updated_at = %s WHERE delivery_id = %s",
                (status, at, delivery.delivery_id),
            )
            return _replace(
                delivery,
                state=DeliveryState.DELIVERED,
                attempts=delivery.attempts + 1,
                last_status=status,
                last_error=None,
                updated_at=at,
            )
        return self._record(delivery, at=at, status=status, error="HTTPError")

    def _send(self, delivery: Delivery, *, at: datetime) -> int:
        """Resolve, validate, sign, and post. Raises `DeliveryError` on refusal."""
        callback = self.callback(delivery.callback_id)
        if callback is None:
            raise DeliveryError("the destination has been removed")

        secret = self.secrets.secret_for(callback.secret_digest)
        if secret is None:
            raise DeliveryError(
                "no configured secret matches this registration, so the delivery cannot be signed"
            )

        run = self._run(delivery)
        if run is None:
            raise DeliveryError("the run this delivery describes is gone")

        # **Re-validated, every attempt.** This line is what closes DNS
        # rebinding; the identical call at registration only closes a typo.
        destination = validate_destination(callback.url, allow_private=self.allow_private)

        body = json.dumps(payload_for(run, delivery_id=delivery.delivery_id)).encode("utf-8")
        timestamp = int(at.timestamp())
        transport = self.transport or _post
        return int(
            transport(
                destination,
                body,
                {
                    "Content-Type": "application/json",
                    SIGNATURE_HEADER: signature(secret, body, timestamp=timestamp),
                },
                timeout=self.timeout_seconds,
            )
        )

    def _run(self, delivery: Delivery) -> Run | None:
        # The queue's own column list and row mapper, imported rather than
        # restated: a migration that adds a column must not leave two places
        # disagreeing about what a run row holds.
        from docdoc.runs.postgres import _COLUMNS, _row_to_run

        row = self.execute(
            f"SELECT {_COLUMNS} FROM runs WHERE run_id = %s AND tenant_id = %s",
            (delivery.run_id, delivery.tenant_id),
            fetch="one",
        )
        return None if row is None else _row_to_run(row)

    def _record(
        self, delivery: Delivery, *, at: datetime, status: int | None, error: str
    ) -> Delivery:
        """A failed attempt: back off, or come to rest at ``failed`` (FR-057).

        Coming to rest matters as much as retrying. A delivery that retried for
        ever would be a queue that grows without bound against a receiver that is
        never coming back, and the state is readable so the tenant can see that
        it stopped and why.
        """
        from docdoc.runs.identity import backoff

        attempts = delivery.attempts + 1
        exhausted = attempts >= self.max_attempts
        state = DeliveryState.FAILED if exhausted else DeliveryState.PENDING
        next_at = (
            delivery.next_attempt_at
            if exhausted
            else backoff(at, attempts=attempts, base_seconds=self.backoff_seconds)
        )

        self.execute(
            "UPDATE deliveries SET state = %s, attempts = %s, last_status = %s, "
            "last_error = %s, next_attempt_at = %s, updated_at = %s WHERE delivery_id = %s",
            (str(state), attempts, status, error, next_at, at, delivery.delivery_id),
        )
        _logger.warning(
            "delivery.attempt_failed",
            extra={
                "docdoc": {
                    "event": "delivery.attempt_failed",
                    "delivery_id": str(delivery.delivery_id),
                    "tenant_id": delivery.tenant_id,
                    "attempts": attempts,
                    "last_status": status,
                    # A class name. The receiver's body is never read into this.
                    "last_error": error,
                    "state": str(state),
                }
            },
        )
        return _replace(
            delivery,
            state=state,
            attempts=attempts,
            next_attempt_at=next_at,
            last_status=status,
            last_error=error,
            updated_at=at,
        )


#: Every column, written out for the reason `postgres._COLUMNS` is: a migration
#: adding one must not silently change what `_row_to_delivery` receives.
_DELIVERY_COLUMNS = (
    "delivery_id, tenant_id, run_id, callback_id, state, attempts, next_attempt_at, "
    "last_status, last_error, created_at, updated_at"
)


def _row_to_delivery(row: dict[str, Any]) -> Delivery:
    return Delivery(
        delivery_id=row["delivery_id"],
        tenant_id=row["tenant_id"],
        run_id=row["run_id"],
        callback_id=row["callback_id"],
        state=DeliveryState(row["state"]),
        attempts=row["attempts"],
        next_attempt_at=row["next_attempt_at"],
        last_status=row["last_status"],
        last_error=row["last_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_callback(row: dict[str, Any]) -> Callback:
    return Callback(
        callback_id=row["callback_id"],
        tenant_id=row["tenant_id"],
        url=row["url"],
        secret_digest=row["secret_digest"],
        created_at=row["created_at"],
        revoked_at=row["revoked_at"],
    )
