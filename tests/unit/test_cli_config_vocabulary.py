"""T137 — the environment and the command line, compared as *sets* (FR-031).

Four convergence passes read this milestone and none found that
``DOCDOC_MAX_DOCUMENT_BYTES``, ``DOCDOC_MAX_PAGES``, and
``DOCDOC_MATCH_VIEW_CACHE`` had no flag, while ``README.md`` and
``contracts/cli.md`` both said in so many words that every setting had one. The
reason is worth stating, because it is the shape of the gap rather than the gap
itself: ``test_documented_api_references_resolve.py`` already checks both
directions between the *variables* and the *documents*, and would have caught a
setting the code reads and nothing documents, or a setting a document names and
nothing reads. It never compared the variables against the **flags**.

FR-031 is not a claim about either list. It is a claim about the relationship
between them, so it needs a test that holds both at once — which is this one. The
map it checks against is `docdoc.cli.config.FLAG_FOR_SETTING` and its deliberate
exclusions `ENVIRONMENT_ONLY`, so a setting added later lands in one of them or
fails here.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

from docdoc.cli import EXIT_BAD_INVOCATION, build_parser, main
from docdoc.cli.config import COMMAND_SCOPED, ENVIRONMENT_ONLY, FLAG_FOR_SETTING, Settings

# The canonical list of every configuration name the code reads. Imported rather
# than restated: a third copy of this list is how the second and third would
# start to disagree.
from tests.unit.test_documented_api_references_resolve import _defined_env_names

FIXTURE = "tests/fixtures/pdf/digital_invoice.pdf"
#: Three pages. `FIXTURE` is one, so it can prove nothing about a page cap.
MULTIPAGE = "tests/fixtures/pdf/mixed_pages.pdf"


# -- the parity itself --------------------------------------------------------


def test_every_setting_has_a_flag_or_a_recorded_reason_not_to() -> None:
    """The assertion four passes did not have.

    A setting on neither list is the defect this file exists for: it is
    environment-only by accident, and the accident is invisible because each
    half of the system is individually correct.
    """
    defined = set(_defined_env_names())
    classified = set(FLAG_FOR_SETTING) | set(ENVIRONMENT_ONLY)

    unclassified = sorted(defined - classified)
    assert not unclassified, (
        f"these settings have neither a command-line flag nor a recorded reason to lack "
        f"one: {unclassified}. FR-031 requires the command line to accept the same "
        f"settings as explicit arguments; if one of these genuinely should not have a "
        f"flag, add it to docdoc.cli.config.ENVIRONMENT_ONLY with the reason, so the "
        f"exclusion is a decision rather than an oversight"
    )


def test_no_setting_is_on_both_lists() -> None:
    """Both lists is as wrong as neither, and quieter."""
    both = sorted(set(FLAG_FOR_SETTING) & set(ENVIRONMENT_ONLY))
    assert not both, f"claimed as both flagged and environment-only: {both}"


def test_the_maps_describe_settings_that_exist() -> None:
    """The other direction: a map entry for a variable nothing reads."""
    defined = set(_defined_env_names())
    phantom = sorted((set(FLAG_FOR_SETTING) | set(ENVIRONMENT_ONLY)) - defined)
    assert not phantom, (
        f"named in docdoc.cli.config but read by no module: {phantom}. The setting was "
        f"renamed or removed and this map is now describing nothing"
    )


def test_every_exclusion_carries_a_reason() -> None:
    """An empty reason is the oversight wearing the decision's clothes."""
    for setting, reason in ENVIRONMENT_ONLY.items():
        assert reason.strip(), f"{setting} is excluded from the flags with no reason given"


# -- the flags in the map are the flags on the parser -------------------------


def _option_strings(parser: argparse.ArgumentParser) -> set[str]:
    return {option for action in parser._actions for option in action.option_strings}


