"""Provider keys are sealed under a master key the database never holds."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from docdoc.runs.secrets import SECRETS_KEY_ENV, Sealer, SecretUnreadable, last4


def _sealer(key: str | None = None) -> Sealer:
    found = Sealer.from_env({SECRETS_KEY_ENV: key or Fernet.generate_key().decode()})
    assert found is not None
    return found


def test_round_trip() -> None:
    sealer = _sealer()
    token = sealer.seal("sk-fake-1234")
    assert b"sk-fake" not in token
    assert sealer.unseal(token) == "sk-fake-1234"


def test_wrong_master_key_is_unreadable() -> None:
    token = _sealer().seal("sk-fake-1234")
    with pytest.raises(SecretUnreadable):
        _sealer().unseal(token)


def test_unset_is_none() -> None:
    assert Sealer.from_env({}) is None
    assert Sealer.from_env({SECRETS_KEY_ENV: "  "}) is None


def test_malformed_master_key_names_the_variable() -> None:
    with pytest.raises(ValueError, match=SECRETS_KEY_ENV) as caught:
        Sealer.from_env({SECRETS_KEY_ENV: "not-a-key"})
    assert "not-a-key" not in str(caught.value)


def test_last4() -> None:
    assert last4("sk-abcdef1234") == "1234"
    assert last4("abc") == "abc"


def test_repr_of_sealer_has_no_key() -> None:
    key = Fernet.generate_key().decode()
    sealer = _sealer(key)
    assert key not in repr(sealer)
    assert key not in str(sealer)
