"""FR-068 — a credential reaches no log line, run record, error body, or argv.

Four surfaces, and each has a plausible way to acquire one:

* a **log line**, because a request logger that recorded headers would take the
  `Authorization` one along with the rest;
* a **run record**, because a `Run` is built from a submission and a submission
  carries a credential;
* an **error body**, because the natural implementation of an authentication
  failure is to say what was wrong with what was presented;
* the **process argument list**, because a flag is the obvious way to configure
  a key — and `argv` is readable by every process on the host, which is a worse
  exposure than the environment variable it would have replaced.

The seeded string is what makes this a measurement rather than an inspection: a
distinctive credential is presented, and every surface is searched for it and for
anything derived from it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from docdoc.api.auth import AuthenticationError, KeyRing, digest_of
from docdoc.cli.config import ENVIRONMENT_ONLY, FLAG_FOR_SETTING
from docdoc.runs.model import Run
from docdoc.runs.observe import log_transition

#: Distinctive enough that a substring search means something, and shaped like a
#: real credential so a test double cannot pass by being obviously unlike one.
SEEDED_KEY = "sk-live-Zq7NEVERLOGTHISvalue0123456789"


@pytest.fixture
def keyring(tmp_path: Path) -> KeyRing:
    path = tmp_path / "keys.json"
    path.write_text(
        json.dumps({"keys": [{"sha256": digest_of(SEEDED_KEY), "tenant_id": "acme"}]}),
        encoding="utf-8",
    )
    return KeyRing.from_file(path)


def _leaks(text: str) -> list[str]:
    """Every trace of the credential in a piece of text.

    The digest is searched for as well as the key. A hash is not a credential,
    but a log line carrying one turns an offline dictionary attack into a
    possibility and is not something to emit by accident.
    """
    found = []
    if SEEDED_KEY in text:
        found.append("the key itself")
    if digest_of(SEEDED_KEY) in text:
        found.append("the key's digest")
    for fragment in ("sk-live", "NEVERLOGTHIS"):
        if fragment in text:
            found.append(fragment)
    return found


# -- the principal ------------------------------------------------------------


def test_a_resolved_principal_carries_no_credential(keyring: KeyRing) -> None:
    """The narrowest form of the rule, and the one the rest depends on.

    A `Principal` that held the key it was resolved from would put one in reach
    of every log line and error body that ever carries a request context — and
    nothing would have to be written wrongly for that to happen.
    """
    principal = keyring.principal_for(SEEDED_KEY)

    assert principal.tenant_id == "acme"
    assert not _leaks(repr(principal)), repr(principal)
    assert not _leaks(str(vars(principal)))


def test_the_keyring_does_not_hold_the_key(keyring: KeyRing) -> None:
    """The file holds hashes, so a leak of the *ring* is not a set of keys."""
    assert not any("the key itself" in _leaks(repr(keyring)) for _ in (0,))
    assert SEEDED_KEY not in repr(keyring)


# -- error bodies --------------------------------------------------------------


def test_a_rejection_message_repeats_nothing_that_was_presented() -> None:
    """The obvious helpful implementation, refused.

    "unrecognised key sk-live-…" is what an error message wants to say, and it
    puts the credential into whatever caught it — usually a log, sometimes a
    ticket.
    """
    ring = KeyRing({digest_of("something-else"): "acme"})

    with pytest.raises(AuthenticationError) as raised:
        ring.principal_for(SEEDED_KEY)

    assert not _leaks(str(raised.value)), str(raised.value)
    assert not _leaks(repr(raised.value))


# -- log lines -----------------------------------------------------------------


def test_no_run_event_can_carry_a_credential(caplog: pytest.LogCaptureFixture) -> None:
    """FR-092's payload is a closed set, and a credential is not in it.

    `log_transition` takes named arguments rather than a mapping, so there is
    nowhere for a credential to arrive even if a caller held one — which is the
    property being pinned, since the alternative shape would make this a matter
    of every call site being careful.
    """
    with caplog.at_level(logging.INFO, logger="docdoc.runs"):
        log_transition(
            run_id=__import__("uuid").uuid4(),
            tenant_id="acme",
            from_state="queued",
            to_state="running",
            attempts=1,
            worker_id="host:1",
            reason="claimed",
        )

    for record in caplog.records:
        assert not _leaks(record.getMessage())


def test_a_run_record_has_no_field_a_credential_could_occupy() -> None:
    """FR-068's run-record clause, asserted on the model's shape.

    Stronger than searching a populated row: a field that exists is a field a
    later change can fill, so the assertion is that there is no such field.
    """
    fields = set(Run.model_fields)

    forbidden = {"credential", "api_key", "key", "token", "secret", "authorization"}
    assert not (fields & forbidden), f"a Run can hold {sorted(fields & forbidden)}"


# -- the process argument list -------------------------------------------------


def test_no_credential_setting_has_a_command_line_flag() -> None:
    """FR-068's argv clause. `/proc` is world-readable and shell history is a file.

    Checked over the classified settings rather than over a hand-written list, so
    a credential added later lands in `ENVIRONMENT_ONLY` or fails here.
    """
    suspicious = [
        name
        for name in (*FLAG_FOR_SETTING, *ENVIRONMENT_ONLY)
        if any(word in name for word in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
    ]
    assert suspicious, "no credential-shaped setting found; this check is vacuous"

    with_flags = [name for name in suspicious if name in FLAG_FOR_SETTING]
    assert not with_flags, (
        f"{with_flags} can be passed on the command line. argv is readable by "
        f"every process on the host, which is a worse exposure than the "
        f"environment variable the flag would have replaced"
    )


def test_the_keys_setting_names_a_file_and_not_a_key() -> None:
    """R14's choice, asserted rather than described.

    A variable holding the keys themselves would put them in the environment of
    every child process docdoc spawns. A path does not, and file permissions are
    a control the environment does not offer.
    """
    from docdoc.api.settings import API_KEYS_FILE_ENV

    assert API_KEYS_FILE_ENV.endswith("_FILE")
