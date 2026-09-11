"""`SecretBook` — the file that decides whether a deployment can sign at all.

**Read by nothing that ran in CI.** `test_delivery_postgres.py` constructs one
in-process and hands it a secret directly, so the four refusals in `from_file`
and the two branches of `from_environment` were never executed. They are the
paths that decide, at startup, whether every callback this deployment registers
will ever deliver — and a quiet failure here produces a registration that
succeeds and a webhook that never arrives.

**The database holds digests and this holds secrets**, joined on `sha256`. That
is what lets `callbacks` record which secret a registration was made with — so a
rotation is detectable and two registrations are distinguishable — while no row
and no process serving a route holds anything that can sign (FR-066).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docdoc.runs.delivery import DeliveryError, SecretBook
from docdoc.runs.identity import DELIVERY_SECRETS_FILE_ENV
from docdoc.runs.principal import digest_of

SECRET = "s" * 32
OTHER = "t" * 32


def _file(tmp_path: Path, raw: object) -> Path:
    path = tmp_path / "delivery-secrets.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return path


# -- what it reads -----------------------------------------------------------


def test_secrets_are_held_by_digest(tmp_path: Path) -> None:
    book = SecretBook.from_file(_file(tmp_path, {"secrets": [SECRET, OTHER]}))

    assert book.secret_for(digest_of(SECRET)) == SECRET
    assert book.knows(SECRET) is True
    assert book.knows("never configured") is False


def test_a_digest_nothing_matches_is_none_rather_than_an_error(tmp_path: Path) -> None:
    """A registration made with a secret since rotated out. `None` is the answer
    the deliverer turns into a `last_error`, not an exception that would stop a
    maintenance tick attempting the next delivery."""
    book = SecretBook.from_file(_file(tmp_path, {"secrets": [SECRET]}))

    assert book.secret_for(digest_of(OTHER)) is None


# -- what it refuses ---------------------------------------------------------


def test_a_file_that_cannot_be_read_names_the_class_and_not_the_contents(
    tmp_path: Path,
) -> None:
    with pytest.raises(DeliveryError, match="cannot be read: "):
        SecretBook.from_file(tmp_path / "absent.json")


def test_a_file_that_is_not_json_does_not_quote_the_line_it_choked_on(
    tmp_path: Path,
) -> None:
    """A parse error from a secrets file can quote the line, and the line is a
    secret. The path, and nothing else."""
    path = tmp_path / "delivery-secrets.json"
    path.write_text('{"secrets": ["' + SECRET + '"', encoding="utf-8")

    with pytest.raises(DeliveryError) as raised:
        SecretBook.from_file(path)

    assert "not valid JSON" in str(raised.value)
    assert SECRET not in str(raised.value)


@pytest.mark.parametrize(
    "raw", [{"secrets": []}, {"secrets": "s"}, {}, ["s"]], ids=["empty", "string", "absent", "list"]
)
def test_a_file_with_no_usable_secrets_is_refused(tmp_path: Path, raw: object) -> None:
    """An empty file configures signing that nothing can verify.

    The quiet version — start anyway with an empty book — produces a callback
    that registers successfully and never delivers, which is indistinguishable
    from a receiver that is down.
    """
    with pytest.raises(DeliveryError, match="non-empty `secrets` array"):
        SecretBook.from_file(_file(tmp_path, raw))


# -- from the environment ----------------------------------------------------


def test_nothing_configured_holds_nothing_and_is_not_an_error() -> None:
    """FR-064. It is what a deployment that registers no callbacks has, and it
    performs no outbound request."""
    assert SecretBook.from_environment().by_digest == {}


def test_the_configured_file_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DELIVERY_SECRETS_FILE_ENV, str(_file(tmp_path, {"secrets": [SECRET]})))

    assert SecretBook.from_environment().knows(SECRET) is True


def test_a_configured_file_that_is_missing_refuses_rather_than_starting_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An operator who named a file asked for signing. Falling back to an empty
    book would give them a deployment that believes it can sign and cannot."""
    monkeypatch.setenv(DELIVERY_SECRETS_FILE_ENV, str(tmp_path / "absent.json"))

    with pytest.raises(DeliveryError):
        SecretBook.from_environment()
