"""The folded text matching runs against, and never the text anyone consumes.

``Document.text`` stays byte-faithful source text. This is a derived, versioned
view of it built for comparison only: it is never exposed as a document, never
persisted as canonical text, and never handed to a consumer (FR-013). Every range
this layer returns is mapped back through the view's offset map and is a range
into the *source*.

**Four rules produce ADR-0006's six effects, and the two missing ones are not an
oversight.** Measured, not assumed (research.md R5):

* NFKC **expands ligatures**: U+FB01 -> ``fi``, U+FB03 -> ``ffi``.
* NFKC **folds non-breaking spaces**: U+00A0, U+202F, and U+2007 all become
  U+0020.
* NFKC does **not** touch U+00AD SOFT HYPHEN, which is why that rule is explicit.

So writing a separate ligature table or NBSP rule after NFKC has already done the
work would be dead code that a later reader would reasonably trust. If you came
here to "add the missing transformations", they are already applied.

**One transformation ADR-0006 pinned for v1 is deliberately absent.** NFKC maps
U+2011 (non-breaking hyphen) to U+2010 (hyphen) but neither to ASCII U+002D, so a
document typeset with U+2010 and a model quoting ASCII ``-`` misses the exact tier
and falls to fuzzy. Adding dash folding would very likely raise the grounding
rate -- and would be a seventh transformation inside a version ADR-0006 pinned,
which the constitution's precedence rule forbids resolving in code. It is a
``v2`` candidate to decide with Milestone 6's measurements (research.md R6).
"""

from __future__ import annotations

import unicodedata
from hashlib import sha256

from docdoc.grounding.offsets import OffsetMap, Segment
from docdoc.kernel import Document, canonical_json

__all__ = ["MATCH_VIEW_VERSION", "MatchView", "fold_claim"]

#: Pinned by ADR-0006. Adding, removing, or altering any transformation below
#: REQUIRES a bump here, enforced by the snapshot test rather than by review.
MATCH_VIEW_VERSION = "v1"

_SOFT_HYPHEN = "­"
_HYPHENS = ("-", "‐")


def _is_lower(ch: str) -> bool:
    return ch.islower() and ch.isalpha()


class MatchView:
    """Folded text plus the map back to the source.

    A plain class rather than a ``pydantic`` model: built once per grounding run
    over text that can be tens of thousands of characters, never serialised, and
    never exposed. Validation on construction would cost measurably and buy
    nothing that :class:`OffsetMap`'s own invariant check does not already give.
    """

    __slots__ = ("document_id", "offsets", "text", "version", "view_id")

    def __init__(self, *, text: str, offsets: OffsetMap, document_id: str) -> None:
        self.text = text
        self.offsets = offsets
        self.document_id = document_id
        self.version = MATCH_VIEW_VERSION
        self.view_id = _view_id_for(document_id, MATCH_VIEW_VERSION)

    def __repr__(self) -> str:
        return f"MatchView({len(self.text)} chars, {self.version}, {len(self.offsets)} segments)"

    @classmethod
    def build(cls, document: Document) -> MatchView:
        """Fold a document's canonical text, recording where every position came from."""
        text, offsets = _fold(document.text)
        return cls(text=text, offsets=offsets, document_id=document.id)


def _view_id_for(document_id: str, version: str) -> str:
    """Identity of one document's view under one version of the rules (GRD-6).

    Reuses the kernel's canonical serialisation rather than inventing a second
    convention, the same reuse ADR-0008 chose for ``schema_hash``.
    """
    payload = canonical_json({"document_id": document_id, "match_view_version": version})
    return f"sha256:{sha256(payload).hexdigest()}"


def fold_claim(claim: str) -> str:
    """Apply the same transformations to a claim before comparing it.

    **Both sides of every comparison must be in the same form** (FR-018). Folding
    only the document would leave a claim containing a non-breaking space unable
    to match a document from which that space was folded away -- the exact class
    of near-miss the view exists to eliminate.

    The claim itself is never modified in place; this returns a copy used for
    comparison, and the stored claim stays byte-identical to what the model
    returned (FR-011).
    """
    return _fold(claim)[0]


