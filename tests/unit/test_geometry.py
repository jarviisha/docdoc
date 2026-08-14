"""T009 — BBox invariants BB-1..BB-4 and Geometry GE-1 (FR-005)."""

from __future__ import annotations

import math

import pytest

from docdoc.kernel import BBox, Geometry, GeometryError


class TestBBoxConstruction:
    def test_create_accepts_a_normalized_box(self) -> None:
        box = BBox.create(0.1, 0.2, 0.5, 0.6)
        assert (box.x0, box.y0, box.x1, box.y1) == (0.1, 0.2, 0.5, 0.6)

    def test_create_accepts_the_full_page(self) -> None:
        BBox.create(0.0, 0.0, 1.0, 1.0)

    def test_create_accepts_a_zero_area_box(self) -> None:
        """BB-4 — a zero-width token box is legitimate."""
        box = BBox.create(0.5, 0.5, 0.5, 0.5)
        assert box.area == 0.0

    @pytest.mark.parametrize(
        "coords",
        [
            (-0.01, 0.0, 0.5, 0.5),  # x0 below range
            (0.0, -0.01, 0.5, 0.5),  # y0 below range
            (0.0, 0.0, 1.01, 0.5),  # x1 above range
            (0.0, 0.0, 0.5, 1.01),  # y1 above range
        ],
    )
    def test_create_rejects_coordinates_outside_the_unit_square(
        self, coords: tuple[float, float, float, float]
    ) -> None:
        """BB-1/BB-3 — geometry is normalized; absolute units never reach the kernel."""
        with pytest.raises(GeometryError):
            BBox.create(*coords)

    @pytest.mark.parametrize(
        "coords",
        [
            (0.6, 0.0, 0.4, 0.5),  # x1 < x0
            (0.0, 0.6, 0.5, 0.4),  # y1 < y0
        ],
    )
    def test_create_rejects_inverted_boxes(self, coords: tuple[float, float, float, float]) -> None:
        with pytest.raises(GeometryError):
            BBox.create(*coords)

    @pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
    def test_create_rejects_non_finite_coordinates(self, bad: float) -> None:
        with pytest.raises(GeometryError):
            BBox.create(0.0, 0.0, bad, 1.0)


class TestBBoxDerived:
    def test_width_and_height(self) -> None:
        box = BBox(0.2, 0.3, 0.7, 0.5)
        assert box.width == pytest.approx(0.5)
        assert box.height == pytest.approx(0.2)

    def test_area(self) -> None:
        assert BBox(0.0, 0.0, 0.5, 0.4).area == pytest.approx(0.2)

    def test_union_covers_both_boxes(self) -> None:
        union = BBox(0.1, 0.1, 0.3, 0.3).union(BBox(0.5, 0.4, 0.8, 0.6))
        assert union == BBox(0.1, 0.1, 0.8, 0.6)

    def test_union_is_commutative(self) -> None:
        a, b = BBox(0.1, 0.1, 0.3, 0.3), BBox(0.5, 0.4, 0.8, 0.6)
        assert a.union(b) == b.union(a)

    @pytest.mark.parametrize(
        ("a", "b", "expected"),
        [
            (BBox(0.0, 0.0, 0.5, 0.5), BBox(0.4, 0.4, 0.9, 0.9), True),
            (BBox(0.0, 0.0, 0.4, 0.4), BBox(0.5, 0.5, 0.9, 0.9), False),
            (BBox(0.0, 0.0, 0.5, 0.5), BBox(0.5, 0.5, 0.9, 0.9), False),  # touching only
        ],
    )
    def test_intersects(self, a: BBox, b: BBox, expected: bool) -> None:
        assert a.intersects(b) is expected


class TestGeometry:
    def test_geometry_anchors_a_box_to_a_page(self) -> None:
        """A BBox alone is never meaningful -- geometry is always page-anchored."""
        geometry = Geometry.create(page_index=2, bbox=BBox(0.1, 0.1, 0.2, 0.2))
        assert geometry.page_index == 2

    def test_geometry_rejects_a_negative_page_index(self) -> None:
        """GE-1 — page indices are 0-based and must reference a real page."""
        with pytest.raises(GeometryError):
            Geometry.create(page_index=-1, bbox=BBox(0.1, 0.1, 0.2, 0.2))

    def test_geometry_equality_is_structural(self) -> None:
        a = Geometry(0, BBox(0.1, 0.1, 0.2, 0.2))
        b = Geometry(0, BBox(0.1, 0.1, 0.2, 0.2))
        assert a == b
        assert hash(a) == hash(b)
