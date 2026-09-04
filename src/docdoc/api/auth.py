"""A static key file, a principal, and one tenant. Off unless configured.

**Disabled by default** (FR-088, ADR-0014 §6). With no ``DOCDOC_API_KEYS_FILE``
set, every route behaves exactly as it did under Milestone 8 — no credential on
anything — and the deployment has one implicit tenant owning all content, whose
namespace is the store root itself. That is the *compatible* default rather than
the safe one, and the distinction is not softened here or in the README: a
deployment that never enables authentication is exactly as exposed as it was
before. What the default buys is that upgrading breaks nothing; what it costs is
that security is opt-in.

**A file rather than a variable holding keys** (research R14). A key set is a
list, and file permissions are a control the environment does not offer. A
credential is never a flag either: ``argv`` is readable by every process on the
host, which is a worse exposure than the variable it would have replaced.

**Hashes, compared in constant time.** The file stores the SHA-256 of each key,
never the key. The comparison is ``secrets.compare_digest``, which is
constant-time either way — hashing is what makes a leaked file not immediately a
set of working credentials.

**One tenant per principal** (FR-060). Not a list: a request has to resolve to
exactly one namespace, and a principal carrying two would push "which one?" into
every store call site. A human who works for two customers holds two keys.

**Validation happens here and nowhere else.** ``tenant_id`` is checked against
``[a-z0-9_-]{1,64}`` at this boundary, so a value that could escape a path
segment never reaches a store. The stores deliberately do not re-validate: one
validation point that always runs beats two that can disagree (ADR-0014 §1, R12).

**Nothing here is mutable through a route** (FR-061). The mapping is read at
startup and there is no endpoint that creates, revokes, or lists a key. That is
why it is a file and not a table: a table invites exactly the endpoint the
requirement forbids.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from docdoc.api.settings import API_KEYS_FILE_ENV
from docdoc.runs.model import DEFAULT_TENANT

__all__ = [
    "TENANT_PATTERN",
    "AuthenticationError",
    "KeyRing",
    "Principal",
    "digest_of",
]

#: ADR-0014 §1. Narrow enough that a tenant identifier is always a safe path
#: segment: no separator, no parent reference, no case that a filesystem or an
#: object store would fold differently.
TENANT_PATTERN = re.compile(r"^[a-z0-9_-]{1,64}$")


class AuthenticationError(Exception):
    """No principal could be resolved from what was presented.

    **One error for absent, malformed, and unrecognised.** A different message
    for each would tell an attacker which keys are well-formed enough to be worth
    guessing, and this class is what makes that impossible to get wrong later:
    there is nowhere to put the distinction.

    Carries no credential and no fragment of one (FR-068). The message is a
    constant.
    """

    def __init__(self) -> None:
        super().__init__("a valid credential is required")


@dataclass(frozen=True)
class Principal:
    """Who is asking, reduced to the only thing docdoc does anything with.

    Exactly one ``tenant_id`` (FR-060), and no name, no key, no key identifier.
    A principal that carried the credential it was resolved from would put one in
    reach of every log line and error body that ever holds a request context.
    """

    tenant_id: str


def digest_of(key: str) -> str:
    """The stored form of a credential. What goes in the file, never the key."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class KeyRing:
    """The credential-to-principal mapping, loaded once.

    Immutable after construction. Reloading on change was considered and left
    out: it would mean either a filesystem watch or a stat on every request, and
    a deployment rotating keys restarts a process — which it is already doing for
    every other configuration change.
    """

    __slots__ = ("_by_digest", "_enabled")

    def __init__(self, by_digest: dict[str, str] | None = None) -> None:
        self._by_digest = dict(by_digest or {})
        self._enabled = by_digest is not None

    # -- construction ---------------------------------------------------------

    @classmethod
    def disabled(cls) -> KeyRing:
        """Authentication off: one implicit tenant owning everything (FR-088)."""
        return cls(None)

    @classmethod
    def from_environment(cls) -> KeyRing:
        """Read ``DOCDOC_API_KEYS_FILE``, or return the disabled ring.

        Read at startup, so a deployment that cannot load its keys fails while
        starting rather than on the first authenticated request — the failure
        that would otherwise arrive after the old process had already been
        drained.
        """
        configured = os.environ.get(API_KEYS_FILE_ENV, "").strip()
        if not configured:
            return cls.disabled()
        return cls.from_file(Path(configured))

    @classmethod
    def from_file(cls, path: Path) -> KeyRing:
        """Load a key file, refusing anything it cannot make sense of.

        Strict on purpose. Every failure here is a deployment that thinks it has
        authentication and does not, and the quiet version of that — skipping an
        unreadable entry and starting anyway — is the worst outcome available:
        the service comes up, serves traffic, and rejects the customer whose key
        was in the line that was skipped.
        """
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise ValueError(
                f"{API_KEYS_FILE_ENV} names {path}, which cannot be read: {type(error).__name__}"
            ) from error
        except json.JSONDecodeError as error:
            # The path and the error, never the contents: a parse error message
            # from a key file can quote the line it choked on.
            raise ValueError(f"{path} is not valid JSON") from error

        entries = raw.get("keys") if isinstance(raw, dict) else None
        if not isinstance(entries, list) or not entries:
            raise ValueError(
                f"{path} must be an object with a non-empty `keys` array of "
                '{"sha256": …, "tenant_id": …} entries. An empty key file '
                "enables authentication that nothing can pass"
            )

        by_digest: dict[str, str] = {}
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ValueError(f"{path}: entry {index} is not an object")
            sha256 = str(entry.get("sha256", "")).strip().lower()
            tenant_id = str(entry.get("tenant_id", "")).strip()
            if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
                raise ValueError(
                    f"{path}: entry {index} has no `sha256` hex digest. The file "
                    "holds hashes and never keys, so a leak of it is not a set of "
                    "working credentials"
                )
            if not TENANT_PATTERN.match(tenant_id):
                raise ValueError(
                    f"{path}: entry {index} has a tenant_id that is not "
                    f"[a-z0-9_-]{{1,64}}. A tenant identifier is a path segment "
                    "in every store, and this is the only place it is checked"
                )
            by_digest[sha256] = tenant_id

        return cls(by_digest)

    # -- use ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return self._enabled

    def principal_for(self, credential: str | None) -> Principal:
        """The principal this credential names, or raise (FR-059, FR-067).

        With authentication disabled, every caller is the default tenant and any
        credential presented is **ignored rather than rejected** — a deployment
        that has not turned authentication on must behave exactly as Milestone 8
        did, including for a client that sends a header it did not need.

        The scan does not stop at the first match. Returning early would make the
        time taken depend on where in the file a key sits, which is a smaller leak
        than a timing-unsafe comparison and the same kind of leak; and the file is
        a handful of entries, so there is nothing to save.
        """
        if not self._enabled:
            return Principal(DEFAULT_TENANT)

        if not credential:
            raise AuthenticationError

        presented = digest_of(credential)
        found: str | None = None
        for stored, tenant_id in self._by_digest.items():
            if secrets.compare_digest(stored, presented):
                found = tenant_id
        if found is None:
            raise AuthenticationError
        return Principal(found)


def bearer_of(header: str | None) -> str | None:
    """The credential in an ``Authorization`` header, or ``None``.

    Case-insensitive on the scheme, because RFC 7235 says it is and a caller who
    sends ``bearer`` is not the problem this system is guarding against.
    """
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None