def _fold(source: str) -> tuple[str, OffsetMap]:
    """Fold text and build its offset map in one pass.

    Order matters and is pinned: NFKC, then soft-hyphen removal, then
    de-hyphenation across line breaks, then whitespace collapsing. De-hyphenation
    must see the line break that whitespace collapsing would otherwise turn into
    a plain space.
    """
    out: list[str] = []
    segments: list[Segment] = []
    view_pos = 0

    # Consecutive characters the transformations leave alone are accumulated into
    # one run rather than one segment each. That is what makes this a segment list
    # instead of a per-character array (research.md R9): a page of ordinary text
    # becomes a single segment, and only the places something actually happened
    # get their own. It also means `identity` is *recorded* rather than inferred,
    # which is the fix for the ligature-plus-combining-mark case (offsets.Segment).
    run_start = -1  # source index where the current untouched run began

    def flush_run() -> None:
        nonlocal view_pos, run_start
        if run_start < 0:
            return
        length = i - run_start
        out.append(source[run_start:i])
        segments.append(Segment(view_pos, run_start, length, length, identity=True))
        view_pos += length
        run_start = -1

    def emit(view_text: str, source_len: int, source_start: int) -> None:
        """A transformed span: never character-wise, so never ``identity``."""
        nonlocal view_pos
        flush_run()
        out.append(view_text)
        segments.append(Segment(view_pos, source_start, len(view_text), source_len))
        view_pos += len(view_text)

    def keep() -> None:
        """Open an untouched run at ``i`` if one is not already open.

        The run's *end* is wherever the loop has advanced to when something
        closes it, so there is nothing to pass here -- ``flush_run`` reads ``i``.
        """
        nonlocal run_start
        if run_start < 0:
            run_start = i

    i = 0
    n = len(source)
    while i < n:
        ch = source[i]

        # 1. Soft hyphen: removed outright. NFKC leaves it alone (measured).
        if ch == _SOFT_HYPHEN:
            emit("", 1, i)
            i += 1
            continue

        # 2. De-hyphenation across a line break (GRD-3, research.md R7).
        #
        # Three measured cases decided this rule, and both obvious answers are
        # wrong. Always de-hyphenating scores 'INV-2024-001' at 0.833 against its
        # own document; never de-hyphenating scores 'amount' at 0.857 against
        # 'am-ount'. Both fall below the 0.90 threshold, so neither is rescued by
        # the fuzzy tier.
        #
        # The rule that separates them without a dictionary: join only when the
        # character before the hyphen and the first non-whitespace character
        # after the break are both lowercase. Typesetting breaks lowercase words
        # mid-word; identifiers carry uppercase or digits around their hyphens.
        #
        # The residual loss is real and recorded: a genuine compound word broken
        # at a line end -- 'well-known' -> 'wellknown' -- then scores EXACTLY
        # 0.900 against the claim, clearing the threshold by nothing at all.
        # Raising the threshold above 0.90 breaks that case, which is a
        # constraint on the Milestone 6 tuning rather than a bug here.
        if ch in _HYPHENS and i + 1 < n and source[i + 1] in "\r\n":
            j = i + 1
            while j < n and source[j] in " \t\r\n":
                j += 1
            before = source[i - 1] if i > 0 else ""
            after = source[j] if j < n else ""
            if _is_lower(before) and _is_lower(after):
                # Drop the hyphen and the break: "am-\nount" -> "amount".
                emit("", j - i, i)
            else:
                # Keep the hyphen, drop the break: "INV-\n2024" -> "INV-2024".
                emit(ch, j - i, i)
            i = j
            continue

        # 3. Whitespace collapsing: any run becomes a single U+0020 (GRD-4).
        if ch.isspace():
            j = i
            while j < n and source[j].isspace():
                j += 1
            emit(" ", j - i, i)
            i = j
            continue

        # 4. NFKC, applied per character so the map stays exact. Applying it to
        #    the whole string would be faster and would lose which source
        #    character produced which view characters -- and that mapping is the
        #    thing this module exists to preserve.
        folded = unicodedata.normalize("NFKC", ch)
        if folded.isspace():
            # NFKC turned this into whitespace (U+00A0 -> U+0020 and friends).
            # Route it back through the collapsing rule so a NBSP adjacent to a
            # real space does not produce two view spaces.
            j = i + 1
            while j < n and unicodedata.normalize("NFKC", source[j]).isspace():
                j += 1
            emit(" ", j - i, i)
            i = j
            continue

        # A combining sequence composes only when its base and mark are folded
        # together, so look ahead while the next character is a combining mark.
        j = i + 1
        while j < n and unicodedata.combining(source[j]):
            j += 1
        if j > i + 1:
            folded = unicodedata.normalize("NFKC", source[i:j])

        if j == i + 1 and folded == ch:
            # NFKC left this character exactly as it was: extend the untouched
            # run rather than closing a segment around it.
            keep()
            i = j
            continue

        emit(folded, j - i, i)
        i = j

    flush_run()
    return "".join(out), OffsetMap(tuple(segments), view_pos, n)
