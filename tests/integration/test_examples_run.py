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

import pathlib
import subprocess
import sys

import pytest

EXAMPLES = pathlib.Path("examples")
TIMEOUT_S = 120


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
        check=False,
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


def test_every_committed_example_is_covered_here() -> None:
    """The assertion that keeps this file honest as examples are added.

    Listing them by directory rather than by hand means a fourth example fails
    this until someone runs it — the same shape as the adapter-coverage check in
    the contract suite, and for the same reason.
    """
    shipped = {path.name for path in EXAMPLES.glob("*.py")}
    covered = {"extract_invoice.py", "build_document.py", "parse_pdf.py"}
    assert shipped <= covered, (
        f"these examples ship but nothing executes them: {sorted(shipped - covered)}"
    )
