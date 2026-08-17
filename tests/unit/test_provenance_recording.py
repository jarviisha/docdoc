"""T040 — what a finished document says about how it came to exist.

SC-003 requires the verdict, the parser, its version, the options, and the
declared capabilities to be readable off any result *without re-reading the
source file*, and the verdict to be present for every page rather than only for
the document. These tests read only the returned object; if any of them needed
the original bytes, the guarantee would not hold.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from docdoc.ingest import parse
from docdoc.ingest.errors import ParserCapabilityError
from docdoc.ingest.source import SourceFile

FIXTURES = Path(__file__).parent.parent / "fixtures"


def parse_fixture(relative: str, **kwargs: object):  # type: ignore[no-untyped-def]
    return parse((FIXTURES / relative).read_bytes(), **kwargs)  # type: ignore[arg-type]


class TestEverythingIsOnTheDocument:
    def test_the_full_record_is_present(self) -> None:
        document = parse_fixture("pdf/digital_invoice.pdf")
        provenance = document.provenance

        assert provenance.parser_id == "pdf-text"
        assert provenance.parser_version.startswith("1.0.0+pymupdf-")
        assert provenance.options == {}
        assert provenance.capabilities.geometry is True
        assert provenance.reading_order == "pymupdf-stream@1"
        assert provenance.text_layer is not None

    def test_options_are_recorded_as_given(self) -> None:
        document = parse_fixture("pdf/digital_invoice.pdf", options={"mode": "strict"})

        assert document.provenance.options == {"mode": "strict"}
        assert document.provenance.options_hash.startswith("sha256:")

    def test_the_verdict_is_readable_without_the_source(self) -> None:
        document = parse_fixture("pdf/digital_invoice.pdf")
        verdict = document.provenance.text_layer

        assert verdict is not None
        assert verdict.rule_id == "text-layer@1"
        assert verdict.text_layer_usable is True
        assert verdict.min_chars_per_page == 100


class TestPerPageEvidence:
    def test_one_verdict_per_page(self) -> None:
        document = parse_fixture("pdf/mixed_pages.pdf")
        verdict = document.provenance.text_layer

        assert verdict is not None
        assert len(verdict.pages) == len(document.pages)

    def test_a_page_that_contributes_nothing_is_identifiable_as_expected(self) -> None:
        """FR-035 — the difference between "no text here" and "this failed".

        Without the per-page verdict, a page with zero tokens inside an
        otherwise healthy document is indistinguishable from a defect.
        """
        document = parse_fixture("pdf/mixed_pages.pdf")
        verdict = document.provenance.text_layer
        assert verdict is not None

        empty_pages = [page.index for page in document.pages if page.span.start == page.span.end]
        not_text_bearing = [page.page_index for page in verdict.pages if not page.text_bearing]

        assert empty_pages == not_text_bearing == [2]

    def test_the_evidence_includes_the_counts_not_just_the_flags(self) -> None:
        document = parse_fixture("pdf/mixed_pages.pdf")
        verdict = document.provenance.text_layer
        assert verdict is not None

        assert [page.char_count > 0 for page in verdict.pages] == [True, True, False]


class TestOverride:
    def test_forcing_the_native_path_is_recorded_with_what_it_overrode(self) -> None:
        # The rule says this scan is not usable natively; the caller insists.
        document = parse_fixture("pdf/scanned_contract.pdf", force="native")
        verdict = document.provenance.text_layer

        assert verdict is not None
        assert verdict.overridden is True
        assert verdict.overridden_verdict is False, "the rule's verdict must survive the override"
        assert verdict.text_layer_usable is True, "the route actually taken"

    def test_the_page_evidence_survives_an_override(self) -> None:
        document = parse_fixture("pdf/scanned_contract.pdf", force="native")
        verdict = document.provenance.text_layer

        assert verdict is not None
        assert len(verdict.pages) == 2
        assert all(page.text_bearing is False for page in verdict.pages)

    def test_forcing_recognition_without_it_installed_still_fails_loudly(self) -> None:
        with pytest.raises(ParserCapabilityError):
            parse_fixture("pdf/digital_invoice.pdf", force="recognition")


class TestUnassessableSource:
    def test_without_the_reader_an_unforced_parse_refuses(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._hide_reader(monkeypatch)

        with pytest.raises(ParserCapabilityError) as caught:
            parse_fixture("pdf/digital_invoice.pdf")

        assert "force=" in str(caught.value)

    def test_a_forced_parse_records_that_the_rule_never_ran(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The recognition-only deployment, which would otherwise be unable to
        parse any PDF at all."""
        from docdoc.ingest.assess import TextLayerRule
        from docdoc.ingest.parse import _route

        self._hide_reader(monkeypatch)
        source = SourceFile.from_bytes((FIXTURES / "pdf/digital_invoice.pdf").read_bytes())

        verdict = _route(source, rule=TextLayerRule(), force="recognition")

        assert verdict.rule_not_run == "reader_unavailable"
        assert verdict.overridden is True
        assert verdict.pages == ()

    @staticmethod
    def _hide_reader(monkeypatch: pytest.MonkeyPatch) -> None:
        import builtins

        real_import = builtins.__import__

        def hide(name: str, *args: object, **kwargs: object) -> object:
            if name == "docdoc.ingest.parsers.pdf_text":
                raise ImportError("no native reader installed")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(builtins, "__import__", hide)
