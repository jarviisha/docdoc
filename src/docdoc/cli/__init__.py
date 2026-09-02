"""The ``docdoc`` command.

Built on :mod:`argparse`, so the base install acquires no dependency and the
command belongs to everyone who typed ``pip install docdoc``. That is the point
rather than an economy: the founding argument for having a CLI at all was that a
developer should not need to deploy five services to try docdoc, and it would be
a poor answer to make them find a second install line instead.

**What it must never contain:** extraction, grounding, or validation logic. It
parses arguments, calls :mod:`docdoc.pipeline`, and formats a result. A
behaviour reachable only through the command line is a bug.

**What it must never import:** :mod:`docdoc.api`. The two are siblings with
different audiences, held apart by an ``independence`` contract rather than by
convention -- sharing a renderer between them would be the first coupling, and
the thing they genuinely share is the result model both already import.

Two output rules make up most of the contract. With ``--json``, standard output
carries exactly one JSON document and nothing else; diagnostics go to standard
error in both forms. And the exit code distinguishes "the document is invalid"
from "docdoc could not run", because a script that confuses the two will treat a
wrong invoice as a broken tool.

**Every command returns; none of them prints.** A command hands back a
:class:`~docdoc.cli.render.Rendering` and :func:`~docdoc.cli.render.emit` writes
it, which is what makes "exactly one JSON document on stdout" structural rather
than a rule four modules have to remember.
"""

from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING, Any

from docdoc.cli.config import RUN_DATABASE_URL_ENV, Settings, add_common_arguments
from docdoc.cli.render import Rendering, emit, warn

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["main"]

#: The run completed and the document is valid.
EXIT_OK = 0
#: The run completed and the document is **invalid** — a real result, not an error.
EXIT_INVALID = 1
#: The run could not complete: a typed docdoc error.
EXIT_COULD_NOT_RUN = 2
#: The invocation itself was wrong — bad arguments, unreadable file. The value is
#: ``EX_USAGE`` from ``sysexits.h``, which is what shells and CI runners already
#: read as "you called this wrong" rather than "this is broken".
EXIT_BAD_INVOCATION = 64


