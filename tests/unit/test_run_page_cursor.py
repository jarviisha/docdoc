"""T033 — the cursor round-trips, and refuses everything else identically.

FR-016 is the requirement being checked, and its second half is the one worth a
test: *"without disclosing anything about the sequence it referred to."* Four
different ways of being wrong have to produce one answer, because four answers
would tell a caller which foreign cursors are well-formed.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from docdoc.api.paging import Cursor, CursorError, decode, encode

TENANT = "acme"
OTHER = "globex"
AT = datetime(2026, 9, 11, 9, 12, 4, tzinfo=UTC)


def test_a_cursor_round_trips() -> None:
    run_id = uuid4()
    raw = encode(Cursor(TENANT, AT, run_id))
    assert decode(raw, tenant_id=TENANT) == Cursor(TENANT, AT, run_id)


def test_the_encoding_is_opaque_and_url_safe() -> None:
    raw = encode(Cursor(TENANT, AT, uuid4()))
    assert "=" not in raw
    assert "+" not in raw
    assert "/" not in raw
    # Opaque means the tenant is not readable at a glance. It is *in* there —
    # that is how the check works — but a client is not invited to parse it.
    assert TENANT not in raw


@pytest.mark.parametrize(
    "raw",
    [
        "not-base64-at-all!!",
        base64.urlsafe_b64encode(b"not json").decode(),
        base64.urlsafe_b64encode(b'"a string, not an object"').decode(),
        base64.urlsafe_b64encode(b'{"t":"acme"}').decode(),  # missing fields
        base64.urlsafe_b64encode(b'{"t":"acme","c":"nope","i":"nope"}').decode(),
        "",
    ],
)
def test_a_malformed_cursor_is_refused(raw: str) -> None:
    with pytest.raises(CursorError):
        decode(raw, tenant_id=TENANT)


def test_another_tenants_cursor_is_refused_the_same_way() -> None:
    foreign = encode(Cursor(OTHER, AT, uuid4()))

    with pytest.raises(CursorError) as refused:
        decode(foreign, tenant_id=TENANT)

    with pytest.raises(CursorError) as malformed:
        decode("garbage", tenant_id=TENANT)

    # One message for both. Two would be a way to learn that a cursor belonged to
    # a tenant that exists, which is the oracle ADR-0014 closed (FR-016).
    assert str(refused.value) == str(malformed.value)


def test_a_cursor_carries_no_offset_or_total() -> None:
    import base64 as b64
    import json

    raw = encode(Cursor(TENANT, AT, UUID(int=1)))
    payload = json.loads(b64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    assert set(payload) == {"t", "c", "i"}
