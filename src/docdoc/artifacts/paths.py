"""Making a directory that is actually owner-only.

One function, and it exists because ``Path.mkdir(parents=True, mode=...)`` does
not do what it reads as doing: the mode applies to the **leaf** only, and every
intermediate directory it creates gets the process default — ``0o777 & ~umask``,
which is ``0o755`` almost everywhere.

Both stores wrote ``path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)``
and got exactly that: a ``0o700`` fan-out directory holding ``0o600`` files,
inside a **world-readable** ``artifacts/`` or ``blobs/`` root. FR-044 says both
stores must be "readable only by the account that owns them" and must not write
to "a shared or world-readable location", and the roots were the one level nobody
checked — the artifact store's permissions test asserted the file and its
immediate parent, which are the two levels that were already right.

**The store root itself is deliberately left alone.** docdoc tightens the
directories it creates and not the one it was pointed at: an operator may set
``DOCDOC_STORE_ROOT`` to a directory that is shared on purpose, or to something
like ``/tmp``, and a library that chmod-ed its way up from there would break the
machine to satisfy a requirement about its own files. Everything docdoc creates
underneath is owner-only, which is what FR-044 asks of docdoc.
"""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "DEFAULT_TENANT_ENV",
    "DIR_MODE",
    "FILE_MODE",
    "PROBE_BLOB_ID",
    "DegradationLog",
    "root_tenant",
    "secure_mkdir",
    "tenant_root",
]

#: A well-formed content identity no run can ever have produced — sixty-four
#: zeroes. Milestone 9's readiness check uses it as a fixed key so that "can this
#: process reach the store?" is a metadata lookup with a known answer, rather
#: than a read of somebody's document (research R13, FR-056).
#:
#: A *miss* is the expected outcome and means the store answered. Probing with an
#: id that is certainly absent is what keeps the check from depending on whether
#: some particular document happens to exist.
PROBE_BLOB_ID = "sha256:" + "0" * 64

#: The tenant a deployment has when authentication is off (FR-088). Mirrors
#: ``docdoc.runs.model.DEFAULT_TENANT``; duplicated rather than imported because
#: ``artifacts`` sits directly above the kernel and importing ``runs`` would
#: invert the layer contract.
DEFAULT_TENANT = "default"

#: Which tenant owns the **unprefixed** store root, when that is not ``default``.
#:
#: FR-089's "a tenant named in configuration". An existing deployment's content
#: sits at ``<root>/blobs/…`` with no tenant segment; enabling authentication has
#: to say who owns it, and the system must not guess. Set this to the tenant that
#: does, and their content stays exactly where it is — no copy, no move, no
#: read-through fallback (ADR-0014 §3).
#:
#: **The same value must be set in every process of a deployment** — the API,
#: every worker, and ``docdoc migrate``. It describes the store's layout, not one
#: invocation's behaviour, and two processes that disagreed about it would look
#: for the same tenant's content in two places.
#:
#: Its flag lives on ``docdoc migrate`` **alone**, which is FR-083 satisfied and
#: the safety argument kept: that command records the answer, so naming it there
#: is an argument, while a ``--default-tenant`` on ``docdoc parse`` would be a
#: per-invocation override of a deployment-wide fact — precisely the disagreement
#: the recorded value exists to prevent. ``migrate`` then refuses to change what
#: it recorded, so a mismatch is caught at deploy time rather than discovered as
#: a cache that quietly stopped hitting.
DEFAULT_TENANT_ENV = "DOCDOC_DEFAULT_TENANT"


class DegradationLog:
    """Reports each way a store is unavailable **once**, not once per call.

    ADR-0010 §4 says an unreachable store "runs without reuse and logs once", and
    Milestone 9's US3/AC3 and Edge Cases repeat it: "the run proceeds without
    reuse and is logged once", explicitly contrasted with *per stage*. The
    pipeline has a once-only flag for this in ``_Reuse._degrade`` — and on the
    filesystem path it never fires, because the store catches its own ``OSError``,
    logs, and returns as though nothing happened. So a four-stage run against an
    unwritable store emitted four identical warnings and the requirement was
    quietly false.

    The guard belongs here rather than in the pipeline because *the store* is
    what knows. Making the pipeline responsible would mean the store reporting
    failure upward, which is precisely the behaviour ADR-0010 §4 rejected: a
    store that cannot be written to must not fail the run.

    **Scoped to the store instance, and what that means depends on the process.**
    The command line and each HTTP request build a store and drop it, so this is
    once per run. A worker builds one and keeps it, so it is once per process per
    condition — stronger than the requirement asks, and the trade is worth
    stating: an operator who starts reading logs an hour into an outage sees the
    original line rather than a fresh one. Repeating it would mean choosing an
    interval, and a log that repeats on a timer is a metric wearing a log's
    clothes. Readiness is the signal for "is it down *now*".

    Keyed by *reason* rather than by a single flag, so "unreadable" and
    "unwritable" are two facts and reporting one does not silence the other.
    """

    __slots__ = ("_reported",)

    def __init__(self) -> None:
        self._reported: set[str] = set()

    def first_time(self, reason: str) -> bool:
        """Whether this condition has not been reported yet. Records that it has."""
        if reason in self._reported:
            return False
        self._reported.add(reason)
        return True


