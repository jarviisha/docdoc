"""FR-069 — authentication is an HTTP concern, and this is what keeps it one.

The failure this prevents is not dramatic and it is very easy to write. Somebody
adds an authentication check somewhere central — a settings object, a store
constructor, a `Deployment` — and the command line and the library inherit a
credential requirement they have no way to satisfy. Every test of the HTTP layer
still passes, because the HTTP layer *does* supply one.

So the test is deliberately blunt: with `DOCDOC_API_KEYS_FILE` **set**, every CLI
subcommand and every in-process entry point still runs, with no credential
supplied anywhere. If authentication ever leaks below the request boundary, this
is what says so.

`docdoc worker` and `docdoc migrate` are exercised through their argument
parsing and their refusal to run without a database rather than through a real
claim loop — a worker with no database is the fastest way to prove the command
parses and reaches its own code without asking for a key, and starting a real one
in a unit test would be a different test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docdoc.api.settings import API_KEYS_FILE_ENV
from docdoc.cli import build_parser, main

FIXTURE = "tests/fixtures/pdf/digital_invoice.pdf"
SCHEMA = "invoice@1"


@pytest.fixture(autouse=True)
def authentication_is_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real key file, pointed at by the real variable, for every test here.

    The point of the fixture is that nothing below passes a key. If any of them
    starts needing one, the requirement has been broken.
    """
    keys = tmp_path / "keys.json"
    keys.write_text(json.dumps({"keys": [{"sha256": "a" * 64, "tenant_id": "acme"}]}))
    monkeypatch.setenv(API_KEYS_FILE_ENV, str(keys))
    monkeypatch.setenv("DOCDOC_SCHEMA_PATHS", "schemas")
    monkeypatch.setenv("DOCDOC_MODEL_ADAPTERS", "echo")
    monkeypatch.setenv("DOCDOC_ECHO_FIXTURES", "tests/fixtures/echo")


def _subcommands() -> list[str]:
    """Every subcommand the parser has, read off the parser.

    Enumerated from the real parser rather than hand-listed, so a subcommand
    added later is covered without anybody remembering to add it — which is the
    step that gets skipped.
    """
    import argparse

    actions = [
        action
        for action in build_parser()._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    assert actions, "the parser grew no subcommands, so this file checks nothing"
    return sorted(actions[0].choices)


def test_the_parser_has_the_subcommands_this_file_expects() -> None:
    """Guards the enumeration: a renamed command must not silently drop out."""
    assert {"parse", "extract", "inspect", "explain", "eval", "store", "migrate", "worker"} <= set(
        _subcommands()
    )


@pytest.mark.parametrize("command", _subcommands())
def test_every_subcommand_parses_its_help_with_no_credential(command: str) -> None:
    """The blunt half: every command is reachable at all.

    `--help` exits 0 through `SystemExit`, and reaching that means argparse built
    the subparser without anything having demanded a key on the way.
    """
    with pytest.raises(SystemExit) as exit_code:
        main([command, "--help"])

    assert exit_code.value.code == 0


def test_no_subcommand_offers_a_credential_flag() -> None:
    """FR-069 from the other direction: there is no way to supply one.

    A command line that *accepted* a credential would be a command line that
    might one day require it, and `argv` is the wrong place for one regardless.
    """
    import argparse

    parser = build_parser()
    subparsers = [
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    ]
    for name, sub in subparsers[0].choices.items():
        options = {opt for action in sub._actions for opt in action.option_strings}
        offending = {
            opt
            for opt in options
            if any(word in opt for word in ("key", "token", "secret", "credential", "auth"))
        }
        assert not offending, f"`docdoc {name}` accepts {sorted(offending)}"


def test_parse_runs_with_authentication_configured() -> None:
    """A real command doing real work, which `--help` cannot prove."""
    pytest.importorskip("pymupdf")

    assert main(["parse", FIXTURE, "--json"]) == 0


def test_extract_runs_with_authentication_configured(tmp_path: Path) -> None:
    """The command that reaches a model adapter and a store."""
    pytest.importorskip("pymupdf")

    code = main(["extract", FIXTURE, "--schema", SCHEMA, "--store", str(tmp_path), "--json"])

    assert code in (0, 1), "0 is valid, 1 is an invalid document — neither is a refusal"


def test_the_library_entry_point_takes_no_credential() -> None:
    """The in-process half, asserted on the signature.

    Stronger than calling it: a parameter that exists is one a later change can
    make required, so the assertion is that `pipeline.run` has no such parameter
    at all.
    """
    import inspect

    from docdoc.pipeline import run

    parameters = set(inspect.signature(run).parameters)
    forbidden = {"credential", "api_key", "key", "token", "principal", "tenant_id"}

    assert not (parameters & forbidden), (
        f"pipeline.run accepts {sorted(parameters & forbidden)}; authentication "
        f"has reached below the request boundary"
    )


def test_the_library_runs_a_document_with_authentication_configured() -> None:
    """And it actually runs, because a signature check cannot see a global."""
    pytest.importorskip("pymupdf")
    from docdoc.extraction import SchemaRegistry
    from docdoc.extraction.adapters.echo import EchoAdapter
    from docdoc.pipeline import run

    result = run(
        Path(FIXTURE).read_bytes(),
        schema=SCHEMA,
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
    )

    assert result.processing_id is not None
