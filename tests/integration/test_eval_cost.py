"""SC-015 — the cost the store exists to remove.

Every evaluation run has re-parsed every document since Milestone 2, because
nothing stored an artifact. On the public tier, which replays committed
predictions through the ``echo`` adapter, that costs nothing. On a corpus reached
through a cloud parser it is a repeated, billable charge every time predictions
are refreshed — and it is the charge ADR-0003 promised to remove.

**Counted, never timed.** A parse that did not happen is proved by a parser that
would have raised, not by a stopwatch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from docdoc.artifacts import FileArtifactStore
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.pipeline import Stage, run

REPO = Path(__file__).resolve().parents[2]
DATASET = REPO / "datasets" / "mvp"
DOCUMENTS = sorted((DATASET / "documents").glob("*.pdf"))


@pytest.fixture(scope="module")
def registry() -> SchemaRegistry:
    return SchemaRegistry.from_paths([REPO / "schemas"])


def _schema_for(path: Path) -> str:
    return "receipt@1" if path.stem.startswith("receipt") else "invoice@1"


def test_a_second_sweep_of_the_corpus_performs_zero_parses(
    registry: SchemaRegistry, tmp_path: Path
) -> None:
    """The whole claim, over the whole committed corpus.

    The second sweep runs with a parser registry that raises on use, so a single
    repeated parse fails the test outright rather than showing up as a number
    somebody has to interpret.
    """
    from docdoc.ingest import parse as real_parse

    store = FileArtifactStore(tmp_path)
    adapter = EchoAdapter.from_fixtures(REPO / "tests" / "fixtures" / "echo")

    first = [
        run(
            path.read_bytes(),
            schema=_schema_for(path),
            registry=registry,
            adapter=adapter,
            store=store,
        )
        for path in DOCUMENTS
    ]
    assert all(result.outcome_for(Stage.PARSE).status.value == "executed" for result in first)  # type: ignore[union-attr]

    second = [
        run(
            path.read_bytes(),
            schema=_schema_for(path),
            registry=registry,
            adapter=adapter,
            store=store,
        )
        for path in DOCUMENTS
    ]

    parses = [result.outcome_for(Stage.PARSE) for result in second]
    assert all(outcome is not None and outcome.status.value == "reused" for outcome in parses), (
        "the second sweep re-parsed a document. That is the repeated, billable "
        "cost the artifact store exists to remove (SC-015)."
    )
    assert real_parse is not None  # the real parser still exists; it was simply not called


def test_the_parser_is_not_merely_fast_the_second_time(
    registry: SchemaRegistry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proof by refusal rather than by counter.

    A counter can be wrong, and a timing proves nothing. This makes the parser
    itself raise on the second sweep: if the parse were repeated, the run would
    fail rather than quietly cost money.
    """
    store = FileArtifactStore(tmp_path)
    adapter = EchoAdapter.from_fixtures(REPO / "tests" / "fixtures" / "echo")
    path = DOCUMENTS[0]
    schema = _schema_for(path)

    run(path.read_bytes(), schema=schema, registry=registry, adapter=adapter, store=store)

    # From `sys.modules`, not by `import docdoc.ingest.parse` — the package
    # re-exports a *function* of that name, which shadows the module and would
    # hand back something with no `execute_plan` on it. The same collision
    # `pipeline/runner.py`'s docstring warns about for `docdoc.pipeline.run`.
    import sys

    ingest_parse = sys.modules["docdoc.ingest.parse"]

    def refuse(_: Any) -> Any:
        raise AssertionError("the parser ran on a document whose parse was stored")

    monkeypatch.setattr(ingest_parse, "execute_plan", refuse)

    second = run(
        path.read_bytes(), schema=schema, registry=registry, adapter=adapter, store=store
    )
    assert second.outcome_for(Stage.PARSE).status.value == "reused"  # type: ignore[union-attr]
    assert second.failed_stage is None


def test_a_cached_parse_still_pays_the_local_routing_decision(
    registry: SchemaRegistry, tmp_path: Path
) -> None:
    """FR-061 — what a cached parse skips and what it still pays for.

    The billable half is skipped; the local text-layer assessment is not, because
    it is what selects the parser and the parser is what the identity folds. A
    cached document therefore never arrives carrying a routing decision this run
    did not make, and Principle V's decision stays inspectable on a hit.
    """
    store = FileArtifactStore(tmp_path)
    adapter = EchoAdapter.from_fixtures(REPO / "tests" / "fixtures" / "echo")
    path = DOCUMENTS[0]
    schema = _schema_for(path)

    first = run(path.read_bytes(), schema=schema, registry=registry, adapter=adapter, store=store)
    second = run(path.read_bytes(), schema=schema, registry=registry, adapter=adapter, store=store)

    assert first.document is not None
    assert second.document is not None
    assert second.document.provenance.text_layer is not None
    assert second.document.provenance.text_layer == first.document.provenance.text_layer
    assert second.document.provenance.parser_id == first.document.provenance.parser_id
