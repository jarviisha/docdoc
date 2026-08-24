"""ADR-0003's promise, measured rather than believed.

Every claim in this file is **counted, never timed**. A criterion a slow machine
can fail is not a criterion, and more importantly a stale cache returns a result
that looks exactly like a correct one — so "it was faster" is not evidence that
anything was reused, and "it was slower" is not evidence that it was not.

The counters come from the run itself (FR-047, SC-004), which is the same source
a user reads. A test that instrumented something the product does not expose
would be asserting a property nobody can check in production.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from docdoc.artifacts import FileArtifactStore
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.pipeline import Stage, run

FIXTURE = Path("tests/fixtures/pdf/digital_invoice.pdf")
SCHEMA = "invoice@1"


@pytest.fixture
def registry() -> SchemaRegistry:
    return SchemaRegistry.from_paths([Path("schemas")])


@pytest.fixture
def adapter() -> EchoAdapter:
    return EchoAdapter.from_fixtures("tests/fixtures/echo")


@pytest.fixture
def store(tmp_path: Path) -> FileArtifactStore:
    return FileArtifactStore(tmp_path)


@pytest.fixture
def source() -> bytes:
    return FIXTURE.read_bytes()


def statuses(result: Any) -> dict[str, str]:
    """What each stage did, as the run itself reports it."""
    return {outcome.stage.value: outcome.status.value for outcome in result.outcomes}


# -- SC-002 ------------------------------------------------------------------


def test_a_second_identical_run_executes_zero_stages(
    source: bytes, registry: SchemaRegistry, adapter: EchoAdapter, store: FileArtifactStore
) -> None:
    run(source, schema=SCHEMA, registry=registry, adapter=adapter, store=store)
    second = run(source, schema=SCHEMA, registry=registry, adapter=adapter, store=store)

    assert second.executed_count == 0
    assert second.reused_count == 4
    assert set(statuses(second).values()) == {"reused"}


def test_a_reused_run_differs_only_in_duration_and_status(
    source: bytes, registry: SchemaRegistry, adapter: EchoAdapter, store: FileArtifactStore
) -> None:
    """SC-002's exception clause, asserted as written.

    "Byte-identical" would be false on a field the run is *required* to record,
    and a criterion that is false as written gets satisfied by deleting the
    inconvenient field. So the durations and the executed/reused statuses are
    named as the only permitted differences, and everything else is compared.
    """
    first = run(source, schema=SCHEMA, registry=registry, adapter=adapter, store=store)
    second = run(source, schema=SCHEMA, registry=registry, adapter=adapter, store=store)

    assert first.processing_id == second.processing_id
    assert first.document == second.document
    assert first.extraction == second.extraction
    assert first.grounding == second.grounding
    assert first.validation == second.validation
    assert first.provenance == second.provenance

    # And the two fields that necessarily differ, differing.
    assert statuses(first) != statuses(second)


def test_a_reused_extraction_keeps_its_python_types(
    source: bytes, registry: SchemaRegistry, adapter: EchoAdapter, store: FileArtifactStore
) -> None:
    """The quiet half of "indistinguishable from the executed one" (FR-012).

    JSON has no decimal and no date, so a stored ``Decimal("1240.00")`` comes
    back the string ``"1240.00"`` unless something retypes it. Left alone,
    validation would compare a total against a sum of *strings* and reach a
    different verdict than the run that produced the artifact — silently, and
    looking exactly like a model that cannot read numbers.
    """
    first = run(source, schema=SCHEMA, registry=registry, adapter=adapter, store=store)
    second = run(source, schema=SCHEMA, registry=registry, adapter=adapter, store=store)

    assert second.reused_count == 4
    assert first.extraction is not None
    assert second.extraction is not None

    total_before = first.extraction.value_at("total")
    total_after = second.extraction.value_at("total")
    assert type(total_after.value) is type(total_before.value)
    assert total_after.value == total_before.value
    assert first.validation is not None
    assert second.validation is not None
    assert first.validation.verdict is second.validation.verdict


# -- SC-003 ------------------------------------------------------------------


def test_a_schema_change_reuses_the_parse_and_recomputes_the_rest(
    source: bytes, registry: SchemaRegistry, adapter: EchoAdapter, store: FileArtifactStore
) -> None:
    run(source, schema=SCHEMA, registry=registry, adapter=adapter, store=store)
    second = run(source, schema="invoice@2", registry=registry, adapter=adapter, store=store)

    assert statuses(second)["parse"] == "reused", "the expensive stage must not repeat"
    assert statuses(second)["extract"] == "executed"
    assert statuses(second)["ground"] == "executed"
    assert statuses(second)["validate"] == "executed"


def test_a_model_change_reuses_the_parse_and_recomputes_the_rest(
    source: bytes, registry: SchemaRegistry, store: FileArtifactStore
) -> None:
    """ADR-0003's headline consequence: changing the LLM must not reuse extraction."""
    first_adapter = EchoAdapter.from_fixtures("tests/fixtures/echo")
    run(source, schema=SCHEMA, registry=registry, adapter=first_adapter, store=store)

    moved = EchoAdapter.from_fixtures("tests/fixtures/echo")
    moved._model_version = "2"
    second = run(source, schema=SCHEMA, registry=registry, adapter=moved, store=store)

    assert statuses(second)["parse"] == "reused"
    assert statuses(second)["extract"] == "executed"


