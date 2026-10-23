"""The documented HTTP client examples are executed, not merely read.

An example nobody executes is wrong the next time a constructor changes, and the
person who finds out is the new contributor it was written for.

These are subprocess runs rather than imports. A copied example is run from a
shell, and `python examples/…` exercises the import path a user actually has —
including that the module can be executed without the repository on `sys.path`
through some test-harness accident.

They live in `tests/integration/` because they are slower than a unit test and
because CI collects that directory. They are deliberately **not** marked
`provider`: every example here must run with no credentials, which is the whole
claim it makes.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

EXAMPLES = pathlib.Path("examples")
TIMEOUT_S = 120


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run an example under a **legacy stdout encoding**, deliberately.

    An example that prints a character its console cannot encode dies with
    ``UnicodeEncodeError`` before reaching the point it was written to make. That
    is not hypothetical: an earlier example drew its provenance tree with box
    characters and crashed on Windows, where Python defaults stdout to the ANSI
    code page rather than UTF-8. Eleven review passes and a full local suite
    missed it, because every one of them ran on Linux where the default is UTF-8.

    Forcing ``ascii`` here reproduces that failure on **every** platform, so the
    cheapest machine finds it rather than the slowest one. It is also stricter
    than Windows itself -- cp1252 would accept an em dash -- and the strictness is
    the point: a contributor's console encoding depends on their locale, and
    "works on the runner's code page" is not a property worth relying on.

    If an example ever genuinely needs to *demonstrate* non-ASCII text -- and for
    a document engine that is a real possibility -- the fix is for that example to
    reconfigure its own stdout, not to relax this.

    **That prescription needed a second half, and CI supplied it.** Milestone 4's
    grounding example prints a ligature, because a ligature resolving at the exact
    tier is the thing it exists to show, so it reconfigures its own stdout to
    UTF-8 exactly as the paragraph above says. It then failed on Windows and only
    on Windows: the child wrote correct UTF-8 and *this function* decoded it with
    the parent's locale codec, cp1252, which cannot decode a continuation byte.
    ``result.stdout`` came back ``None`` and the assertion died on that instead.

    So the decoding is pinned to UTF-8 here. That does not weaken the rule the
    ``ascii`` environment enforces: ``PYTHONIOENCODING`` governs the *child's*
    default stdout encoding, so an example that prints non-ASCII without
    reconfiguring still dies exactly as before. What changes is only that the
    reader can read an example that did reconfigure -- previously the harness
    could set the trap but not report what fell into it.
    """
    return subprocess.run(
        [sys.executable, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TIMEOUT_S,
        check=False,
        env={**os.environ, "PYTHONIOENCODING": "ascii", **(env or {})},
    )


def _assert_ran(result: subprocess.CompletedProcess[str], name: str) -> None:
    assert result.returncode == 0, (
        f"{name} exited {result.returncode}. An example that does not run is worse than "
        f"no example, because it is the first thing a new contributor tries.\n"
        f"--- stdout ---\n{result.stdout[-2000:]}\n--- stderr ---\n{result.stderr[-2000:]}"
    )


def test_the_asynchronous_example_runs_with_nothing_listening() -> None:
    """Milestone 9's example, in the mode every contributor will first hit.

    It needs a running composition to do its real work, and an example that
    crashed on a refused connection would tell a new contributor the example is
    broken when what is true is that the service is not up. So the unreachable
    path is part of what it teaches, and it is the path this asserts — under a
    URL that is guaranteed to refuse, so the assertion does not depend on
    whether the developer happens to have something on port 8000.

    **That last sentence was true of the intent and not of the code.** Nothing
    set `DOCDOC_EXAMPLE_URL`, so the example used its own default of
    `localhost:8000` — and the moment the development composition was up with
    authentication enabled, this failed with `AuthenticationError`, which is not
    "nothing listening" and not what the example was being asked to demonstrate.
    Found by running the quickstart scenarios with the composition up.

    Port 9 is `discard`, reserved by RFC 863 and bound by nothing on a developer
    machine, so the refusal is now guaranteed rather than hoped for.
    """
    result = _run(
        str(EXAMPLES / "submit_async_run.py"), env={"DOCDOC_EXAMPLE_URL": "http://127.0.0.1:9"}
    )
    _assert_ran(result, "submit_async_run.py")


def test_the_asynchronous_example_names_the_commands_that_make_it_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A diagnostic that does not say what to do next is a diagnostic wasted."""
    monkeypatch.setenv("DOCDOC_EXAMPLE_URL", "http://127.0.0.1:1")
    result = _run(str(EXAMPLES / "submit_async_run.py"))
    _assert_ran(result, "submit_async_run.py")

    assert "docker compose" in result.stdout
    assert "docdoc migrate" in result.stdout, (
        "the example no longer names the migration step, which is the one thing "
        "that is not applied automatically and the one an operator forgets"
    )


def test_the_webhook_receiver_example_verifies_what_docdoc_signs() -> None:
    """The one example that must not be executed as a server, tested as a library.

    `receive_webhook.py` blocks on `serve_forever`, so running it as a subprocess
    the way every example above is run would hang the suite. What is worth
    checking is the part a reader copies — `verify` — and it is worth checking
    the way a receiver actually meets it: against a signature this repository
    produced, not against a second implementation of the same formula.
    """
    import importlib.util
    import time

    from docdoc.runs.delivery import signature

    spec = importlib.util.spec_from_file_location(
        "receive_webhook", EXAMPLES / "receive_webhook.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    secret = "whsec_a-secret-two-parties-hold"
    body = b'{"delivery_id":"b41e","run_id":"0f8b","status":"succeeded"}'
    now = time.time()
    header = signature(secret, body, timestamp=int(now))

    ok, reason = module.verify(body, header, secret=secret, now=now)
    assert ok, reason

    # A byte changed anywhere, and it stops verifying.
    tampered, _ = module.verify(
        body.replace(b"succeeded", b"failedxxx"), header, secret=secret, now=now
    )
    assert not tampered

    # A replay, which is what the signed timestamp is for.
    stale, why = module.verify(body, header, secret=secret, now=now + 86_400)
    assert not stale
    assert "older" in why


def test_every_committed_example_is_covered_here() -> None:
    """The assertion that keeps this file honest as examples are added.

    Listing them by directory rather than by hand means a fourth example fails
    this until someone runs it — the same shape as the adapter-coverage check in
    the contract suite, and for the same reason.
    """
    shipped = {path.name for path in EXAMPLES.glob("*.py")}
    covered = {"submit_async_run.py", "receive_webhook.py"}
    assert shipped <= covered, (
        f"these examples ship but nothing executes them: {sorted(shipped - covered)}"
    )
