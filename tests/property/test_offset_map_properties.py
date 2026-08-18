"""T012 — the offset map's invariants (GRD-7, GRD-8, GRD-9, FR-015, FR-017).

ADR-0006 names this "the highest-risk component in the grounding path" and asks
for "the strongest tests outside the kernel itself". These are those tests.

The reason it earns them: every other failure in this feature is loud. A wrong
offset map is not. It returns a well-formed range, a plausible page, and a real
bounding box -- pointing at the wrong place. Nothing else in this repository
would catch that.

**On the direction the round trip is tested in.** GRD-9 states the invariant as a
source range mapped into the view and back. The map is deliberately one-way in
production -- grounding only ever needs view-to-source -- and adding a reverse
mapping used by nothing but a test would be an abstraction without a
present-tense reason (Principle XI). So the invariant is asserted in the form
that is both checkable with the real API and is the actual safety property:
**re-folding the source range a view range maps to must reproduce that view
range, exactly where the boundaries survive and as a superset otherwise.** A map
that narrowed or moved a range fails this; a map that widened one does not.
"""

from __future__ import annotations

from hypothesis import assume, given
from hypothesis import strategies as st

from docdoc.grounding.view import _fold

# Characters chosen so the generated text actually exercises the folding rules
# rather than sampling a space of strings the view leaves alone.
INTERESTING = st.sampled_from(
    [
        "a",
        "b",
        "z",
        "A",
        "Z",
        "0",
        "9",
        "-",
        ".",
        ",",
        " ",
        "\n",
        "\t",
        "\r",
        "­",  # soft hyphen
        "ﬁ",  # fi ligature
        "ﬃ",  # ffi ligature
        " ",  # NBSP
        " ",  # narrow NBSP
        " ",  # figure space
        "́",  # combining acute
        "‐",  # hyphen
        "é",
        "ﬁ",
        "①",
        "１",
    ]
)
TEXT = st.lists(INTERESTING, min_size=0, max_size=60).map("".join)


@given(TEXT)
def test_map_is_contiguous_and_total(source: str) -> None:
    """GRD-7: segments cover every view position and every source position once.

    Constructed by OffsetMap's own validator, so this asserts the validator is
    actually reached and that _fold never produces a map it would reject.
    """
    view, omap = _fold(source)
    assert sum(s.length for s in omap.segments) == len(view)
    assert sum(s.source_length for s in omap.segments) == len(source)


@given(TEXT)
def test_mapping_is_monotonic_non_decreasing(source: str) -> None:
    """GRD-8. A non-monotonic map produces ranges that run backwards."""
    view, omap = _fold(source)
    positions = [omap.source_start_for(i) for i in range(len(view) + 1)]
    assert positions == sorted(positions)
    ends = [omap.source_end_for(i) for i in range(len(view) + 1)]
    assert ends == sorted(ends)


@given(TEXT)
def test_every_view_position_maps_into_the_source(source: str) -> None:
    """FR-015: total in the view-to-source direction, and always in range."""
    view, omap = _fold(source)
    for i in range(len(view) + 1):
        assert 0 <= omap.source_start_for(i) <= len(source)
        assert 0 <= omap.source_end_for(i) <= len(source)


@given(TEXT, st.integers(min_value=0), st.integers(min_value=0))
def test_round_trip_never_narrows_or_moves(source: str, a: int, b: int) -> None:
    """GRD-9, FR-017 — the invariant that stops a confidently wrong box.

    Re-folding the source range must reproduce the view range. Widening is
    permitted and shows up as extra characters around it; narrowing or moving
    would drop the match, and is what this asserts cannot happen.
    """
    view, omap = _fold(source)
    assume(len(view) > 0)
    start, end = sorted((a % (len(view) + 1), b % (len(view) + 1)))
    span = omap.source_span_for(start, end)

    assert span.start <= span.end, "mapped range runs backwards"
    refolded, _ = _fold(source[span.start : span.end])
    assert view[start:end] in refolded, (
        f"round trip lost the match: view[{start}:{end}]={view[start:end]!r} "
        f"not in refolded source {refolded!r}"
    )


#: Text the transformations leave alone entirely: no ligature to expand, no space
#: run to collapse, no combining mark to compose, no hyphen at a line break.
UNCHANGED_TEXT = st.text(
    alphabet=st.sampled_from(list("abcdefghijklmnopqrstuvwxyzABCXYZ0123456789.,-")),
    min_size=1,
    max_size=60,
)


@given(UNCHANGED_TEXT, st.integers(min_value=0), st.integers(min_value=0))
def test_round_trip_is_exact_where_boundaries_survive(source: str, a: int, b: int) -> None:
    """GRD-9's first clause: identity where the transformations left both edges alone.

    Asserted over text the view does not change at all, where *every* boundary
    survives and the round trip must therefore be exactly the identity for every
    range -- no widening permitted.

    An earlier version of this test tried to characterise surviving boundaries
    inside mixed text, by collecting positions belonging to identity segments. It
    was wrong, and Hypothesis found it: in ``'a' + 'fi'`` the position between the
    two is the end of an identity segment *and* the start of an expanding one, so
    a range ending there legitimately widens to cover the ligature. The predicate
    is fiddlier than the property is worth, and the mixed case is already covered
    -- ``test_round_trip_never_narrows_or_moves`` runs over the full alphabet and
    asserts the containment that actually keeps a bounding box honest.
    """
    view, omap = _fold(source)
    assert view == source, "alphabet is supposed to be fold-invariant"
    start, end = sorted((a % (len(view) + 1), b % (len(view) + 1)))

    span = omap.source_span_for(start, end)
    assert (span.start, span.end) == (start, end)
    assert source[span.start : span.end] == view[start:end]


@given(TEXT)
def test_the_whole_view_maps_to_the_whole_source(source: str) -> None:
    """The degenerate case, which a segment-list bug breaks first."""
    view, omap = _fold(source)
    assert omap.source_span_for(0, len(view)).start == 0
    assert omap.source_span_for(0, len(view)).end == len(source)


# ---------------------------------------------------------------------------
# Regressions. Both were found by the properties above rather than by review,
# and both are the same failure mode: a range that came back *narrower* than
# what matched, which produces a bounding box missing part of the value.
# ---------------------------------------------------------------------------


def test_a_ligature_carrying_a_combining_mark_does_not_narrow() -> None:
    """`ﬁ` + U+0301 folds to `fí`: two view characters from two source characters.

    Equal lengths, and emphatically not a character-wise mapping -- the `í` was
    contributed by *both* source characters. An earlier version inferred
    "identity" from `length == source_length` and mapped by addition, landing on
    the combining mark alone and losing the `i`. `Segment.identity` is recorded
    rather than inferred because of this case.
    """
    view, omap = _fold("aaaﬁ́")
    assert view == "aaafí"
    span = omap.source_span_for(4, 5)
    refolded, _ = _fold("aaaﬁ́"[span.start : span.end])
    assert "í" in refolded


def test_a_range_ending_at_a_line_break_does_not_swallow_it() -> None:
    """The mirror failure: widening *past* what matched rather than to contain it.

    A claim ending where a collapsed whitespace run begins sits at the segment
    *boundary*, not inside it. Mapping the boundary to the segment's far edge
    appended the newline to every such range.
    """
    source = "Widget 25.00\nWidget 30.00"
    view, omap = _fold(source)
    end = view.index("\n") if "\n" in view else view.index(" 25.00") + len(" 25.00")
    span = omap.source_span_for(0, end)
    assert source[span.start : span.end] == "Widget 25.00"
