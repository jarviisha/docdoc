"""Choosing a parser by capability, never by name.

This is the boundary that keeps docdoc from becoming a vendor wrapper. A caller
says what it needs; which parser supplies it is a deployment's configuration
(Principle IV, FR-015).

Selection is deterministic and inspectable. An explicit priority list decides,
defaulting to offline parsers ahead of service-backed ones, with the parser id
as the final tie-break. Registration order, dictionary iteration order, and
whatever happens to be installed never influence the outcome (R11, FR-016).

A parser that is installed but unusable stays in the registry marked unavailable
*with its reason*. Dropping it silently would turn "you have no credentials
configured" into "no parser can do that", which is a different and much less
useful thing to be told (FR-018).
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import TYPE_CHECKING

from docdoc.ingest.capabilities import CapabilityRequest, ParserCapabilities
from docdoc.ingest.errors import ParserCapabilityError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docdoc.ingest.parser import Parser

__all__ = ["DEFAULT_PRIORITY", "ParserRegistry", "RegistryEntry", "default_registry"]

#: Offline before service-backed. A deployment reorders this; nothing in the
#: code privileges one adapter over the other.
#:
#: Among the two recognition parsers, ``azure-di`` leads because it declares
#: strictly more: it is the only one that supplies tables, so a deployment with
#: both installed gets the richer result by default and reorders this list to
#: prefer the cheaper one. For an image with no table requested the two are
#: interchangeable, which is exactly when a stable order matters -- selection
#: must not depend on which adapter happened to import first (R11, FR-016).
DEFAULT_PRIORITY: tuple[str, ...] = ("pdf-text", "azure-di", "gcv")


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    """One parser the running system knows about, usable or not."""

    id: str
    capabilities: ParserCapabilities
    parser: Parser | None = None
    available: bool = True
    unavailable_reason: str | None = None


class ParserRegistry:
    """The set of parsers available to a running system."""

    __slots__ = ("_entries", "_priority")

    def __init__(self, priority: Sequence[str] | None = None) -> None:
        self._entries: dict[str, RegistryEntry] = {}
        self._priority: tuple[str, ...] = tuple(
            priority if priority is not None else DEFAULT_PRIORITY
        )

    def register(
        self,
        parser: Parser,
        *,
        available: bool = True,
        reason: str | None = None,
    ) -> None:
        self._entries[parser.id] = RegistryEntry(
            id=parser.id,
            capabilities=parser.capabilities,
            parser=parser,
            available=available,
            unavailable_reason=reason,
        )

    def register_unavailable(
        self, parser_id: str, capabilities: ParserCapabilities, *, reason: str
    ) -> None:
        """Record a parser that cannot be used, and why.

        Used when the adapter's extra is not installed at all, so there is no
        object to register -- and the caller still deserves to be told the
        difference between "not installed" and "does not exist".
        """
        self._entries[parser_id] = RegistryEntry(
            id=parser_id,
            capabilities=capabilities,
            parser=None,
            available=False,
            unavailable_reason=reason,
        )

    def candidates_all(self) -> tuple[RegistryEntry, ...]:
        """Every known parser, in priority order."""
        return tuple(sorted(self._entries.values(), key=self._rank))

    def candidates(self, require: CapabilityRequest) -> tuple[RegistryEntry, ...]:
        """Every parser that *declares* what was asked for, in priority order.

        Includes unavailable ones, so a caller can see that the capability
        exists but is not currently usable. ``select`` is the version that
        raises; this one is for diagnostics.
        """
        return tuple(
            sorted(
                (
                    entry
                    for entry in self._entries.values()
                    if entry.capabilities.satisfies(require)
                ),
                key=self._rank,
            )
        )

    def select(self, require: CapabilityRequest) -> Parser:
        """The parser that will serve this request.

        Raises:
            ParserCapabilityError: nothing available satisfies it. The error
                carries every candidate with its availability and reason, so the
                answer to "why not?" does not require reading docdoc's source.
        """
        for entry in self.candidates(require):
            if entry.available and entry.parser is not None:
                return entry.parser

        raise ParserCapabilityError(
            self._explain(require),
            required=require.required_names(),
            media_type=require.media_type,
            candidates=tuple(
                (entry.id, entry.available, entry.unavailable_reason)
                for entry in self.candidates_all()
            ),
        )

    def _explain(self, require: CapabilityRequest) -> str:
        matching = self.candidates(require)
        if matching:
            blocked = ", ".join(
                f"{entry.id} ({entry.unavailable_reason or 'unavailable'})" for entry in matching
            )
            return (
                f"no available parser for {require.media_type} with "
                f"{', '.join(require.required_names())}; installed but unusable: {blocked}"
            )
        return f"no parser declares {', '.join(require.required_names())} for {require.media_type}"

    def _rank(self, entry: RegistryEntry) -> tuple[int, str]:
        """Priority position, then parser id. Never registration order."""
        position = (
            self._priority.index(entry.id) if entry.id in self._priority else len(self._priority)
        )
        return position, entry.id


def _sdk_installed(module: str) -> bool:
    """Whether an adapter's provider SDK is importable.

    An adapter module imports its SDK lazily, inside the method that reaches the
    network, so that the mapping half stays testable on a base install. The
    consequence is that importing the adapter *succeeds* with the extra missing,
    and an `except ImportError` around that import therefore never fires. Left
    unchecked, a deployment with credentials set but no extra installed selects
    the parser and dies on a bare ImportError from inside `parse` -- a provider
    failure crossing the public API, which the error model forbids outright
    (FR-025). Probing the spec asks the question the import no longer answers.

    `find_spec` raises rather than returning None when a *parent* package is
    absent, and both outcomes mean the same thing here.
    """
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _register_service_parser(
    registry: ParserRegistry,
    parser: Parser,
    *,
    sdk_module: str,
    configured: bool,
) -> None:
    """Record a service-backed parser with the reason it cannot be used, if any.

    The three states are distinct and a caller acts differently on each: install
    the extra, configure the credentials, or use it. Collapsing the first two
    into "unavailable" is the failure FR-018 exists to prevent.
    """
    if not _sdk_installed(sdk_module):
        registry.register_unavailable(parser.id, parser.capabilities, reason="extra_not_installed")
        return
    registry.register(
        parser,
        available=configured,
        reason=None if configured else "credentials_not_configured",
    )


def default_registry(priority: Sequence[str] | None = None) -> ParserRegistry:
    """A registry holding whichever adapters this installation can use.

    An adapter whose extra is missing, or whose credentials are not configured,
    is recorded as unavailable rather than omitted.
    """
    registry = ParserRegistry(priority)

    try:
        from docdoc.ingest.parsers.pdf_text import PdfTextParser
    except ImportError:
        from docdoc.ingest.source import PDF

        registry.register_unavailable(
            "pdf-text",
            ParserCapabilities(
                text=True, geometry=True, media_types=frozenset({PDF}), requires_network=False
            ),
            reason="extra_not_installed",
        )
    else:
        # The native reader is the one adapter that does import its library at
        # module scope, so the ImportError above is a real signal for it.
        registry.register(PdfTextParser())

    from docdoc.ingest.parsers import azure_di, gcv

    _register_service_parser(
        registry,
        azure_di.AzureDocumentIntelligenceParser(),
        sdk_module="azure.ai.documentintelligence",
        configured=azure_di.credentials_available(),
    )
    _register_service_parser(
        registry,
        gcv.GoogleCloudVisionParser(),
        sdk_module="google.cloud.vision",
        configured=gcv.credentials_available(),
    )

    return registry
