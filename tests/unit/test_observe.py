"""T064 — one event per extraction, and no content anywhere near a log (SC-015).

The leak assertion is the important one. Document text, extracted values, claimed
source text, prompt content, and credentials must never reach a log, and this is
the only place that is checked mechanically rather than promised in a docstring.

It runs over the *whole* fixture set including the failure paths, because a
failure path is exactly where a well-meaning `logger.exception(payload)` gets
added.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

import pytest

from docdoc.extraction import (
    ExtractionError,
    ExtractionOptions,
    ModelProviderError,
    SchemaRegistry,
    extract,
)
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.extraction.observe import EVENT_NAME, emit_extraction_event
from tests.support import make_document

DOCUMENT_TEXT = "ACME LTD\nINV-001\n1 March 2026\nWidget, large 1,000.00\nTotal 1,240.00\n"

#: Every string that must never appear in log output: document text, an extracted
#: value, a claimed text, and a slice of the prompt.
FORBIDDEN = (
    "ACME LTD",
    "INV-001",
    "1,240.00",
    "Widget, large",
    "Corner Store",
    "exactly as it appears in the document",
    "Here is the document",
)


@pytest.fixture
def registry() -> SchemaRegistry:
    return SchemaRegistry.from_paths(["schemas"])


@pytest.fixture
def echo() -> EchoAdapter:
    return EchoAdapter.from_fixtures("tests/fixtures/echo")


@pytest.fixture
def events(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    caplog.set_level(logging.INFO, logger="docdoc.extraction")
    return caplog.records  # type: ignore[return-value]


# -- the event itself --------------------------------------------------------


def test_one_event_per_successful_extraction(
    registry: SchemaRegistry, echo: EchoAdapter, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="docdoc.extraction")
    extract(make_document(DOCUMENT_TEXT), schema="invoice@1", registry=registry, adapter=echo)
    events = [r for r in caplog.records if r.getMessage() == EVENT_NAME]
    assert len(events) == 1
    payload = events[0].docdoc  # type: ignore[attr-defined]
    assert payload["outcome"] == "success"
    for key in (
        "document_id",
        "schema_identity",
        "schema_hash",
        "artifact_id",
        "adapter_id",
        "adapter_version",
        "model_id",
        "model_version",
        "extractor_version",
        "duration_ms",
    ):
        assert key in payload, f"{key} missing from the event"


@pytest.mark.parametrize(
    ("label", "factory", "expected_reason"),
    [
        ("malformed", EchoAdapter.malformed, "missing_field"),
        ("refusal", EchoAdapter.refusing, "refusal"),
        ("provider", lambda: EchoAdapter.failing(reason="service"), "service"),
    ],
)
def test_one_event_per_failed_extraction(
    registry: SchemaRegistry,
    caplog: pytest.LogCaptureFixture,
    label: str,
    factory: Any,
    expected_reason: str,
) -> None:
    """FR-040 -- an event only on success makes a failure answerable only by re-running.

    For a paid model call, "re-run it to find out why it failed" is the wrong
    answer.
    """
    caplog.set_level(logging.INFO, logger="docdoc.extraction")
    with pytest.raises((ExtractionError, ModelProviderError)):
        extract(
            make_document(DOCUMENT_TEXT),
            schema="invoice@1",
            registry=registry,
            adapter=factory(),
        )
    events = [r for r in caplog.records if r.getMessage() == EVENT_NAME]
    assert len(events) == 1
    payload = events[0].docdoc  # type: ignore[attr-defined]
    assert payload["outcome"] == "failure"
    assert payload["reason"] == expected_reason


def test_the_event_counts_values_without_carrying_them(
    registry: SchemaRegistry, echo: EchoAdapter, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="docdoc.extraction")
    extract(make_document(DOCUMENT_TEXT), schema="invoice@1", registry=registry, adapter=echo)
    record = next(r for r in caplog.records if r.getMessage() == EVENT_NAME)
    payload = record.docdoc  # type: ignore[attr-defined]
    assert payload["values_present"] > 0
    assert payload["values_absent"] > 0


# -- the leak assertion ------------------------------------------------------


def test_no_content_reaches_the_log_over_the_whole_fixture_set(
    registry: SchemaRegistry, caplog: pytest.LogCaptureFixture
) -> None:
    """SC-015 -- successes and failures alike, every schema, every failure mode."""
    caplog.set_level(logging.DEBUG)  # every logger, not just ours

    adapters = [
        EchoAdapter.from_fixtures("tests/fixtures/echo"),
        EchoAdapter.malformed(),
        EchoAdapter.refusing(category="cyber"),
        EchoAdapter.failing(reason="timeout"),
    ]
    for identity in registry.identities():
        for adapter in adapters:
            # The outcome is not the subject here; the log output is.
            with contextlib.suppress(ExtractionError, ModelProviderError):
                extract(
                    make_document(DOCUMENT_TEXT),
                    schema=identity,
                    registry=registry,
                    adapter=adapter,
                )
    # And an over-budget refusal, which formats a message of its own.
    with contextlib.suppress(ExtractionError):
        extract(
            make_document(DOCUMENT_TEXT),
            schema="invoice@1",
            registry=registry,
            adapter=adapters[0],
            options=ExtractionOptions(input_budget_tokens=5),
        )

    assert caplog.records, "a leak test that captured nothing proves nothing"
    haystack = "\n".join(
        [r.getMessage() for r in caplog.records]
        + [repr(getattr(r, "docdoc", "")) for r in caplog.records]
    )
    for secret in FORBIDDEN:
        assert secret not in haystack, f"{secret!r} reached the log output"


# -- the closed key set ------------------------------------------------------


def test_an_unknown_event_field_is_refused() -> None:
    """The mechanism that keeps the leak test from having to be exhaustive.

    A content field cannot be added at a call site, because the key set is closed
    here. That turns "remember not to log the payload" into a build failure.
    """
    with pytest.raises(ValueError, match="unknown field"):
        emit_extraction_event(document_id="sha256:x", payload={"total": "1,240.00"})


def test_none_valued_fields_are_dropped_rather_than_logged_as_null() -> None:
    event = emit_extraction_event(document_id="sha256:x", input_tokens=None)
    assert "input_tokens" not in event
    assert event["document_id"] == "sha256:x"


def test_the_event_carries_its_own_name() -> None:
    assert emit_extraction_event(outcome="success")["event"] == EVENT_NAME
