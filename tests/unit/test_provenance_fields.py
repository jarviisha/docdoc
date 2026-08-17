"""T009 — the additive provenance fields, and the Milestone 1 back-compat they preserve.

data-model.md §7, ING-18, ING-19. The point of these tests is less the new fields
themselves than the promise made when they were added: that a `Document` built by
Milestone 1 code stays valid, and that identity cannot notice the difference.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from docdoc.kernel import (
    Capabilities,
    IngestProvenance,
    PageTextVerdict,
    TextLayerRecord,
    blob_id_for,
    document_id_for,
    options_hash_for,
)

CAPS = Capabilities(text=True, geometry=True, tables=False, handwriting=False)
EMPTY_HASH = options_hash_for({})


def make_record(**overrides: object) -> TextLayerRecord:
    defaults: dict[str, object] = {
        "rule_id": "text-layer@1",
        "min_chars_per_page": 100,
        "min_text_bearing_fraction": 0.5,
        "pages": (PageTextVerdict(0, 242, True),),
        "text_layer_usable": True,
    }
    defaults.update(overrides)
    return TextLayerRecord(**defaults)  # type: ignore[arg-type]


def make_provenance(**overrides: object) -> IngestProvenance:
    defaults: dict[str, object] = {
        "parser_id": "pdf-text",
        "parser_version": "1.0.0+pymupdf-1.28.2",
        "options": {},
        "options_hash": EMPTY_HASH,
        "capabilities": CAPS,
        "text_layer_used": True,
    }
    defaults.update(overrides)
    return IngestProvenance(**defaults)  # type: ignore[arg-type]


class TestBackCompat:
    """ING-19 — the fields are optional so Milestone 1 keeps working."""

    def test_provenance_without_the_new_fields_is_still_valid(self) -> None:
        provenance = make_provenance()

        assert provenance.text_layer is None
        assert provenance.reading_order is None

    def test_identity_cannot_see_the_new_fields(self) -> None:
        # document_id reads blob, parser, version, and options hash -- nothing
        # else. If provenance could change it, adding a field would have
        # invalidated every document ever produced.
        without = make_provenance()
        with_record = make_provenance(text_layer=make_record(), reading_order="pymupdf-stream@1")
        blob = blob_id_for(b"same bytes")

        ids = {
            document_id_for(
                blob_id=blob,
                parser_id=provenance.parser_id,
                parser_version=provenance.parser_version,
                options_hash=provenance.options_hash,
            )
            for provenance in (without, with_record)
        }

        assert len(ids) == 1


class TestVerdictConsistency:
    """ING-18 — the summary bool and the detailed record must not disagree."""

    def test_agreeing_verdict_is_accepted(self) -> None:
        provenance = make_provenance(
            text_layer_used=True, text_layer=make_record(text_layer_usable=True)
        )

        assert provenance.text_layer is not None
        assert provenance.text_layer.text_layer_usable is True

    def test_contradiction_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="contradicts"):
            make_provenance(text_layer_used=False, text_layer=make_record(text_layer_usable=True))

    def test_override_may_disagree_because_that_is_what_an_override_is(self) -> None:
        # The rule said "usable"; the caller forced recognition anyway. The
        # record keeps the verdict that was not acted on (FR-012).
        provenance = make_provenance(
            text_layer_used=False,
            text_layer=make_record(
                text_layer_usable=False, overridden=True, overridden_verdict=True
            ),
        )

        assert provenance.text_layer is not None
        assert provenance.text_layer.overridden_verdict is True

    def test_a_skipped_rule_may_disagree_because_it_never_ran(self) -> None:
        provenance = make_provenance(
            text_layer_used=False,
            text_layer=make_record(
                pages=(), text_layer_usable=False, rule_not_run="reader_unavailable"
            ),
        )

        assert provenance.text_layer is not None
        assert provenance.text_layer.rule_not_run == "reader_unavailable"


class TestPageVerdicts:
    """ING-11 — one entry per page, so 'present for every page' is checkable."""

    def test_ascending_one_per_page_is_accepted(self) -> None:
        record = make_record(
            pages=(
                PageTextVerdict(0, 242, True),
                PageTextVerdict(1, 307, True),
                PageTextVerdict(2, 0, False),
            )
        )

        assert len(record.pages) == 3

    @pytest.mark.parametrize(
        "pages",
        [
            pytest.param((PageTextVerdict(1, 10, False),), id="does-not-start-at-zero"),
            pytest.param(
                (PageTextVerdict(0, 10, False), PageTextVerdict(2, 10, False)),
                id="skips-a-page",
            ),
            pytest.param(
                (PageTextVerdict(0, 10, False), PageTextVerdict(0, 10, False)),
                id="repeats-a-page",
            ),
            pytest.param(
                (PageTextVerdict(1, 10, False), PageTextVerdict(0, 10, False)),
                id="out-of-order",
            ),
        ],
    )
    def test_gaps_and_repeats_are_rejected(self, pages: tuple[PageTextVerdict, ...]) -> None:
        with pytest.raises(ValidationError, match="one per page"):
            make_record(pages=pages)

    def test_empty_is_allowed_only_as_the_skipped_rule_case(self) -> None:
        # ING-10/ING-11: emptiness is legal, but rule_not_run has to explain it.
        record = make_record(pages=(), text_layer_usable=False, rule_not_run="reader_unavailable")

        assert record.pages == ()

    def test_immutability(self) -> None:
        record = make_record()

        with pytest.raises(ValidationError):
            record.rule_id = "text-layer@2"  # type: ignore[misc]
