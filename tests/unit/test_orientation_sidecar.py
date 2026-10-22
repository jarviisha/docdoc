"""Page angles kept beside the cached parse, found again on a cache hit (020)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from docdoc.artifacts import FileArtifactStore
from docdoc.ingest.parsers.paddlex import OcrEndpoint
from docdoc.kernel import Document
from docdoc.pipeline import StageStatus, parse_only
from docdoc.pipeline.orientation import (
    ORIENTATION_FORMAT_VERSION,
    PageOrientation,
    orientation_id_for,
    read_orientation,
)
from tests.support.ocr import CountingPost, paddle_registry, response

IMAGE = (Path(__file__).parent.parent / "fixtures" / "image" / "sample_page.png").read_bytes()


class TurnedPost(CountingPost):
    """A PaddleX answer whose page it straightened by `raw_angle` (its own label)."""

    def __init__(self, raw_angle: int) -> None:
        super().__init__()
        self.raw_angle = raw_angle

    def __call__(self, endpoint: OcrEndpoint, body: dict[str, Any], timeout_s: float) -> Any:
        self.calls += 1
        answer = response(self.text)
        answer["result"]["ocrResults"][0]["prunedResult"]["doc_preprocessor_res"] = {
            "angle": self.raw_angle
        }
        return answer


def test_orientation_id_is_derived_and_distinct() -> None:
    document_id = "sha256:" + "ab" * 32
    derived = orientation_id_for(document_id)
    assert derived.startswith("sha256:")
    assert len(derived) == len(document_id)
    assert derived != document_id
    assert derived == orientation_id_for(document_id)
    assert derived != orientation_id_for("sha256:" + "cd" * 32)


def test_only_quarter_turns_are_valid() -> None:
    assert PageOrientation(angles=(0, 90, 180, 270)).angles == (0, 90, 180, 270)
    with pytest.raises(ValidationError):
        PageOrientation(angles=(45,))
    with pytest.raises(ValidationError):
        PageOrientation(angles=(90,), extra=1)  # type: ignore[call-arg]


def test_angles_survive_a_cache_hit(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)
    post = TurnedPost(raw_angle=270)
    registry = paddle_registry(post)

    first, _ = parse_only(IMAGE, store=store, parser="paddle", registry=registry)
    assert read_orientation(store, first.id) == (90,)

    second, outcome = parse_only(IMAGE, store=store, parser="paddle", registry=registry)

    assert outcome.status is StageStatus.REUSED
    assert post.calls == 1
    assert second.id == first.id
    assert read_orientation(store, second.id) == (90,)


def test_the_sidecar_leaves_the_document_unchanged(tmp_path: Path) -> None:
    # A half turn: unlike a quarter turn it leaves the page size alone, so the
    # two parses differ only in whether angles were reported and stored.
    turned, _ = parse_only(
        IMAGE,
        store=FileArtifactStore(tmp_path / "a"),
        parser="paddle",
        registry=paddle_registry(TurnedPost(raw_angle=180)),
    )
    plain, _ = parse_only(
        IMAGE, store=None, parser="paddle", registry=paddle_registry(CountingPost())
    )
    assert turned.model_dump_json() == plain.model_dump_json()


def test_a_parse_without_a_sidecar_reports_unknown(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)
    post = CountingPost()
    document, _ = parse_only(IMAGE, store=store, parser="paddle", registry=paddle_registry(post))
    # A Document filed before this change: present, with no sidecar beside it.
    assert store.delete(orientation_id_for(document.id))

    again, outcome = parse_only(IMAGE, store=store, parser="paddle", registry=paddle_registry(post))

    assert outcome.status is StageStatus.REUSED
    assert post.calls == 1, "a missing sidecar is 'unknown', never a reason to re-parse"
    assert store.get(again.id, model=Document, artifact_format_version=1) is not None
    assert read_orientation(store, again.id) is None


def test_an_unreadable_store_reports_unknown() -> None:
    class Broken:
        def get(self, *args: Any, **kwargs: Any) -> Any:
            raise OSError("disk gone")

    assert read_orientation(Broken(), "sha256:" + "ab" * 32) is None  # type: ignore[arg-type]


def test_the_sidecar_is_a_parse_stage_artifact_of_its_document(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path)
    document, _ = parse_only(
        IMAGE, store=store, parser="paddle", registry=paddle_registry(TurnedPost(raw_angle=180))
    )
    envelope = store.envelope(orientation_id_for(document.id))
    assert envelope is not None
    assert envelope.stage == "parse"
    assert envelope.input_artifact_id == document.id
    assert envelope.artifact_format_version == ORIENTATION_FORMAT_VERSION


def test_a_sidecar_conflict_keeps_the_run_going(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    store = FileArtifactStore(tmp_path)
    upright, _ = parse_only(
        IMAGE, store=None, parser="paddle", registry=paddle_registry(CountingPost())
    )
    # A different turn already filed under the same sidecar id.
    store.put(
        orientation_id_for(upright.id),
        PageOrientation(angles=(0,)),
        stage="parse",
        input_artifact_id=upright.id,
        processor_id="paddle",
        processor_version="1.0.0",
        options_hash="x",
        artifact_format_version=ORIENTATION_FORMAT_VERSION,
    )

    with caplog.at_level("WARNING", logger="docdoc.pipeline"):
        document, outcome = parse_only(
            IMAGE, store=store, parser="paddle", registry=paddle_registry(TurnedPost(raw_angle=180))
        )

    assert outcome.status is StageStatus.EXECUTED
    assert store.get(document.id, model=Document, artifact_format_version=1) is not None
    assert read_orientation(store, document.id) == (0,), "the first write stands"
    assert any(
        getattr(record, "event", None) == "pipeline.orientation_conflict"
        for record in caplog.records
    )


def test_an_extraction_run_cache_miss_writes_the_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("pypdfium2", reason="a real extraction run parses the fixture PDF")
    from docdoc.extraction import SchemaRegistry
    from docdoc.extraction.adapters.echo import EchoAdapter
    from docdoc.pipeline import Stage, run, runner

    real = runner._execute_parse

    def turned(plan: Any) -> tuple[Document, tuple[int, ...] | None]:
        document, _ = real(plan)
        return document, tuple(90 for _ in document.pages)

    monkeypatch.setattr(runner, "_execute_parse", turned)
    store = FileArtifactStore(tmp_path)
    root = Path(__file__).parent.parent.parent
    result = run(
        (root / "tests/fixtures/pdf/digital_invoice.pdf").read_bytes(),
        schema="invoice@1",
        registry=SchemaRegistry.from_paths([root / "schemas"]),
        adapter=EchoAdapter.from_fixtures(str(root / "tests/fixtures/echo")),
        store=store,
    )

    parse = next(o for o in result.outcomes if o.stage is Stage.PARSE)
    assert parse.status is StageStatus.EXECUTED
    assert parse.artifact_id is not None
    angles = read_orientation(store, parse.artifact_id)
    assert angles is not None
    assert set(angles) == {90}
