"""T051 — nothing is transmitted for a request that was always going to fail (SC-016, FR-041).

The guarantee is not "we try not to waste calls". It is that a request failing
schema resolution, credential availability, or the budget guard sends **zero
bytes**. Every one of those checks is local and every one runs before the adapter
is reached.

Asserted against a transport that records every attempt, because the only way to
prove a call did not happen is to count the calls.
"""

from __future__ import annotations

from typing import Any

import pytest

from docdoc.extraction import (
    ExtractionError,
    ExtractionOptions,
    ModelProviderError,
    SchemaError,
    SchemaRegistry,
    extract,
)
from docdoc.extraction.adapters.gemini import GeminiAdapter
from tests.support import make_document

DOCUMENT_TEXT = "ACME LTD\nINV-001\nTotal 1,240.00\n"


class _RecordingModels:
    """Counts every generate_content that reaches the wire."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> Any:  # pragma: no cover - must never run
        self.calls.append(kwargs)
        raise AssertionError("a call reached the transport when none should have")


class _RecordingClient:
    def __init__(self) -> None:
        self.models = _RecordingModels()


@pytest.fixture
def registry() -> SchemaRegistry:
    return SchemaRegistry.from_paths(["schemas"])


@pytest.fixture
def recorder() -> _RecordingClient:
    return _RecordingClient()


def test_an_unresolvable_schema_transmits_nothing(
    registry: SchemaRegistry, recorder: _RecordingClient
) -> None:
    adapter = GeminiAdapter(api_key="test-key", client=recorder)
    for identity in ("invoice", "invoice@9", "purchase_order@1", "invoice@latest"):
        with pytest.raises(SchemaError):
            extract(
                make_document(DOCUMENT_TEXT),
                schema=identity,
                registry=registry,
                adapter=adapter,
            )
    assert recorder.models.calls == []


def test_a_missing_credential_transmits_nothing(
    registry: SchemaRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-041 — the check precedes the client, so no connection is even opened."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(ModelProviderError) as caught:
        extract(
            make_document(DOCUMENT_TEXT),
            schema="invoice@1",
            registry=registry,
            adapter=GeminiAdapter(),
        )
    assert caught.value.reason == "unavailable"


def test_an_over_budget_document_transmits_nothing(
    registry: SchemaRegistry, recorder: _RecordingClient
) -> None:
    with pytest.raises(ExtractionError) as caught:
        extract(
            make_document(DOCUMENT_TEXT),
            schema="invoice@1",
            registry=registry,
            adapter=GeminiAdapter(api_key="test-key", client=recorder),
            options=ExtractionOptions(input_budget_tokens=5),
        )
    assert caught.value.reason == "input_budget"
    assert recorder.models.calls == []


def test_the_document_body_is_never_in_a_failed_request(
    registry: SchemaRegistry, recorder: _RecordingClient
) -> None:
    """The stronger statement: not "fewer calls", but "no bytes"."""
    with pytest.raises(SchemaError):
        extract(
            make_document(DOCUMENT_TEXT),
            schema="invoice@9",
            registry=registry,
            adapter=GeminiAdapter(api_key="test-key", client=recorder),
        )
    assert not any("ACME" in str(call) for call in recorder.models.calls)
    assert recorder.models.calls == []


def test_a_valid_request_does_reach_the_transport(registry: SchemaRegistry) -> None:
    """Guards the guard: the assertions above would pass if nothing ever called."""

    class _Counting(_RecordingModels):
        def generate_content(self, **kwargs: Any) -> Any:
            self.calls.append(kwargs)
            raise RuntimeError("reached, as expected")

    client = _RecordingClient()
    client.models = _Counting()
    with pytest.raises(Exception, match="reached, as expected"):
        extract(
            make_document(DOCUMENT_TEXT),
            schema="invoice@1",
            registry=registry,
            adapter=GeminiAdapter(api_key="test-key", client=client),
        )
    assert len(client.models.calls) == 1
