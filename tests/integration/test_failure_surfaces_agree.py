"""One failure, three surfaces, one story.

The spec's Edge Cases are explicit and were never asserted:

> A document fails at the third stage. The command line, the HTTP interface, and
> the recorder must agree on what is returned: the stages that succeeded, the
> stage that failed, and its typed error class — never the exception message,
> which can quote the document.

Three surfaces, and until now no test compared any two of them. Each had its own
tests and each passed; nothing checked that they say the same thing. That is the
failure mode this whole milestone was built to prevent — FR-009 exists because
the stage order had two definitions, and SC-014 asserts it now has one. The same
argument applies to what a *failure* looks like: three renderings of one event
that nobody compares will drift, and the drift surfaces when somebody diffs a CLI
transcript against an HTTP response during an incident.

**The comparison is of facts, not of shapes.** The three surfaces legitimately
differ in form — an exit code, a JSON body, a `DocumentPrediction` — so what is
compared is the three things the Edge Case names: which stage failed, what its
typed error class was, and which earlier results survived.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.pipeline import Stage, run

pytest.importorskip("pymupdf")

FIXTURE = Path("tests/fixtures/pdf/digital_invoice.pdf")
SCHEMA = "invoice@1"

#: A schema no registry holds. Chosen because it fails identically on every
#: surface without any of them being rigged: the failure is the caller's
#: configuration, which is the most common real one.
ABSENT_SCHEMA = "no-such-schema@1"


def _registry() -> SchemaRegistry:
    return SchemaRegistry.from_paths([Path("schemas")])


def _adapter() -> EchoAdapter:
    return EchoAdapter.from_fixtures("tests/fixtures/echo")


def _facts(stage: str | None, failure_class: str | None, survived: set[str]) -> dict[str, Any]:
    """The three things the Edge Case says the surfaces must agree on."""
    return {"failed_stage": stage, "failure_class": failure_class, "survived": survived}


# -- what each surface says --------------------------------------------------


def _from_the_library() -> dict[str, Any]:
    result = run(
        FIXTURE.read_bytes(),
        schema=ABSENT_SCHEMA,
        registry=_registry(),
        adapter=_adapter(),
    )
    assert result.failed_stage is not None
    return _facts(
        result.failed_stage.value,
        result.failure_of(result.failed_stage),
        {
            name
            for name, value in (
                ("extraction", result.extraction),
                ("grounding", result.grounding),
                ("validation", result.validation),
            )
            if value is not None
        },
    )


def _from_the_command_line(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    from docdoc.cli import main

    # A *populated* registry that does not hold this schema — the same failure
    # the other three surfaces see. With no search path the CLI takes a
    # different and deliberately better route (US1, scenario 5), which the test
    # below pins rather than smooths over.
    monkeypatch.setenv("DOCDOC_SCHEMA_PATHS", str(Path("schemas").resolve()))
    monkeypatch.setenv("DOCDOC_MODEL_ADAPTERS", "echo")
    monkeypatch.setenv("DOCDOC_ECHO_FIXTURES", str(Path("tests/fixtures/echo").resolve()))

    code = main(
        ["extract", str(FIXTURE), "--schema", ABSENT_SCHEMA, "--json", "--no-store"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 2, "a run that could not complete exits 2"

    outcome = next(
        item for item in payload["outcomes"] if item["stage"] == payload["failed_stage"]
    )
    return _facts(
        payload["failed_stage"],
        outcome["failure_class"],
        {
            name
            for name, key in (("extraction", "fields"),)
            if payload.get(key)
        },
    )


def _from_the_http_interface(tmp_path: Path) -> dict[str, Any]:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from docdoc.api.app import _Deployment, build_app
    from docdoc.artifacts import BlobStore, FileArtifactStore

    deployment = _Deployment(
        store=FileArtifactStore(tmp_path),
        blobs=BlobStore(tmp_path),
        registry=_registry(),
        adapter=_adapter(),
    )
    client = TestClient(build_app(deployment))

    blob_id = client.post("/v1/documents", content=FIXTURE.read_bytes()).json()["blob_id"]
    body = client.post(
        f"/v1/documents/{blob_id}/extract", params={"schema": ABSENT_SCHEMA}
    ).json()

    return _facts(
        body["error"]["stage"],
        body["error"]["class"],
        set(body["results"]),
    )


def _from_the_recorder(tmp_path: Path) -> dict[str, Any]:
    """The same failure, reached the only way the recorder allows.

    A `GoldenDocument` validates that its schema resolves — it records the
    `schema_hash`, so a golden set naming an absent schema cannot be built at
    all. So the set is well-formed and the *recorder's registry* is the one that
    cannot resolve it, which produces exactly the failure the other three see:
    `SchemaError`, at extract, with the parse already done.
    """
    from tests.fixtures.evaluation.datasets import golden_set
    from tests.fixtures.evaluation.predictions import document_for

    from docdoc.extraction import SchemaRegistry
    from docdoc.recording import record_predictions

    full = golden_set(include_restricted=False)
    one = full.documents[0].document_id
    single = full.model_copy(
        update={
            "documents": (full.documents[0],),
            "labels": {one: full.labels_for(one)},
        }
    )

    recorded = record_predictions(
        single,
        adapter=_adapter(),
        registry=SchemaRegistry(),  # empty: nothing resolves
        documents={one: document_for("clean")},
    ).predictions[one]

    return _facts(
        None if recorded.failed_stage is None else str(recorded.failed_stage),
        recorded.failure_reason,
        {
            name
            for name, value in (
                ("extraction", recorded.extraction),
                ("grounding", recorded.grounding),
                ("validation", recorded.validation),
            )
            if value is not None
        },
    )


# -- the agreement -----------------------------------------------------------


def test_the_three_surfaces_agree_on_a_failed_run(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Edge Case, asserted rather than described.

    Each surface is asked the same question and the three answers are compared
    against the library's, which is the one they all serialise.
    """
    library = _from_the_library()
    assert library["failed_stage"] == Stage.EXTRACT.value
    assert library["failure_class"] == "SchemaError"

    command_line = _from_the_command_line(capsys, monkeypatch)
    http = _from_the_http_interface(tmp_path / "http")
    recorder = _from_the_recorder(tmp_path / "rec")

    for name, surface in (
        ("the command line", command_line),
        ("the HTTP interface", http),
        ("the recorder", recorder),
    ):
        assert surface["failed_stage"] == library["failed_stage"], (
            f"{name} names a different stage than the library does"
        )
        assert surface["failure_class"] == library["failure_class"], (
            f"{name} reports a different error class than the library does"
        )
        assert surface["survived"] == library["survived"], (
            f"{name} kept a different set of results than the library did — "
            "FR-004 says a failed run discards none of them, and the surfaces "
            "must agree on which there were"
        )


