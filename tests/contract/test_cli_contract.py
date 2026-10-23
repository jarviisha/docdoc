"""Every claim in ``contracts/cli.md`` that the operating commands still make.

The command line operates a deployment; documents are processed through the HTTP
API. What is left to assert is the command set, the one-JSON-document output
rule, the exit codes for a bad invocation, and the configuration precedence the
worker reads through ``Settings``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from docdoc.cli import (
    EXIT_BAD_INVOCATION,
    EXIT_COULD_NOT_RUN,
    EXIT_OK,
    build_parser,
    main,
)
from docdoc.cli.config import Settings

if TYPE_CHECKING:
    from collections.abc import Sequence


def run(argv: Sequence[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# -- §1 the command set ------------------------------------------------------


def test_the_command_set_is_the_seven_operating_commands() -> None:
    parser = build_parser()
    subcommands = next(
        action for action in parser._actions if hasattr(action, "choices") and action.choices
    )
    assert set(subcommands.choices) == {
        "store",
        # Milestone 9. The only command that touches a database, and the only one
        # whose absence would leave a deployment's schema to whichever worker
        # started first (FR-078).
        "migrate",
        # Also Milestone 9. Blocks until signalled, which is what it is for, and
        # takes no --concurrency: one run per process, concurrency is replicas
        # (FR-025).
        "worker",
        # Milestone 10. The worker sweeps between claims, so this exists for the
        # two cases that does not cover: an operator who wants a sweep *now*, and
        # a deployment running no worker, which would otherwise never sweep at
        # all (FR-117).
        "sweep",
        # Also Milestone 10, and the only home of `--purge-store-root`. The HTTP
        # route refuses the default tenant and points here, because erasing the
        # store root is a thing an operator types rather than a thing a URL does
        # (ADR-0015 section 5).
        "erase",
        # Also Milestone 10. `--admin` lives on `issue` and at no URL: a route
        # that could mint an administrative credential is a route that needs no
        # credential (FR-032).
        "credential",
        # ADR-0023 §1. The console itself has no account-management screen, so
        # this is the only place a console account is created, re-keyed,
        # disabled, or listed.
        "console-user",
    }


# -- §2 the output form ------------------------------------------------------


def test_json_writes_exactly_one_document_to_stdout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole of §2: stdout carries one JSON document and nothing else."""
    code, out, _ = run(["store", "clear", "--store", str(tmp_path), "--json"], capsys)
    assert code == EXIT_OK
    parsed = json.loads(out)  # would raise on a banner, a warning, or two documents
    assert parsed["cleared"] == 0


def test_diagnostics_go_to_stderr_and_leave_stdout_parseable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A diagnostic must not be able to corrupt the document a caller is piping.

    A bad invocation writes its sentence to stderr and the failure document to
    stdout, and the document must still parse.
    """
    code, out, err = run(["store", "clear", "--stage", "extarct", "--no-store", "--json"], capsys)
    assert code == EXIT_BAD_INVOCATION
    assert "extarct" in err
    assert json.loads(out)["error"]["class"] == "ValueError"


# -- §3 exit codes -----------------------------------------------------------


def test_a_command_that_could_not_complete_exits_two(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A typed docdoc error: here, no run-state database to sweep."""
    monkeypatch.delenv("DOCDOC_RUN_DATABASE_URL", raising=False)
    code, _, _ = run(["erase", "--tenant", "acme"], capsys)
    assert code == EXIT_COULD_NOT_RUN


def test_worker_refuses_an_unknown_kind(capsys: pytest.CaptureFixture[str]) -> None:
    """014: `--kind` is `ocr` or `extraction`; argparse refuses anything else (exit 2)."""
    with pytest.raises(SystemExit) as refused:
        build_parser().parse_args(["worker", "--kind", "bogus"])
    assert refused.value.code == 2


def test_worker_parses_a_kind() -> None:
    args = build_parser().parse_args(["worker", "--kind", "ocr"])
    assert args.kind == "ocr"


