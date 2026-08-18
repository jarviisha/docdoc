"""The entry point: an extraction result plus its document, in; locations, out.

Three things happen in a fixed order here, and the order is load-bearing.

1. **The wrong-document guard, first.** Before any work, before the view is
   built, before a single claim is read. Grounding one parse's claims against
   another returns ranges that are valid and wrong, and doing it cheaply first
   means a mismatched call costs nothing and can never half-succeed.
2. **The match view, once.** Not once per value (FR-019). Cost scales with the
   document, not with the product of document size and value count.
3. **One outcome per value that carried a claim** -- and none at all for a value
   the model reported absent.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

from docdoc.extraction.value import ExtractedValue
from docdoc.grounding.errors import GroundingError
from docdoc.grounding.identity import (
    GROUNDER_ID,
    GROUNDER_VERSION,
    GROUNDING_VERSION,
    grounding_artifact_id_for,
)
from docdoc.grounding.match import resolve
from docdoc.grounding.observe import log_grounding, log_refusal
from docdoc.grounding.options import GroundingOptions
from docdoc.grounding.result import (
    GroundingCounts,
    GroundingOutcome,
    GroundingProvenance,
    GroundingResult,
    GroundingStatus,
)
from docdoc.grounding.view import MATCH_VIEW_VERSION, MatchView

if TYPE_CHECKING:
    from collections.abc import Iterator

    from docdoc.extraction.extract import ExtractionResult
    from docdoc.kernel import Document, Span

__all__ = ["ground"]


def _walk(tree: Any, prefix: str = "") -> Iterator[tuple[str, ExtractedValue]]:
    """Every extracted value in the tree, with its dotted path.

    Repeating-group entries are indexed (``line_items[0].description``) so two
    occurrences of one group are distinguishable -- which is what US2's
    group-scoped uniqueness rule needs in order to exist.
    """
    for name, node in tree.items():
        path = f"{prefix}{name}"
        if isinstance(node, ExtractedValue):
            yield path, node
        elif isinstance(node, tuple):
            for index, entry in enumerate(node):
                yield from _walk(entry, f"{path}[{index}].")
        elif isinstance(node, dict):
            yield from _walk(node, f"{path}.")


_INDEX = re.compile(r"\[\d+\]")


def _group_slot(path: str) -> str | None:
    """The repeating-group slot a path belongs to, or ``None`` for a plain field.

    ``line_items[0].description`` and ``line_items[1].description`` share the
    slot ``line_items[].description``; ``invoice_date`` has none. This is the key
    the uniqueness rule is scoped by, and building it from the path is what keeps
    that rule narrow -- a field outside any repeating group can never collide
    with one inside it, or with another field entirely.
    """
    if "[" not in path:
        return None
    return _INDEX.sub("[]", path)


def ground(
    document: Document,
    extraction: ExtractionResult,
    *,
    options: GroundingOptions | None = None,
) -> GroundingResult:
    """Resolve every claim in ``extraction`` to a place in ``document``.

    Exactly one result or an explicit error; never a partially grounded one
    (FR-001). The document and the extraction result are read and never modified,
    on the success path and on every failure path alike (FR-007).

    Raises:
        GroundingError: the extraction did not come from this document (FR-002),
            or an offset-map invariant failed.
    """
    options = options or GroundingOptions()

    # A monotonic clock, and the one place this layer reads one. It measures the
    # run and never influences it: the duration reaches the log event (FR-047) and
    # nothing else, so Principle III's "no clock in the deterministic path" holds
    # -- the same separation Milestone 3 made between transport settings and
    # artifact identity.
    started = time.perf_counter()

    if extraction.provenance.document_id != document.id:
        log_refusal(
            document_id=document.id,
            extraction_document_id=extraction.provenance.document_id,
            duration_ms=(time.perf_counter() - started) * 1000,
        )
        raise GroundingError(
            "this extraction result did not come from this document: "
            f"extraction names {extraction.provenance.document_id}, "
            f"document is {document.id}. Ranges anchor to one specific parse "
            "(ADR-0002), so resolving these claims here would produce locations "
            "that are structurally valid and point at the wrong text.",
            document_id=document.id,
            extraction_document_id=extraction.provenance.document_id,
        )

    view = MatchView.build(document)

    outcomes: dict[str, GroundingOutcome] = {}
    not_applicable = 0

    # Ranges already won, keyed by repeating-group slot. Scoped to the slot and
    # never global: two *distinct* fields that legitimately read the same text --
    # an invoice date serving as both issue date and due date -- must both
    # resolve to the one range it occupies, and a global constraint would force
    # the second to invent a location (GRD-13a, research.md R16).
    taken: dict[str, set[Span]] = {}

    for path, value in _walk(extraction.values):
        if not value.present:
            # The model said the document does not contain this field. There is
            # nothing to resolve, so no outcome is produced and it stays out of
            # the rate's denominator. Reporting it as `ungrounded` would make the
            # grounding rate depend on how many fields a schema declares
            # (FR-008).
            not_applicable += 1
            continue

        slot = _group_slot(path)
        excluded = frozenset(taken.get(slot, ())) if slot else frozenset()
        outcome = resolve(
            field_path=path,
            claim=value.claimed_text,
            document=document,
            view=view,
            options=options,
            excluded=excluded,
        )
        outcomes[path] = outcome
        if slot and outcome.span is not None:
            taken.setdefault(slot, set()).add(outcome.span)

    counts = _count(outcomes, not_applicable)
    provenance = GroundingProvenance(
        document_id=document.id,
        extraction_artifact_id=extraction.artifact_id,
        grounding_version=GROUNDING_VERSION,
        match_view_version=MATCH_VIEW_VERSION,
        view_id=view.view_id,
        options=options,
        grounder_id=GROUNDER_ID,
        grounder_version=GROUNDER_VERSION,
    )
    result = GroundingResult(
        outcomes=outcomes,
        counts=counts,
        provenance=provenance,
        artifact_id=grounding_artifact_id_for(
            extraction_artifact_id=extraction.artifact_id,
            options=options,
        ),
    )
    log_grounding(result, duration_ms=(time.perf_counter() - started) * 1000)
    return result


def _count(outcomes: dict[str, GroundingOutcome], not_applicable: int) -> GroundingCounts:
    tally = dict.fromkeys(GroundingStatus, 0)
    truncated = 0
    for outcome in outcomes.values():
        tally[outcome.status] += 1
        truncated += outcome.truncated
    return GroundingCounts(
        exact=tally[GroundingStatus.EXACT],
        fuzzy=tally[GroundingStatus.FUZZY],
        ungrounded=tally[GroundingStatus.UNGROUNDED],
        not_applicable=not_applicable,
        truncated=truncated,
    )
