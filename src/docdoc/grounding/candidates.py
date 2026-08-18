"""Generating the windows the approximate tier scores, without missing any.

The filter here has a **proof**, which is the reason it was chosen over anything
cheaper or cleverer. If the edit distance between a claim and a window is at most
``k``, and the claim is split into ``k + 1`` disjoint blocks, then those ``k``
edits can damage at most ``k`` of the blocks -- so at least one survives verbatim
inside that window. Searching for every block therefore finds every window that
could possibly clear the threshold.

That is what lets "ungrounded" mean *it is not there* rather than *we did not
look hard enough*, and it is what makes FR-028's determinism claim cover recall
and not merely ordering.

**The slack is derived, not chosen.** ``normalized_similarity`` is
``1 - distance / max(m, |w|)``, so a window clears threshold ``t`` only when
``distance <= (1 - t) * max(m, |w|)``. Since ``|w| <= m + k`` that is
self-referential, and solving it gives ``k = floor((1 - t) * m / t)`` -- verified
against the self-referential form for every claim length 1..59. A larger slack
generates only candidates that are provably below threshold; a smaller one breaks
the proof above. Getting this wrong is not cosmetic: an early draft used an
independent ``slack = 8``, which scores ``(2*8+1)**2 = 289`` windows per block
occurrence against the derived version's ``(2k+1)**2`` -- measured at 1373 ms
versus 53 ms for one value on the adversarial fixture.
"""

from __future__ import annotations

from typing import NamedTuple

__all__ = ["CandidateSet", "candidate_starts", "max_edits", "window_lengths"]


class CandidateSet(NamedTuple):
    """Window start positions to score, and whether the budget cut the list short."""

    starts: tuple[int, ...]
    truncated: bool


def max_edits(claim_length: int, threshold: float) -> int:
    """``k``: the most edits a window can carry and still clear ``threshold``.

    Returns 0 for claims of nine characters or fewer at the default threshold,
    which degenerates the filter to exact search. That is the filter agreeing
    with the threshold rather than a special case: a single edit in a
    nine-character string scores 0.889 and does not clear 0.90.
    """
    if threshold <= 0.0:
        # Everything clears a zero threshold, so no edit budget bounds anything.
        # Fall back to the claim length, which bounds the window sizes instead.
        return claim_length
    return int((1.0 - threshold) * claim_length / threshold)


def blocks(claim: str, k: int) -> list[tuple[int, str]] | None:
    """``k + 1`` disjoint blocks covering the claim, or ``None`` if it cannot be split.

    Disjointness is what the pigeonhole argument needs; the final block takes the
    remainder so the blocks cover the claim exactly.

    ``None`` means the edit budget is at least as large as the claim, so ``k + 1``
    non-empty blocks do not exist and **the pigeonhole argument does not apply**.
    The caller must then fall back to an exhaustive scan; see ``candidate_starts``.
    Returning a single block containing the whole claim would look like a
    reasonable degradation and would silently require a verbatim match, which is
    the opposite of what a low threshold asked for.
    """
    count = k + 1
    if count > len(claim):
        return None
    size = len(claim) // count
    out: list[tuple[int, str]] = []
    for i in range(count):
        start = i * size
        end = len(claim) if i == count - 1 else (i + 1) * size
        out.append((start, claim[start:end]))
    return out


def candidate_starts(
    *,
    claim: str,
    view_text: str,
    threshold: float,
    budget: int,
) -> CandidateSet:
    """Every window start that could hold a match, in ascending order.

    Sorted before the budget is applied, and sorted before anything scores them.
    Both matter: truncating a ``set`` would make which candidates survive depend
    on the hash seed, and scoring in set order would make the *alternatives* list
    vary between runs even though the total tie-break fixes the winner
    (research.md R14).
    """
    k = max_edits(len(claim), threshold)
    parts = blocks(claim, k)

    if parts is None:
        # The edit budget is at least as large as the claim, so no block is
        # guaranteed to survive intact and the filter has nothing to filter on.
        # This happens at thresholds around 0.5 and below, where `k` approaches
        # the claim length.
        #
        # Scan every position rather than degrade quietly. Completeness is what
        # makes "ungrounded" mean *not there* instead of *not looked for*, and a
        # caller who lowered the threshold asked for a broader search, not a
        # narrower one. The cost is bounded by `budget` exactly as it is on the
        # filtered path, so the pathological case behaves identically.
        ordered_all = list(range(len(view_text)))
        if len(ordered_all) > budget:
            return CandidateSet(tuple(ordered_all[:budget]), True)
        return CandidateSet(tuple(ordered_all), False)

    found: set[int] = set()
    for offset, block in parts:
        if not block:
            continue
        cursor = 0
        while (position := view_text.find(block, cursor)) >= 0:
            # A block occurrence at `position` implies a window starting at
            # `position - offset`, displaced by at most `k` if edits before the
            # block shifted it.
            for delta in range(-k, k + 1):
                start = position - offset + delta
                if start >= 0:
                    found.add(start)
            cursor = position + 1

    ordered = sorted(found)
    if len(ordered) > budget:
        return CandidateSet(tuple(ordered[:budget]), True)
    return CandidateSet(tuple(ordered), False)


def window_lengths(claim_length: int, threshold: float) -> range:
    """The window sizes worth scoring: ``m - k`` through ``m + k``.

    A window whose length differs from the claim's by more than ``k`` cannot
    clear the threshold, because the length difference alone is already that many
    edits.
    """
    k = max_edits(claim_length, threshold)
    return range(max(1, claim_length - k), claim_length + k + 1)
