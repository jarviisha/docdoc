"""The three states a service-backed parser can be in, and telling them apart.

``default_registry`` has to distinguish "the extra is not installed" from "the
credentials are not configured" from "usable". A caller acts differently on each
— pip install, set an environment variable, or nothing — and collapsing the first
two into a single "unavailable" is the failure FR-018 exists to prevent.

This was not merely cosmetic. An adapter module imports its SDK lazily, inside the
method that reaches the network, so the ``except ImportError`` that used to guard
the adapter import never fired: with the extra missing but credentials set, the
registry reported the parser *available*, selection picked it, and the parse died
on a bare ``ImportError`` from inside ``parse`` — a provider failure crossing the
public API, which the error model forbids outright (FR-025).

These tests need no provider SDK, which is the point: they assert what a base
install reports.
"""

from __future__ import annotations

import pytest

from docdoc.ingest.registry import DEFAULT_PRIORITY, default_registry

#: Every parser that talks to a service, with the SDK module its extra provides
#: and the module holding its credential check.
SERVICE_PARSERS = [
    ("azure-di", "azure.ai.documentintelligence", "docdoc.ingest.parsers.azure_di"),
    ("gcv", "google.cloud.vision", "docdoc.ingest.parsers.gcv"),
]


def entry_for(parser_id: str):  # type: ignore[no-untyped-def]
    (entry,) = [e for e in default_registry().candidates_all() if e.id == parser_id]
    return entry


class TestEveryParserStaysVisible:
    def test_all_of_them_are_registered_whatever_is_installed(self) -> None:
        registered = {entry.id for entry in default_registry().candidates_all()}

        assert registered == set(DEFAULT_PRIORITY)

    @pytest.mark.parametrize(("parser_id", "_sdk", "_module"), SERVICE_PARSERS)
    def test_a_service_parser_declares_it_needs_the_network(
        self, parser_id: str, _sdk: str, _module: str
    ) -> None:
        # The offline-first default priority is derived from this flag rather than
        # from a hard-coded provider name.
        assert entry_for(parser_id).capabilities.requires_network is True


class TestTheReasonIsSpecific:
    @pytest.mark.parametrize(("parser_id", "sdk_module", "module"), SERVICE_PARSERS)
    def test_a_missing_extra_says_so_even_with_credentials_configured(
        self,
        monkeypatch: pytest.MonkeyPatch,
        parser_id: str,
        sdk_module: str,
        module: str,
    ) -> None:
        # The case that used to report `credentials_not_configured` and then die
        # on an ImportError deep inside parse.
        monkeypatch.setattr(f"{module}.credentials_available", lambda: True)
        monkeypatch.setattr(
            "docdoc.ingest.registry._sdk_installed", lambda name: name != sdk_module
        )

        entry = entry_for(parser_id)

        assert entry.available is False
        assert entry.unavailable_reason == "extra_not_installed"

    @pytest.mark.parametrize(("parser_id", "sdk_module", "module"), SERVICE_PARSERS)
    def test_an_installed_extra_without_credentials_says_that_instead(
        self,
        monkeypatch: pytest.MonkeyPatch,
        parser_id: str,
        sdk_module: str,
        module: str,
    ) -> None:
        monkeypatch.setattr(f"{module}.credentials_available", lambda: False)
        monkeypatch.setattr("docdoc.ingest.registry._sdk_installed", lambda name: True)

        entry = entry_for(parser_id)

        assert entry.available is False
        assert entry.unavailable_reason == "credentials_not_configured"

    @pytest.mark.parametrize(("parser_id", "sdk_module", "module"), SERVICE_PARSERS)
    def test_both_present_makes_it_usable_with_no_reason_to_report(
        self,
        monkeypatch: pytest.MonkeyPatch,
        parser_id: str,
        sdk_module: str,
        module: str,
    ) -> None:
        monkeypatch.setattr(f"{module}.credentials_available", lambda: True)
        monkeypatch.setattr("docdoc.ingest.registry._sdk_installed", lambda name: True)

        entry = entry_for(parser_id)

        assert entry.available is True
        assert entry.unavailable_reason is None
        assert entry.parser is not None


class TestTheProbeItself:
    def test_a_missing_parent_package_is_not_installed_rather_than_an_error(self) -> None:
        """``find_spec`` raises instead of returning None when the *parent* is
        absent, and an uncaught one here would break the registry on exactly the
        base install it is supposed to describe."""
        from docdoc.ingest.registry import _sdk_installed

        assert _sdk_installed("nosuchvendor.nosuchpackage.nosuchmodule") is False

    def test_a_module_that_is_present_reads_as_installed(self) -> None:
        from docdoc.ingest.registry import _sdk_installed

        assert _sdk_installed("json") is True


class TestDeclaredCapabilitiesSurviveTheExtraBeingAbsent:
    """An unavailable parser still has to describe itself.

    Its capabilities are what the selection error lists as a candidate, so a
    caller can be told "the parser that would have done this needs installing"
    rather than "nothing can do this".
    """

    def test_the_vision_parser_declares_images_and_no_tables(self) -> None:
        capabilities = entry_for("gcv").capabilities

        assert capabilities.media_types == frozenset({"image/jpeg", "image/png"})
        assert capabilities.tables is False
        assert capabilities.handwriting is True

    def test_a_pdf_never_selects_the_vision_parser(self) -> None:
        # It is an image OCR path; the synchronous API takes no PDF, and the
        # declaration is what keeps a PDF away from it (no runtime check needed).
        assert "application/pdf" not in entry_for("gcv").capabilities.media_types
