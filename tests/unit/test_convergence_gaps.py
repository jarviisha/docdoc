"""T061-T067 - the gaps `/speckit-converge` found, each pinned by a test.

Grouped in one module because that is what they have in common: every test here
exists because an assessment of the code against the spec found something the
first implementation pass left unguarded. Each class names the finding it closes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

import pytest

# Constitution XII: "Provider adapters MUST have integration tests; those tests
# MUST NOT be required to run the unit and property suites." This module reaches a
# provider library at import time, so without this guard the whole file fails
# *collection* on a base install — and SC-013 requires the offline suite to pass
# there. `importorskip` is the mechanism that makes the requirement structural
# rather than a convention every new test file has to remember.
pytest.importorskip("pymupdf")

from docdoc.ingest import parse
from docdoc.ingest.assess import TextLayerRule
from docdoc.ingest.capabilities import ParserCapabilities
from docdoc.ingest.errors import ParserError, ProviderError, UnsupportedDocumentError
from docdoc.ingest.options import TransportSettings
from docdoc.ingest.parsers.azure_di import (
    AzureDocumentIntelligenceParser,
    map_analyze_result,
)
from docdoc.ingest.parsers.pdf_text import PdfTextParser
from docdoc.ingest.source import Limits, SourceFile
from docdoc.ingest.validate import validate_output

FIXTURES = Path(__file__).parent.parent / "fixtures"


def hide_native_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate an install without `docdoc[pdf]`."""
    import builtins

    real_import = builtins.__import__

    def hide(name: str, *args: object, **kwargs: object) -> object:
        if name == "docdoc.ingest.parsers.pdf_text":
            raise ImportError("no native reader installed")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", hide)


class RecordingParser:
    """A stand-in for a service-backed parser that records what it was asked.

    It builds a real document attributed to *itself*. It used to hand back a
    document parsed earlier by `pdf-text`, which T090 now refuses — correctly, and
    the stub was the thing at fault: a parser returning someone else's document is
    exactly the defect that check exists to catch.
    """

    id = "recording"
    version = "1.0.0+stub-1"
    reading_order = "stub@1"
    capabilities = ParserCapabilities(
        text=True,
        geometry=True,
        media_types=frozenset({"application/pdf"}),
        requires_network=True,
    )

    def __init__(self, pages: int = 1) -> None:
        self.pages = pages
        self.calls = 0

    def parse(self, source: Any, options: Any, transport: Any, text_layer: Any = None) -> Any:
        from docdoc.ingest.normalize import DocumentBuilder
        from docdoc.ingest.options import options_fingerprint
        from docdoc.kernel import BBox, IngestProvenance

        self.calls += 1
        builder = DocumentBuilder(geometry=True)
        for _ in range(self.pages):
            builder.start_page(width=595.0, height=842.0)
            builder.add_line([("stub", BBox(0.1, 0.1, 0.2, 0.12))])

        materialized, options_hash = options_fingerprint(options)
        return builder.build(
            source=source.blob_ref(),
            provenance=IngestProvenance(
                parser_id=self.id,
                parser_version=self.version,
                options=materialized,
                options_hash=options_hash,
                capabilities=self.capabilities.to_kernel(),
                text_layer_used=False,
                text_layer=text_layer,
                reading_order=self.reading_order,
            ),
        )


class TestPageLimitOnTheForcedPath:
    """T061, FR-028 — the limit was unenforceable when the rule was skipped.

    Previously `check_page_count` ran only from the assessment's page list, so a
    forced parse in a deployment without the native reader skipped it entirely
    and an over-limit document became a `Document`.
    """

    def test_an_over_limit_document_is_refused_even_when_the_rule_was_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = SourceFile.from_bytes((FIXTURES / "pdf" / "mixed_pages.pdf").read_bytes())
        parser = RecordingParser(pages=3)

        from docdoc.ingest.registry import ParserRegistry

        registry = ParserRegistry(priority=("recording",))
        registry.register(parser)
        hide_native_reader(monkeypatch)

        with pytest.raises(UnsupportedDocumentError) as caught:
            parse(
                source,
                registry=registry,
                force="recognition",
                limits=Limits(max_pages=1),
            )

        assert caught.value.reason == "page_limit"

    def test_a_within_limit_document_still_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        source = SourceFile.from_bytes((FIXTURES / "pdf" / "mixed_pages.pdf").read_bytes())

        from docdoc.ingest.registry import ParserRegistry

        registry = ParserRegistry(priority=("recording",))
        registry.register(RecordingParser(pages=3))
        hide_native_reader(monkeypatch)

        document = parse(
            source, registry=registry, force="recognition", limits=Limits(max_pages=10)
        )

        assert len(document.pages) == 3

    def test_the_pre_parse_check_still_happens_when_the_rule_did_run(self) -> None:
        # The cheap path must not have regressed: when the rule ran, the refusal
        # comes before any parser is invoked.
        with pytest.raises(UnsupportedDocumentError) as caught:
            parse(
                (FIXTURES / "pdf" / "mixed_pages.pdf").read_bytes(),
                limits=Limits(max_pages=2),
            )

        assert caught.value.reason == "page_limit"


