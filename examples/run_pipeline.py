"""The whole pipeline in one call, offline, with and without a store.

Runs standalone with no credentials, no network, no database, and no object
storage:

    uv run python examples/run_pipeline.py

Two things this shows that the earlier examples cannot, because until Milestone 7
neither existed:

**One call, four stages.** ``parse -> extract -> ground -> validate`` is a thing
now rather than a sequence you write out. The four earlier examples each drive
one stage and are still the right way to understand what each stage *does*; this
is the way to actually process a document.

**What a second run costs.** With a store configured, the second run of the same
document executes zero stages and returns a result equal to the first in every
field but the durations and the executed/reused statuses. That is ADR-0003's
promise, and it is the reason the store exists.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from docdoc.artifacts import FileArtifactStore
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.pipeline import PipelineResult, run

REPO = Path(__file__).resolve().parent.parent
DOCUMENT = REPO / "tests" / "fixtures" / "pdf" / "digital_invoice.pdf"
SCHEMA = "invoice@1"


def show(title: str, result: PipelineResult) -> None:
    """What the run produced and what it cost, read off the run itself."""
    cost = result.cost_summary()
    print(f"\n{title}")
    print(f"  verdict      {result.validation.verdict if result.validation else '-'}")
    print(f"  processing   {result.processing_id}")
    print(f"  cost         {cost['executed']} executed, {cost['reused']} reused")
    for stage, status in cost["stages"].items():
        print(f"    {stage:<9} {status}")


def located(result: PipelineResult) -> None:
    """The Definition of Done: every value, and where it physically came from."""
    if result.grounding is None:
        return
    print("\n  where the values came from")
    for path, outcome in sorted(result.grounding.outcomes.items()):
        if outcome.geometry:
            box = outcome.geometry[0]
            where = (
                f"page {box.page_index}  "
                f"[{box.bbox.x0:.3f} {box.bbox.y0:.3f} {box.bbox.x1:.3f} {box.bbox.y1:.3f}]"
            )
        else:
            # Ungrounded values stay machine-distinguishable at every layer. A
            # value docdoc could not locate never gets a rectangle it did not earn.
            where = f"({outcome.status})"
        print(f"    {path:<28} {where}")


def main() -> int:
    registry = SchemaRegistry.from_paths([REPO / "schemas"])

    # The offline adapter, which answers from committed fixtures. A real
    # deployment calls `default_adapter()` and names no provider in code.
    adapter = EchoAdapter.from_fixtures(REPO / "tests" / "fixtures" / "echo")
    source = DOCUMENT.read_bytes()

    # 1. No store. Every stage executes; the result is complete and correct.
    #    This is the default, and nothing about correctness depends on a store.
    first = run(source, schema=SCHEMA, registry=registry, adapter=adapter)
    show("No store - everything executes", first)
    located(first)

    # 2. With a store, twice. The second run is a lookup.
    root = Path(tempfile.mkdtemp(prefix="docdoc-example-"))
    store = FileArtifactStore(root)

    cold = run(source, schema=SCHEMA, registry=registry, adapter=adapter, store=store)
    warm = run(source, schema=SCHEMA, registry=registry, adapter=adapter, store=store)
    show(f"With a store at {root} - first run", cold)
    show("With a store - second run", warm)

    print("\n  the second run's result equals the first:")
    for name in ("extraction", "grounding", "validation"):
        equal = getattr(cold, name) == getattr(warm, name)
        print(f"    {name:<12} {equal}")

    # 3. Change the schema. The parse is reused; everything downstream is not.
    #    This is the whole point of a per-stage chain rather than one flat key.
    other = run(source, schema="invoice@2", registry=registry, adapter=adapter, store=store)
    show("A different schema - the parse is still reused", other)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
