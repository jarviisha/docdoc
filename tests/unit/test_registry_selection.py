"""T048 — capability-based selection (ING-14 … ING-17).

The property worth protecting is that *nothing incidental* decides which parser
runs: not registration order, not dictionary iteration, not which extra happens
to be installed. A deployment's configured priority decides, and everything else
is a tie-break by id.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from docdoc.ingest.capabilities import CapabilityRequest, ParserCapabilities
from docdoc.ingest.errors import ParserCapabilityError
from docdoc.ingest.options import TransportSettings
from docdoc.ingest.registry import ParserRegistry, default_registry
from docdoc.ingest.source import PDF, PNG, SourceFile
from docdoc.kernel import Document

PDF_TEXT = CapabilityRequest(media_type=PDF, text=True)
PDF_GEOMETRY = CapabilityRequest(media_type=PDF, text=True, geometry=True)
PDF_TABLES = CapabilityRequest(media_type=PDF, text=True, tables=True)


class StubParser:
    """A parser that declares things and never runs."""

    def __init__(self, parser_id: str, **capabilities: Any) -> None:
        self.id = parser_id
        self.version = f"1.0.0+stub-{parser_id}"
        self.reading_order = "stub@1"
        self.capabilities = ParserCapabilities(media_types=frozenset({PDF}), **capabilities)

    def parse(
        self,
        source: SourceFile,
        options: Mapping[str, Any],
        transport: TransportSettings,
        text_layer: Any = None,
    ) -> Document:  # pragma: no cover - never called
        raise NotImplementedError


def registry_with(*parsers: StubParser, priority: tuple[str, ...] | None = None) -> ParserRegistry:
    registry = ParserRegistry(priority)
    for parser in parsers:
        registry.register(parser)
    return registry


class TestSelection:
    def test_picks_a_parser_that_declares_what_was_asked_for(self) -> None:
        plain = StubParser("plain", text=True)
        rich = StubParser("rich", text=True, geometry=True)

        assert registry_with(plain, rich).select(PDF_GEOMETRY).id == "rich"

    def test_declaring_more_than_asked_is_fine(self) -> None:
        rich = StubParser("rich", text=True, geometry=True, tables=True)

        assert registry_with(rich).select(PDF_TEXT).id == "rich"

    def test_media_type_is_part_of_the_match(self) -> None:
        pdf_only = StubParser("pdf-only", text=True)

        with pytest.raises(ParserCapabilityError):
            registry_with(pdf_only).select(CapabilityRequest(media_type=PNG))


class TestDeterminism:
    """ING-14 — the same request always yields the same parser."""

    def test_registration_order_does_not_matter(self) -> None:
        first = StubParser("alpha", text=True, geometry=True)
        second = StubParser("beta", text=True, geometry=True)
        priority = ("beta", "alpha")

        forward = registry_with(first, second, priority=priority).select(PDF_GEOMETRY)
        backward = registry_with(second, first, priority=priority).select(PDF_GEOMETRY)

        assert forward.id == backward.id == "beta"

    def test_configured_priority_decides(self) -> None:
        alpha = StubParser("alpha", text=True, geometry=True)
        beta = StubParser("beta", text=True, geometry=True)

        assert (
            registry_with(alpha, beta, priority=("alpha", "beta")).select(PDF_GEOMETRY).id
            == "alpha"
        )
        assert (
            registry_with(alpha, beta, priority=("beta", "alpha")).select(PDF_GEOMETRY).id == "beta"
        )

    def test_parser_id_breaks_a_remaining_tie(self) -> None:
        # Neither is named in the priority list, so the outcome must still not
        # depend on anything incidental.
        zulu = StubParser("zulu", text=True, geometry=True)
        alpha = StubParser("alpha", text=True, geometry=True)

        assert registry_with(zulu, alpha, priority=()).select(PDF_GEOMETRY).id == "alpha"

    def test_repeated_selection_is_stable(self) -> None:
        registry = registry_with(
            StubParser("alpha", text=True, geometry=True),
            StubParser("beta", text=True, geometry=True),
        )

        assert len({registry.select(PDF_GEOMETRY).id for _ in range(20)}) == 1

    def test_the_default_priority_puts_offline_first(self) -> None:
        from docdoc.ingest.registry import DEFAULT_PRIORITY

        registry = default_registry()
        offline = {
            entry.id
            for entry in registry.candidates_all()
            if not entry.capabilities.requires_network
        }

        assert DEFAULT_PRIORITY[0] in offline


class TestNoMatch:
    """ING-15 — the failure explains itself in terms of capabilities."""

    def test_raises_rather_than_substituting_a_partial_match(self) -> None:
        text_only = StubParser("text-only", text=True)

        with pytest.raises(ParserCapabilityError) as caught:
            registry_with(text_only).select(PDF_TABLES)

        assert "tables" in str(caught.value)

    def test_the_error_names_the_required_capabilities(self) -> None:
        with pytest.raises(ParserCapabilityError) as caught:
            ParserRegistry().select(PDF_GEOMETRY)

        assert set(caught.value.required) == {"text", "geometry"}
        assert caught.value.media_type == PDF

    def test_the_error_lists_every_candidate_with_its_availability(self) -> None:
        registry = ParserRegistry(priority=("usable", "broken"))
        registry.register(StubParser("usable", text=True))
        registry.register(
            StubParser("broken", text=True, geometry=True),
            available=False,
            reason="credentials_not_configured",
        )

        with pytest.raises(ParserCapabilityError) as caught:
            registry.select(PDF_GEOMETRY)

        assert caught.value.candidates == (
            ("usable", True, None),
            ("broken", False, "credentials_not_configured"),
        )

    def test_no_provider_name_is_needed_to_read_the_failure(self) -> None:
        with pytest.raises(ParserCapabilityError) as caught:
            ParserRegistry().select(PDF_TABLES)

        assert "azure" not in str(caught.value).lower()


class TestUnavailableParsersStayVisible:
    """ING-16 — "installed but unusable" is different from "does not exist"."""

    def test_an_unusable_parser_is_not_dropped(self) -> None:
        registry = ParserRegistry()
        registry.register(
            StubParser("needs-keys", text=True, geometry=True),
            available=False,
            reason="credentials_not_configured",
        )

        (entry,) = registry.candidates(PDF_GEOMETRY)
        assert entry.available is False
        assert entry.unavailable_reason == "credentials_not_configured"

    def test_an_uninstalled_parser_is_recorded_with_its_reason(self) -> None:
        registry = ParserRegistry()
        registry.register_unavailable(
            "not-installed",
            ParserCapabilities(text=True, geometry=True, media_types=frozenset({PDF})),
            reason="extra_not_installed",
        )

        (entry,) = registry.candidates(PDF_GEOMETRY)
        assert entry.parser is None
        assert entry.unavailable_reason == "extra_not_installed"

    def test_selection_skips_it_but_the_diagnostics_do_not(self) -> None:
        registry = ParserRegistry(priority=("blocked", "working"))
        registry.register(
            StubParser("blocked", text=True, geometry=True),
            available=False,
            reason="credentials_not_configured",
        )
        registry.register(StubParser("working", text=True, geometry=True))

        assert registry.select(PDF_GEOMETRY).id == "working"
        assert [entry.id for entry in registry.candidates(PDF_GEOMETRY)] == ["blocked", "working"]


class TestContractCoverage:
    def test_every_offline_parser_is_under_the_shared_contract_test(self) -> None:
        """Guards the contract suite's parameter list.

        A parser that runs offline but is missing from ``offline_parsers()``
        would silently escape the contract every parser is supposed to meet.
        """
        from tests.contract.test_parser_contract import offline_parsers

        registered = {
            entry.id
            for entry in default_registry().candidates_all()
            if entry.available and not entry.capabilities.requires_network
        }
        covered = {case[0] for case in offline_parsers()}

        assert registered <= covered, f"not under contract test: {registered - covered}"


class TestSwappingTheProvider:
    """T075, US4/AC5 — swapping the provider changes provenance and identity, and
    nothing else a caller can observe.

    Priority ordering is tested above; this asserts the claim the acceptance
    scenario actually makes, which is about what a *caller* sees afterwards.
    """

    @pytest.fixture
    def data(self) -> bytes:
        from pathlib import Path

        return (
            Path(__file__).parent.parent / "fixtures" / "pdf" / "digital_invoice.pdf"
        ).read_bytes()

    def relabelled(self, parser_id: str, version: str) -> Any:
        """The same reader, registered under a different identity.

        Standing in for a deployment swapping one geometry-capable parser for
        another that produces the same output.
        """
        from docdoc.ingest.parsers.pdf_text import PdfTextParser

        # Bound to different names: a class body cannot read a name it also
        # assigns, so `version = version` would look up the class attribute.
        new_id, new_version = parser_id, version

        class Relabelled(PdfTextParser):  # type: ignore[misc, valid-type]
            id = new_id
            version = new_version
            reading_order = "pymupdf-stream@1"

        return Relabelled()

    def parse_with(self, data: bytes, parser: Any) -> Any:
        from docdoc.ingest import parse

        registry = ParserRegistry(priority=(parser.id,))
        registry.register(parser)
        return parse(data, registry=registry)

    def test_the_content_is_identical(self, data: bytes) -> None:
        first = self.parse_with(data, self.relabelled("provider-a", "1.0.0+a-1"))
        second = self.parse_with(data, self.relabelled("provider-b", "1.0.0+b-1"))

        assert first.text == second.text
        assert [token.span for token in first.tokens] == [token.span for token in second.tokens]
        assert [token.geometry for token in first.tokens] == [
            token.geometry for token in second.tokens
        ]
        assert [page.span for page in first.pages] == [page.span for page in second.pages]

    def test_provenance_and_identity_are_the_observable_difference(self, data: bytes) -> None:
        first = self.parse_with(data, self.relabelled("provider-a", "1.0.0+a-1"))
        second = self.parse_with(data, self.relabelled("provider-b", "1.0.0+b-1"))

        assert first.provenance.parser_id != second.provenance.parser_id
        assert first.id != second.id, "two parses must never be interchangeable"
        assert first.source.blob_id == second.source.blob_id, "the file did not change"

    def test_no_application_code_changed_between_them(self, data: bytes) -> None:
        # The only difference between the two calls above is which parser the
        # registry holds. Both go through the same `parse(...)` with the same
        # arguments, which is the whole point of capability-based selection.
        from docdoc.ingest import parse

        for parser_id in ("provider-a", "provider-b"):
            registry = ParserRegistry(priority=(parser_id,))
            registry.register(self.relabelled(parser_id, f"1.0.0+{parser_id}"))

            document = parse(data, registry=registry)

            assert document.find("INV-001")
