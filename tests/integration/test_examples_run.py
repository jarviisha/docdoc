"""T102 — the documented examples are executed, not merely read (SC-020).

SC-020 says a new contributor extracts fields from a real document by following a
single documented example. Until this file existed, nothing ran any example: the
only reference to `extract_invoice.py` read its source looking for forbidden
strings, and the ingest layer's `parse_pdf.py` had the same gap.

That mattered more than it sounds. `extract_invoice.py` broke three times while it
was being written — against the real `IngestProvenance` signature — so it is code
that has already demonstrated it rots. An example nobody executes is an example
that is wrong the next time a constructor changes, and the person who finds out is
the new contributor it was written for.

These are subprocess runs rather than imports. A copied example is run from a
shell, and `python examples/…` exercises the import path a user actually has —
including that the module can be executed without the repository on `sys.path`
through some test-harness accident.

They live in `tests/integration/` because they are slower than a unit test and
because CI collects that directory. They are deliberately **not** marked
`provider`: every example here must run with no credentials, which is the whole
claim SC-020 makes.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

EXAMPLES = pathlib.Path("examples")
TIMEOUT_S = 120


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """Run an example under a **legacy stdout encoding**, deliberately.

    An example that prints a character its console cannot encode dies with
    ``UnicodeEncodeError`` before reaching the point it was written to make. That
    is not hypothetical: `build_document.py` drew its provenance tree with box
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
        env={**os.environ, "PYTHONIOENCODING": "ascii"},
    )


def _assert_ran(result: subprocess.CompletedProcess[str], name: str) -> None:
    assert result.returncode == 0, (
        f"{name} exited {result.returncode}. An example that does not run is worse than "
        f"no example, because it is the first thing a new contributor tries.\n"
        f"--- stdout ---\n{result.stdout[-2000:]}\n--- stderr ---\n{result.stderr[-2000:]}"
    )


def test_the_extraction_example_runs_with_no_credentials() -> None:
    """SC-020, and SC-001's claim that the whole path works offline."""
    result = _run(str(EXAMPLES / "extract_invoice.py"))
    _assert_ran(result, "extract_invoice.py")


def test_the_extraction_example_demonstrates_what_it_claims_to() -> None:
    """A zero exit code proves it runs. These prove it still shows what it teaches.

    Each assertion is one of the example's own teaching points, so a change that
    quietly drops one — a Decimal becoming a float, grounding starting to be
    resolved here — fails rather than passing with less to say.
    """
    result = _run(str(EXAMPLES / "extract_invoice.py"))
    _assert_ran(result, "extract_invoice.py")
    out = result.stdout

    assert "Decimal('1240.00')" in out, "the total must stay a Decimal, not become a float"
    assert "grounding      : None" in out, (
        "every value is ungrounded until Milestone 4; if this line changes, the stage "
        "boundary has moved and the example is teaching something new"
    )
    assert "present=False" in out, "an absence must still be shown as a recorded outcome"
    assert "schema_hash" in out, "the two-identity split is the example's central lesson"
    assert "UNTRUSTED" in out, "model_confidence must stay labelled where a reader sees it"


def test_the_extraction_example_names_no_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """FR-021 — and it must not quietly reach a real one when a key is present.

    A key in the developer's environment must not change what this example does.
    It uses the echo adapter, so a run with credentials configured must produce
    exactly the same output as one without.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "a-key-this-example-must-not-use")
    with_key = _run(str(EXAMPLES / "extract_invoice.py"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    without_key = _run(str(EXAMPLES / "extract_invoice.py"))

    _assert_ran(with_key, "extract_invoice.py")
    assert with_key.stdout == without_key.stdout, (
        "the example produced different output with a credential present, so it is "
        "reaching a real provider rather than the echo adapter"
    )
    assert "adapter      : echo" in with_key.stdout


def test_the_kernel_example_runs() -> None:
    """Milestone 1's example. Included because the gap was never extraction-specific."""
    _assert_ran(_run(str(EXAMPLES / "build_document.py")), "build_document.py")


def test_the_ingest_example_runs() -> None:
    """Milestone 2's example, which had the same gap and the same fix."""
    pytest.importorskip("pymupdf", reason="parse_pdf.py needs the docdoc[pdf] extra")
    fixture = pathlib.Path("tests/fixtures/pdf/digital_invoice.pdf")
    assert fixture.is_file(), "the committed fixture the documented command names"
    _assert_ran(_run(str(EXAMPLES / "parse_pdf.py"), str(fixture)), "parse_pdf.py")


