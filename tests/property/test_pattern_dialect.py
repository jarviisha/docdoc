"""T043 — the differential oracle that contains the milestone's one complexity entry.

docdoc ships its own regular-expression engine, which is exactly the thing
Principle XI warns against. The justification is a time bound: CPython's `re`
takes 1,183 ms on `^(a+)+$` against 24 characters and doubles per character, and
a timeout would make a verdict depend on machine speed.

That justification buys a bound and nothing else. **Correctness is not
re-derived here** — for every pattern inside the dialect, docdoc's matcher must
agree with `re.fullmatch` on every input. The stdlib is the oracle for *what*
matches; docdoc's engine exists only for *how long it may take*.

If this file ever fails, the engine is wrong and the stdlib is right.
"""

from __future__ import annotations

import re

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from docdoc.validation.pattern import compile_pattern

_LITERALS = st.sampled_from(list("abc012-_. "))
_CLASSES = st.sampled_from([r"\d", r"\w", r"\s", r"\D", r"\W", r"\S", "."])
_SETS = st.sampled_from(["[abc]", "[a-c]", "[^abc]", "[0-9a-f]", r"[\d_]", "[-a]"])
_QUANTIFIERS = st.sampled_from(["", "*", "+", "?", "{2}", "{1,3}", "{0,2}", "{2,}"])


@st.composite
def _atoms(draw) -> str:
    atom = draw(st.one_of(_LITERALS, _CLASSES, _SETS))
    return atom + draw(_QUANTIFIERS)


@st.composite
def _patterns(draw) -> str:
    """Concatenations, with a chance of one alternation group.

    Deliberately shallow: the risk this file guards is a wrong *semantic* for a
    construct, not a deep nesting the parser cannot reach. Depth is covered by
    the explicit cases in `tests/unit/test_pattern_dialect_rejections.py`.
    """
    parts = draw(st.lists(_atoms(), min_size=1, max_size=5))
    if draw(st.booleans()):
        left = draw(st.lists(_atoms(), min_size=1, max_size=2))
        right = draw(st.lists(_atoms(), min_size=1, max_size=2))
        group = "(" + "".join(left) + "|" + "".join(right) + ")"
        parts.append(group + draw(_QUANTIFIERS))
    return "".join(parts)


_INPUTS = st.text(alphabet="abc012-_. \n", max_size=12)


@given(_patterns(), _INPUTS)
@settings(max_examples=600)
def test_the_dialect_agrees_with_the_stdlib(pattern: str, text: str) -> None:
    try:
        oracle = re.compile(pattern)
    except re.error:  # pragma: no cover - the strategy builds valid patterns
        assume(False)
        return
    mine = compile_pattern(pattern)
    assert mine.fullmatch(text) == (oracle.fullmatch(text) is not None), (
        f"pattern {pattern!r} against {text!r}"
    )


@given(_patterns(), _INPUTS)
@settings(max_examples=200)
def test_matching_is_a_pure_function(pattern: str, text: str) -> None:
    """The same pattern and the same value always give the same answer (FR-051)."""
    compiled = compile_pattern(pattern)
    assert compiled.fullmatch(text) == compiled.fullmatch(text)
    assert compile_pattern(pattern).fullmatch(text) == compiled.fullmatch(text)


@given(_patterns())
@settings(max_examples=200)
def test_anchors_at_the_edges_are_redundant(pattern: str) -> None:
    """`^...$` is what most authors write, and it means what they think it means."""
    assume(not pattern.endswith("\\"))
    bare = compile_pattern(pattern)
    anchored = compile_pattern(f"^{pattern}$")
    for sample in ("", "abc", "012", "a-b_c"):
        assert bare.fullmatch(sample) == anchored.fullmatch(sample)
