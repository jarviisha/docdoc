"""T020 — normalization lands inside the unit square, for any page and any box.

SC-002 claims that 100% of tokens produced by any parser carry geometry within
0..1 on both axes. A handful of examples cannot support a claim about 100% of
anything, which is what the property suite is for.
"""

from __future__ import annotations

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from docdoc.ingest.normalize import OUT_OF_PAGE_TOLERANCE, normalize_bbox
from docdoc.kernel import GeometryError

# Page sizes from a postage stamp to a large plan sheet, in points.
page_dimensions = st.floats(min_value=1.0, max_value=5000.0, allow_nan=False, allow_infinity=False)
coordinates = st.floats(
    min_value=-10000.0, max_value=10000.0, allow_nan=False, allow_infinity=False
)


@given(
    width=page_dimensions,
    height=page_dimensions,
    x0=coordinates,
    y0=coordinates,
    x1=coordinates,
    y1=coordinates,
    page_index=st.integers(min_value=0, max_value=999),
)
@settings(max_examples=500)
def test_output_is_always_inside_the_unit_square_or_an_explicit_error(
    width: float,
    height: float,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    page_index: int,
) -> None:
    """The only two outcomes are a normalized box or a GeometryError.

    Never a coordinate outside 0..1, and never a silently clamped box that was
    nowhere near the page.
    """
    try:
        box = normalize_bbox(x0, y0, x1, y1, width=width, height=height, page_index=page_index)
    except GeometryError:
        return

    for value in box:
        assert 0.0 <= value <= 1.0
    assert box.x0 <= box.x1
    assert box.y0 <= box.y1


@given(
    width=page_dimensions,
    height=page_dimensions,
    x0=st.floats(min_value=0.0, max_value=1.0),
    y0=st.floats(min_value=0.0, max_value=1.0),
    x1=st.floats(min_value=0.0, max_value=1.0),
    y1=st.floats(min_value=0.0, max_value=1.0),
)
@settings(max_examples=500)
def test_a_box_inside_the_page_round_trips(
    width: float, height: float, x0: float, y0: float, x1: float, y1: float
) -> None:
    """Normalizing a box already expressed as a page fraction is the identity.

    Guards against a scaling error that happens to keep values in range -- the
    kind of bug a bounds-only assertion cannot see.
    """
    box = normalize_bbox(
        x0 * width,
        y0 * height,
        x1 * width,
        y1 * height,
        width=width,
        height=height,
        page_index=0,
    )

    assert box.x0 == pytest.approx(min(x0, x1), abs=1e-9)
    assert box.y0 == pytest.approx(min(y0, y1), abs=1e-9)
    assert box.x1 == pytest.approx(max(x0, x1), abs=1e-9)
    assert box.y1 == pytest.approx(max(y0, y1), abs=1e-9)


@given(
    width=page_dimensions,
    height=page_dimensions,
    overshoot=st.floats(min_value=0.0, max_value=OUT_OF_PAGE_TOLERANCE),
)
@settings(max_examples=200)
def test_slop_within_the_tolerance_never_raises(
    width: float, height: float, overshoot: float
) -> None:
    box = normalize_bbox(
        -overshoot * width,
        -overshoot * height,
        width * (1.0 + overshoot),
        height * (1.0 + overshoot),
        width=width,
        height=height,
        page_index=0,
    )

    assert box == (0.0, 0.0, 1.0, 1.0)


@given(
    width=page_dimensions,
    height=page_dimensions,
    overshoot=st.floats(min_value=OUT_OF_PAGE_TOLERANCE * 2, max_value=5.0),
)
@settings(max_examples=200)
def test_anything_well_outside_the_page_always_raises(
    width: float, height: float, overshoot: float
) -> None:
    # Keep the overshoot large enough in absolute terms that floating-point
    # scaling cannot pull it back inside the tolerance.
    assume(overshoot * width > 1e-6)

    with pytest.raises(GeometryError):
        normalize_bbox(
            -overshoot * width,
            0.0,
            width * 0.5,
            height * 0.5,
            width=width,
            height=height,
            page_index=0,
        )
