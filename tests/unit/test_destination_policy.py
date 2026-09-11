"""T118, SC-013 — the destination policy, address by address.

The requirement this file exists for is FR-060's *"any resolved address"*. A
policy that checked the first one would pass every test written against a host
with a single address, and fail exactly once, in production, against the host an
attacker set up to resolve to one public and one private address.

So the resolution is substituted and the *set* is what varies.
"""

from __future__ import annotations

import socket

import pytest

from docdoc.runs.delivery import validate_destination
from docdoc.runs.errors import DeliveryError

PUBLIC = "93.184.216.34"

#: One per category FR-060 names. `is_global` would collapse them into one
#: check; enumerating is what lets a refusal say which category fired, and what
#: makes a category quietly dropped from the implementation fail here.
UNROUTABLE = {
    "loopback": "127.0.0.1",
    "loopback_v6": "::1",
    "link_local": "169.254.169.254",
    "private_10": "10.0.0.5",
    "private_172": "172.16.3.9",
    "private_192": "192.168.1.1",
    "multicast": "224.0.0.1",
    "reserved": "240.0.0.1",
    "unspecified": "0.0.0.0",
}


@pytest.fixture
def resolves(monkeypatch: pytest.MonkeyPatch):
    """Make `getaddrinfo` answer a chosen list, in a chosen order."""

    def _install(*addresses: str) -> None:
        def _fake(host: str, port: int, **_: object) -> list:
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))
                for address in addresses
            ]

        monkeypatch.setattr(socket, "getaddrinfo", _fake)

    return _install


def test_a_public_destination_is_accepted(resolves) -> None:
    resolves(PUBLIC)

    destination = validate_destination("https://hooks.example.com/docdoc")

    assert destination.address == PUBLIC
    assert destination.hostname == "hooks.example.com"
    assert destination.port == 443
    assert destination.path == "/docdoc"


@pytest.mark.parametrize("address", sorted(UNROUTABLE.values()))
def test_each_unroutable_category_is_refused(resolves, address: str) -> None:
    resolves(address)

    with pytest.raises(DeliveryError):
        validate_destination("https://hooks.example.com/docdoc")


def test_every_address_is_checked_and_not_only_the_first(resolves) -> None:
    """**The test this file exists for** (FR-060, R8).

    A host resolving to one public and one private address is the attack. The
    public one is first, so an implementation that checked `resolved[0]` and
    stopped would accept this — and then connect to whichever address the
    resolver handed the connection at attempt time.
    """
    resolves(PUBLIC, "169.254.169.254")

    with pytest.raises(DeliveryError):
        validate_destination("https://hooks.example.com/docdoc")


def test_the_order_does_not_change_the_answer(resolves) -> None:
    """The same set the other way round, because the receiver chooses the order."""
    resolves("10.0.0.5", PUBLIC)

    with pytest.raises(DeliveryError):
        validate_destination("https://hooks.example.com/docdoc")


def test_plain_http_is_refused(resolves) -> None:
    """A delivery carries a run's identity and its terminal state."""
    resolves(PUBLIC)

    with pytest.raises(DeliveryError):
        validate_destination("http://hooks.example.com/docdoc")


def test_a_non_http_scheme_is_refused(resolves) -> None:
    resolves(PUBLIC)

    for url in ("file:///etc/passwd", "gopher://example.com/", "ftp://example.com/x"):
        with pytest.raises(DeliveryError):
            validate_destination(url)


def test_an_unresolvable_host_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(*_: object, **__: object) -> list:
        raise socket.gaierror("no")

    monkeypatch.setattr(socket, "getaddrinfo", _fail)

    with pytest.raises(DeliveryError):
        validate_destination("https://nowhere.invalid/hook")


def test_allow_private_permits_both_and_is_the_only_thing_that_does(resolves) -> None:
    """One flag, and it is named for the capability rather than for the guard.

    Loopback over plain http is what a test receiver is, and it is the only use
    either relaxation has. Keeping them one flag means an operator cannot turn
    off the address policy while believing they only permitted `http`.
    """
    resolves("127.0.0.1")

    destination = validate_destination("http://127.0.0.1:8080/hook", allow_private=True)

    assert destination.address == "127.0.0.1"
    assert destination.port == 8080


def test_the_refusal_does_not_name_the_addresses(resolves) -> None:
    """FR-060's disclosure half.

    A 422 that reported what a hostname resolved to would make the registration
    route a name resolver for a caller who has one credential and no other way to
    ask. The class of refusal is the whole of what is said.
    """
    resolves("10.11.12.13")

    with pytest.raises(DeliveryError) as raised:
        validate_destination("https://hooks.example.com/docdoc")

    assert "10.11.12.13" not in str(raised.value)


def test_the_resolution_stub_is_actually_installed(resolves) -> None:
    """Guards the guard: a fixture that silently failed would pass everything."""
    resolves(PUBLIC)

    assert socket.getaddrinfo("anything", 443)[0][4][0] == PUBLIC
