"""`should_continue`, and the promise that its default changes nothing.

This is the milestone's one deviation from FR-040, recorded in plan.md's
Complexity Tracking with the three alternatives rejected. The parameter exists
because `run()` executes four stages behind one call and offers no interposition
point, so without it the only cancellable moment is *before* a run starts — and
the boundary worth catching is the one before the model call, which is where the
money is (research R4).

Two claims, and the first is the larger one:

**`should_continue=None` produces results identical to before.** Every existing
caller passes it by omission, so "identical" has to mean identical — the same
`processing_id`, the same outcomes, the same everything — rather than "equivalent".

**Returning `False` stops before the next stage.** Completed stages keep their
artifacts, the remaining ones are recorded as skipped rather than omitted, and
there is no `processing_id`, because no terminal artifact was produced. That last
part is why this does not breach Principle III: cancellation produces no identity
at all, so there is no identity under which two different results could ever be
observed.

**It is consulted at boundaries and never inside a stage.** Asserted by counting:
four stages, four consultations, and a fifth would mean something is asking mid-
stage — which is where a cancellation could abandon a provider call that has
already been billed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pymupdf", reason="a real four-stage run is what is being cancelled")

from docdoc.artifacts import FileArtifactStore
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.pipeline import run as run_pipeline
from docdoc.pipeline.result import StageStatus
from docdoc.pipeline.stages import Stage

FIXTURE = Path("tests/fixtures/pdf/digital_invoice.pdf")
SCHEMA = "invoice@1"


def _registry() -> SchemaRegistry:
    return SchemaRegistry.from_paths([Path("schemas")])


def _adapter() -> EchoAdapter:
    return EchoAdapter.from_fixtures("tests/fixtures/echo")


def _run(store: FileArtifactStore | None = None, **extra: object):
    from docdoc.artifacts import NullArtifactStore

    return run_pipeline(
        FIXTURE.read_bytes(),
        schema=SCHEMA,
        registry=_registry(),
        adapter=_adapter(),
        store=store or NullArtifactStore(),
        **extra,  # type: ignore[arg-type]
    )


def _comparable(result) -> dict:  # type: ignore[no-untyped-def]
    """The result, with wall time zeroed.

    `duration_ms` is the one field two identical runs are *expected* to disagree
    on: it is measured, it enters no identity, no artifact, and no verdict
    (FR-060), and asserting on it would be asserting that the machine was equally
    busy twice. Everything else — every value, verdict, location, and identity —
    is compared whole rather than field by field.
    """
    serialised = result.model_dump(mode="json")
    for outcome in serialised["outcomes"]:
        outcome["duration_ms"] = 0
    return dict(serialised)


class _StopBefore:
    """Allows `n` boundaries, then refuses. Counts every consultation."""

    def __init__(self, allow: int) -> None:
        self._allow = allow
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        return self.calls <= self._allow


# -- the default changes nothing ----------------------------------------------


def test_the_default_produces_an_identical_result(tmp_path: Path) -> None:
    """FR-040 as amended: no change in behaviour for existing callers.

    Compared on the serialised whole rather than on a chosen list of fields. A
    comparison of ten fields passes when an eleventh drifts, and the eleventh is
    the one nobody thought of.
    """
    without = _run(FileArtifactStore(tmp_path / "a"))
    explicit_none = _run(FileArtifactStore(tmp_path / "b"), should_continue=None)

    assert without.processing_id == explicit_none.processing_id
    assert _comparable(without) == _comparable(explicit_none)


def test_a_callback_that_always_continues_produces_an_identical_result(
    tmp_path: Path,
) -> None:
    """The other half of "changes nothing": supplying one must not, either.

    A callback that always says yes is the case where the parameter is present
    and inert, and a run that took a different path through the code because a
    callback existed would be a difference the default cannot hide.
    """
    without = _run(FileArtifactStore(tmp_path / "a"))
    always = _run(FileArtifactStore(tmp_path / "b"), should_continue=lambda: True)

    assert _comparable(without) == _comparable(always)


# -- stopping ------------------------------------------------------------------


def test_stopping_before_the_first_stage_runs_nothing() -> None:
    """The cheapest cancellation, and the one a queued run gets."""
    stopper = _StopBefore(allow=0)

    result = _run(should_continue=stopper)

    assert result.processing_id is None
    assert result.failed_stage is None, "a cancellation is not a failure"
    assert all(o.status is StageStatus.SKIPPED for o in result.outcomes)
    assert result.executed_count == 0


def test_stopping_before_extract_keeps_the_parse_and_pays_for_no_model_call(
    tmp_path: Path,
) -> None:
    """The boundary worth catching, because the next stage is the billable one."""
    stopper = _StopBefore(allow=1)

    result = _run(FileArtifactStore(tmp_path), should_continue=stopper)

    assert result.processing_id is None
    assert result.failed_stage is None
    assert result.outcome_for(Stage.PARSE).status is StageStatus.EXECUTED  # type: ignore[union-attr]
    for stage in (Stage.EXTRACT, Stage.GROUND, Stage.VALIDATE):
        assert result.outcome_for(stage).status is StageStatus.SKIPPED  # type: ignore[union-attr]
    assert result.extraction is None


def test_the_completed_stages_artifacts_are_still_written(tmp_path: Path) -> None:
    """Cancelling costs the work not yet done, and never the work already done.

    A cancelled run that discarded its parse would make cancelling *expensive* —
    the next attempt would re-parse — which is the opposite of what the feature is
    for.
    """
    store = FileArtifactStore(tmp_path)
    _run(store, should_continue=_StopBefore(allow=1))

    stored = list((tmp_path / "artifacts").rglob("*.json"))
    assert stored, "the parse artifact was discarded when the run was cancelled"

    # And it is reused: a full run afterwards executes three stages, not four.
    resumed = _run(store)
    assert resumed.outcome_for(Stage.PARSE).status is StageStatus.REUSED  # type: ignore[union-attr]
    assert resumed.processing_id is not None


def test_a_cancelled_run_yields_no_processing_id_at_any_boundary(
    tmp_path: Path,
) -> None:
    """Principle III's answer, asserted at every stop.

    A cancelled run produces no terminal artifact, so there is no identity under
    which two different results could ever be observed — which is why an
    input-sensitive `run()` is still deterministic. If any boundary produced one,
    that argument would be false.
    """
    for allow in range(4):
        result = _run(FileArtifactStore(tmp_path / str(allow)), should_continue=_StopBefore(allow))
        assert result.processing_id is None, f"stopping after {allow} stages named a result"


def test_allowing_every_boundary_completes_the_run(tmp_path: Path) -> None:
    """Guards the four tests above: a callback that broke runs would satisfy them."""
    stopper = _StopBefore(allow=99)

    result = _run(FileArtifactStore(tmp_path), should_continue=stopper)

    assert result.processing_id is not None
    assert result.executed_count == 4


# -- boundaries only -----------------------------------------------------------


def test_it_is_consulted_exactly_once_per_stage(tmp_path: Path) -> None:
    """FR-028's "at stage boundaries", counted.

    Four stages, four consultations. More would mean something asks *inside* a
    stage, which is where a cancellation could abandon a provider call that has
    already been billed — the thing FR-029 says explicitly does not happen.
    """
    stopper = _StopBefore(allow=99)

    _run(FileArtifactStore(tmp_path), should_continue=stopper)

    assert stopper.calls == 4, (
        f"consulted {stopper.calls} times for a four-stage run. At the boundaries "
        f"and nowhere else means exactly one per stage"
    )