def test_the_grounding_example_runs_with_no_credentials() -> None:
    """Milestone 4's example. Grounding reaches no network at all, so unlike the
    extraction example there is not even an adapter to stand in for one."""
    _assert_ran(_run(str(EXAMPLES / "ground_invoice.py")), "ground_invoice.py")


def test_the_grounding_example_demonstrates_what_it_claims_to() -> None:
    """It claims a ligature still resolves at the exact tier and points at the source.

    Asserting the output rather than the exit code, because an example that runs
    and prints something else is worse than one that fails: it is documentation
    that has quietly stopped being true.
    """
    result = _run(str(EXAMPLES / "ground_invoice.py"))
    out = result.stdout
    assert "exact" in out
    # The located text read back out of the untouched source, ligature intact.
    assert "Ofﬁce" in out, "the example should show the source's own ligature"
    assert "grounding rate: 100%" in out
    assert "not_applicable = 1" in out, "the reported absence should stay out of the rate"


def test_the_validation_example_runs_with_no_credentials() -> None:
    """Milestone 5's example. It passes no document to the validator at all."""
    _assert_ran(_run(str(EXAMPLES / "validate_invoice.py")), "validate_invoice.py")


def test_the_validation_example_demonstrates_what_it_claims_to() -> None:
    """It claims a sound invoice passes, a line-short one is rejected, and the
    rejection can be pointed at on the page.

    Asserting the output rather than the exit code: an example that runs and
    prints something else is documentation that has quietly stopped being true.
    """
    out = _run(str(EXAMPLES / "validate_invoice.py")).stdout
    assert "verdict: valid" in out
    assert "verdict: invalid" in out
    assert "sum_mismatch" in out
    assert "expected 1420.00, got 1240.00" in out
    # The location the finding carries came from the grounding outcome.
    assert "found on page 0" in out
    assert "reading '1420.00'" in out


def test_the_harness_reads_utf8_whatever_the_parent_locale_is() -> None:
    """The Windows failure of this file's own `_run`, reproduced on every platform.

    `_run` sets the *child's* encoding and, until Milestone 4, left its own
    decoding to the parent's locale. On Linux and macOS that is UTF-8, so a
    ligature round-tripped and every local run and review pass was clean. On
    Windows it is cp1252, which cannot decode a UTF-8 continuation byte, so
    `stdout` came back `None` and the assertion died on that rather than on
    anything the example did.

    This is the same shape as the bug `_run` was built to catch, one level up:
    the harness had a platform assumption it did not know it was making. So it
    is checked the same way -- force the failing locale rather than wait for the
    slowest machine. `PYTHONCOERCECLOCALE` and `PYTHONUTF8` are needed because
    PEP 538 and PEP 540 would otherwise quietly rescue the C locale into UTF-8,
    which is exactly the kind of rescue Windows does not perform.
    """
    ascii_locale = {
        **os.environ,
        "LC_ALL": "C",
        "LANG": "C",
        "PYTHONCOERCECLOCALE": "0",
        "PYTHONUTF8": "0",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            # The needle is written as an escape, not as the character. Under an
            # ASCII locale Python decodes `-c` itself with that codec, so a
            # literal ligature here would arrive mangled and the test would fail
            # on its own comparison string rather than on the harness.
            "import tests.integration.test_examples_run as m, pathlib;"
            "r = m._run(str(pathlib.Path('examples') / 'ground_invoice.py'));"
            "print('LIGATURE_READ' if 'Of\\ufb01ce' in (r.stdout or '') else 'LOST')",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TIMEOUT_S,
        check=False,
        env=ascii_locale,
    )
    assert "LIGATURE_READ" in (result.stdout or ""), (
        "_run could not read back an example's non-ASCII output under an ASCII "
        f"locale. It is decoding with the parent's locale rather than explicitly.\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr[-1500:]}"
    )


def test_every_committed_example_is_covered_here() -> None:
    """The assertion that keeps this file honest as examples are added.

    Listing them by directory rather than by hand means a fourth example fails
    this until someone runs it — the same shape as the adapter-coverage check in
    the contract suite, and for the same reason.
    """
    shipped = {path.name for path in EXAMPLES.glob("*.py")}
    covered = {
        "extract_invoice.py",
        "build_document.py",
        "parse_pdf.py",
        "ground_invoice.py",
        "validate_invoice.py",
    }
    assert shipped <= covered, (
        f"these examples ship but nothing executes them: {sorted(shipped - covered)}"
    )