def test_no_subcommand_exits_sixty_four(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run([], capsys)
    assert code == EXIT_BAD_INVOCATION
    assert out == "", "usage belongs on stderr; stdout is for results"


@pytest.mark.parametrize(
    "store_argv",
    [
        pytest.param(["--store", "/tmp/x"], id="store-configured"),
        pytest.param(["--no-store"], id="no-store"),
        pytest.param([], id="store-unset"),
    ],
)
def test_an_unknown_stage_is_an_invocation_error(
    store_argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """``clear(stage="extarct")`` would remove nothing and report success.

    A typo that reads as a completed teardown is the kind of silence that makes a
    later cache incident inexplicable, so the stage name is checked.

    **Under every store state**, which is the part this test did not cover until
    2026-08-25. The check sat behind the "is a store configured?" branch, so
    `--stage extarct --no-store` exited 0 saying only that there was nothing to
    clear — and the reader most likely to mistype a stage is the one who has not
    set the store up yet. An argument is well-formed or it is not, and that
    judgement owes nothing to configuration elsewhere.
    """
    code, _, _ = run(["store", "clear", "--stage", "extarct", *store_argv], capsys)
    assert code == EXIT_BAD_INVOCATION


def test_a_valid_stage_with_no_store_is_not_an_error(capsys: pytest.CaptureFixture[str]) -> None:
    """Guards the guard above: the refusal must be the typo, not the missing store.

    "Nothing to clear" is a correct answer and exit 0, which is what keeps
    `docdoc store clear` usable in a teardown script that runs whether or not a
    store was ever configured.
    """
    code, _, _ = run(["store", "clear", "--stage", "extract", "--no-store"], capsys)
    assert code == EXIT_OK


def test_the_store_command_advertises_only_the_action_it_has(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """§1 grants `clear` and nothing else, and the help text must say so.

    It read "inspect and clear the artifact store" for a milestone, and there is
    no inspect action — FR-019 deliberately grants two subsets and no query
    language, so a store you can interrogate is precisely what it withholds.
    Help output is the one piece of documentation that ships inside the program,
    which makes it the last place a promise should outrun the code.
    """
    subcommands = next(
        action
        for action in build_parser()._actions
        if hasattr(action, "choices") and action.choices
    )
    assert "inspect" not in subcommands.choices["store"].format_help()

    store_help = subcommands._get_subactions()
    summary = next(item.help or "" for item in store_help if item.dest == "store")
    assert "inspect" not in summary, f"the store command still advertises inspect: {summary!r}"


# -- FR-051, over every command ----------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["store", "clear", "--store", "/no/such/store"],
        ["store", "clear", "--stage", "extarct"],
        ["store"],
        ["credential"],
        ["console-user"],
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
    assert code in {EXIT_OK, EXIT_COULD_NOT_RUN, EXIT_BAD_INVOCATION}


# -- §4 configuration --------------------------------------------------------
#
# The worker reads its schemas, adapter, store and limits through `Settings`, so
# the precedence is asserted on what `Settings.resolve` hands it.


def _worker_settings(*argv: str) -> Settings:
    return Settings.resolve(build_parser().parse_args(["worker", *argv]))


def test_an_explicit_flag_beats_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCDOC_SCHEMA_PATHS", "/no/such/schemas")
    assert _worker_settings("--schema-path", "schemas").schema_paths == (Path("schemas"),)
    assert _worker_settings().schema_paths == (Path("/no/such/schemas"),)


def test_no_store_beats_a_configured_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The safer reading of an ambiguous invocation is the one that writes nothing."""
    monkeypatch.setenv("DOCDOC_STORE_ROOT", str(tmp_path))
    monkeypatch.setenv("DOCDOC_STORE_URL", "s3://bucket")
    assert _worker_settings().has_store
    settings = _worker_settings("--store", str(tmp_path), "--no-store")
    assert not settings.has_store
    assert settings.blobs() is None
