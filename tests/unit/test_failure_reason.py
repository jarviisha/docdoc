"""`reason_of`: a failed run's `error_reason`, drawn only from a closed vocabulary.

Spec decision 7 (``docs/superpowers/sdd/2026-10-20-console-monitoring``): a
failed run names *why* a parser or provider refused, so an operator can tell a
host timeout from a host 5xx from a rejected language -- but the no-content
rule (FR-037) still applies, so the word has to come from the error's own
declared vocabulary and never from whatever happens to sit in `.reason`, which
nothing stops a caller from setting to anything, including document content.
"""

from __future__ import annotations

from typing import Any

import pytest

from docdoc.extraction.adapters import EchoAdapter
from docdoc.extraction.errors import ModelProviderError
from docdoc.ingest.errors import ProviderError, UnsupportedDocumentError
from docdoc.pipeline import Stage, run
from docdoc.pipeline.result import reason_of
from docdoc.runs.model import RunOutcome, RunStatus
from tests.fixtures.evaluation.datasets import registry
from tests.fixtures.evaluation.predictions import RESPONSES, document_for

DOCUMENT = document_for("clean")


def _run(**overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "schema": "invoice@1",
        "registry": registry(),
        "adapter": EchoAdapter.returning("invoice@1", RESPONSES["clean"]),
        "document": DOCUMENT,
    }
    kwargs.update(overrides)
    return run(b"", **kwargs)


def test_reason_of_reads_a_listed_reason() -> None:
    assert reason_of(ProviderError("x", reason="timeout", parser_id="p")) == "timeout"
    assert (
        reason_of(UnsupportedDocumentError("x", reason="encrypted", blob_id="sha256:aa"))
        == "encrypted"
    )


def test_reason_of_ignores_unlisted_reasons() -> None:
    error = ProviderError("x", reason="timeout", parser_id="p")
    error.reason = "the invoice said 42"
    assert reason_of(error) is None
    assert reason_of(ValueError("boom")) is None


def test_a_failed_parse_records_its_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """`StageOutcome.failure_reason`, set by the pipeline runner at the point of
    failure -- the same place `failure_class` has always been set."""

    def _explode(*args: object, **kwargs: object) -> None:
        raise ModelProviderError("upstream timed out", reason="timeout", adapter_id="echo")

    monkeypatch.setattr("docdoc.pipeline.runner._extract", _explode)
    result = _run()

    outcome = result.outcome_for(Stage.EXTRACT)
    assert outcome is not None
    assert outcome.failure_class == "ModelProviderError"
    assert outcome.failure_reason == "timeout"


def test_an_unreasoned_failure_records_no_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """An error outside the closed vocabulary (or with none at all) stores `None`.

    A bare `ValueError` declares no layer, so `_stage_of` attributes it to
    `VALIDATE` (FR-005) regardless of which stage function raised it -- the
    point here is `failure_reason`, not the attribution.
    """

    def _explode(*args: object, **kwargs: object) -> None:
        raise ValueError("no reason attribute at all")

    monkeypatch.setattr("docdoc.pipeline.runner._extract", _explode)
    result = _run()

    assert result.failed_stage is not None
    outcome = result.outcome_for(result.failed_stage)
    assert outcome is not None
    assert outcome.failure_class == "ValueError"
    assert outcome.failure_reason is None


def test_run_outcome_copies_the_failing_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """`RunOutcome.of` projects `error_reason` the same way it projects `error_class`."""

    def _explode(*args: object, **kwargs: object) -> None:
        raise ModelProviderError("upstream timed out", reason="timeout", adapter_id="echo")

    monkeypatch.setattr("docdoc.pipeline.runner._extract", _explode)
    result = _run()

    outcome = RunOutcome.of(result)
    assert outcome.status is RunStatus.FAILED
    assert outcome.error_class == "ModelProviderError"
    assert outcome.error_reason == "timeout"


def test_a_successful_run_outcome_carries_no_reason() -> None:
    outcome = RunOutcome.of(_run())
    assert outcome.status is RunStatus.SUCCEEDED
    assert outcome.error_reason is None