def test_reuse_is_per_stage_in_both_directions(
    source: bytes, registry: SchemaRegistry, adapter: EchoAdapter, store: FileArtifactStore
) -> None:
    """FR-013 — a changed stage invalidates itself and everything downstream, and
    nothing upstream. Asserted by going back: after running invoice@2, running
    invoice@1 again reuses all four, because nothing was deleted."""
    run(source, schema=SCHEMA, registry=registry, adapter=adapter, store=store)
    run(source, schema="invoice@2", registry=registry, adapter=adapter, store=store)
    back = run(source, schema=SCHEMA, registry=registry, adapter=adapter, store=store)

    assert back.executed_count == 0, (
        "the store is append-only; a later run under a different schema must not "
        "have invalidated an earlier run's artifacts"
    )


# -- FR-059 ------------------------------------------------------------------


def test_a_fully_reused_run_needs_no_credentials(
    source: bytes,
    registry: SchemaRegistry,
    adapter: EchoAdapter,
    store: FileArtifactStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Computing an identity must never need a provider — only executing may.

    The second run uses an adapter that raises if it is called at all, which is
    the only way to prove the model was not reached: a counter could be wrong,
    and a timing proves nothing.
    """
    run(source, schema=SCHEMA, registry=registry, adapter=adapter, store=store)

    class Unreachable(EchoAdapter):
        def complete(self, request: Any, options: Any) -> Any:
            raise AssertionError("a fully reused run called the model")

    second = run(
        source,
        schema=SCHEMA,
        registry=registry,
        adapter=Unreachable.from_fixtures("tests/fixtures/echo"),
        store=store,
    )
    assert second.executed_count == 0


# -- FR-061 ------------------------------------------------------------------


def test_a_cached_parse_still_records_the_text_layer_verdict(
    source: bytes, registry: SchemaRegistry, adapter: EchoAdapter, store: FileArtifactStore
) -> None:
    """Principle V's decision must stay inspectable on a cache hit.

    The routing verdict is computed on every run — it is what selects the parser,
    and the parser is what the identity folds — so a reused document can never
    arrive carrying a routing decision this run did not make.
    """
    first = run(source, schema=SCHEMA, registry=registry, adapter=adapter, store=store)
    second = run(source, schema=SCHEMA, registry=registry, adapter=adapter, store=store)

    assert statuses(second)["parse"] == "reused"
    assert second.document is not None
    assert second.document.provenance.text_layer is not None
    assert first.document is not None
    assert second.document.provenance.text_layer == first.document.provenance.text_layer


# -- SC-005 ------------------------------------------------------------------


def test_a_corrupt_artifact_raises_rather_than_being_returned(
    source: bytes,
    registry: SchemaRegistry,
    adapter: EchoAdapter,
    store: FileArtifactStore,
    tmp_path: Path,
) -> None:
    """FR-014 — recomputing over corruption hides a failing disk behind a slow run."""
    from docdoc.artifacts import ArtifactError

    run(source, schema=SCHEMA, registry=registry, adapter=adapter, store=store)

    entry = next((tmp_path / "artifacts").glob("*/*.json"))
    stored = json.loads(entry.read_text())
    stored["payload"]["artifact_id"] = "sha256:" + "f" * 64
    entry.write_text(json.dumps(stored))

    with pytest.raises(ArtifactError):
        run(source, schema=SCHEMA, registry=registry, adapter=adapter, store=store)


def test_an_incompatible_format_version_misses_rather_than_raising(
    source: bytes,
    registry: SchemaRegistry,
    adapter: EchoAdapter,
    store: FileArtifactStore,
    tmp_path: Path,
) -> None:
    """FR-015 — a version bump is an expected event on upgrade.

    Making it fatal would mean every run fails after an upgrade until somebody
    clears a directory by hand, which is why this is a miss and the corruption
    above is an error.
    """
    run(source, schema=SCHEMA, registry=registry, adapter=adapter, store=store)

    for entry in (tmp_path / "artifacts").glob("*/*.json"):
        stored = json.loads(entry.read_text())
        stored["artifact_format_version"] = 99
        # `content_id` covers the payload only, so bumping the envelope's version
        # leaves the integrity check intact — which is the situation being tested.
        entry.write_text(json.dumps(stored))

    second = run(source, schema=SCHEMA, registry=registry, adapter=adapter, store=store)
    assert second.executed_count == 4, "every stage should have missed and re-run"
    assert second.failed_stage is None


# -- FR-063 ------------------------------------------------------------------


def test_an_unwritable_store_degrades_rather_than_failing_the_run(
    source: bytes, registry: SchemaRegistry, adapter: EchoAdapter
) -> None:
    """The result is already computed and correct; losing a cache entry is no
    reason to lose it."""
    result = run(
        source,
        schema=SCHEMA,
        registry=registry,
        adapter=adapter,
        store=FileArtifactStore("/proc/definitely-not-writable"),
    )
    assert result.failed_stage is None
    assert result.validation is not None
    assert result.executed_count == 4, "a stage that ran must be reported as executed"


def test_a_run_with_no_store_produces_the_same_result_as_one_with(
    source: bytes, registry: SchemaRegistry, adapter: EchoAdapter, store: FileArtifactStore
) -> None:
    """FR-017 — nothing about correctness may depend on the store being present."""
    stored = run(source, schema=SCHEMA, registry=registry, adapter=adapter, store=store)
    storeless = run(source, schema=SCHEMA, registry=registry, adapter=adapter)

    assert stored.processing_id == storeless.processing_id
    assert stored.validation == storeless.validation
    assert stored.extraction == storeless.extraction


# -- FR-064 ------------------------------------------------------------------


def test_verify_executes_every_stage_and_still_writes(
    source: bytes, registry: SchemaRegistry, adapter: EchoAdapter, store: FileArtifactStore
) -> None:
    """Without it, a drifted processor is only ever caught by a cache miss that
    happens not to occur."""
    run(source, schema=SCHEMA, registry=registry, adapter=adapter, store=store)
    verified = run(
        source, schema=SCHEMA, registry=registry, adapter=adapter, store=store, verify=True
    )

    assert verified.executed_count == 4
    assert verified.reused_count == 0
    assert verified.failed_stage is None, "identical output must be a silent no-op"


def test_verify_surfaces_a_processor_whose_output_moved(
    source: bytes,
    registry: SchemaRegistry,
    adapter: EchoAdapter,
    store: FileArtifactStore,
    tmp_path: Path,
) -> None:
    """FR-062's conflicting write — the one symptom the system *can* detect.

    Simulated by editing a stored payload without moving the identity it is filed
    under, which is exactly what a processor whose output changed while its
    version did not would produce on the next run.
    """
    from docdoc.artifacts import ArtifactError
    from docdoc.artifacts.envelope import content_id_of

    run(source, schema=SCHEMA, registry=registry, adapter=adapter, store=store)

    for entry in (tmp_path / "artifacts").glob("*/*.json"):
        stored = json.loads(entry.read_text())
        if stored["stage"] != Stage.GROUND.value:
            continue
        stored["payload"]["counts"]["exact"] += 1
        # Re-sign it, so this is a *divergent* stored result rather than a
        # corrupt one — the integrity check must not be what catches this.
        stored["content_id"] = content_id_of(stored["payload"])
        entry.write_text(json.dumps(stored))
        break

    with pytest.raises(ArtifactError, match="different result"):
        run(source, schema=SCHEMA, registry=registry, adapter=adapter, store=store, verify=True)


# -- FR-019 ------------------------------------------------------------------


def test_clearing_one_stage_keeps_the_expensive_one(
    source: bytes,
    registry: SchemaRegistry,
    adapter: EchoAdapter,
    store: FileArtifactStore,
) -> None:
    """What makes a suspect result reproducible without discarding the parses."""
    run(source, schema=SCHEMA, registry=registry, adapter=adapter, store=store)

    removed = store.clear(stage=Stage.VALIDATE.value)
    assert removed == 1

    after = run(source, schema=SCHEMA, registry=registry, adapter=adapter, store=store)
    assert statuses(after)["parse"] == "reused"
    assert statuses(after)["extract"] == "reused"
    assert statuses(after)["ground"] == "reused"
    assert statuses(after)["validate"] == "executed"