def build_parser() -> argparse.ArgumentParser:
    """The command set of ``contracts/cli.md`` §1, and nothing beyond it."""
    parser = argparse.ArgumentParser(
        prog="docdoc",
        description="Turn documents into structured, validated, traceable data.",
    )
    add_common_arguments(parser)
    subcommands = parser.add_subparsers(dest="command", metavar="COMMAND")

    parse_command = subcommands.add_parser(
        "parse", help="route, parse, and report what the parse produced"
    )
    parse_command.add_argument("file", metavar="FILE")
    add_common_arguments(parse_command)

    extract_command = subcommands.add_parser(
        "extract", help="run the whole pipeline and report the result"
    )
    extract_command.add_argument("file", metavar="FILE")
    extract_command.add_argument(
        "--schema", required=True, metavar="NAME@V", help="schema identity, e.g. invoice@1"
    )
    add_common_arguments(extract_command)

    # `inspect` takes a file *or* a stored identity, so neither can be
    # unconditionally required — argparse cannot express "exactly one of these"
    # for a positional and an option together, so `main` checks it and reports a
    # usage error. FR-026 asks for a command that inspects "a result", and a
    # result that only exists as a file to re-run is not one.
    inspect_command = subcommands.add_parser(
        "inspect", help="report where every value came from, from a file or a stored identity"
    )
    inspect_command.add_argument("file", metavar="FILE", nargs="?", default=None)
    inspect_command.add_argument(
        "--schema", metavar="NAME@V", help="schema identity, e.g. invoice@1 (with FILE)"
    )
    inspect_command.add_argument(
        "--result",
        metavar="PROCESSING_ID",
        default=None,
        help="read a completed run back from the store instead of running one",
    )
    add_common_arguments(inspect_command)

    explain = subcommands.add_parser("explain", help="how an artifact identity was derived")
    explain.add_argument("artifact_id", metavar="ARTIFACT_ID")
    explain.add_argument(
        "--chain", action="store_true", help="walk the derivation back to the source blob"
    )
    add_common_arguments(explain)

    evaluate = subcommands.add_parser("eval", help="score a golden set")
    evaluate.add_argument("manifest", metavar="MANIFEST")
    evaluate.add_argument("--predictions", required=True, metavar="DIR")
    add_common_arguments(evaluate)

    # "clear", not "inspect and clear". There is no inspect action and the
    # contract grants none: FR-019 allows two subsets and no query language, so a
    # store you can interrogate is the thing it deliberately withholds. Help text
    # is the one piece of documentation that ships inside the program, and it
    # promised a command for a milestone before anybody typed it.
    store = subcommands.add_parser("store", help="clear the artifact store, all of it or one stage")
    store_actions = store.add_subparsers(dest="action", metavar="ACTION")
    clear = store_actions.add_parser("clear", help="clear all of it, or one stage")
    clear.add_argument(
        "--stage", default=None, metavar="STAGE", help="parse|extract|ground|validate"
    )
    add_common_arguments(clear)

    # Explicit, never on boot (FR-078). With several workers starting at once an
    # implicit migration is several processes altering one table, and the schema
    # a deployment ends up with depends on which container won.
    migrate = subcommands.add_parser("migrate", help="apply the run-state schema")
    migrate.add_argument(
        "--check",
        action="store_true",
        help="report pending migrations and apply nothing; non-zero if any are pending",
    )
    migrate.add_argument(
        "--run-database-url",
        default=None,
        metavar="URL",
        help=f"where run state lives. Overrides ${RUN_DATABASE_URL_ENV}",
    )
    add_common_arguments(migrate)

    # One run at a time, and no --concurrency. Concurrency is replica count
    # (FR-025): a threaded worker lets one long parse starve a sibling's
    # heartbeat until it loses a lease it is still executing.
    worker = subcommands.add_parser("worker", help="claim runs and execute them")
    worker.add_argument(
        "--lease-seconds", type=int, default=None, metavar="N", help="claim duration"
    )
    worker.add_argument(
        "--max-attempts", type=int, default=None, metavar="N", help="before abandoning a run"
    )
    worker.add_argument(
        "--run-database-url",
        default=None,
        metavar="URL",
        help=f"where run state lives. Overrides ${RUN_DATABASE_URL_ENV}",
    )
    add_common_arguments(worker)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, run one command, write one form, return one code.

    The whole error boundary lives here. FR-051 says no untyped exception may
    escape to a caller, and the way to make that true is to have exactly one
    place where it could — not four commands each remembering to be careful.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "command", None):
        parser.print_help(sys.stderr)
        return EXIT_BAD_INVOCATION

    usage = _usage_error(args)
    if usage is not None:
        warn(usage)
        return EXIT_BAD_INVOCATION

    settings = Settings.resolve(args)

    try:
        rendering = _dispatch(args)(args, settings)
    except Exception as error:
        # The boundary FR-051 names. Broad on purpose: the requirement is that no
        # untyped exception reaches a caller, and a narrower clause here would
        # be a list of the exception types somebody remembered.
        return emit(_failure(error), as_json=settings.as_json)

    return emit(rendering, as_json=settings.as_json)


def _usage_error(args: argparse.Namespace) -> str | None:
    """The constraints argparse cannot express, checked in one place.

    ``docdoc inspect`` takes a file *or* a stored identity, and argparse has no
    way to require exactly one of a positional and an option. Rather than make
    both optional and let a command discover the problem halfway through, the
    check lives here beside the other invocation errors and earns the same
    exit code.

    The limit flags are here for a related reason. ``Limits`` rejects a
    non-positive bound with a ``ValidationError``, which would surface as a
    pydantic dump naming a field the user never typed; caught here it earns the
    same ``64`` and a sentence naming the flag they did type.
    """
    limit_error = _limit_usage_error(args)
    if limit_error is not None:
        return limit_error

    if args.command != "inspect":
        return None

    if args.result and args.file:
        return "inspect takes either FILE or --result, not both"
    if args.result:
        return None
    if not args.file:
        return "inspect needs a FILE, or --result PROCESSING_ID to read a stored run"
    if not args.schema:
        return "inspect FILE needs --schema NAME@V"
    return None


