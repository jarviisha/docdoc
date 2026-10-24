"""Seal provider API keys under a master key the database never holds.

The console stores a provider's key in Postgres so an operator need not edit the
worker's environment, but a dump of that table must not be a dump of working
keys. Fernet under ``DOCDOC_SECRETS_KEY`` makes the table useless without the
deployment's environment. The master key is held only inside the ``Fernet``
object: nothing here puts it in a repr or an error message.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from cryptography.fernet import Fernet

SECRETS_KEY_ENV = "DOCDOC_SECRETS_KEY"


class SecretUnreadable(Exception):  # noqa: N818 -- name fixed by the plan
    """A sealed value does not open under the current master key."""


class Sealer:
    __slots__ = ("_fernet",)

    def __init__(self, fernet: Fernet) -> None:
        self._fernet = fernet

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> Sealer | None:
        raw = environ.get(SECRETS_KEY_ENV, "").strip()
        if not raw:
            return None
        # Lazy: `cryptography` ships only in the postgres extra, and an install
        # without it must still import the API when no master key is set.
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            raise ValueError(
                f"{SECRETS_KEY_ENV} is set but the cryptography package is missing; "
                "install the postgres extra (pip install 'docdoc[postgres]')"
            ) from None
        try:
            return cls(Fernet(raw.encode()))
        except ValueError:
            # Not `from exc`: the cause's text must not be able to carry the value.
            raise ValueError(
                f"{SECRETS_KEY_ENV} is not a valid Fernet key (32 url-safe base64 bytes)"
            ) from None

    def seal(self, text: str) -> bytes:
        return self._fernet.encrypt(text.encode())

    def unseal(self, token: bytes | memoryview) -> str:
        """Plaintext, or `SecretUnreadable` for anything that does not open cleanly.

        A memoryview is what psycopg hands back for ``bytea``. A wrong type, a bad
        token and non-UTF-8 plaintext are all "unreadable" to a caller.
        """
        from cryptography.fernet import InvalidToken

        try:
            return self._fernet.decrypt(bytes(token)).decode()
        except (InvalidToken, TypeError, UnicodeDecodeError):
            raise SecretUnreadable("sealed value does not open under the master key") from None


def last4(text: str) -> str:
    return text[-4:]
