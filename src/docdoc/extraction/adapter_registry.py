"""Choosing an adapter without naming one.

FR-021 says a caller must not name a provider, a model family, or a model version
anywhere in application code, and that which model answers is configuration. Until
this module existed that requirement was simply unmet: every documented example
wrote ``adapter=GeminiAdapter()``, which is the thing the requirement forbids, and
``contracts/extraction-api.md`` claimed the opposite of what the code did.

The shape mirrors the ingest layer's parser registry deliberately, so a reader who
has understood one understands this. An adapter whose extra is missing, or whose
credentials are absent, is **recorded as unavailable with its reason** rather than
omitted -- silence would make "not installed" indistinguishable from "no such
thing" (FR-028), and the failure would name nothing.

**The echo adapter is never selected automatically.** It returns canned fixture
responses, so auto-selecting it would turn a missing credential into a stream of
confident, fabricated extractions -- the worst failure this layer could have.

*Automatically* is the load-bearing word, and Milestone 7 sharpened it. Echo is
never a **fallback**: no configuration that merely fails to name a usable adapter
can land on it, which is the failure above. But configuration that *does* name it
is a decision, not an accident, and FR-029 requires the offline path to be
reachable from the command line rather than only from Python. Reaching it takes
two explicit settings -- ``DOCDOC_MODEL_ADAPTERS=echo`` says *use fabricated
answers*, ``DOCDOC_ECHO_FIXTURES`` says *these ones* -- and an echo with no
fixtures answers nothing at all.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, NamedTuple

# Imported at runtime, not under TYPE_CHECKING: `AdapterCandidate` is a NamedTuple
# and pydantic-style runtime annotation resolution applies to it, so the name must
# actually exist when the class body executes.
from docdoc.extraction.adapter import ModelAdapter
from docdoc.extraction.errors import ModelProviderError

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "ADAPTERS_ENV",
    "ECHO_FIXTURES_ENV",
    "AdapterCandidate",
    "AdapterRegistry",
    "default_adapter",
    "default_adapter_registry",
]

#: Offline-first is not an option here the way it is for parsers -- every real
#: adapter calls a service. The order is simply the one configuration prefers,
#: with the adapter id as a deterministic tie-break so selection never depends on
#: registration order or dictionary iteration.
DEFAULT_PRIORITY: tuple[str, ...] = ("gemini",)

#: The configuration name that reorders adapter preference, comma-separated
#: (``DOCDOC_MODEL_ADAPTERS=gemini``). The companion to
#: ``gemini.MODEL_ENV``: between them, "which provider and which model answer"
#: is configuration end to end, which is what US3/AC2 asks for. An explicit
#: ``priority`` argument still wins.
ADAPTERS_ENV = "DOCDOC_MODEL_ADAPTERS"

#: Where the ``echo`` adapter reads its canned answers: a directory of
#: ``<schema-identity>.json``. No default, because a canned answer is written
#: against a specific schema and docdoc ships none.
ECHO_FIXTURES_ENV = "DOCDOC_ECHO_FIXTURES"

#: Adapters that answer from fixtures rather than from a model. Registering one is
#: allowed and useful; *selecting* one implicitly is not. Naming one explicitly —
#: in ``DOCDOC_MODEL_ADAPTERS`` or in ``priority`` — is not implicit, and
#: ``select`` honours it.
_NEVER_AUTO_SELECT = frozenset({"echo"})


def _configured_priority() -> tuple[str, ...]:
    """The adapter order configuration names, or the shipped default.

    Blank entries are dropped rather than treated as an adapter id, so a trailing
    comma is a typo that costs nothing instead of a candidate that matches nothing.
    """
    raw = os.environ.get(ADAPTERS_ENV, "")
    configured = tuple(part.strip() for part in raw.split(",") if part.strip())
    return configured or DEFAULT_PRIORITY


class AdapterCandidate(NamedTuple):
    """One adapter the registry knows about, and whether it can be used."""

    id: str
    available: bool
    reason: str | None
    adapter: ModelAdapter | None


class AdapterRegistry:
    """The adapters a running system knows, and the rule that picks one."""

    def __init__(self, priority: Sequence[str] | None = None) -> None:
        self._priority = tuple(priority) if priority is not None else _configured_priority()
        self._entries: dict[str, AdapterCandidate] = {}

    def register(self, adapter: ModelAdapter) -> None:
        """Add an adapter, asking it whether it is usable."""
        availability = adapter.available()
        self._entries[adapter.id] = AdapterCandidate(
            id=adapter.id,
            available=availability.usable,
            reason=availability.reason,
            adapter=adapter,
        )

    def register_unavailable(self, adapter_id: str, *, reason: str) -> None:
        """Record an adapter that could not even be imported.

        Without this, an uninstalled extra would vanish from the candidate list
        and the resulting error would be unable to say "install docdoc[google]".
        """
        self._entries[adapter_id] = AdapterCandidate(
            id=adapter_id, available=False, reason=reason, adapter=None
        )

    def candidates(self) -> tuple[AdapterCandidate, ...]:
        """Every known adapter, in selection order.

        Configured priority first, then anything unlisted, with the adapter id as
        the final tie-break so the order is total and never depends on
        registration order or dictionary iteration.
        """
        ranked = sorted(
            self._entries.values(),
            key=lambda c: (
                self._priority.index(c.id) if c.id in self._priority else len(self._priority),
                c.id,
            ),
        )
        return tuple(ranked)

    def select(self) -> ModelAdapter:
        """The adapter that will answer, chosen by configuration.

        Raises:
            ModelProviderError: nothing usable is registered. The error carries
                every candidate with its reason, so "why not?" is answerable
                without reading docdoc's source (FR-028).
        """
        for candidate in self.candidates():
            if candidate.id in _NEVER_AUTO_SELECT and candidate.id not in self._priority:
                # "Never selected *automatically*" is not "never selectable". A
                # fixture adapter must not win a default selection, and naming it
                # in `DOCDOC_MODEL_ADAPTERS` or in `priority` is not a default —
                # it is somebody saying which adapter they want. Without this
                # clause the offline path FR-029 requires cannot be reached from
                # configuration at all.
                continue
            if candidate.available and candidate.adapter is not None:
                return candidate.adapter
        raise ModelProviderError(
            self._explain(),
            reason="unavailable",
            adapter_id="(none selected)",
        )

    def _explain(self) -> str:
        if not self._entries:
            return (
                "no model adapter is registered. Install one — `pip install docdoc[google]` — "
                "or pass an adapter explicitly"
            )
        lines = [
            f"  {c.id}: {'usable' if c.available else c.reason or 'unavailable'}"
            + (" (never selected automatically)" if c.id in _NEVER_AUTO_SELECT else "")
            for c in self.candidates()
        ]
        return "no usable model adapter is registered. Candidates:\n" + "\n".join(lines)

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, adapter_id: object) -> bool:
        return adapter_id in self._entries


def default_adapter_registry(priority: Sequence[str] | None = None) -> AdapterRegistry:
    """The adapters this installation can use, with their reasons if it cannot.

    An adapter whose extra is not installed is registered as unavailable rather
    than skipped, so the error a caller sees names what to install.
    """
    registry = AdapterRegistry(priority)

    try:
        from docdoc.extraction.adapters.gemini import GeminiAdapter
    except ImportError:  # pragma: no cover - the module has no import-time SDK use
        registry.register_unavailable(
            "gemini", reason="extra not installed; run `pip install docdoc[google]`"
        )
    else:
        registry.register(GeminiAdapter())

    _register_echo(registry)
    return registry


def _register_echo(registry: AdapterRegistry) -> None:
    """Register the fixture adapter, which answers offline and costs nothing.

    It is in ``_NEVER_AUTO_SELECT``, so registering it cannot make it win a
    default selection; a caller reaches it by naming it. What registering it does
    is make the offline path FR-029 requires reachable from configuration —
    ``DOCDOC_MODEL_ADAPTERS=echo`` — rather than only from Python.

    Its canned answers come from ``DOCDOC_ECHO_FIXTURES``: a directory of
    ``<schema-identity>.json``, one per schema it can answer for. There is no
    default location and docdoc ships no fixtures, for the same reason it ships
    no schemas — a canned answer is written against a specific schema, and
    guessing one would be inventing an extraction.
    """
    from docdoc.extraction.adapters.echo import EchoAdapter

    fixtures = os.environ.get(ECHO_FIXTURES_ENV, "").strip()
    if not fixtures:
        # Registered with nothing to say. It reports itself usable and then names
        # the schema it was asked for and the empty set it holds, which is a far
        # better error than vanishing from the candidate list would give.
        registry.register(EchoAdapter())
        return

    try:
        registry.register(EchoAdapter.from_fixtures(fixtures))
    except OSError as error:
        registry.register_unavailable(
            "echo",
            reason=(f"${ECHO_FIXTURES_ENV} is set to {fixtures!r}, which is unreadable: {error}"),
        )


def default_adapter(priority: Sequence[str] | None = None) -> ModelAdapter:
    """The adapter configuration selects, without naming one here.

    This is the call FR-021 asks application code to make instead of constructing
    a provider adapter:

        adapter = default_adapter()

    Swapping providers is then a change to what is installed and to the priority
    order, not to the line above.
    """
    return default_adapter_registry(priority).select()
