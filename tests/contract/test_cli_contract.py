"""Every claim in ``contracts/cli.md``, asserted rather than described.

The contract is short and each of its four sections is checkable, so this file is
organised the way that document is: the command set, the two output forms, the
exit codes, and the configuration precedence.

The one that earns its keep is the ``0``/``1`` split. A caller must never have to
grep output text to tell a failed validation from a failed run, and a script that
treats "this invoice is wrong" as "docdoc is broken" is the outcome a single
non-zero code guarantees.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from docdoc.cli import (
    EXIT_BAD_INVOCATION,
    EXIT_COULD_NOT_RUN,
    EXIT_INVALID,
    EXIT_OK,
    build_parser,
    main,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

FIXTURE = "tests/fixtures/pdf/digital_invoice.pdf"
SCHEMA = "invoice@1"


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The offline path: schemas, the echo adapter, and its fixtures.

    Set for every test here, because the contract's §5 says these commands run
    with no credentials and no network — so a test that needed a credential
    would be testing something the contract does not describe.
    """
    monkeypatch.setenv("DOCDOC_SCHEMA_PATHS", str(Path("schemas").resolve()))
    monkeypatch.setenv("DOCDOC_MODEL_ADAPTERS", "echo")
    monkeypatch.setenv("DOCDOC_ECHO_FIXTURES", str(Path("tests/fixtures/echo").resolve()))
    monkeypatch.delenv("DOCDOC_STORE_ROOT", raising=False)


