"""The command line is for operating a deployment, and nothing else.

docdoc is a service: documents reach it through the HTTP API and the console.
The commands that processed documents on a laptop (`parse`, `extract`,
`inspect`, `explain`, `eval`) are gone, and these tests keep them gone while
proving every operating command still parses.
"""

from __future__ import annotations

import argparse

import pytest

from docdoc.cli import build_parser, main

OPERATING = {"migrate", "sweep", "credential", "console-user", "erase", "worker", "store"}


def _subcommand_names(parser: argparse.ArgumentParser) -> set[str]:
    return {
        name
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
        for name in action.choices
    }


def test_the_cli_offers_only_operating_commands() -> None:
    assert _subcommand_names(build_parser()) == OPERATING


@pytest.mark.parametrize("command", sorted(OPERATING))
def test_every_operating_command_still_parses(command: str) -> None:
    # SystemExit(0) from --help means the subparser and every option it needs still exist.
    with pytest.raises(SystemExit) as done:
        main([command, "--help"])
    assert done.value.code == 0
