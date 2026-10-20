"""`docdoc.pipeline.parse_only`: the parse stage alone, cached like the pipeline's (014)."""

from __future__ import annotations

from pathlib import Path

import pytest

from docdoc.artifacts import FileArtifactStore
from docdoc.ingest.errors import ParserCapabilityError
from docdoc.kernel import Document
from docdoc.pipeline import StageStatus, parse_only
from tests.support.ocr import CountingPost, paddle_registry

IMAGE = (Path(__file__).parent.parent / "fixtures" / "image" / "sample_page.png").read_bytes()


def test_parse_only_stores_document(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)
    post = CountingPost("INV-001")
    document, outcome = parse_only(
        IMAGE, store=store, parser="paddle", registry=paddle_registry(post)
    )
    assert outcome.status is StageStatus.EXECUTED
    assert outcome.artifact_id == document.id
    stored = store.get(document.id, model=Document, artifact_format_version=1)
    assert stored is not None
    assert stored.text == document.text
    assert "INV-001" in document.text


def test_parse_only_reuses(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)
    post = CountingPost()
    registry = paddle_registry(post)
    first, _ = parse_only(IMAGE, store=store, parser="paddle", registry=registry)
    second, outcome = parse_only(IMAGE, store=store, parser="paddle", registry=registry)
    assert outcome.status is StageStatus.REUSED
    assert second.id == first.id
    assert post.calls == 1


def test_parse_only_named_unknown_parser_raises(tmp_path: Path) -> None:
    with pytest.raises(ParserCapabilityError):
        parse_only(
            IMAGE,
            store=FileArtifactStore(tmp_path),
            parser="nope",
            registry=paddle_registry(CountingPost()),
        )
