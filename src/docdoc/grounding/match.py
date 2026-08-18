"""Finding a claim in a document, and deciding which occurrence wins.

The exact tier lives here now; the approximate tier joins it in US2. Both feed
one selection rule, and that rule is **total**: highest score, then earliest
start, then shortest range. Because the ordering is total, exactly one winner
exists for any candidate set, and no outcome can depend on iteration order, hash
seed, or platform.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from rapidfuzz.distance import Levenshtein

from docdoc.grounding.candidates import candidate_starts, window_lengths
from docdoc.grounding.errors import GroundingError
from docdoc.grounding.result import Alternative, GroundingOutcome, GroundingStatus
from docdoc.grounding.view import MatchView, fold_claim
from docdoc.kernel import CapabilityError, Document, Geometry, Span

# `find_exact`, `find_fuzzy`, `outcome_for`, and `geometry_for` are deliberately
# absent: they are the tiers and the mapping step that `resolve` composes, and
# nothing outside this module calls them except tests reaching in on purpose.
# An `__all__` entry advertises a surface someone may depend on, so it lists
# what this module is *for* rather than everything it happens to define.
__all__ = ["MAX_ALTERNATIVES", "Candidate", "resolve", "select"]

if TYPE_CHECKING:
    from docdoc.grounding.options import GroundingOptions

#: ADR-0005's limit on recorded runners-up.
MAX_ALTERNATIVES = 5


class Candidate(NamedTuple):
    """A scored window in *view* coordinates. Never leaves this module."""

    score: float
    view_start: int
    view_end: int


def _order(candidate: Candidate) -> tuple[float, int, int]:
    """The total tie-break of ADR-0005, as a sort key.

    Negated score so that ascending sort puts the best first; then earliest
    start; then shortest range. Every component is a number and every candidate
    has all three, so no two distinct candidates compare equal unless they *are*
    the same window -- which is what makes the winner deterministic.
    """
    return (-candidate.score, candidate.view_start, candidate.view_end - candidate.view_start)


def select(candidates: list[Candidate]) -> tuple[Candidate | None, list[Candidate]]:
    """The winner and up to ``MAX_ALTERNATIVES`` runners-up, in tie-break order."""
    if not candidates:
        return None, []
    ordered = sorted(candidates, key=_order)
    return ordered[0], ordered[1 : 1 + MAX_ALTERNATIVES]


def find_exact(view: MatchView, folded_claim: str) -> list[Candidate]:
    """Every occurrence of the claim in the view, scoring exactly 1.0.

    The score is assigned **structurally**, not by running the fuzzy scorer
    (research.md R13). The scorer would also return 1.0, so the two agree today.
    Deriving this one from that one would quietly make them the same quantity,
    and ADR-0004 says they are not comparable.

    Overlapping candidates resolve left to right, resuming after each match, the
    same rule the kernel's own ``find`` uses.
    """
    if not folded_claim:
        return []
    out: list[Candidate] = []
    cursor = 0
    width = len(folded_claim)
    while (position := view.text.find(folded_claim, cursor)) >= 0:
        out.append(Candidate(1.0, position, position + width))
        cursor = position + width
    return out


def find_fuzzy(
    view: MatchView,
    folded_claim: str,
    options: GroundingOptions,
) -> tuple[list[Candidate], bool]:
    """Every window at or above the threshold, and whether the budget cut the search.

    ``rapidfuzz`` appears here and **only** here, as a pure function from two
    strings to a number. It is deliberately not asked to do more (research.md R2):

    * ``fuzz.partial_ratio_alignment`` would locate the best window in C++ at
      0.22 ms over a 50k view. It returns *one* alignment, so it cannot fill the
      alternatives list ADR-0005 requires -- and which alignment it returns when
      several tie is an undocumented internal. A minor-version change to it would
      silently move every result while ``grounding_version`` stayed at ``v1``,
      which is the exact failure the version exists to prevent.
    * ``process.cdist`` would score every window in one call, and requires numpy,
      which is not in the constitution's sanctioned stack.

    So candidate generation, selection, and ordering are docdoc's own code, and
    the library contributes only a distance -- which has no choices to make.
    """
    claim_length = len(folded_claim)
    candidates = candidate_starts(
        claim=folded_claim,
        view_text=view.text,
        threshold=options.threshold,
        budget=options.candidate_budget,
    )
    lengths = window_lengths(claim_length, options.threshold)
    view_length = len(view.text)

    out: list[Candidate] = []
    for start in candidates.starts:
        for length in lengths:
            end = start + length
            if end > view_length:
                break
            score = Levenshtein.normalized_similarity(
                folded_claim,
                view.text[start:end],
                score_cutoff=options.threshold,
            )
            if score:
                out.append(Candidate(score, start, end))
    return out, candidates.truncated


def geometry_for(document: Document, span: Span) -> tuple[Geometry, ...] | None:
    """Boxes covering ``span``, or ``None`` when the parser supplied none.

    ``locate`` raises ``CapabilityError`` rather than returning an empty tuple so
    a caller can never mistake "unavailable" for "nothing there" (kernel FR-022).
    Grounding absorbs that here: a value located in a document without geometry
    is still perfectly well grounded, and failing it would be wrong.
    """
    try:
        return document.locate(span)
    except CapabilityError:
        return None


def outcome_for(
    *,
    field_path: str,
    document: Document,
    view: MatchView,
    winner: Candidate | None,
    runners_up: list[Candidate],
    truncated: bool = False,
) -> GroundingOutcome:
    """Turn a winning view-space candidate into a source-space outcome.

    Every range crosses back through the offset map here, which is the only place
    it happens. No view position may escape into a result (FR-016).
    """
    if winner is None:
        return GroundingOutcome(
            field_path=field_path,
            status=GroundingStatus.UNGROUNDED,
            truncated=truncated,
        )

    # The one place a view position crosses back to a source position, so the one
    # place a map failure can be attributed to the value that triggered it. The
    # map already names the document; `field_path` is what this layer knows and
    # it does not (FR-043).
    try:
        span = view.offsets.source_span_for(winner.view_start, winner.view_end)
    except GroundingError as failure:
        raise GroundingError(
            f"{failure} (resolving {field_path!r})",
            document_id=failure.document_id,
            field_path=field_path,
        ) from failure

    status = GroundingStatus.EXACT if winner.score == 1.0 else GroundingStatus.FUZZY
    return GroundingOutcome(
        field_path=field_path,
        status=status,
        score=winner.score,
        span=span,
        pages=document.page_for(span),
        geometry=geometry_for(document, span),
        alternatives=tuple(
            Alternative(
                span=view.offsets.source_span_for(c.view_start, c.view_end),
                score=c.score,
            )
            for c in runners_up
        ),
        truncated=truncated,
    )


def resolve(
    *,
    field_path: str,
    claim: str | None,
    document: Document,
    view: MatchView,
    options: GroundingOptions,
    excluded: frozenset[Span] = frozenset(),
) -> GroundingOutcome:
    """Locate one claim: exact first, then approximate, then honestly ungrounded.

    ``excluded`` carries source ranges already taken by earlier entries of the
    same repeating group, and is empty for every ordinary field. It filters the
    **winner** only -- an alternative may still name a range another entry won,
    because alternatives record what was there rather than what was assigned
    (GRD-13a).
    """
    if claim is None:
        # Present, but asserted with no evidence. Ungrounded, and it counts
        # against the rate -- unlike an absence the model correctly reported,
        # which never reaches this function at all (FR-009 vs FR-008).
        return GroundingOutcome(field_path=field_path, status=GroundingStatus.UNGROUNDED)

    folded = fold_claim(claim)
    if not folded.strip():
        # An empty or whitespace-only claim would match everywhere, which would
        # mean nothing (FR-026).
        return GroundingOutcome(field_path=field_path, status=GroundingStatus.UNGROUNDED)

    # The exact tier runs first and short-circuits the approximate one (FR-021).
    # That ordering is also what keeps the pathological case rare: a claim whose
    # blocks are near-ubiquitous is usually a claim that appears verbatim, and
    # the exact tier resolves it with a `str.find` loop rather than a scan.
    truncated = False
    candidates = find_exact(view, folded)
    if not candidates:
        candidates, truncated = find_fuzzy(view, folded, options)

    ordered = sorted(candidates, key=_order)
    winner: Candidate | None = None
    runners_up: list[Candidate] = []
    for candidate in ordered:
        span = view.offsets.source_span_for(candidate.view_start, candidate.view_end)
        if winner is None and span not in excluded:
            winner = candidate
            continue
        # Everything else becomes an alternative -- **including** a candidate
        # excluded because an earlier entry of the same repeating group won it.
        # Alternatives record what was there, not what was assigned, so filtering
        # them would hide the ambiguity the list exists to surface. A consequence
        # to expect: an alternative may outrank the winner, which is the honest
        # reading when the better range was already taken (GRD-13a).
        if len(runners_up) < MAX_ALTERNATIVES:
            runners_up.append(candidate)

    return outcome_for(
        field_path=field_path,
        document=document,
        view=view,
        winner=winner,
        runners_up=runners_up,
        truncated=truncated,
    )
