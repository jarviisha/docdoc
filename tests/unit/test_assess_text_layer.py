"""T035, T037 — the text-layer rule, and the thresholds it stands on.

The fixture set is the evidence base for the defaults (T037). Measured on it:
text-bearing pages run 242-307 meaningful characters, and the page furniture on
a scan measures 8. A threshold of 100 sits in the empty middle of that gap,
which is the property worth asserting -- not the number itself, but that it
separates the two populations with room on both sides.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from docdoc.ingest.assess import TextLayerRule, assess_text_layer, meaningful_length
from docdoc.ingest.errors import ParserCapabilityError
from docdoc.ingest.source import SourceFile

# SC-013: the offline suite must pass on a base install — one with no provider
# SDK at all. Constitution XII: "Provider adapters MUST have integration tests;
# those tests MUST NOT be required to run the unit and property suites." These
# tests exercise the text-layer assessment, which reads page text, so they
# skip rather than fail when it is absent.
pytest.importorskip("pymupdf")

FIXTURES = Path(__file__).parent.parent / "fixtures"


def source_for(relative: str) -> SourceFile:
    path = FIXTURES / relative
    return SourceFile.from_bytes(path.read_bytes(), filename=path.name)


class TestMeaningfulLength:
    def test_whitespace_is_not_evidence(self) -> None:
        assert meaningful_length("   \n\t  ") == 0

    def test_replacement_characters_are_not_evidence(self) -> None:
        # What a broken decode leaves behind. Counting it would let a failed
        # extraction pass as a successful one.
        assert meaningful_length("���") == 0

    def test_control_characters_are_not_evidence(self) -> None:
        assert meaningful_length("\x00\x01\x02") == 0

    def test_real_text_counts(self) -> None:
        assert meaningful_length("Invoice INV-001") == 14

    def test_non_latin_scripts_count(self) -> None:
        # "Hóa" + "đơn" + "発票" = 3 + 3 + 2 non-space characters.
        assert meaningful_length("Hóa đơn 発票") == 8


class TestVerdicts:
    def test_a_digital_pdf_is_usable(self) -> None:
        verdict = assess_text_layer(source_for("pdf/digital_invoice.pdf"))

        assert verdict.text_layer_usable is True
        assert verdict.rule_id == "text-layer@1"

    def test_a_scanned_pdf_is_not_usable(self) -> None:
        verdict = assess_text_layer(source_for("pdf/scanned_contract.pdf"))

        assert verdict.text_layer_usable is False
        assert all(page.char_count == 0 for page in verdict.pages)

    def test_a_sparse_text_layer_is_not_usable(self) -> None:
        """The case a naive "any text at all" rule gets wrong.

        A scan carrying a stamped page number has a text layer. It is not a
        usable one, and routing it natively would produce a near-empty document
        that looks like a successful parse.
        """
        verdict = assess_text_layer(source_for("pdf/sparse_text_layer.pdf"))

        assert verdict.text_layer_usable is False
        assert 0 < verdict.pages[0].char_count < 100

    def test_a_mixed_document_follows_the_majority(self) -> None:
        verdict = assess_text_layer(source_for("pdf/mixed_pages.pdf"))

        assert verdict.text_layer_usable is True
        assert [page.text_bearing for page in verdict.pages] == [True, True, False]

    def test_an_image_is_not_usable_and_is_not_inspected(self) -> None:
        verdict = assess_text_layer(source_for("image/sample_page.png"))

        assert verdict.text_layer_usable is False
        assert verdict.pages == ((0, 0, False),)


class TestEvidenceIsRecorded:
    def test_one_verdict_per_page(self) -> None:
        verdict = assess_text_layer(source_for("pdf/mixed_pages.pdf"))

        assert [page.page_index for page in verdict.pages] == [0, 1, 2]

    def test_the_thresholds_are_part_of_the_evidence(self) -> None:
        # Without them, a past verdict cannot be re-derived after a retune.
        verdict = assess_text_layer(source_for("pdf/digital_invoice.pdf"))

        assert verdict.min_chars_per_page == 100
        assert verdict.min_text_bearing_fraction == 0.5

    def test_character_counts_are_recorded_not_just_the_booleans(self) -> None:
        verdict = assess_text_layer(source_for("pdf/digital_invoice.pdf"))

        assert verdict.pages[0].char_count > 0


class TestDeterminism:
    def test_the_same_bytes_yield_an_identical_assessment(self) -> None:
        first = assess_text_layer(source_for("pdf/mixed_pages.pdf"))
        second = assess_text_layer(source_for("pdf/mixed_pages.pdf"))

        assert first == second

    def test_including_the_character_counts(self) -> None:
        counts = [
            tuple(
                page.char_count
                for page in assess_text_layer(source_for("pdf/mixed_pages.pdf")).pages
            )
            for _ in range(3)
        ]

        assert len(set(counts)) == 1


class TestThresholdsAreJustified:
    """T037 — validating the defaults against the committed sample set."""

    def test_the_threshold_sits_in_the_gap_between_the_two_populations(self) -> None:
        text_bearing = [
            page.char_count
            for name in ("pdf/digital_invoice.pdf", "pdf/two_column.pdf")
            for page in assess_text_layer(source_for(name)).pages
        ]
        not_text_bearing = [
            page.char_count
            for name in ("pdf/scanned_contract.pdf", "pdf/sparse_text_layer.pdf")
            for page in assess_text_layer(source_for(name)).pages
        ]

        assert min(text_bearing) > 100 * 2, "text pages should clear the threshold comfortably"
        assert max(not_text_bearing) < 100 / 2, "scans should fall well short of it"

    def test_the_rule_is_configurable_and_the_id_carries_the_thresholds(self) -> None:
        strict = TextLayerRule(id="text-layer@test", min_chars_per_page=10_000)
        verdict = strict.verdict_for(("plenty of text here, but not ten thousand characters",))

        assert verdict.text_layer_usable is False
        assert verdict.rule_id == "text-layer@test"

    def test_a_document_with_no_pages_is_not_usable(self) -> None:
        assert TextLayerRule().verdict_for(()).text_layer_usable is False


class TestUnanswerableIsNotGuessed:
    def test_a_pdf_without_the_native_reader_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FR-012 — an inability to assess is never silently resolved.

        Simulated by hiding the adapter module, which is what an install without
        ``docdoc[pdf]`` amounts to.
        """
        import builtins

        real_import = builtins.__import__

        def hide_pdf_reader(name: str, *args: object, **kwargs: object) -> object:
            if name == "docdoc.ingest.parsers.pdf_text":
                raise ImportError("no native reader installed")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(builtins, "__import__", hide_pdf_reader)

        with pytest.raises(ParserCapabilityError) as caught:
            assess_text_layer(source_for("pdf/digital_invoice.pdf"))

        assert "force=" in str(caught.value)
        assert caught.value.candidates == (("pdf-text", False, "extra_not_installed"),)

    def test_a_skipped_rule_explains_itself(self) -> None:
        verdict = TextLayerRule().skipped("reader_unavailable")

        assert verdict.pages == ()
        assert verdict.rule_not_run == "reader_unavailable"
        assert verdict.text_layer_usable is False