def run(argv: Sequence[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# -- §1 the command set ------------------------------------------------------


def test_the_command_set_is_the_six_the_contract_names() -> None:
    parser = build_parser()
    subcommands = next(
        action for action in parser._actions if hasattr(action, "choices") and action.choices
    )
    assert set(subcommands.choices) == {"parse", "extract", "inspect", "explain", "eval", "store"}


# -- §2 the two output forms -------------------------------------------------


def test_json_writes_exactly_one_document_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """The whole of §2: stdout carries one JSON document and nothing else."""
    pytest.importorskip("pymupdf")  # SC-013: skips on a base install
    code, out, _ = run(["extract", FIXTURE, "--schema", SCHEMA, "--json"], capsys)
    assert code in {EXIT_OK, EXIT_INVALID}
    parsed = json.loads(out)  # would raise on a banner, a warning, or two documents
    assert parsed["processing_id"].startswith("sha256:")


def test_diagnostics_go_to_stderr_and_leave_stdout_parseable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A warning must not be able to corrupt the document a caller is piping.

    Provoked with a real diagnostic rather than a synthetic one: a store root
    that cannot be written degrades, logs, and continues (FR-063), which is
    exactly the shape of event that would land on stdout if anything did.
    """
    pytest.importorskip("pymupdf")  # SC-013: skips on a base install
    code, out, _ = run(
        ["extract", FIXTURE, "--schema", SCHEMA, "--json", "--store", "/proc/nonexistent"],
        capsys,
    )
    assert code in {EXIT_OK, EXIT_INVALID}
    json.loads(out)


def test_the_human_form_writes_no_json(capsys: pytest.CaptureFixture[str]) -> None:
    _, out, _ = run(["inspect", FIXTURE, "--schema", SCHEMA], capsys)
    assert out.strip()
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


def test_both_forms_carry_the_same_verdict(capsys: pytest.CaptureFixture[str]) -> None:
    """The human form is a projection of the machine form, not a second report."""
    pytest.importorskip("pymupdf")  # SC-013: skips on a base install
    _, machine, _ = run(["extract", FIXTURE, "--schema", SCHEMA, "--json"], capsys)
    _, human, _ = run(["extract", FIXTURE, "--schema", SCHEMA], capsys)
    verdict = json.loads(machine)["verdict"]
    assert verdict in human


# -- §3 exit codes -----------------------------------------------------------


def test_a_valid_document_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    pytest.importorskip("pymupdf")  # SC-013: skips on a base install
    code, _, _ = run(["extract", FIXTURE, "--schema", SCHEMA], capsys)
    assert code == EXIT_OK


def test_a_run_that_could_not_complete_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    """A schema the registry does not hold. The run is real; the answer is not."""
    code, _, _ = run(["extract", FIXTURE, "--schema", "no-such-schema@1"], capsys)
    assert code == EXIT_COULD_NOT_RUN


def test_a_bad_invocation_exits_sixty_four(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, _ = run(["parse", "/no/such/file.pdf"], capsys)
    assert code == EXIT_BAD_INVOCATION


def test_no_subcommand_exits_sixty_four(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run([], capsys)
    assert code == EXIT_BAD_INVOCATION
    assert out == "", "usage belongs on stderr; stdout is for results"


def test_an_unknown_stage_is_an_invocation_error(capsys: pytest.CaptureFixture[str]) -> None:
    """``clear(stage="extarct")`` would remove nothing and report success.

    A typo that reads as a completed teardown is the kind of silence that makes a
    later cache incident inexplicable, so the stage name is checked.
    """
    code, _, _ = run(["store", "clear", "--stage", "extarct", "--store", "/tmp/x"], capsys)
    assert code == EXIT_BAD_INVOCATION


def test_a_failed_run_still_reports_the_stages_that_succeeded(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """§3's last line, and FR-004 at the boundary rather than in the library.

    Needs a native PDF reader, unlike the rest of this module: the assertion is
    that the parse *succeeded* and was reported, and on a base install with no
    reader the parse is what fails. Guarded here rather than at module scope so
    the exit codes and output forms — which need no reader — still run there.
    """
    pytest.importorskip("pymupdf")
    code, out, _ = run(
        ["extract", FIXTURE, "--schema", "no-such-schema@1", "--json"], capsys
    )
    assert code == EXIT_COULD_NOT_RUN

    payload = json.loads(out)
    assert payload["failed_stage"] == "extract"
    statuses = {item["stage"]: item["status"] for item in payload["outcomes"]}
    assert statuses["parse"] == "executed", "the parse happened and must be reported"
    assert statuses["extract"] == "failed"
    assert statuses["ground"] == "skipped", "never attempted is not the same as failed"


# -- FR-051, over every command ----------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["parse", "/no/such/file.pdf"],
        ["extract", FIXTURE, "--schema", "no-such-schema@1"],
        ["inspect", "/no/such/file.pdf", "--schema", SCHEMA],
        ["explain", "not-an-identity"],
        ["eval", "/no/such/manifest.json", "--predictions", "/no/such/dir"],
        ["store", "clear", "--store", "/no/such/store"],
    ],
)
def test_no_untyped_exception_escapes_any_command(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """SC-011, asserted the only way it can be: by trying to make one escape.

    Every one of these fails, and every one must fail as an exit code rather than
    a traceback. ``main`` returning at all is the assertion.
    """
    code, _, _ = run(argv, capsys)
    assert code in {EXIT_OK, EXIT_INVALID, EXIT_COULD_NOT_RUN, EXIT_BAD_INVOCATION}


# -- §4 configuration --------------------------------------------------------


def test_an_explicit_flag_beats_the_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("pymupdf")  # SC-013: skips on a base install
    monkeypatch.setenv("DOCDOC_SCHEMA_PATHS", "/no/such/schemas")
    code, _, _ = run(
        ["extract", FIXTURE, "--schema", SCHEMA, "--schema-path", "schemas"], capsys
    )
    assert code == EXIT_OK


def test_no_store_beats_a_configured_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The safer reading of an ambiguous invocation is the one that writes nothing."""
    pytest.importorskip("pymupdf")  # SC-013: skips on a base install
    monkeypatch.setenv("DOCDOC_STORE_ROOT", str(tmp_path))
    code, _, _ = run(["extract", FIXTURE, "--schema", SCHEMA, "--no-store"], capsys)
    assert code == EXIT_OK
    assert not (tmp_path / "artifacts").exists()


def test_an_empty_registry_says_so_and_names_the_setting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """US1 scenario 5. "Schema not found" would send the reader hunting a typo."""
    monkeypatch.setenv("DOCDOC_SCHEMA_PATHS", "")
    code, _, err = run(["extract", FIXTURE, "--schema", SCHEMA], capsys)
    assert code == EXIT_COULD_NOT_RUN
    assert "registry is empty" in err
    assert "DOCDOC_SCHEMA_PATHS" in err


# -- FR-026: inspecting a *result*, not only a file --------------------------


def test_inspect_reads_a_stored_result_by_its_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR-026 and CHK024 — the asymmetry with `GET /v1/jobs/{id}/result`, closed.

    Somebody holding a `processing_id` from a log had an HTTP path to the result
    and no command-line one, because `inspect` took a file and re-ran the
    pipeline. Reading the stored run must produce the same values, verdicts, and
    locations — and must execute nothing.
    """
    pytest.importorskip("pymupdf")
    store = str(tmp_path)

    code, out, _ = run(
        ["extract", FIXTURE, "--schema", SCHEMA, "--store", store, "--json"], capsys
    )
    assert code == EXIT_OK
    produced = json.loads(out)

    code, out, _ = run(["inspect", "--result", produced["processing_id"], "--store", store,
                        "--json"], capsys)
    assert code == EXIT_OK

    read_back = json.loads(out)
    assert read_back["source"] == "store"
    assert read_back["processing_id"] == produced["processing_id"]
    assert read_back["verdict"] == produced["verdict"]
    assert read_back["fields"] == produced["fields"]
    assert read_back["outcomes"] == [], (
        "a retrieval must not report stage statuses for work it did not do"
    )


def test_inspect_does_not_recompute_a_result_it_cannot_find(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR-036 — the inputs may have moved, and a different result under the same
    identity would break the one promise that identity makes."""
    absent = "sha256:" + "0" * 64
    code, out, err = run(
        ["inspect", "--result", absent, "--store", str(tmp_path), "--json"], capsys
    )

    assert code == EXIT_OK, "being asked about an unknown identity is not a failure"
    assert json.loads(out)["result"] is None
    assert json.loads(out)["reason"] == "not_in_store"
    assert "not recomputed" in err


def test_inspect_with_no_store_says_so_rather_than_running(
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = ["inspect", "--result", "sha256:" + "0" * 64, "--no-store", "--json"]
    code, out, err = run(argv, capsys)
    assert code == EXIT_OK
    assert json.loads(out)["reason"] == "no_store"
    assert "DOCDOC_STORE_ROOT" in err


@pytest.mark.parametrize(
    "argv",
    [
        ["inspect"],
        ["inspect", FIXTURE],
        ["inspect", "--result", "sha256:" + "0" * 64, FIXTURE],
    ],
)
def test_inspect_refuses_an_ambiguous_or_incomplete_invocation(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """One of FILE or --result, and FILE needs --schema. Exit 64, not a traceback."""
    code, _, _ = run(argv, capsys)
    assert code == EXIT_BAD_INVOCATION
