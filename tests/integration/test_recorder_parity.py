"""SC-014 — the stage sequence exists in exactly one place, proved by the numbers.

The recorder used to sequence parse → extract → ground → validate itself. It now
calls :mod:`docdoc.pipeline`. The way to know that rewrite changed nothing is not
to read both versions: it is to regenerate the committed public-tier prediction
set and compare it, byte for byte, with the one on disk.

That comparison is stronger than it looks. Every prediction carries its
extraction, its grounding, its validation, and the artifact ids of all three — so
a single reordered stage, a dropped provenance field, or a differently-derived
identity moves a byte and fails this test. It is also the guarantee behind the
metrics check: a prediction set that has not moved cannot have moved a metric.

**The recording is reproduced the way ``make_dataset.py`` does it**, including its
per-document adapter. The dataset has four documents across two schemas, and the
echo adapter keys its answers by *schema* — so a single adapter would give both
invoices the same answer and the set would measure one document twice. Importing
that script's own constants rather than restating them is the point: a test that
kept its own copy of the canned answers would pass while describing a dataset
nobody has.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from docdoc.evaluation import PredictionSet, load_golden_set, schema_facts
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.recording import (
    RECORDER_ID,
    RECORDER_VERSION,
    record_predictions,
    write_prediction_set,
)

REPO = Path(__file__).resolve().parents[2]
DATASET = REPO / "datasets" / "mvp"
COMMITTED = DATASET / "predictions"


def _make_dataset() -> Any:
    """Load ``datasets/mvp/make_dataset.py`` as a module.

    It is a script rather than a package, and it holds the canned answers and the
    per-document mapping this test has to reproduce exactly.
    """
    spec = importlib.util.spec_from_file_location(
        "docdoc_make_dataset", DATASET / "make_dataset.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def registry() -> SchemaRegistry:
    return SchemaRegistry.from_paths([REPO / "schemas"])


@pytest.fixture(scope="module")
def regenerated(registry: SchemaRegistry) -> PredictionSet:
    """The public tier, recorded again by today's code."""
    from docdoc.ingest import parse

    dataset = _make_dataset()
    facts = schema_facts([registry.describe(name) for name in registry.identities()])
    golden = load_golden_set(DATASET / "manifest.json", facts=facts)

    adapters = {
        name: EchoAdapter.returning(dataset.SCHEMAS[name], dataset.RESPONSES[name])
        for name in dataset.PAGES
    }
    documents = {
        name: parse((DATASET / "documents" / f"{name}.pdf").read_bytes()) for name in dataset.PAGES
    }

    recorded: dict[str, Any] = {}
    for name in dataset.PAGES:
        one = golden.model_copy(
            update={
                "documents": tuple(d for d in golden.documents if d.document_id == name),
                "labels": {name: golden.labels_for(name)},
            }
        )
        result = record_predictions(
            one,
            adapter=adapters[name],
            registry=registry,
            documents={name: documents[name]},
        )
        recorded.update(result.predictions)

    return PredictionSet(
        predictions=recorded,
        recorder_id=RECORDER_ID,
        recorder_version=RECORDER_VERSION,
    )


def test_regenerating_the_public_tier_is_byte_identical(
    regenerated: PredictionSet, tmp_path: Path
) -> None:
    """FR-009 and SC-014, asserted the only way that means anything.

    If the pipeline reimplemented any stage's behaviour — reordered them, passed
    a different option, derived an identity differently — a byte would move here.
    """
    write_prediction_set(regenerated, tmp_path)

    committed = sorted(p.name for p in COMMITTED.glob("*.json"))
    produced = sorted(p.name for p in tmp_path.glob("*.json"))
    assert produced == committed

    for name in committed:
        expected = (COMMITTED / name).read_text(encoding="utf-8")
        actual = (tmp_path / name).read_text(encoding="utf-8")
        assert actual == expected, (
            f"{name} changed when the recorder was rewritten to call the pipeline. "
            "Either a stage's behaviour moved, or the pipeline is not sequencing "
            "them the way the recorder did (FR-003, FR-009)."
        )


def test_the_artifact_identities_did_not_move(regenerated: PredictionSet) -> None:
    """The half of the comparison worth naming separately.

    A prediction's *values* could match while its identities moved — a changed
    options hash, a bumped processor version — and that would silently invalidate
    every stored artifact in every deployment. Called out here so a failure says
    which of the two kinds of change happened.
    """
    for document_id, prediction in regenerated.predictions.items():
        committed = json.loads(
            (COMMITTED / f"{document_id.replace(':', '_')}.json").read_text(encoding="utf-8")
        )
        for stage in ("extraction", "grounding", "validation"):
            produced = getattr(prediction, stage)
            if produced is None:
                assert committed[stage] is None
                continue
            assert produced.artifact_id == committed[stage]["artifact_id"], (
                f"{document_id}'s {stage} identity moved"
            )


def test_the_recorder_never_consults_a_store(
    registry: SchemaRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A committed prediction set is always the product of full execution.

    This is what keeps a stale artifact from quietly moving a published metric,
    and it is why the store can be a cost optimisation without becoming an input
    to the numbers of record.

    Asserted at the call rather than by counting stages: the recorder hands the
    pipeline an already-parsed document, which the pipeline correctly reports as
    ``reused`` — work this run did not do — so a counter cannot tell that apart
    from a store hit. What must be true is that no store is passed at all.
    """
    import docdoc.pipeline
    from docdoc.artifacts import NullArtifactStore

    dataset = _make_dataset()
    facts = schema_facts([registry.describe(name) for name in registry.identities()])
    golden = load_golden_set(DATASET / "manifest.json", facts=facts)

    seen: list[Any] = []
    # `_record_one` does `from docdoc.pipeline import run` at call time, so the
    # package attribute is the one it will pick up.
    real_run = docdoc.pipeline.run

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("store"))
        return real_run(*args, **kwargs)

    monkeypatch.setattr("docdoc.pipeline.run", spy)

    name = next(iter(dataset.PAGES))
    one = golden.model_copy(
        update={
            "documents": tuple(d for d in golden.documents if d.document_id == name),
            "labels": {name: golden.labels_for(name)},
        }
    )
    record_predictions(
        one,
        adapter=EchoAdapter.returning(dataset.SCHEMAS[name], dataset.RESPONSES[name]),
        registry=registry,
        root=DATASET,
    )

    assert seen, "the recorder did not call the pipeline at all"
    for store in seen:
        assert store is None or isinstance(store, NullArtifactStore), (
            "the recorder passed a store to the pipeline. A committed prediction "
            "set must be the product of full execution, or a stale artifact can "
            "move a published metric."
        )