def _limit_usage_error(args: argparse.Namespace) -> str | None:
    """A size limit of zero or less is an invocation error, not a run failure.

    Checked for every command that carries the flags rather than only the two
    that consult them, because ``docdoc explain --max-pages 0`` is just as wrong
    and telling the user so costs nothing.
    """
    flags = (("--max-document-bytes", "max_document_bytes"), ("--max-pages", "max_pages"))
    for flag, attribute in flags:
        value = getattr(args, attribute, None)
        if value is not None and value <= 0:
            return f"{flag} must be a positive integer, not {value}"
    return None


def _dispatch(args: argparse.Namespace) -> Any:
    """The command's entry point, imported at call time.

    Deferred deliberately. Importing every command at module scope would drag
    ``docdoc.ingest`` and its parser registry — and through it PyMuPDF and the
    Azure SDK — into ``docdoc store clear``, which needs none of them. The base
    install has to stay usable with no extras at all (FR-053, SC-013).
    """
    from docdoc.cli.commands import eval as eval_command
    from docdoc.cli.commands import explain, extract, inspect, migrate, parse, store, worker

    if args.command == "migrate":
        return migrate.run

    if args.command == "worker":
        return worker.run

    if args.command == "store":
        if getattr(args, "action", None) != "clear":
            raise ValueError("usage: docdoc store clear [--stage STAGE]")
        return store.run

    return {
        "parse": parse.run,
        "extract": extract.run,
        "inspect": inspect.run,
        "explain": explain.run,
        "eval": eval_command.run,
    }[args.command]


def _failure(error: Exception) -> Rendering:
    """Turn any exception into a typed, content-free rendering.

    **The class name, never the message, in the machine form.** An exception
    message can quote the document it choked on, and the JSON document is what
    gets pasted into an issue. The human form does carry docdoc's own message
    because it is going to the person who ran the command and is holding the
    file already — but a *provider's* message is never repeated in either, since
    that is the one that may contain the document's text.
    """
    from docdoc.kernel.errors import DocdocError

    typed = isinstance(error, DocdocError)
    code = EXIT_COULD_NOT_RUN if typed else _code_for_untyped(error)

    data = {
        "error": {
            "class": type(error).__name__,
            "typed": typed,
            "stage": _stage_of(error),
        }
    }
    message = str(error) if typed else _untyped_message(error)
    warn(message)
    return Rendering(code=code, data=data, lines=[])


def _code_for_untyped(error: Exception) -> int:
    """An untyped exception is a bad invocation or a bug, and they differ.

    ``OSError`` from an unreadable file and ``ValueError`` from a rejected
    argument are the user's invocation and earn ``64``; anything else is docdoc's
    fault and earns ``2``. The distinction matters because a CI job that retries
    on ``2`` should not retry on a path that will never exist.
    """
    if isinstance(error, (OSError, ValueError, KeyError)):
        return EXIT_BAD_INVOCATION
    return EXIT_COULD_NOT_RUN


def _untyped_message(error: Exception) -> str:
    if isinstance(error, OSError):
        return f"cannot read {getattr(error, 'filename', None) or 'the input'}: {error.strerror}"
    return f"{type(error).__name__}: {error}"


def _stage_of(error: Exception) -> str | None:
    """Which layer declared this error, read off its own module.

    The same rule the pipeline uses (FR-005), applied to errors that never
    reached the pipeline — a schema that would not resolve, a file that would not
    open. Attributing by declaring layer rather than by call site is what sends a
    reader to the right code.
    """
    module = type(error).__module__
    for marker, stage in (
        (".ingest", "parse"),
        (".extraction", "extract"),
        (".grounding", "ground"),
        (".validation", "validate"),
        (".artifacts", "store"),
        (".evaluation", "eval"),
        (".pipeline", "pipeline"),
    ):
        if marker in module:
            return stage
    return None
