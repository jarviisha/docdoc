"""T117, SC-011 — the signature, and why the timestamp is inside it.

Signing the body alone produces a value that stays valid for as long as the
secret does. A delivery captured once could then be replayed against the receiver
at any point in the future and verify perfectly, and the receiver would have no
way to tell — the whole point of signing is to make a delivery attributable, and
an attributable message with no time on it is a permanent credential.

Putting `t` inside the signed material means a receiver can reject what is too
old, and means moving `t` invalidates what was signed. Both halves are asserted
here.
"""

from __future__ import annotations

import hashlib
import hmac

from docdoc.runs.delivery import signature

SECRET = "whsec_a-secret-that-only-two-parties-hold"
BODY = b'{"delivery_id":"3c1a","run_id":"0f8b","status":"succeeded"}'
AT = 1_788_000_000


def _parts(header: str) -> dict[str, str]:
    return dict(piece.split("=", 1) for piece in header.split(","))


def test_the_header_carries_the_timestamp_and_one_version() -> None:
    """`t=<unix>,v1=<hex>`. A version, because a scheme with no version is one
    that can never be changed without breaking every receiver at once."""
    parts = _parts(signature(SECRET, BODY, timestamp=AT))

    assert parts["t"] == str(AT)
    assert len(parts["v1"]) == 64
    assert set(parts) == {"t", "v1"}


def test_the_signature_is_over_the_timestamp_and_the_body() -> None:
    """Stated as the construction a receiver has to implement.

    Written out rather than compared against another call of `signature`, which
    would only prove that a function equals itself.
    """
    expected = hmac.new(SECRET.encode(), f"{AT}.".encode() + BODY, hashlib.sha256).hexdigest()

    assert _parts(signature(SECRET, BODY, timestamp=AT))["v1"] == expected


def test_one_byte_of_the_body_changes_it() -> None:
    altered = BODY.replace(b"succeeded", b"failedxxx")
    assert len(altered) == len(BODY), "same length, so only the content differs"

    assert signature(SECRET, altered, timestamp=AT) != signature(SECRET, BODY, timestamp=AT)


def test_a_replay_is_detectable_because_the_timestamp_is_signed() -> None:
    """**The requirement** (SC-011).

    A captured delivery is a `(body, header)` pair. To present it as fresh, an
    attacker has to move `t` — and the signature no longer verifies. To keep the
    signature, they have to keep `t` — and the receiver rejects it as old.
    """
    captured = signature(SECRET, BODY, timestamp=AT)

    much_later = AT + 60 * 60 * 24 * 30
    forged = f"t={much_later},v1={_parts(captured)['v1']}"

    # Verifying the forged header the way a receiver does: recompute over the
    # timestamp the header claims.
    claimed = hmac.new(
        SECRET.encode(), f"{much_later}.".encode() + BODY, hashlib.sha256
    ).hexdigest()

    assert not hmac.compare_digest(claimed, _parts(forged)["v1"])
    # And the untouched capture still carries its original instant, which is what
    # the receiver's freshness window reads.
    assert int(_parts(captured)["t"]) == AT


def test_a_different_secret_does_not_verify() -> None:
    assert signature("whsec_someone-elses", BODY, timestamp=AT) != signature(
        SECRET, BODY, timestamp=AT
    )


def test_the_same_inputs_give_the_same_signature() -> None:
    """No nonce, no salt, no clock read inside. A receiver retrying verification
    against a stored body has to get the same answer."""
    assert signature(SECRET, BODY, timestamp=AT) == signature(SECRET, BODY, timestamp=AT)
