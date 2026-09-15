"""The run listing's cursor: a position, and the tenant it belongs to.

**Opaque to a client and transparent to this module.** A cursor is base64url over
a three-field JSON object — the tenant, the ``created_at``, and the ``run_id`` of
the last row on a page. It carries no offset, no count, and no total: a total
would be a second query on every page and a number that is already wrong by the
time it is read.

**It is not signed, and that is a decision rather than an omission** (specs/011
research R3). A forged cursor can only name a position *inside the forger's own
tenant*, because the tenant predicate in the query comes from the credential and
never from the cursor (FR-014). The worst a caller achieves by editing their own
cursor is paging over their own runs from a different place. An HMAC would be a
key to manage and rotate, bought against that.

**A cursor issued for another tenant is refused exactly as a malformed one is**
(FR-016). One refusal, one body: two would tell a caller which foreign cursors
are well-formed, which is the existence oracle ADR-0014 closed.

Importable with no web framework installed, like :mod:`docdoc.api.settings`: a
cursor is a string transformation, and asking it a question should not require
FastAPI.
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from typing import NamedTuple
from uuid import UUID

__all__ = ["Cursor", "CursorError", "decode", "encode"]


class CursorError(ValueError):
    """The cursor could not be read, or was not this caller's.

    One class for both, deliberately. The handler turns it into one response, and
    a second class would eventually become a second message.
    """


class Cursor(NamedTuple):
    """A position in the ordering ``(created_at DESC, run_id DESC)``."""

    tenant_id: str
    created_at: datetime
    run_id: UUID


def encode(cursor: Cursor) -> str:
    """The opaque string a client is handed."""
    payload = json.dumps(
        {
            "t": cursor.tenant_id,
            "c": cursor.created_at.isoformat(),
            "i": str(cursor.run_id),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    # Unpadded: `=` is legal in a query string but survives a round trip through
    # enough proxies and copy-paste steps badly enough to be worth not emitting.
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode(raw: str, *, tenant_id: str) -> Cursor:
    """Read a cursor, refusing anything that is not this tenant's.

    The tenant is checked here rather than by the caller so that there is exactly
    one place the check can be forgotten, and it is this one.
    """
    padding = "=" * (-len(raw) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(raw + padding))
    except (binascii.Error, ValueError, UnicodeDecodeError) as error:
        raise CursorError("this cursor cannot be read") from error

    if not isinstance(payload, dict):
        raise CursorError("this cursor cannot be read")

    try:
        owner = payload["t"]
        created_at = datetime.fromisoformat(payload["c"])
        run_id = UUID(payload["i"])
    except (KeyError, TypeError, ValueError) as error:
        raise CursorError("this cursor cannot be read") from error

    if owner != tenant_id:
        # The same message a malformed cursor gets. A caller learns that their
        # cursor was refused and nothing about whose it was.
        raise CursorError("this cursor cannot be read")

    return Cursor(tenant_id=owner, created_at=created_at, run_id=run_id)
