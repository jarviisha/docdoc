"""Who is asking, and the one capability there is.

**Here rather than in `docdoc.api.auth`, where Milestone 9 put it.** A principal
is a *run-layer* concept: a run records its owning tenant, a store is namespaced
by one, and a sweep works tenant by tenant. HTTP is one way to arrive carrying a
principal and not the only one — `docdoc credential issue` arrives carrying none
at all.

Milestone 10 forced the question rather than raising it. `docdoc.runs.keys` needs
`Principal` and `digest_of`, and `runs` sits **below** `api` in the layers
contract, so importing them upward broke the build immediately — through a chain
that ended in `urllib`, which is how `import-linter` reported it.

`docdoc.api.auth` re-exports every name here, so nothing that imported them from
there has to change. The file ring, the bearer-token parsing, and the refusal
stay where they were: those are HTTP's, and this is not.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

__all__ = [
    "ADMIN_SCOPE",
    "TENANT_PATTERN",
    "AuthenticationError",
    "Principal",
    "digest_of",
]

#: The one capability this project has (ADR-0016 §4). A constant rather than a
#: string literal at each call site, so the set of things a scope can be is
#: enumerable by reading one line.
ADMIN_SCOPE = "admin"

#: ADR-0014 §1. Narrow enough that a tenant identifier is always a safe path
#: segment: no separator, no parent reference, no case that a filesystem or an
#: object store would fold differently.
TENANT_PATTERN = re.compile(r"^[a-z0-9_-]{1,64}$")


class AuthenticationError(Exception):
    """No principal could be resolved from what was presented.

    **One error for absent, malformed, unrecognised, and revoked.** A different
    message for each would tell an attacker which keys are well-formed enough to
    be worth guessing, and this class is what makes that impossible to get wrong
    later: there is nowhere to put the distinction.

    Milestone 10 adds the fourth cause without adding a fourth answer. A revoked
    credential is refused exactly as an unknown one is, which is what keeps a
    revocation from being observable as anything other than "no".

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

    ``scopes`` is Milestone 10's addition and is a **capability**, not an
    identity (ADR-0016 §4). One scope exists, ``admin``. It is deliberately not a
    second tenant and deliberately not a role: ``TENANT_PATTERN`` would happily
    accept a customer named ``admin``, which is exactly why the two live in
    different fields.

    A set rather than a boolean so a second scope is not a migration. Empty by
    default, and the file-backed ring never populates it, so a deployment that
    upgrades and changes nothing gains no administrative access (FR-038).
    """

    tenant_id: str
    scopes: frozenset[str] = frozenset()

    def has(self, scope: str) -> bool:
        """Whether this principal carries a capability.

        A method rather than ``scope in principal.scopes`` at every call site,
        because the route layer asking "may this caller do the thing" should not
        also be the layer that knows capabilities are a set.
        """
        return scope in self.scopes


def digest_of(key: str) -> str:
    """The stored form of a credential. What goes in the file or the table.

    Never the key. Hashing is what makes a leaked key file — or a leaked database
    backup — not immediately a set of working credentials.
    """
    return hashlib.sha256(key.encode("utf-8")).hexdigest()