def root_tenant(explicit: str | None = None) -> str:
    """The tenant whose namespace is the store root itself.

    Explicit argument, then environment, then ``default`` — the precedence every
    setting follows (FR-083). The argument exists for ``docdoc migrate``, which
    is the command that *records* the answer and therefore the one invocation
    where naming it is an argument rather than a statement about the deployment.

    Read here rather than threaded through every store constructor because it is
    a property of the *store*, identical for every caller in a process, and a
    parameter on each would be one more thing for two call sites to pass
    differently. Nothing but ``migrate`` passes one.
    """
    if explicit and explicit.strip():
        return explicit.strip()
    return os.environ.get(DEFAULT_TENANT_ENV, "").strip() or DEFAULT_TENANT


#: Owner-only, on directories and files alike. FR-044: artifacts hold extracted
#: values and blobs hold whole source documents.
DIR_MODE = 0o700
FILE_MODE = 0o600


def secure_mkdir(path: Path, *, below: Path) -> None:
    """Create ``path``, and make every level docdoc owns owner-only.

    ``below`` is the store root — the directory the operator chose. Levels
    strictly under it are docdoc's to create and to tighten; ``below`` itself and
    anything above it are not touched.

    Existing directories under ``below`` are tightened too, not just newly
    created ones. A store root populated by an earlier docdoc, or a directory an
    operator made by hand, is exactly the case FR-044 is about, and skipping it
    would make the guarantee depend on who got there first. Tightening can only
    remove access, so it is the safe direction to be wrong in.

    An ``OSError`` from a chmod is swallowed. On a shared deployment the
    directory may belong to somebody else, and docdoc should not fail a run over
    a mode it cannot set — the store degrades rather than failing (FR-063), and
    the write itself reports the real problem if there is one.
    """
    path.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)

    if not path.is_relative_to(below):
        # Not under the root we were given. Tighten the leaf and nothing else,
        # rather than guessing which ancestors are ours.
        _chmod(path)
        return

    level = below
    for part in path.relative_to(below).parts:
        level = level / part
        _chmod(level)


def _chmod(path: Path) -> None:
    with contextlib.suppress(OSError):
        os.chmod(path, DIR_MODE)


def tenant_root(tenant_id: str) -> str:
    """The path segment a tenant's content lives under, relative to the store root.

    ``""`` for the default tenant. ``"t/<tenant_id>"`` for every other.

    **The empty string is a compatibility rule, and removing it would strand
    every existing deployment's data** (FR-084a, ADR-0014 §3). Content written
    before Milestone 9 sits at ``<root>/blobs/<aa>/<hash>`` with no prefix at
    all. Giving the default tenant a prefix would put it at a path the new code
    never looks at, and the result is the worst kind of regression: correct
    answers, and a silent re-payment for every parse, because a miss is
    indistinguishable from an absence. SC-018 exists to catch exactly that, and
    the specification's own first draft would have failed it.

    Two alternatives bought a uniform path shape and were rejected on cost.
    Relocating on upgrade means copying every artifact a deployment has ever
    written — on an object store a move is a copy then a delete — and it would
    run for operators who never enable authentication. A read-through fallback to
    the legacy path moves nothing and pays a second round trip on every *miss*,
    which is the common case for a new document rather than the rare one.

    So this is the one conditional permitted in path derivation, and it is a
    stated rule rather than a behavioural difference between tenants. Do not
    "tidy" it into an unconditional prefix.

    The segment goes **above** the two-character fan-out, which keeps ADR-0010
    §1's reason for that fan-out intact and makes Milestone 10's per-tenant
    deletion a prefix operation rather than a scan.

    Nothing here touches identity: ``blob_id``, ``artifact_id``, ``content_id``,
    and ``processing_id`` are derived exactly as before, so two tenants
    processing identical bytes arrive at identical identities independently
    (FR-085). Only the location differs.

    ``tenant_id`` is validated at the authentication boundary, not here — one
    validation point that always runs beats two that can disagree.

    *Which* tenant gets the empty string is configuration (``root_tenant``), so
    that an upgrading deployment can name the owner of content written before
    tenants existed rather than have one inferred for it (FR-089).
    """
    if tenant_id == root_tenant():
        return ""
    return f"t/{tenant_id}"
