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

**Nothing here is mutable through a route** (`specs/009` FR-061). The mapping is
read at startup and there is no endpoint that creates, revokes, or lists a key.
That is why it is a file and not a table: a table invites exactly the endpoint
the requirement forbids.

**And Milestone 10 built that endpoint** (ADR-0016). FR-061's own text ended "in
this milestone", and this is the one it was pointing at. The reasoning above is
kept rather than deleted because it is still true *of this module*: the ring is a
file, it is read once, and nothing here can be mutated. What changed is that it
is no longer the only source of principals — ``docdoc.runs.keys`` holds a table
with issuance, revocation, and a bounded cache, and this ring is consulted
**first** so a deployment configured as Milestone 9 configured it authenticates
exactly as before (FR-038).

The limitation this docstring used to leave implicit is worth stating plainly,
because it was a security defect and not a design note: a key deleted from this
file keeps working until the process restarts. That is why the table exists.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from docdoc.api.settings import API_KEYS_FILE_ENV
from docdoc.runs.model import DEFAULT_TENANT

# Moved to `docdoc.runs.principal` by Milestone 10 and re-exported here, so that
# nothing which imported them from this module has to change. The move was forced
# rather than chosen: `docdoc.runs.keys` needs both, and `runs` sits below `api`
# in the layers contract — see that module's docstring.
from docdoc.runs.principal import (
    ADMIN_SCOPE,
    TENANT_PATTERN,
    AuthenticationError,
    Principal,
    digest_of,
)

__all__ = [
    "ADMIN_SCOPE",
    "TENANT_PATTERN",
    "AuthenticationError",
    "KeyRing",
    "Principal",
    "digest_of",
]


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

    def resolve(self, digest: str) -> Principal | None:
        """`KeyStore.resolve`, so this ring can head a `ChainedKeyStore`.

        **The shape reconciliation ADR-0016 §7 anticipated.** That section says
        the file ring "implements the same `resolve`"; when Milestone 10 came to
        build it, this class had `principal_for`, which takes a *plaintext*
        credential and *raises*. The protocol takes a digest and returns `None`.
        Both differences are deliberate on both sides:

        * a digest, because a store further down the chain holds digests and
          nothing should re-hash per source;
        * `None`, because a chain has to be able to ask the next source, and an
          exception is not a question.

        `principal_for` keeps its signature and its behaviour, because it is what
        the HTTP layer calls and what turns four causes into one indistinguishable
        refusal. This is the narrower verb underneath it.

        Returns `None` when authentication is disabled: a disabled ring resolves
        *everyone* to the default tenant, which `principal_for` expresses and a
        digest lookup cannot.
        """
        if not self._enabled:
            return None
        found: str | None = None
        for stored, tenant_id in self._by_digest.items():
            if secrets.compare_digest(stored, digest):
                found = tenant_id
        return None if found is None else Principal(found)

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
