"""The two pure pieces of the native PDF parser: where a box lands on the page
as displayed, and how PDFium's characters become words.

No PDF here. The 90-degree fixture is the only rotated file there is, and an
offset crop box has none at all, so the arithmetic is pinned with numbers.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pypdfium2")

from docdoc.ingest.parsers.pdf_text import displayed_box, displayed_size, group_words

CROP = (10.0, 20.0, 110.0, 220.0)  # W=100, H=200, offset from the origin
BOX = (20.0, 200.0, 30.0, 210.0)  # unrotated, top-left origin: x 10..20, y 10..20


@pytest.mark.parametrize(
    ("rotation", "expected"),
    [
        (0, (10.0, 10.0, 20.0, 20.0)),
        (90, (180.0, 10.0, 190.0, 20.0)),
        (180, (80.0, 180.0, 90.0, 190.0)),
        (270, (10.0, 80.0, 20.0, 90.0)),
    ],
)
def test_a_box_lands_where_the_page_displays_it(
    rotation: int, expected: tuple[float, float, float, float]
) -> None:
    assert displayed_box(BOX, crop=CROP, rotation=rotation) == pytest.approx(expected)


def test_quarter_turns_swap_the_displayed_size() -> None:
    assert displayed_size(CROP, 0) == (100.0, 200.0)
    assert displayed_size(CROP, 180) == (100.0, 200.0)
    assert displayed_size(CROP, 90) == (200.0, 100.0)
    assert displayed_size(CROP, 270) == (200.0, 100.0)


def test_an_odd_rotation_is_refused() -> None:
    with pytest.raises(ValueError, match="not a quarter turn"):
        displayed_box(BOX, crop=CROP, rotation=45)


def test_generated_and_control_characters_split_words() -> None:
    b = (0.0, 0.0, 1.0, 1.0)
    chars = [
        ("A", b), ("B", b), (" ", b), ("C", b), ("\x02", b), ("D", b),
        ("\r", b), ("\n", b), ("E", b), ("\x03", b),
    ]  # fmt: skip
    lines = group_words(chars)
    assert [[word for word, _ in line] for line in lines] == [["AB", "C", "D"], ["E"]]


def test_a_word_box_is_the_union_of_its_characters() -> None:
    (line,) = group_words([("A", (0.0, 0.0, 1.0, 2.0)), ("B", (1.0, -1.0, 3.0, 1.0))])
    assert line == [("AB", (0.0, -1.0, 3.0, 2.0))]


def test_blank_lines_are_dropped() -> None:
    b = (0.0, 0.0, 1.0, 1.0)
    assert group_words([("\n", b), (" ", b), ("\n", b), ("X", b)]) == [[("X", b)]]


def test_a_line_end_hyphen_marker_becomes_a_hyphen_and_ends_the_line() -> None:
    # PDFium replaces a hyphen at a line end with U+FFFE and drops the line
    # break after it; the hyphen is real text and the break is real layout.
    b = (0.0, 0.0, 1.0, 1.0)
    lines = group_words([("C", b), ("\ufffe", b), ("D", b)])
    assert [[word for word, _ in line] for line in lines] == [["C-"], ["D"]]
