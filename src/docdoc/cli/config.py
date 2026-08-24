"""Flag beats environment beats default, and no second vocabulary.

Every setting docdoc already had keeps its name and gains a flag of the same
meaning (FR-031). The table is short enough to state in full:

===========================  ======================  =====================
Setting                      Flag                    Default
===========================  ======================  =====================
``DOCDOC_SCHEMA_PATHS``      ``--schema-path``       empty registry
``DOCDOC_MODEL_ADAPTERS``    ``--adapter``           adapter registry's own
``DOCDOC_ECHO_FIXTURES``     ``--echo-fixtures``     none
``DOCDOC_STORE_ROOT``        ``--store``             **no store**
===========================  ======================  =====================

**There is no default store root**, and that is the one row worth reading twice.
The artifacts hold extracted values and the blobs hold whole source documents, so
a default location would be docdoc choosing where somebody's documents pile up
(FR-017, FR-044). ``--no-store`` therefore names the behaviour you already have;
``--store`` is the opt-in.

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

__all__ = [
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