def test_every_mapped_flag_exists_on_the_root_parser() -> None:
    """The map is only worth trusting if it describes the real parser.

    Command-scoped flags are excluded: they live on their own subparsers by
    design, and `_option_strings` on the root sees only the shared set. The
    subcommand test below is what checks those, from both directions.
    """
    available = _option_strings(build_parser())
    missing = sorted(
        flag
        for setting, flag in FLAG_FOR_SETTING.items()
        if setting not in COMMAND_SCOPED and flag not in available
    )
    assert not missing, f"named in FLAG_FOR_SETTING and absent from the parser: {missing}"


@pytest.mark.parametrize(
    "command",
    ["parse", "extract", "inspect", "explain", "eval", "migrate"],
)
def test_every_mapped_flag_exists_on_each_subcommand(command: str) -> None:
    """`docdoc extract --store` must work as readily as `docdoc --store extract`.

    The flags are added per-subparser for that reason, and a flag added to the
    common set but reached only from the root would be a puzzle rather than a
    tool.
    """
    subparsers = [
        action
        for action in build_parser()._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    assert subparsers, "the parser grew no subcommands"
    available = _option_strings(subparsers[0].choices[command])

    expected = {
        setting: flag
        for setting, flag in FLAG_FOR_SETTING.items()
        if command in COMMAND_SCOPED.get(setting, ()) or setting not in COMMAND_SCOPED
    }
    missing = sorted(flag for flag in expected.values() if flag not in available)
    assert not missing, f"`docdoc {command}` is missing {missing}"

    # The other half: a scoped flag must not leak onto commands it means nothing
    # to. `--run-database-url` on `docdoc parse` would be a flag that does
    # nothing to the command carrying it.
    leaked = sorted(
        flag
        for setting, flag in FLAG_FOR_SETTING.items()
        if setting in COMMAND_SCOPED
        and command not in COMMAND_SCOPED[setting]
        and flag in available
    )
    assert not leaked, f"`docdoc {command}` carries {leaked}, which it cannot use"


# -- flag beats environment beats default, for the two new settings -----------


def _settings(argv: list[str]) -> Settings:
    return Settings.resolve(build_parser().parse_args(argv))


def test_the_limit_defaults_are_the_documented_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOCDOC_MAX_DOCUMENT_BYTES", raising=False)
    monkeypatch.delenv("DOCDOC_MAX_PAGES", raising=False)

    limits = _settings(["parse", FIXTURE]).limits()

    assert limits.max_size_bytes == 50 * 1024 * 1024
    assert limits.max_pages == 1000


def test_the_environment_moves_the_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCDOC_MAX_DOCUMENT_BYTES", "4096")
    monkeypatch.setenv("DOCDOC_MAX_PAGES", "7")

    limits = _settings(["parse", FIXTURE]).limits()

    assert limits.max_size_bytes == 4096
    assert limits.max_pages == 7


def test_the_flag_beats_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The precedence every other setting uses, applied per setting.

    ``--max-pages`` given and ``--max-document-bytes`` not: the flag wins for the
    one it names and the environment still answers for the other. Per setting,
    not per source (FR-031).
    """
    monkeypatch.setenv("DOCDOC_MAX_DOCUMENT_BYTES", "4096")
    monkeypatch.setenv("DOCDOC_MAX_PAGES", "7")

    limits = _settings(["parse", FIXTURE, "--max-pages", "3"]).limits()

    assert limits.max_pages == 3
    assert limits.max_size_bytes == 4096


def test_the_flag_answers_with_no_environment_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOCDOC_MAX_DOCUMENT_BYTES", raising=False)
    monkeypatch.delenv("DOCDOC_MAX_PAGES", raising=False)

    limits = _settings(["parse", FIXTURE, "--max-document-bytes", "1024"]).limits()

    assert limits.max_size_bytes == 1024


# -- the flags actually reach the parse, and a bad one is an invocation error --


def test_an_undersized_limit_refuses_the_document_before_it_is_parsed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The point of the flag: an operator can set the limit FR-039 promises.

    Proving it through the *refusal* rather than through the resolved settings
    is deliberate — a limit that resolves correctly and is never handed to the
    parse would satisfy every test above and change nothing.
    """
    monkeypatch.delenv("DOCDOC_MAX_DOCUMENT_BYTES", raising=False)
    size = Path(FIXTURE).stat().st_size

    code = main(["parse", FIXTURE, "--max-document-bytes", str(size - 1), "--json"])

    assert code != 0
    assert "UnsupportedDocumentError" in capsys.readouterr().out


def test_the_same_document_passes_under_a_sufficient_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guards the guard: the refusal above must be the limit, not the fixture.

    This one needs a parser that can actually read a PDF; the refusal above does
    not, because the size check runs before a parser is selected. That asymmetry
    is FR-039's "before parsing it and before contacting any provider" showing up
    as a property of the test suite.
    """
    pytest.importorskip("pymupdf")  # SC-013: skips on a base install
    monkeypatch.delenv("DOCDOC_MAX_DOCUMENT_BYTES", raising=False)
    size = Path(FIXTURE).stat().st_size

    assert main(["parse", FIXTURE, "--max-document-bytes", str(size), "--json"]) == 0


def test_the_page_limit_refuses_a_document_with_too_many_pages(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other flag, against a fixture that actually has pages to exceed.

    `digital_invoice.pdf` is one page, so it can prove nothing about a page cap;
    `mixed_pages.pdf` is three. The check fires as soon as the page count is
    known — after routing, before the parser runs — which is the earliest point
    it can (ING-2).
    """
    pytest.importorskip("pymupdf")  # SC-013: skips on a base install
    monkeypatch.delenv("DOCDOC_MAX_PAGES", raising=False)

    refused = main(["parse", MULTIPAGE, "--max-pages", "2", "--json"])
    assert refused != 0
    assert "UnsupportedDocumentError" in capsys.readouterr().out

    assert main(["parse", MULTIPAGE, "--max-pages", "3", "--json"]) == 0


def _document_id(argv: list[str], capsys: pytest.CaptureFixture[str]) -> str:
    assert main(argv) == 0
    return json.loads(capsys.readouterr().out)["document_id"]


def test_moving_a_limit_moves_no_identity(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR-060, for the lever T137 opened — and the reason it needed one.

    `tests/unit/test_stage_identity.py` asserts the *shape*: no options-hash
    function accepts a limit. This asserts the *behaviour* through the command a
    user actually runs, because the two can diverge — a limit could reach an
    identity by being folded into the parse options mapping rather than by
    appearing in a signature, and the shape test would not see it.

    A limit says what this deployment is willing to accept. It cannot change what
    a parse produces from a document it did accept, so every cap that accepts
    this document must yield the same `document_id`. If it did not, two
    deployments with different caps would share a store and reuse nothing from
    each other, and neither could tell.
    """
    pytest.importorskip("pymupdf")  # SC-013: skips on a base install
    monkeypatch.delenv("DOCDOC_MAX_DOCUMENT_BYTES", raising=False)
    monkeypatch.delenv("DOCDOC_MAX_PAGES", raising=False)
    size = Path(MULTIPAGE).stat().st_size

    unbounded = _document_id(["parse", MULTIPAGE, "--json"], capsys)
    generous = _document_id(
        ["parse", MULTIPAGE, "--json", "--max-pages", "500", "--max-document-bytes", str(size * 4)],
        capsys,
    )
    # Exactly at both limits: still accepted, and still the same document.
    exact = _document_id(
        ["parse", MULTIPAGE, "--json", "--max-pages", "3", "--max-document-bytes", str(size)],
        capsys,
    )

    assert unbounded == generous == exact, (
        "the document's identity moved with a limit that only decided whether to accept it; "
        "a limit folded into an identity fragments the store across deployments (FR-060)"
    )


def test_the_environment_form_of_a_limit_moves_no_identity_either(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The flag and the variable are one setting, so both directions are checked.

    Pinned separately because they reach `Limits` by different routes — the flag
    through `Settings.limits()`, the variable through the field's own default
    factory — and only one of them was exercised above.
    """
    pytest.importorskip("pymupdf")  # SC-013: skips on a base install
    monkeypatch.delenv("DOCDOC_MAX_PAGES", raising=False)
    plain = _document_id(["parse", MULTIPAGE, "--json"], capsys)

    monkeypatch.setenv("DOCDOC_MAX_PAGES", "3")
    bounded = _document_id(["parse", MULTIPAGE, "--json"], capsys)

    assert plain == bounded


@pytest.mark.parametrize("flag", ["--max-document-bytes", "--max-pages"])
@pytest.mark.parametrize("value", ["0", "-1"])
def test_a_non_positive_limit_is_an_invocation_error(
    flag: str, value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """64, and a sentence naming the flag the user typed.

    Left to `Limits`, this would surface as a pydantic ``ValidationError`` naming
    ``max_size_bytes`` — a field nobody typed — and would be classed as an
    untyped exception on the way out.
    """
    code = main(["parse", FIXTURE, flag, value])

    assert code == EXIT_BAD_INVOCATION
    assert flag in capsys.readouterr().err


def test_a_non_integer_limit_does_not_reach_a_traceback() -> None:
    """argparse owns this one; it must still not raise past the caller."""
    with pytest.raises(SystemExit):
        main(["parse", FIXTURE, "--max-pages", "lots"])


# -- the exclusions are true ---------------------------------------------------


def test_the_request_cap_is_unreachable_from_the_command_line() -> None:
    """`DOCDOC_MAX_REQUEST_BYTES` is excluded because the CLI reads no body.

    Pinned rather than argued: if the command line ever grows a path that reads
    a request body, this fails and the exclusion is re-examined.
    """
    from docdoc.api.settings import REQUEST_BYTES_ENV

    assert REQUEST_BYTES_ENV in ENVIRONMENT_ONLY
    assert REQUEST_BYTES_ENV not in FLAG_FOR_SETTING


def test_no_credential_is_reachable_as_a_flag() -> None:
    """FR-042, from the direction nobody checks: `argv` is world-readable.

    A credential passed as an argument is visible in `/proc` to every process on
    the host and in any shell history that recorded the line. That is a worse
    exposure than the environment variable it would have replaced, so the
    exclusion is a security property rather than an ergonomic preference.
    """
    for name in _defined_env_names():
        if "KEY" in name or "TOKEN" in name or "SECRET" in name:
            assert name not in FLAG_FOR_SETTING, (
                f"{name} looks like a credential and has a command-line flag; argv is "
                f"readable by any process on the host"
            )


def test_the_environment_only_settings_are_still_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exclusion is only honest while the variable still does something.

    The match-view bound is the one worth pinning: it is read at import time, and
    a refactor that made it lazy would change whether a flag could work at all —
    which is half the reason it has none.
    """
    from docdoc.grounding import view

    monkeypatch.setenv(view.MATCH_VIEW_CACHE_ENV, "3")
    assert view._configured_limit() == 3

    monkeypatch.delenv(view.MATCH_VIEW_CACHE_ENV, raising=False)
    assert view._configured_limit() == view.MATCH_VIEW_CACHE_LIMIT


def test_no_flag_introduces_a_second_name_for_a_setting() -> None:
    """FR-031's other half: the flag is the variable, spelled for a shell.

    `DOCDOC_MAX_DOCUMENT_BYTES` → `--max-document-bytes`. A flag that renamed the
    concept would be the second vocabulary the requirement forbids, and it is
    exactly the kind of drift that arrives one well-meant abbreviation at a time.
    """
    aliases = {
        "DOCDOC_SCHEMA_PATHS": "--schema-path",  # singular: it is repeatable
        "DOCDOC_MODEL_ADAPTERS": "--adapter",  # singular, and "model" is implied
        "DOCDOC_STORE_ROOT": "--store",  # "root" is what a directory is
        # Milestone 9. Both live only on `docdoc worker`, which already supplies
        # the word "run": `--run-lease-seconds` on a command called `worker`
        # reads as a second concept rather than as the same one. The variable
        # keeps the prefix because it is read from an environment shared with
        # `DOCDOC_RUN_DATABASE_URL`, where "which run?" is a real question.
        "DOCDOC_RUN_LEASE_SECONDS": "--lease-seconds",
        "DOCDOC_RUN_MAX_ATTEMPTS": "--max-attempts",
    }
    for setting, flag in FLAG_FOR_SETTING.items():
        if setting in aliases:
            assert flag == aliases[setting]
            continue
        expected = "--" + setting.removeprefix("DOCDOC_").lower().replace("_", "-")
        assert flag == expected, (
            f"{setting} is spelled {flag} on the command line, not {expected}. If the "
            f"difference is deliberate, record it in this test's alias map"
        )


def test_the_parity_check_can_actually_fail() -> None:
    """Guards the guard. A set comparison that compares nothing passes loudly."""
    defined = set(_defined_env_names()) | {"DOCDOC_INVENTED_FOR_THIS_TEST"}
    classified = set(FLAG_FOR_SETTING) | set(ENVIRONMENT_ONLY)

    assert defined - classified == {"DOCDOC_INVENTED_FOR_THIS_TEST"}


def test_the_fixtures_are_where_this_file_thinks_they_are() -> None:
    """These tests are worthless against a path that moved."""
    for path in (FIXTURE, MULTIPAGE):
        assert Path(path).is_file(), path
        assert os.path.getsize(path) > 0, path


def test_the_multipage_fixture_still_has_pages_to_exceed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Guards the page-limit tests against a fixture that was regenerated flat.

    A one-page `mixed_pages.pdf` would make the refusal test pass for the wrong
    reason only if the cap were also wrong — but it would make the *acceptance*
    half assert nothing at all, which is the quieter failure.
    """
    pytest.importorskip("pymupdf")  # SC-013: skips on a base install
    assert main(["parse", MULTIPAGE, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["pages"] == 3


# -- the other blind spot: a flag with no setting behind it (T102) -------------
#
# Every check above starts from the *environment* names and asks whether each has
# a flag. None of them starts from the flags, so a flag introduced with no paired
# variable is in neither `FLAG_FOR_SETTING` nor `ENVIRONMENT_ONLY` and this file
# cannot see it at all. `--health-port` arrived that way.
#
# The rule these enforce is not "every flag needs a variable" — some genuinely do
# not, and `--health-port` is one. It is that the exceptions are **declared**, so
# a flag with no setting behind it is a decision somebody made rather than an
# omission nobody noticed. That is the same argument `ENVIRONMENT_ONLY` makes in
# the other direction.

#: Flags that deliberately pair with no `DOCDOC_*` variable, each with the reason.
#:
#: Kept here rather than in `docdoc.cli.config` because these are properties of
#: the *parser*, not settings docdoc reads: there is nothing in `config.py` for
#: them to be an exception to.
FLAGS_WITHOUT_A_SETTING: dict[str, str] = {
    "--json": "an output form, chosen per invocation; there is nothing to configure",
    "--no-store": "the negation of --store, not a second setting",
    "--verify-cache": "a per-invocation mode, like --json",
    "--schema": "the schema this invocation runs; the *search path* is the setting",
    "--result": "an identity to read back, which is an argument and not a setting",
    "--chain": "a per-invocation mode",
    "--stage": "which subset of the store to clear; an argument",
    "--predictions": "where one evaluation's predictions are; an argument",
    "--check": "a per-invocation mode: report rather than apply",
    "--health-port": (
        "which port a process listens on is a property of how it was started, "
        "like uvicorn's --port, and not a setting docdoc reads from anywhere"
    ),
    "--help": "argparse's",
    "-h": "argparse's",
}


def _all_flags() -> set[str]:
    """Every option string on the root parser and on every subcommand."""
    parser = build_parser()
    flags = _option_strings(parser)
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                flags |= _option_strings(sub)
                # `store` has its own sub-subcommands, and `clear` carries flags.
                for nested in sub._actions:
                    if isinstance(nested, argparse._SubParsersAction):
                        for deeper in nested.choices.values():
                            flags |= _option_strings(deeper)
    return flags


def test_every_flag_names_a_setting_or_a_recorded_reason_not_to() -> None:
    """The assertion this file did not have, in the direction it did not look.

    A flag on neither list is the defect: it configures something, nothing
    documents what, and the parity check that exists to catch exactly that is
    blind to it because it enumerates variables.
    """
    mapped = set(FLAG_FOR_SETTING.values())
    unexplained = sorted(_all_flags() - mapped - set(FLAGS_WITHOUT_A_SETTING))

    assert not unexplained, (
        f"these flags pair with no setting and carry no recorded reason: "
        f"{unexplained}. If a flag genuinely configures nothing docdoc reads "
        f"elsewhere, add it to FLAGS_WITHOUT_A_SETTING with the reason, so the "
        f"absence is a decision rather than an oversight"
    )


def test_no_flag_is_both_mapped_and_excused() -> None:
    """Both lists is as wrong as neither, and quieter — the same rule as above."""
    both = sorted(set(FLAG_FOR_SETTING.values()) & set(FLAGS_WITHOUT_A_SETTING))
    assert not both, f"claimed as both a setting's flag and setting-less: {both}"


def test_every_excused_flag_exists_on_the_parser() -> None:
    """The other direction: an excuse for a flag that was removed describes nothing."""
    phantom = sorted(set(FLAGS_WITHOUT_A_SETTING) - _all_flags())
    assert not phantom, f"excused here and absent from the parser: {phantom}"


def test_every_excused_flag_carries_a_reason() -> None:
    for flag, reason in FLAGS_WITHOUT_A_SETTING.items():
        assert reason.strip(), f"{flag} is excused with no reason given"


def test_the_flag_walk_reaches_the_subcommands() -> None:
    """Guards the guard: a walk that saw only the root would excuse everything."""
    flags = _all_flags()

    assert "--lease-seconds" in flags, "the walk missed `docdoc worker`"
    assert "--check" in flags, "the walk missed `docdoc migrate`"
    assert "--stage" in flags, "the walk missed `docdoc store clear`"
    assert "--health-port" in flags, "the walk missed the flag that motivated it"


def test_the_default_tenant_flag_is_scoped_to_migrate() -> None:
    """FR-083 satisfied without weakening what the setting is for.

    It has a flag, so the requirement's "MUST gain a flag" is met literally. It
    has one only on the command that *records* the answer, so a per-invocation
    override of a deployment-wide fact — the disagreement the recorded value
    exists to prevent — is still impossible everywhere else.
    """
    assert COMMAND_SCOPED["DOCDOC_DEFAULT_TENANT"] == ("migrate",)
    assert FLAG_FOR_SETTING["DOCDOC_DEFAULT_TENANT"] == "--default-tenant"

    subparsers = next(
        action
        for action in build_parser()._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    assert "--default-tenant" in _option_strings(subparsers.choices["migrate"])
    for command in ("parse", "extract", "worker"):
        assert "--default-tenant" not in _option_strings(subparsers.choices[command]), (
            f"`docdoc {command}` can override where every other process looks for "
            f"content, which is the disagreement DOCDOC_DEFAULT_TENANT prevents"
        )