class TestRefusalsNameTheirParser:
    """T062, FR-025 and SC-007 — an adapter's refusal must name the adapter."""

    def test_an_encrypted_pdf_names_the_parser_that_refused_it(self) -> None:
        with pytest.raises(UnsupportedDocumentError) as caught:
            parse((FIXTURES / "pdf" / "encrypted.pdf").read_bytes())

        assert caught.value.parser_id == "pdf-text"

    def test_a_corrupt_pdf_names_the_parser_that_refused_it(self) -> None:
        source = SourceFile.from_bytes(b"%PDF-1.7\nnot a pdf body")

        with pytest.raises(UnsupportedDocumentError) as caught:
            PdfTextParser().parse(source, {}, TransportSettings())

        assert caught.value.parser_id == "pdf-text"

    def test_the_log_event_carries_it(self, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        caplog.set_level(logging.INFO, logger="docdoc.ingest")

        with pytest.raises(UnsupportedDocumentError):
            parse((FIXTURES / "pdf" / "encrypted.pdf").read_bytes())

        (record,) = caplog.records
        assert record.docdoc["parser_id"] == "pdf-text"  # type: ignore[attr-defined]

    def test_a_pre_parser_refusal_correctly_names_nobody(self) -> None:
        # Nothing had been chosen yet, and inventing a culprit would be worse
        # than reporting the absence of one.
        with pytest.raises(UnsupportedDocumentError) as caught:
            parse(b"not a document at all")

        assert caught.value.parser_id is None


class TestServicePageNumbering:
    """T063, FR-005 — tokens and tables must agree about which page they are on.

    Page indices used to be derived twice: by position for tokens, and from
    `pageNumber - 1` for tables. Those agree only when the response starts at
    page 1 and runs contiguously.
    """

    @pytest.fixture
    def ranged(self) -> dict[str, Any]:
        """A response as a page-ranged analyze returns it: pages 3 and 4."""
        recorded = json.loads((FIXTURES / "azure" / "scanned_contract.analyze.json").read_text())
        for offset, page in enumerate(recorded["pages"]):
            page["pageNumber"] = 3 + offset
        for table in recorded["tables"]:
            for region in table["boundingRegions"]:
                region["pageNumber"] = 4
            for cell in table["cells"]:
                for region in cell["boundingRegions"]:
                    region["pageNumber"] = 4
        return recorded

    @pytest.fixture
    def source(self) -> SourceFile:
        return SourceFile.from_bytes((FIXTURES / "pdf" / "scanned_contract.pdf").read_bytes())

    def test_a_ranged_response_maps_consistently(
        self, ranged: dict[str, Any], source: SourceFile
    ) -> None:
        document = map_analyze_result(ranged, source=source, options={}, text_layer=None)

        token_pages = {token.geometry.page_index for token in document.tokens if token.geometry}
        table_pages = {table.page_index for table in document.tables}

        assert table_pages <= token_pages, "a table is anchored to a page its tokens never use"
        assert table_pages == {1}, "service page 4 is this document's second page"

    def test_an_unknown_page_number_names_the_real_problem(
        self, ranged: dict[str, Any], source: SourceFile
    ) -> None:
        # Previously this surfaced as a misleading "geometry outside page N".
        ranged["tables"][0]["boundingRegions"][0]["pageNumber"] = 99

        with pytest.raises(ParserError) as caught:
            map_analyze_result(ranged, source=source, options={}, text_layer=None)

        assert "not among the pages it returned" in str(caught.value)


class TestCapabilityHonestyCoversWhatItCan:
    """T064, FR-004 and ING-4 — `text` is now checked too."""

    def test_declaring_no_text_while_producing_some_is_rejected(self) -> None:
        document = parse((FIXTURES / "pdf" / "digital_invoice.pdf").read_bytes())
        dishonest = ParserCapabilities(
            text=False, geometry=True, media_types=frozenset({"application/pdf"})
        )

        with pytest.raises(ParserError) as caught:
            validate_output(document, dishonest, parser_id="liar")

        assert caught.value.reason == "capability_mismatch"
        assert "no text" in str(caught.value)

    def test_an_honest_parser_still_passes(self) -> None:
        document = parse((FIXTURES / "pdf" / "digital_invoice.pdf").read_bytes())

        assert validate_output(document, PdfTextParser().capabilities, parser_id="pdf-text")


class TestImageInput:
    """T065, US3/AC3 — an image yields a single-page document, verified offline."""

    @pytest.fixture
    def recorded(self) -> dict[str, Any]:
        return json.loads((FIXTURES / "azure" / "sample_page.analyze.json").read_text())

    @pytest.fixture
    def source(self) -> SourceFile:
        path = FIXTURES / "image" / "sample_page.png"
        return SourceFile.from_bytes(path.read_bytes(), filename=path.name)

    def test_one_page(self, recorded: dict[str, Any], source: SourceFile) -> None:
        document = map_analyze_result(recorded, source=source, options={}, text_layer=None)

        assert len(document.pages) == 1

    def test_it_satisfies_the_parser_contract(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        document = map_analyze_result(recorded, source=source, options={}, text_layer=None)

        validate_output(
            document,
            AzureDocumentIntelligenceParser.capabilities,
            parser_id="azure-di",
            blob_id=source.blob_id,
        )

    def test_a_value_resolves_to_a_page_and_a_box(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        document = map_analyze_result(recorded, source=source, options={}, text_layer=None)

        (span,) = document.find("INV-001")
        (geometry,) = document.locate(span)

        assert geometry.page_index == 0
        assert all(0.0 <= value <= 1.0 for value in geometry.bbox)

    def test_an_image_never_takes_the_native_path(self, source: SourceFile) -> None:
        # ING-13: an image has no text layer, so routing must send it to
        # recognition without inspecting a byte.
        from docdoc.ingest.assess import assess_text_layer

        verdict = assess_text_layer(source, rule=TextLayerRule())

        assert verdict.text_layer_usable is False


class TestRetryAfterTranslation:
    """T067, FR-038 — the header the service uses to ask for a wait."""

    class FakeResponse:
        def __init__(self, headers: dict[str, str]) -> None:
            self.headers = headers

    class FakeHttpError(Exception):
        def __init__(self, status_code: int, headers: dict[str, str] | None = None) -> None:
            super().__init__(f"HTTP {status_code}")
            self.status_code = status_code
            self.response = (
                TestRetryAfterTranslation.FakeResponse(headers) if headers is not None else None
            )

    @pytest.fixture
    def source(self) -> SourceFile:
        return SourceFile.from_bytes((FIXTURES / "pdf" / "scanned_contract.pdf").read_bytes())

    def test_a_rate_limit_with_retry_after_carries_the_interval(self, source: SourceFile) -> None:
        parser = AzureDocumentIntelligenceParser()

        translated = parser._from_http(self.FakeHttpError(429, {"Retry-After": "7"}), source)

        assert isinstance(translated, ProviderError)
        assert translated.reason == "rate_limit"
        assert getattr(translated, "retry_after_s", None) == pytest.approx(7.0)

    def test_a_rate_limit_without_the_header_falls_back_to_backoff(
        self, source: SourceFile
    ) -> None:
        parser = AzureDocumentIntelligenceParser()

        translated = parser._from_http(self.FakeHttpError(429, {}), source)

        assert translated.reason == "rate_limit"
        assert getattr(translated, "retry_after_s", None) is None

    @pytest.mark.parametrize(
        ("status", "expected"),
        [(400, "corrupt"), (415, "corrupt"), (401, "auth"), (403, "auth"), (503, "service")],
    )
    def test_statuses_map_to_the_documented_reasons(
        self, source: SourceFile, status: int, expected: str
    ) -> None:
        translated = AzureDocumentIntelligenceParser()._from_http(
            self.FakeHttpError(status, {}), source
        )

        assert translated.reason == expected

    def test_a_permanent_status_is_not_marked_transient(self, source: SourceFile) -> None:
        translated = AzureDocumentIntelligenceParser()._from_http(
            self.FakeHttpError(401, {}), source
        )

        assert isinstance(translated, ProviderError)
        assert translated.transient is False


class TestTableCellsAreNeverDroppedSilently:
    """T071, FR-022 and US3/AC4 — a table that cannot be fully placed is reported.

    A cell without a text anchor used to be skipped while the table went on
    declaring its full dimensions, so a 2x2 table could carry three cells and
    nothing recorded that the fourth had ever existed. Ordering and geometry are
    rejected rather than repaired for the same reason (ING-8); a cell is no
    different.
    """

    @pytest.fixture
    def recorded(self) -> dict[str, Any]:
        return json.loads((FIXTURES / "azure" / "scanned_contract.analyze.json").read_text())

    @pytest.fixture
    def source(self) -> SourceFile:
        return SourceFile.from_bytes((FIXTURES / "pdf" / "scanned_contract.pdf").read_bytes())

    def test_a_complete_table_still_maps(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        document = map_analyze_result(recorded, source=source, options={}, text_layer=None)

        (table,) = document.tables
        assert len(table.cells) == table.n_rows * table.n_columns

    def test_an_unanchored_cell_is_reported(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        del recorded["tables"][0]["cells"][1]["spans"]

        with pytest.raises(ParserError) as caught:
            map_analyze_result(recorded, source=source, options={}, text_layer=None)

        assert caught.value.reason == "internal"
        assert caught.value.parser_id == "azure-di"

    def test_the_error_says_which_cell_and_what_the_table_claimed(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        # Without this a reader knows only that "a table failed", which is not
        # enough to take to a provider.
        del recorded["tables"][0]["cells"][3]["spans"]

        with pytest.raises(ParserError) as caught:
            map_analyze_result(recorded, source=source, options={}, text_layer=None)

        assert "row 1" in str(caught.value)
        assert "column 1" in str(caught.value)
        assert "2x2" in caught.value.detail

    def test_no_partial_table_ever_reaches_the_document(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        for position in range(4):
            maimed = json.loads(json.dumps(recorded))
            del maimed["tables"][0]["cells"][position]["spans"]

            with pytest.raises(ParserError):
                map_analyze_result(maimed, source=source, options={}, text_layer=None)


class TestRetryAfterIsADeclaredField:
    """T072 — the retry loop's one input is part of the error's contract."""

    def test_it_defaults_to_none(self) -> None:
        error = ProviderError("boom", reason="rate_limit", parser_id="azure-di")

        assert error.retry_after_s is None

    def test_it_can_be_set_at_construction(self) -> None:
        error = ProviderError(
            "slow down", reason="rate_limit", parser_id="azure-di", retry_after_s=12.5
        )

        assert error.retry_after_s == pytest.approx(12.5)

    def test_the_retry_loop_reads_it_without_getattr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # An attribute that only sometimes exists is not a contract; this asserts
        # the field is always there to be read.
        waits = self.waits_for(monkeypatch, retry_after_s=0.25)

        assert waits, "the service-supplied interval was never used"

    @staticmethod
    def waits_for(
        monkeypatch: pytest.MonkeyPatch,
        *,
        retry_after_s: float | None,
        rounds: int = 1,
        backoff: float = 0.5,
    ) -> list[float]:
        """Every interval the retry loop slept for, without actually sleeping."""
        waits: list[float] = []
        monkeypatch.setattr("docdoc.ingest.parsers.azure_di.time.sleep", waits.append)

        def refuse(source: Any, transport: Any, deadline: Any) -> Any:
            raise ProviderError(
                "slow down",
                reason="rate_limit",
                parser_id="azure-di",
                retry_after_s=retry_after_s,
            )

        parser = AzureDocumentIntelligenceParser(analyze=refuse)
        source = SourceFile.from_bytes((FIXTURES / "pdf" / "scanned_contract.pdf").read_bytes())

        for _ in range(rounds):
            with pytest.raises(ProviderError):
                parser.parse(
                    source,
                    {},
                    TransportSettings(max_attempts=2, initial_backoff_s=backoff, deadline_s=3600.0),
                )
        return waits


class TestAServiceSuppliedWaitIsAFloor:
    """T083, FR-038 — honouring an interval means never coming back early.

    This class replaces an assertion that had it backwards. The old test checked
    the wait was *at most* 1.5x the requested interval, which passed happily
    while jitter was scaling a 30-second request down to 17 — and which would
    have failed once the code was made right. A test that defends the defect is
    worse than no test, because it makes fixing the code look like breaking it.

    Run over many rounds, because a single sample can be right by luck.
    """

    ROUNDS = 40

    def test_never_shorter_than_the_service_asked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        requested = 0.25
        waits = TestRetryAfterIsADeclaredField.waits_for(
            monkeypatch, retry_after_s=requested, rounds=self.ROUNDS
        )

        assert len(waits) == self.ROUNDS
        early = [wait for wait in waits if wait < requested]
        assert not early, f"{len(early)} retries came back before the service allowed"

    def test_jitter_may_extend_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Extending is the safe direction, and some spread still helps when many
        # clients are rate-limited at once.
        requested = 0.25
        waits = TestRetryAfterIsADeclaredField.waits_for(
            monkeypatch, retry_after_s=requested, rounds=self.ROUNDS
        )

        assert max(waits) > requested
        assert max(waits) <= requested * 1.25

    def test_the_requested_interval_beats_the_configured_backoff(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # FR-038: "in preference to its own backoff". With a backoff far smaller
        # than the request, the request must still decide.
        waits = TestRetryAfterIsADeclaredField.waits_for(
            monkeypatch, retry_after_s=2.0, rounds=self.ROUNDS, backoff=0.01
        )

        assert min(waits) >= 2.0

    def test_docdoc_own_backoff_still_jitters_both_ways(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The floor rule applies to an interval the *server* chose. docdoc's own
        # backoff keeps full jitter, which is the usual defence against a fleet
        # of clients retrying in lockstep.
        base = 4.0
        waits = TestRetryAfterIsADeclaredField.waits_for(
            monkeypatch, retry_after_s=None, rounds=self.ROUNDS, backoff=base
        )

        assert min(waits) < base, "backoff should sometimes be shortened"
        assert max(waits) > base, "backoff should sometimes be lengthened"
        assert all(base * 0.5 <= wait <= base * 1.5 for wait in waits)

    def test_a_request_longer_than_the_deadline_is_refused_not_shortened(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The one case where docdoc declines to wait: it fails on the deadline
        # rather than splitting the difference.
        waits: list[float] = []
        monkeypatch.setattr("docdoc.ingest.parsers.azure_di.time.sleep", waits.append)

        def refuse(source: Any, transport: Any, deadline: Any) -> Any:
            raise ProviderError(
                "slow down", reason="rate_limit", parser_id="azure-di", retry_after_s=60.0
            )

        parser = AzureDocumentIntelligenceParser(analyze=refuse)
        source = SourceFile.from_bytes((FIXTURES / "pdf" / "scanned_contract.pdf").read_bytes())

        with pytest.raises(ProviderError) as caught:
            parser.parse(source, {}, TransportSettings(max_attempts=3, deadline_s=0.1))

        assert caught.value.reason == "deadline"
        assert waits == [], "no partial wait should have happened"


class TestNothingIsTransmittedForARefusedFile:
    """T082, quickstart V5 — "rejected before any parse" asserted, not just claimed.

    The rejection itself was already tested. What was not was the part that
    matters for cost and for privacy: that no parser ever saw the bytes. A
    validation guide that claims something no test checks is worse than one that
    claims less.
    """

    @pytest.fixture
    def data(self) -> bytes:
        return (FIXTURES / "pdf" / "mixed_pages.pdf").read_bytes()

    def registry_with_recorder(self, document: Any = None) -> tuple[Any, RecordingParser]:
        from docdoc.ingest.registry import ParserRegistry

        parser = RecordingParser()
        registry = ParserRegistry(priority=("recording",))
        registry.register(parser)
        return registry, parser

    def test_an_over_size_file_reaches_no_parser(self, data: bytes) -> None:
        registry, parser = self.registry_with_recorder(parse(data))

        with pytest.raises(UnsupportedDocumentError) as caught:
            parse(data, registry=registry, limits=Limits(max_size_bytes=64))

        assert caught.value.reason == "size_limit"
        assert parser.calls == 0, "an over-limit file was handed to a parser"

    def test_an_over_page_file_reaches_no_parser_when_the_rule_counted_it(
        self, data: bytes
    ) -> None:
        # Here the assessment already knows the page count, so the refusal still
        # precedes the parse — the post-parse check T061 added is only for the
        # case where the count did not exist yet.
        registry, parser = self.registry_with_recorder(parse(data))

        with pytest.raises(UnsupportedDocumentError) as caught:
            parse(data, registry=registry, limits=Limits(max_pages=1))

        assert caught.value.reason == "page_limit"
        assert parser.calls == 0

    def test_an_unrecognized_file_reaches_no_parser(self, data: bytes) -> None:
        registry, parser = self.registry_with_recorder(parse(data))

        with pytest.raises(UnsupportedDocumentError):
            parse(b"not a document at all", registry=registry)

        assert parser.calls == 0

    def test_a_disallowed_media_type_reaches_no_parser(self, data: bytes) -> None:
        registry, parser = self.registry_with_recorder(parse(data))
        tiff = b"II*\x00" + b"\x00" * 64

        with pytest.raises(UnsupportedDocumentError) as caught:
            parse(tiff, registry=registry)

        assert caught.value.media_type == "image/tiff"
        assert parser.calls == 0

    def test_an_accepted_file_does_reach_the_parser(self, data: bytes) -> None:
        # The counter has to be capable of moving, or the assertions above prove
        # nothing at all.
        registry, parser = self.registry_with_recorder(parse(data))

        parse(data, registry=registry, force="recognition")

        assert parser.calls == 1


class TestNothingProviderShapedEscapes:
    """T086, Constitution IV and §Error model, FR-025, ING-20 — the adapter
    boundary holds against a response it did not expect.

    Found by handing the mapper hostile payloads rather than by reading it: five
    of six probes leaked a raw `KeyError` or a pydantic `ValidationError`. The
    whole point of an adapter is that the provider's shape stops there, and
    `KeyError('span')` is the provider's shape.
    """

    @pytest.fixture
    def recorded(self) -> dict[str, Any]:
        return json.loads((FIXTURES / "azure" / "scanned_contract.analyze.json").read_text())

    @pytest.fixture
    def source(self) -> SourceFile:
        return SourceFile.from_bytes((FIXTURES / "pdf" / "scanned_contract.pdf").read_bytes())

    #: Each entry breaks the response the way a schema change would.
    BREAKAGES: ClassVar[dict[str, Any]] = {
        "word-missing-span": lambda d: d["pages"][0]["words"][0].pop("span"),
        "word-missing-polygon": lambda d: d["pages"][0]["words"][0].pop("polygon"),
        "span-missing-offset": lambda d: d["pages"][0]["words"][0]["span"].pop("offset"),
        "page-width-null": lambda d: d["pages"][0].update(width=None),
        "cell-missing-row": lambda d: d["tables"][0]["cells"][0].pop("rowIndex"),
        "pages-not-a-list": lambda d: d.update(pages="nonsense"),
        "words-not-a-list": lambda d: d["pages"][0].update(words={"bad": 1}),
        "polygon-not-numbers": lambda d: d["pages"][0]["words"][0].update(
            polygon=["a", "b", "c", "d", "e", "f", "g", "h"]
        ),
        "span-offset-not-a-number": lambda d: d["pages"][0]["words"][0]["span"].update(
            offset="third"
        ),
    }

    @pytest.mark.parametrize("name", list(BREAKAGES))
    def test_a_malformed_response_yields_a_docdoc_error(
        self, recorded: dict[str, Any], source: SourceFile, name: str
    ) -> None:
        from docdoc.kernel import DocdocError

        self.BREAKAGES[name](recorded)

        with pytest.raises(DocdocError) as caught:
            map_analyze_result(recorded, source=source, options={}, text_layer=None)

        assert not isinstance(caught.value, (KeyError, TypeError, AttributeError))
        assert caught.value.blob_id == source.blob_id

    def test_the_original_failure_is_kept_as_the_cause(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        # Translated, not swallowed: whoever debugs this still needs the traceback.
        recorded["pages"][0]["words"][0].pop("span")

        with pytest.raises(ParserError) as caught:
            map_analyze_result(recorded, source=source, options={}, text_layer=None)

        assert isinstance(caught.value.__cause__, KeyError)

    def test_a_missing_field_is_named_because_a_schema_name_is_safe(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        recorded["pages"][0]["words"][0].pop("polygon")

        with pytest.raises(ParserError) as caught:
            map_analyze_result(recorded, source=source, options={}, text_layer=None)

        assert "polygon" in caught.value.detail

    def test_the_detail_never_quotes_a_value_from_the_document(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        """A pydantic message can echo the offending input, and an input from a
        document is document content (FR-029)."""
        recorded["pages"][0].update(width="SERVICE AGREEMENT")

        with pytest.raises(ParserError) as caught:
            map_analyze_result(recorded, source=source, options={}, text_layer=None)

        rendered = f"{caught.value} {caught.value.detail}"
        assert "SERVICE AGREEMENT" not in rendered

    def test_a_precise_error_is_not_replaced_by_the_generic_one(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        # The boundary must not flatten errors this module raised on purpose.
        del recorded["tables"][0]["spans"]

        with pytest.raises(ParserError) as caught:
            map_analyze_result(recorded, source=source, options={}, text_layer=None)

        assert "no text anchor" in str(caught.value)


class TestEveryFailureLeavesATrace:
    """T087, FR-040 — one event per parse, whatever went wrong.

    The handler used to catch only `IngestError`, so a failure from outside the
    error model escaped with no record at all — the hardest kind to chase being
    the one kind that left nothing behind. Measured: zero events.
    """

    class Exploding:
        id = "boom"
        version = "1.0.0+stub"
        reading_order = "stub@1"
        capabilities = ParserCapabilities(
            text=True,
            geometry=True,
            media_types=frozenset({"application/pdf"}),
            requires_network=True,
        )

        def __init__(self, error: BaseException) -> None:
            self._error = error

        def parse(self, *args: Any, **kwargs: Any) -> Any:
            raise self._error

    def registry_raising(self, error: BaseException) -> Any:
        from docdoc.ingest.registry import ParserRegistry

        registry = ParserRegistry(priority=("boom",))
        registry.register(self.Exploding(error))
        return registry

    def test_an_unexpected_exception_still_emits_one_event(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        caplog.set_level(logging.INFO, logger="docdoc.ingest")
        data = (FIXTURES / "pdf" / "digital_invoice.pdf").read_bytes()

        with pytest.raises(KeyError):
            parse(data, registry=self.registry_raising(KeyError("span")), force="recognition")

        (record,) = caplog.records
        fields = record.docdoc  # type: ignore[attr-defined]
        assert fields["outcome"] == "error"
        assert fields["error_type"] == "KeyError"

    def test_the_exception_is_re_raised_unchanged(self) -> None:
        # A witness, not a handler: the layer records what happened and gets out
        # of the way.
        original = ValueError("something odd")
        data = (FIXTURES / "pdf" / "digital_invoice.pdf").read_bytes()

        with pytest.raises(ValueError, match="something odd") as caught:
            parse(data, registry=self.registry_raising(original), force="recognition")

        assert caught.value is original

    def test_a_keyboard_interrupt_is_not_narrated_on_its_way_out(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # BaseException is deliberately outside the net: an interrupt should
        # unwind immediately, not stop to write a log line.
        import logging

        caplog.set_level(logging.INFO, logger="docdoc.ingest")
        data = (FIXTURES / "pdf" / "digital_invoice.pdf").read_bytes()

        with pytest.raises(KeyboardInterrupt):
            parse(data, registry=self.registry_raising(KeyboardInterrupt()), force="recognition")

        assert caplog.records == []


class TestLimitsApplyToEitherInputForm:
    """T088, FR-028 — `limits` means the same thing for bytes and for SourceFile.

    `parse()` accepts `SourceFile | bytes` as equals, but the limits were only
    enforced inside `SourceFile.from_bytes`. A caller handing in an object it
    built earlier got no enforcement at all while the docstring promised some:
    measured a 2915-byte file parsing under `max_size_bytes=64`.
    """

    @pytest.fixture
    def data(self) -> bytes:
        return (FIXTURES / "pdf" / "digital_invoice.pdf").read_bytes()

    @pytest.fixture
    def permissive(self, data: bytes) -> SourceFile:
        """Built under limits that allow everything, as an outer layer might."""
        return SourceFile.from_bytes(data, limits=Limits(max_size_bytes=50 * 1024 * 1024))

    def test_a_size_limit_applies_to_a_prebuilt_source(self, permissive: SourceFile) -> None:
        with pytest.raises(UnsupportedDocumentError) as caught:
            parse(permissive, limits=Limits(max_size_bytes=64))

        assert caught.value.reason == "size_limit"

    def test_a_media_type_limit_applies_to_a_prebuilt_source(self, permissive: SourceFile) -> None:
        with pytest.raises(UnsupportedDocumentError) as caught:
            parse(permissive, limits=Limits(allowed_media_types=frozenset({"image/png"})))

        assert caught.value.reason == "mime_type"

    def test_a_size_limit_still_applies_to_raw_bytes(self, data: bytes) -> None:
        with pytest.raises(UnsupportedDocumentError) as caught:
            parse(data, limits=Limits(max_size_bytes=64))

        assert caught.value.reason == "size_limit"

    def test_a_permitted_prebuilt_source_still_parses(self, permissive: SourceFile) -> None:
        assert parse(permissive).find("INV-001")

    def test_the_check_is_idempotent(self, data: bytes) -> None:
        # It runs once in `from_bytes` and again in `parse`; running it twice
        # must not turn an accepted file into a rejected one.
        source = SourceFile.from_bytes(data)
        source.check_limits(Limits())
        source.check_limits(Limits())

        assert parse(source).find("INV-001")

    def test_nothing_reaches_a_parser_when_a_prebuilt_source_is_refused(
        self, permissive: SourceFile
    ) -> None:
        from docdoc.ingest.registry import ParserRegistry

        parser = RecordingParser()
        registry = ParserRegistry(priority=("recording",))
        registry.register(parser)

        with pytest.raises(UnsupportedDocumentError):
            parse(permissive, registry=registry, limits=Limits(max_size_bytes=64))

        assert parser.calls == 0


class TestARequestedMediaTypeIsNotRewritten:
    """T089, FR-022 and Constitution VIII — a contradicted request is refused.

    `_select` used to rewrite `require.media_type` to match the bytes and carry
    on, so asking for a PNG parser on a PDF produced a PDF parse and no word
    about it. The bytes do decide what a file is; answering a question nobody
    asked is a different matter.
    """

    @pytest.fixture
    def data(self) -> bytes:
        return (FIXTURES / "pdf" / "digital_invoice.pdf").read_bytes()

    def test_a_contradicted_request_raises(self, data: bytes) -> None:
        from docdoc.ingest.capabilities import CapabilityRequest

        with pytest.raises(UnsupportedDocumentError) as caught:
            parse(data, require=CapabilityRequest(media_type="image/png", geometry=True))

        assert caught.value.reason == "mime_type"

    def test_the_error_names_both_types_and_says_what_to_do(self, data: bytes) -> None:
        from docdoc.ingest.capabilities import CapabilityRequest

        with pytest.raises(UnsupportedDocumentError) as caught:
            parse(data, require=CapabilityRequest(media_type="image/jpeg"))

        message = str(caught.value)
        assert "image/jpeg" in message
        assert "application/pdf" in message
        assert "drop the media_type" in message

    def test_a_matching_request_is_honoured(self, data: bytes) -> None:
        from docdoc.ingest.capabilities import CapabilityRequest

        document = parse(
            data, require=CapabilityRequest(media_type="application/pdf", geometry=True)
        )

        assert document.find("INV-001")

    def test_omitting_the_request_still_works(self, data: bytes) -> None:
        # The default is derived from the bytes, so a caller with no opinion is
        # not forced to form one.
        assert parse(data).find("INV-001")


class TestOutputMustBelongToItsInput:
    """T090, FR-002 and ADR-0002 — the document has to be *of* the file given.

    Nothing established this before. A parser received bytes and returned a
    `Document`, and that the two were related was taken on trust — while the
    `Parser` protocol openly invites third parties. Measured with a stub
    returning another file's document: accepted, wrong `blob_id`, provenance
    naming a parser that never ran, and the routing verdict overwritten.
    """

    @pytest.fixture
    def invoice(self) -> bytes:
        return (FIXTURES / "pdf" / "digital_invoice.pdf").read_bytes()

    @pytest.fixture
    def scan(self) -> bytes:
        return (FIXTURES / "pdf" / "scanned_contract.pdf").read_bytes()

    def returning(self, document: Any) -> Any:
        """A registry whose only parser returns the document it was handed."""
        from docdoc.ingest.registry import ParserRegistry

        class Stub:
            id = "stub"
            version = "1.0.0+stub"
            reading_order = "stub@1"
            capabilities = ParserCapabilities(
                text=True,
                geometry=True,
                media_types=frozenset({"application/pdf"}),
                requires_network=True,
            )

            def parse(self, *args: Any, **kwargs: Any) -> Any:
                return document

        registry = ParserRegistry(priority=("stub",))
        registry.register(Stub())
        return registry

    def test_a_document_of_another_file_is_refused(self, invoice: bytes) -> None:
        other = parse((FIXTURES / "pdf" / "two_column.pdf").read_bytes())

        with pytest.raises(ParserError) as caught:
            parse(invoice, registry=self.returning(other), force="recognition")

        assert caught.value.reason == "wrong_document"
        assert "different file" in str(caught.value)

    def test_the_error_names_both_identities(self, invoice: bytes) -> None:
        other = parse((FIXTURES / "pdf" / "two_column.pdf").read_bytes())

        with pytest.raises(ParserError) as caught:
            parse(invoice, registry=self.returning(other), force="recognition")

        assert other.source.blob_id in caught.value.detail
        assert SourceFile.from_bytes(invoice).blob_id in caught.value.detail

    def test_provenance_naming_another_parser_is_refused(self, invoice: bytes) -> None:
        # The document is of the right file, but claims `pdf-text` produced it.
        native = parse(invoice)

        with pytest.raises(ParserError) as caught:
            parse(invoice, registry=self.returning(native), force="recognition")

        assert caught.value.reason == "wrong_document"
        assert "attributed to" in str(caught.value)

    def test_a_rewritten_verdict_is_refused(self, invoice: bytes, scan: bytes) -> None:
        # Routed to recognition because the scan has no text layer; the parser
        # hands back something claiming a native parse of a different document.
        native = parse(invoice)

        with pytest.raises(ParserError) as caught:
            parse(scan, registry=self.returning(native))

        assert caught.value.reason == "wrong_document"

    def test_the_shipped_parsers_pass_unchanged(self, invoice: bytes, scan: bytes) -> None:
        # The check must not be so strict that a correct parser trips it.
        assert parse(invoice).find("INV-001")
        assert parse(invoice, options={"mode": "strict"}).find("INV-001")

    def test_the_contract_suite_still_holds_for_every_offline_parser(self) -> None:
        from tests.contract.test_parser_contract import offline_parsers

        for _, parser, fixture in offline_parsers():
            source = SourceFile.from_bytes(fixture.read_bytes())
            document = parser.parse(source, {}, TransportSettings())

            validate_output(
                document,
                parser.capabilities,
                parser_id=parser.id,
                blob_id=source.blob_id,
                parser_version=parser.version,
            )

    def test_each_expectation_is_optional(self, invoice: bytes) -> None:
        # A caller checking only what it knows still gets what it can.
        from docdoc.ingest.validate import check_corresponds_to_input

        document = parse(invoice)

        check_corresponds_to_input(document, parser_id="pdf-text")
        check_corresponds_to_input(document, parser_id="pdf-text", blob_id=document.source.blob_id)
