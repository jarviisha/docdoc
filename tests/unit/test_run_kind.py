"""A run has a kind, and an OCR run has no schema (014, ADR-0022)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from docdoc.runs.model import Run, RunKind, RunStatus
from tests.fixtures.run_queue import InMemoryRunQueue

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


def _run(**fields: Any) -> Run:
    base: dict[str, Any] = {
        "run_id": uuid4(),
        "tenant_id": "default",
        "blob_id": "sha256:" + "b" * 64,
        "schema_identity": "invoice@1",
        "status": RunStatus.QUEUED,
        "created_at": NOW,
        "updated_at": NOW,
        "expires_at": NOW + timedelta(days=1),
    }
    return Run(**{**base, **fields})


class _OcrSpec:
    tenant_id = "default"
    blob_id = "sha256:" + "c" * 64
    schema_identity = None
    request_id = None
    idempotency_key = None
    kind = "ocr"
    ocr_parser = "paddle"
    ocr_language = "vie"


def test_extraction_run_requires_schema() -> None:
    with pytest.raises(ValidationError):
        _run(kind="extraction", schema_identity=None)


def test_ocr_run_has_no_schema() -> None:
    run = _run(kind="ocr", schema_identity=None, ocr_parser="paddle", ocr_language="vie")
    assert run.kind is RunKind.OCR
    with pytest.raises(ValidationError):
        _run(kind="ocr", schema_identity="x@1")


def test_extraction_run_carries_no_ocr_choices() -> None:
    with pytest.raises(ValidationError):
        _run(kind="extraction", ocr_parser="paddle")


def test_default_kind_is_extraction() -> None:
    assert _run().kind is RunKind.EXTRACTION


def test_in_memory_submit_carries_kind() -> None:
    run = InMemoryRunQueue().submit(
        _OcrSpec(),  # type: ignore[arg-type]
        run_id=uuid4(),
        now=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    assert run.kind == "ocr"
    assert run.schema_identity is None
    assert run.ocr_parser == "paddle"
    assert run.ocr_language == "vie"


def test_dump_public_includes_kind() -> None:
    run = _run(kind="ocr", schema_identity=None, ocr_parser="paddle")
    body = run.dump_public()
    assert body["kind"] == "ocr"
    assert body["ocr_parser"] == "paddle"
