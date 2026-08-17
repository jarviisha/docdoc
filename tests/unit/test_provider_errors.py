"""T043 — retries, deadlines, and the failures that must not be retried.

The analyze call is injectable, so every one of these runs offline in
milliseconds. What is under test is docdoc's own policy (R12, FR-038, ING-21),
not the service's behaviour.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from docdoc.ingest.errors import ProviderError, UnsupportedDocumentError
from docdoc.ingest.options import TransportSettings
from docdoc.ingest.parsers.azure_di import AzureDocumentIntelligenceParser
from docdoc.ingest.source import SourceFile

FIXTURES = Path(__file__).parent.parent / "fixtures"

# Fast enough that the retry tests cost nothing, long enough to be real waits.
QUICK = TransportSettings(
    max_attempts=3, initial_backoff_s=0.001, max_backoff_s=0.004, attempt_timeout_s=1.0
)


@pytest.fixture
def source() -> SourceFile:
    path = FIXTURES / "pdf" / "scanned_contract.pdf"
    return SourceFile.from_bytes(path.read_bytes(), filename=path.name)


class Analyzer:
    """A stand-in for the service, scripted per test."""

    def __init__(self, *outcomes: object) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self, source: SourceFile, transport: Any, deadline: Any) -> Any:
        self.calls += 1
        outcome = self.outcomes[min(self.calls - 1, len(self.outcomes) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def provider_error(reason: str) -> ProviderError:
    return ProviderError(f"simulated {reason}", reason=reason, parser_id="azure-di")


class TestTransientFailures:
    @pytest.mark.parametrize("reason", ["timeout", "rate_limit", "transport", "service"])
    def test_are_retried_to_the_limit_then_raised(self, reason: str, source: SourceFile) -> None:
        analyzer = Analyzer(provider_error(reason))
        parser = AzureDocumentIntelligenceParser(analyze=analyzer)

        with pytest.raises(ProviderError) as caught:
            parser.parse(source, {}, QUICK)

        assert analyzer.calls == QUICK.max_attempts
        assert caught.value.reason == reason
        assert caught.value.attempts == QUICK.max_attempts

    def test_a_retry_that_succeeds_returns_the_document(self, source: SourceFile) -> None:
        analyzer = Analyzer(provider_error("rate_limit"), {"content": "", "pages": []})
        parser = AzureDocumentIntelligenceParser(analyze=analyzer)

        document = parser.parse(source, {}, QUICK)

        assert analyzer.calls == 2
        assert document.provenance.parser_id == "azure-di"

    def test_the_attempt_limit_is_configurable(self, source: SourceFile) -> None:
        analyzer = Analyzer(provider_error("service"))
        parser = AzureDocumentIntelligenceParser(analyze=analyzer)

        with pytest.raises(ProviderError):
            parser.parse(source, {}, QUICK.model_copy(update={"max_attempts": 5}))

        assert analyzer.calls == 5


class TestPermanentFailures:
    def test_a_rejected_credential_is_not_retried(self, source: SourceFile) -> None:
        """Trying again cannot change the answer, and doing so only spends the
        deadline."""
        analyzer = Analyzer(provider_error("auth"))
        parser = AzureDocumentIntelligenceParser(analyze=analyzer)

        with pytest.raises(ProviderError) as caught:
            parser.parse(source, {}, QUICK)

        assert analyzer.calls == 1
        assert caught.value.reason == "auth"
        assert caught.value.transient is False

    def test_an_unsupported_document_is_not_retried(self, source: SourceFile) -> None:
        analyzer = Analyzer(UnsupportedDocumentError("rejected by the service", reason="corrupt"))
        parser = AzureDocumentIntelligenceParser(analyze=analyzer)

        with pytest.raises(UnsupportedDocumentError):
            parser.parse(source, {}, QUICK)

        assert analyzer.calls == 1


class TestDeadline:
    def test_a_slow_service_hits_the_overall_deadline(self, source: SourceFile) -> None:
        import time

        def slow(source: SourceFile, transport: Any, deadline: Any) -> Any:
            time.sleep(0.05)
            raise provider_error("timeout")

        parser = AzureDocumentIntelligenceParser(analyze=slow)

        with pytest.raises(ProviderError) as caught:
            parser.parse(
                source,
                {},
                TransportSettings(
                    max_attempts=10,
                    initial_backoff_s=0.02,
                    attempt_timeout_s=1.0,
                    deadline_s=0.12,
                ),
            )

        assert caught.value.reason == "deadline"

    def test_the_error_names_which_bound_was_exceeded(self, source: SourceFile) -> None:
        analyzer = Analyzer(provider_error("timeout"))
        parser = AzureDocumentIntelligenceParser(analyze=analyzer)

        with pytest.raises(ProviderError) as caught:
            parser.parse(
                source,
                {},
                TransportSettings(max_attempts=5, initial_backoff_s=10.0, deadline_s=0.05),
            )

        assert caught.value.reason == "deadline"
        assert "deadline" in str(caught.value)

    def test_a_wait_longer_than_the_budget_is_refused_rather_than_slept_through(
        self, source: SourceFile
    ) -> None:
        # The service asks for 30 seconds; the budget is a tenth of one. The
        # parse fails on the deadline instead of sleeping past it.
        error = provider_error("rate_limit")
        error.retry_after_s = 30.0  # type: ignore[attr-defined]
        analyzer = Analyzer(error)
        parser = AzureDocumentIntelligenceParser(analyze=analyzer)

        with pytest.raises(ProviderError) as caught:
            parser.parse(source, {}, TransportSettings(max_attempts=3, deadline_s=0.1))

        assert caught.value.reason == "deadline"
        assert analyzer.calls == 1


class TestConfiguration:
    def test_missing_credentials_fail_before_anything_is_transmitted(
        self, source: SourceFile, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DOCDOC_AZURE_DI_ENDPOINT", raising=False)
        monkeypatch.delenv("DOCDOC_AZURE_DI_KEY", raising=False)
        parser = AzureDocumentIntelligenceParser()

        with pytest.raises(ProviderError) as caught:
            parser.parse(source, {}, TransportSettings(max_attempts=1))

        assert caught.value.reason == "auth"
        assert "DOCDOC_AZURE_DI_ENDPOINT" in str(caught.value)

    def test_the_error_names_no_secret(
        self, source: SourceFile, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DOCDOC_AZURE_DI_ENDPOINT", "https://example.invalid")
        monkeypatch.setenv("DOCDOC_AZURE_DI_KEY", "super-secret-key-value")
        analyzer = Analyzer(provider_error("auth"))
        parser = AzureDocumentIntelligenceParser(analyze=analyzer)

        with pytest.raises(ProviderError) as caught:
            parser.parse(source, {}, TransportSettings(max_attempts=1))

        assert "super-secret-key-value" not in str(caught.value)


class TestNoFallback:
    def test_a_failed_recognition_parse_does_not_try_the_native_parser(
        self, source: SourceFile
    ) -> None:
        """FR-014 — a failure surfaces; it never quietly becomes another
        parser's problem, which would produce a near-empty document from a scan
        and call it success."""
        from docdoc.ingest import parse
        from docdoc.ingest.registry import ParserRegistry

        analyzer = Analyzer(provider_error("service"))
        registry = ParserRegistry()
        registry.register(AzureDocumentIntelligenceParser(analyze=analyzer))

        with pytest.raises(ProviderError):
            parse(source, registry=registry, transport=QUICK)
