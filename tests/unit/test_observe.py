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
    Availability,
    ExtractionError,
    ExtractionOptions,
    ModelProviderError,
    SchemaError,
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


def test_a_failure_event_names_the_model_it_was_aimed_at(
    registry: SchemaRegistry, caplog: pytest.LogCaptureFixture
) -> None:
    """T115, FR-040 -- "the model and adapter identities and versions", on every event.

    A failed call reached no model, so there is no *reported* one to record. The
    model the request was aimed at is known before the call is made, and it is
    what makes a failure attributable to a model rather than only to an adapter.
    """
    caplog.set_level(logging.INFO, logger="docdoc.extraction")
    with pytest.raises(ExtractionError):
        extract(
            make_document(DOCUMENT_TEXT),
            schema="invoice@1",
            registry=registry,
            adapter=EchoAdapter.malformed(),
        )
    payload = _sole_event(caplog)
    assert payload["outcome"] == "failure"
    for key in ("model_id", "model_version", "adapter_id", "adapter_version"):
        assert key in payload, f"{key} missing from a failure event"


# -- the failures that happen before the model is ever called -----------------


def _sole_event(caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
    events = [r for r in caplog.records if r.getMessage() == EVENT_NAME]
    assert len(events) == 1, f"expected exactly one event, got {len(events)}"
    return events[0].docdoc  # type: ignore[no-any-return,attr-defined]


def test_an_unregistered_schema_still_emits_an_event(
    registry: SchemaRegistry, echo: EchoAdapter, caplog: pytest.LogCaptureFixture
) -> None:
    """T112, FR-040, SC-012.

    This failure happens before the schema resolves, so the event cannot name a
    schema identity -- and that is the point: it omits what it does not know
    rather than not existing. SC-012 counts "unregistered schema" as one of the
    eight failures it requires to surface properly.
    """
    caplog.set_level(logging.INFO, logger="docdoc.extraction")
    with pytest.raises(SchemaError):
        extract(make_document(DOCUMENT_TEXT), schema="nope@1", registry=registry, adapter=echo)
    payload = _sole_event(caplog)
    assert payload["outcome"] == "failure"
    assert payload["reason"] == "schema"
    assert payload["document_id"]
    assert "schema_identity" not in payload  # unknown, so absent rather than null


def test_an_unavailable_adapter_still_emits_an_event(
    registry: SchemaRegistry, caplog: pytest.LogCaptureFixture
) -> None:
    """T112, FR-028, FR-041 -- a missing credential is the most common failure of all.

    It also transmits nothing, so without an event there is no record anywhere
    that an extraction was attempted.
    """

    class Unavailable(EchoAdapter):
        def available(self) -> Availability:
            return Availability(usable=False, reason="no API key configured")

    caplog.set_level(logging.INFO, logger="docdoc.extraction")
    with pytest.raises(ModelProviderError):
        extract(
            make_document(DOCUMENT_TEXT),
            schema="invoice@1",
            registry=registry,
            adapter=Unavailable(),
        )
    payload = _sole_event(caplog)
    assert payload["reason"] == "unavailable"
    assert payload["schema_identity"] == "invoice@1"


def test_an_over_budget_document_still_emits_an_event(
    registry: SchemaRegistry, echo: EchoAdapter, caplog: pytest.LogCaptureFixture
) -> None:
    """T112, FR-030, SC-012 -- refused locally, and therefore invisible without this."""
    caplog.set_level(logging.INFO, logger="docdoc.extraction")
    with pytest.raises(ExtractionError):
        extract(
            make_document(DOCUMENT_TEXT),
            schema="invoice@1",
            registry=registry,
            adapter=echo,
            options=ExtractionOptions(input_budget_tokens=1),
        )
    payload = _sole_event(caplog)
    assert payload["reason"] == "input_budget"
    assert payload["schema_identity"] == "invoice@1"


def test_every_failure_class_emits_exactly_one_event(
    registry: SchemaRegistry, echo: EchoAdapter, caplog: pytest.LogCaptureFixture
) -> None:
    """T112 -- the general form, so a new failure path cannot land unobserved.

    Written as a sweep rather than as five separate cases because the requirement
    is about *coverage of the paths*, and a sweep is what fails when someone adds
    a sixth early return.
    """
    caplog.set_level(logging.INFO, logger="docdoc.extraction")
    document = make_document(DOCUMENT_TEXT)
    cases: tuple[tuple[str, dict[str, Any]], ...] = (
        ("schema", {"schema": "nope@1", "adapter": echo}),
        (
            "input_budget",
            {
                "schema": "invoice@1",
                "adapter": echo,
                "options": ExtractionOptions(input_budget_tokens=1),
            },
        ),
        ("missing_field", {"schema": "invoice@1", "adapter": EchoAdapter.malformed()}),
        ("refusal", {"schema": "invoice@1", "adapter": EchoAdapter.refusing()}),
        ("service", {"schema": "invoice@1", "adapter": EchoAdapter.failing(reason="service")}),
    )
    for expected_reason, kwargs in cases:
        caplog.clear()
        with pytest.raises((ExtractionError, ModelProviderError, SchemaError)):
            extract(document, registry=registry, **kwargs)
        payload = _sole_event(caplog)
        assert payload["outcome"] == "failure"
        assert payload["reason"] == expected_reason
        assert payload["model_id"], f"{expected_reason} event names no model"


def test_a_failure_before_transmission_reports_zero_attempts(
    registry: SchemaRegistry, echo: EchoAdapter, caplog: pytest.LogCaptureFixture
) -> None:
    """T121, FR-040, SC-016 — the attempt count means calls the model received.

    These are the failures where SC-016's guarantee is that **zero bytes were
    transmitted**, so an event claiming an attempt is the one record that
    contradicts it. Before this they all reported 1, indistinguishable from a
    single genuine call.
    """
    caplog.set_level(logging.INFO, logger="docdoc.extraction")
    document = make_document(DOCUMENT_TEXT)

    caplog.clear()
    with pytest.raises(SchemaError):
        extract(document, schema="nope@1", registry=registry, adapter=echo)
    assert _sole_event(caplog)["attempts"] == 0

    caplog.clear()
    with pytest.raises(ExtractionError):
        extract(
            document,
            schema="invoice@1",
            registry=registry,
            adapter=echo,
            options=ExtractionOptions(input_budget_tokens=1),
        )
    assert _sole_event(caplog)["attempts"] == 0


def test_a_failure_after_transmission_reports_the_real_count(
    registry: SchemaRegistry, caplog: pytest.LogCaptureFixture
) -> None:
    """T121 — the other half, so zero does not become the new blanket answer.

    A transient failure retried to the limit made three real calls, and a
    conformance failure made exactly one. Both are worth distinguishing from a
    request that never left the process.
    """
    caplog.set_level(logging.INFO, logger="docdoc.extraction")
    document = make_document(DOCUMENT_TEXT)

    caplog.clear()
    with pytest.raises(ModelProviderError):
        extract(
            document,
            schema="invoice@1",
            registry=registry,
            adapter=EchoAdapter.failing(reason="service"),
        )
    assert _sole_event(caplog)["attempts"] == 3

    caplog.clear()
    with pytest.raises(ExtractionError):
        extract(document, schema="invoice@1", registry=registry, adapter=EchoAdapter.malformed())
    assert _sole_event(caplog)["attempts"] == 1


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