def test_no_surface_carries_the_exception_message(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Edge Case's last clause: "never the exception message".

    A message can quote the document it choked on, and all three of these travel
    — into a terminal, into an HTTP body, into a committed prediction set. What
    each carries is the error's **class name**.

    Checked by shape rather than by content: a class name is a single
    identifier, so a value containing a space is a message that leaked.
    """
    for name, surface in (
        ("the library", _from_the_library()),
        ("the command line", _from_the_command_line(capsys, monkeypatch)),
        ("the HTTP interface", _from_the_http_interface(tmp_path / "http")),
        ("the recorder", _from_the_recorder(tmp_path / "rec")),
    ):
        failure = surface["failure_class"]
        assert failure, f"{name} reported no failure class at all"
        assert " " not in failure, (
            f"{name} reported {failure!r}, which is a message rather than a class name"
        )
        assert failure.endswith("Error"), f"{name} reported a class that is not an error"


def test_an_empty_registry_is_the_one_place_they_deliberately_differ(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The asymmetry found while writing the test above, pinned as intended.

    With **no** schema search path the command line short-circuits before the
    pipeline runs: US1 scenario 5 requires it to say the registry is *empty* and
    name the setting that fills it, rather than reporting that the schema does
    not exist — which would send a reader hunting a typo in a name that was never
    the problem.

    So it returns no stage outcomes and no partial results, while the HTTP
    interface runs the pipeline and fails at extract with both. That is not the
    surfaces disagreeing about a failure; it is the command line declining to
    *start* one it knows must fail, which costs nothing and explains more. Pinned
    here so the difference stays a decision.
    """
    from docdoc.cli import main

    monkeypatch.setenv("DOCDOC_SCHEMA_PATHS", "")
    monkeypatch.setenv("DOCDOC_MODEL_ADAPTERS", "echo")

    code = main(["extract", str(FIXTURE), "--schema", SCHEMA, "--json", "--no-store"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert code == 2
    assert "outcomes" not in payload, "nothing ran, so there are no stage outcomes"
    assert payload["error"]["class"] == "SchemaError"
    assert "registry is empty" in captured.err, (
        "the whole point of short-circuiting is the better message; without it "
        "the command line should just run the pipeline like everything else"
    )
    assert "DOCDOC_SCHEMA_PATHS" in captured.err, "and it must name the setting that fills it"
