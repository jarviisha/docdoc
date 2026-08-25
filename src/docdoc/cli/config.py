"""Flag beats environment beats default, and no second vocabulary.

Every setting that can change what *one command-line invocation does* keeps its
name and gains a flag of the same meaning (FR-031). The table is short enough to
state in full:

=============================  =========================  =====================
Setting                        Flag                       Default
=============================  =========================  =====================
``DOCDOC_SCHEMA_PATHS``        ``--schema-path``          empty registry
``DOCDOC_MODEL_ADAPTERS``      ``--adapter``              adapter registry's own
``DOCDOC_ECHO_FIXTURES``       ``--echo-fixtures``        none
``DOCDOC_STORE_ROOT``          ``--store``                **no store**
``DOCDOC_MAX_DOCUMENT_BYTES``  ``--max-document-bytes``   50 MiB
``DOCDOC_MAX_PAGES``           ``--max-pages``            1000
=============================  =========================  =====================

**There is no default store root**, and that is the one row worth reading twice.
The artifacts hold extracted values and the blobs hold whole source documents, so
a default location would be docdoc choosing where somebody's documents pile up
(FR-017, FR-044). ``--no-store`` therefore names the behaviour you already have;
``--store`` is the opt-in.

**Three settings are deliberately environment-only**, and the exclusions are
listed here rather than left to be noticed, because FR-031 as first written read
as though every variable gained a flag and two documents went on to say so. The
amendment of 2026-08-25 narrows it to the settings that can change an
invocation's outcome, and these three cannot:

``DOCDOC_MAX_REQUEST_BYTES``
    Caps an HTTP request body while it is being read. The command line reads no
    request body, so the flag would name a limit that is never consulted.

``DOCDOC_MATCH_VIEW_CACHE``
    Bounds how many folded match views one **process** holds. A command-line run
    grounds one document once, so the cache holds a single entry and any bound
    above zero behaves identically. It is also read when
    ``docdoc.grounding.view`` is imported, so a flag parsed afterwards could not
    take effect without new machinery for a knob that changes nothing.

``DOCDOC_GEMINI_MODEL`` and the provider credentials
    Per-provider settings. A ``--gemini-model`` flag would put one vendor's
    vocabulary on a provider-agnostic command line (Principle IV), and a
    credential must never be passed as an argument at all: ``argv`` is readable
    by any process on the host, which would defeat FR-042 far more thoroughly
    than an environment variable does.

The rule that survives is the one FR-031 exists for: **no second vocabulary.**
Nothing here is named differently on the command line than in the environment,
and ``tests/unit/test_cli_config_vocabulary.py`` compares the two as sets so that
a setting added later cannot quietly land on neither list.

**The empty registry gets its own error.** docdoc ships no schema, so a fresh
install resolving ``invoice@1`` finds nothing -- and "schema not found" would
send the reader looking for a typo in a name that was never the problem. When the
registry is empty the error says so and names the setting that fills it (US1,
scenario 5).
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import argparse

    from docdoc.artifacts import ArtifactStore
    from docdoc.ingest import Limits

__all__ = [
    "ENVIRONMENT_ONLY",
    "FLAG_FOR_SETTING",
    "STORE_ROOT_ENV",
    "Settings",
    "add_common_arguments",
    "empty_registry_message",
]

#: New in Milestone 7, and named in the style of the two that already existed
#: rather than in a style of its own.
STORE_ROOT_ENV = "DOCDOC_STORE_ROOT"

_SCHEMA_PATHS_ENV = "DOCDOC_SCHEMA_PATHS"
_ADAPTERS_ENV = "DOCDOC_MODEL_ADAPTERS"
_ECHO_FIXTURES_ENV = "DOCDOC_ECHO_FIXTURES"

#: The docstring's table, as data. Declared so the parity check can read it
#: rather than re-parse prose, and so a flag renamed here fails that check in the
#: same commit rather than in a convergence pass two rounds later (FR-031).
#:
#: The names below are string literals rather than imports of the constants that
#: define them, and deliberately so: importing them would drag ``ingest``,
#: ``api.settings``, and the Gemini adapter's module into ``docdoc store clear``,
#: which needs none of them and must stay usable on a base install (FR-053). The
#: cost of a literal is that it can go stale, and that cost is paid by
#: ``test_the_maps_describe_settings_that_exist``, which compares both maps
#: against every setting the code actually reads.
FLAG_FOR_SETTING: dict[str, str] = {
    _SCHEMA_PATHS_ENV: "--schema-path",
    _ADAPTERS_ENV: "--adapter",
    _ECHO_FIXTURES_ENV: "--echo-fixtures",
    STORE_ROOT_ENV: "--store",
    "DOCDOC_MAX_DOCUMENT_BYTES": "--max-document-bytes",
    "DOCDOC_MAX_PAGES": "--max-pages",
}

#: Settings that deliberately have no flag, each with the reason, because an
#: exclusion nobody wrote down is indistinguishable from an oversight — which is
#: exactly how two of these came to be documented as having flags they did not
#: have. The reasons are argued in full in this module's docstring.
ENVIRONMENT_ONLY: dict[str, str] = {
    "DOCDOC_MAX_REQUEST_BYTES": "caps an HTTP request body; the command line reads none",
    "DOCDOC_MATCH_VIEW_CACHE": "a process-level memory bound; one run grounds one document once",
    "DOCDOC_GEMINI_MODEL": "per-provider; a vendor's vocabulary belongs to its adapter",
    "DOCDOC_AZURE_DI_ENDPOINT": "per-provider; a vendor's vocabulary belongs to its adapter",
    "DOCDOC_AZURE_DI_KEY": "a credential; argv is readable by any process on the host (FR-042)",
}


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    """The flags every command shares.

    Added per-subparser rather than only on the root, so that ``docdoc extract
    --json`` works as readily as ``docdoc --json extract``. A user who has to
    remember which side of the subcommand a flag lives on has been given a
    puzzle rather than a tool.
    """
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="write exactly one JSON document to stdout; diagnostics go to stderr",
    )
    parser.add_argument(
        "--schema-path",
        action="append",
        default=None,
        metavar="DIR",
        help=f"directory of schemas; repeatable. Overrides ${_SCHEMA_PATHS_ENV}",
    )
    parser.add_argument(
        "--adapter",
        action="append",
        default=None,
        metavar="ID",
        help=f"model adapter, in priority order. Overrides ${_ADAPTERS_ENV}",
    )
    parser.add_argument(
        "--echo-fixtures",
        default=None,
        metavar="DIR",
        help=f"canned answers for the offline echo adapter. Overrides ${_ECHO_FIXTURES_ENV}",
    )
    parser.add_argument(
        "--store",
        default=None,
        metavar="DIR",
        help=f"artifact store root. Overrides ${STORE_ROOT_ENV}. There is no default",
    )
    parser.add_argument(
        "--no-store",
        action="store_true",
        help="run with no store even if one is configured in the environment",
    )
    parser.add_argument(
        "--verify-cache",
        action="store_true",
        help="execute every stage and still write, so a drifted processor surfaces",
    )
    # FR-039 requires a configurable maximum document size, and until now it was
    # configurable only by a caller importing the library or setting a variable.
    # `type=int` rather than a validating callable: argparse turns an
    # ArgumentTypeError into its own exit 2, and the invocation errors here owe
    # the caller 64 (FR-028). The positivity check therefore lives beside the
    # other one argparse cannot express, in `cli.__init__._usage_error`.
    parser.add_argument(
        "--max-document-bytes",
        type=int,
        default=None,
        metavar="N",
        help="largest document accepted, before any parse. Overrides $DOCDOC_MAX_DOCUMENT_BYTES",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        metavar="N",
        help="page-count limit, checked as soon as it is known. Overrides $DOCDOC_MAX_PAGES",
    )


@dataclasses.dataclass(frozen=True)
class Settings:
    """One invocation's resolved configuration.

    Resolved once, in one place, so that a command never reads the environment
    itself. A command that did would be a second precedence rule, and the second
    one is always the one that disagrees.
    """

    as_json: bool = False
    schema_paths: tuple[Path, ...] = ()
    adapters: tuple[str, ...] = ()
    store_root: Path | None = None
    verify_cache: bool = False
    echo_fixtures: Path | None = None
    #: ``None`` means "no flag was given", not "no limit". The environment and
    #: the documented default are applied by ``Limits`` itself, which is what
    #: keeps one precedence rule instead of two — see :meth:`limits`.
    max_document_bytes: int | None = None
    max_pages: int | None = None

    @classmethod
    def resolve(cls, args: argparse.Namespace) -> Settings:
        """Flag, then environment, then default — for each setting separately.

        Per setting, not per source: a caller who sets ``--store`` on the command
        line and ``DOCDOC_SCHEMA_PATHS`` in the environment gets both, which is
        the arrangement the library already uses.
        """
        return cls(
            as_json=bool(getattr(args, "as_json", False)),
            schema_paths=_paths(
                getattr(args, "schema_path", None), os.environ.get(_SCHEMA_PATHS_ENV, "")
            ),
            adapters=_names(getattr(args, "adapter", None), os.environ.get(_ADAPTERS_ENV, "")),
            store_root=_store_root(args),
            verify_cache=bool(getattr(args, "verify_cache", False)),
            echo_fixtures=_one_path(
                getattr(args, "echo_fixtures", None), os.environ.get(_ECHO_FIXTURES_ENV, "")
            ),
            max_document_bytes=getattr(args, "max_document_bytes", None),
            max_pages=getattr(args, "max_pages", None),
        )

    # -- the things a command actually asks for -------------------------------

    def registry(self) -> Any:
        """The schema registry, built from the resolved paths."""
        from docdoc.extraction import SchemaRegistry

        return SchemaRegistry.from_paths(self.schema_paths)

    def adapter(self) -> Any:
        """The model adapter configuration selects.

        Imported here rather than at module scope: reaching
        ``docdoc.extraction``'s adapter registry pulls provider SDKs into the
        import graph, and ``docdoc store clear`` has no business requiring the
        Gemini client to be installed.

        ``--echo-fixtures`` is applied by setting the environment variable the
        registry already reads, rather than by threading a second parameter
        through it. The flag and the variable are the same setting (FR-031), and
        the registry should have one place to look for it.
        """
        from docdoc.extraction.adapter_registry import ECHO_FIXTURES_ENV, default_adapter

        if self.echo_fixtures is not None:
            os.environ[ECHO_FIXTURES_ENV] = str(self.echo_fixtures)

        return default_adapter(self.adapters or None)

    def limits(self) -> Limits:
        """What this invocation is willing to accept (FR-039).

        Only the flags that were actually given are passed through. Everything
        else is left to ``Limits``' own field defaults, which read
        ``DOCDOC_MAX_DOCUMENT_BYTES`` and ``DOCDOC_MAX_PAGES`` and fall back to
        the documented numbers — so "flag beats environment beats default" is the
        library's existing precedence being *reused* rather than a second copy of
        it living here. A second copy is how the two eventually disagree.
        """
        from docdoc.ingest import Limits

        overrides: dict[str, int] = {}
        if self.max_document_bytes is not None:
            overrides["max_size_bytes"] = self.max_document_bytes
        if self.max_pages is not None:
            overrides["max_pages"] = self.max_pages
        return Limits(**overrides)

    def store(self) -> ArtifactStore:
        """The artifact store, which is the null one unless somebody said where."""
        from docdoc.artifacts import FileArtifactStore, NullArtifactStore

        if self.store_root is None:
            return NullArtifactStore()
        return FileArtifactStore(self.store_root)

    @property
    def has_store(self) -> bool:
        return self.store_root is not None


def _store_root(args: argparse.Namespace) -> Path | None:
    """``--no-store`` wins outright; then ``--store``; then the environment.

    ``--no-store`` beating an explicit ``--store`` on the same line is a
    deliberate choice among two defensible ones: the flag that turns a thing
    *off* is the one a user reaches for when they are unsure, and the safer
    reading of an ambiguous invocation is the one that writes nothing.
    """
    if getattr(args, "no_store", False):
        return None
    explicit = getattr(args, "store", None)
    if explicit:
        return Path(explicit).expanduser()
    from_env = os.environ.get(STORE_ROOT_ENV, "").strip()
    return Path(from_env).expanduser() if from_env else None


def _one_path(flag: str | None, env: str) -> Path | None:
    """A single directory setting: the flag, then the environment, then nothing."""
    if flag:
        return Path(flag).expanduser()
    return Path(env.strip()).expanduser() if env.strip() else None


def _paths(flag: list[str] | None, env: str) -> tuple[Path, ...]:
    if flag:
        return tuple(Path(item).expanduser() for item in flag)
    return tuple(Path(item.strip()).expanduser() for item in env.split(os.pathsep) if item.strip())


def _names(flag: list[str] | None, env: str) -> tuple[str, ...]:
    if flag:
        return tuple(flag)
    return tuple(item.strip() for item in env.split(",") if item.strip())


def empty_registry_message(settings: Settings) -> str | None:
    """The message for a schema that could not be resolved, when it is *this*.

    Returns ``None`` when the registry has schemas in it, in which case the
    unresolvable-schema error the extraction layer already raises is the right
    one and this must not replace it.
    """
    if settings.schema_paths:
        return None
    return (
        "the schema registry is empty: docdoc ships no schemas, and no search path "
        f"is configured. Set ${_SCHEMA_PATHS_ENV} or pass --schema-path DIR."
    )
