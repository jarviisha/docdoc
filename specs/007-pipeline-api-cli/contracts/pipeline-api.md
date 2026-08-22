# Public API Contract: Pipeline and Artifact Store

**Feature**: `007-pipeline-api-cli` | **Date**: 2026-08-22

The Python surface of `docdoc.pipeline` and `docdoc.artifacts`. Both are public; the CLI and the
HTTP interface are two callers of this, not two privileged ones.

## 1. Entry point

```python
from docdoc.pipeline import run

result = run(
    source,                    # bytes | SourceFile
    schema="invoice@1",        # the schema identity, as data
    registry=registry,         # SchemaRegistry
    adapter=adapter,           # ModelAdapter; configuration picks it
    store=store,               # ArtifactStore; defaults to NullArtifactStore
    limits=None,               # ingest.Limits; defaults as ingest defaults
    options=None,              # parse options, folded into document_id
    request_id=None,           # correlation only; never enters an identity
)
```

Returns one `PipelineResult`. Raises only `PipelineError` and the typed errors of the layers below;
never an untyped exception (FR-051).

Synchronous, in-process, and usable with no store, no service, no database, and no network beyond
whatever the configured adapter and parser themselves need (FR-008).

## 2. The stages are four, named, and versioned

`Stage.PARSE`, `Stage.EXTRACT`, `Stage.GROUND`, `Stage.VALIDATE`, executed in that order. Each
carries a stable `processor_id` and a `processor_version` that MUST move whenever its output moves
for fixed inputs — a review obligation ADR-0003 already places on every processor, inherited here
unchanged.

The pipeline accepts no stage graph, no conditional stage, and no user-supplied stage. It is not a
DAG engine and MUST NOT become one (Principle XI).

The pipeline itself is a processor: `pipeline_id` and `pipeline_version`, folded into the terminal
artifact id and into nothing else.

## 3. Reuse

Before each stage the pipeline computes that stage's artifact id from its recorded inputs and asks
the store. A hit is returned instead of executing (FR-012); the result MUST be indistinguishable from
the executed one.

The parse stage's id is computed after routing and parser selection and before the parser runs
(research R2), so a cached parse pays the local text-layer assessment and skips the parser —
including the billable cloud call.

Reuse is per-stage and partial (FR-013). Editing a prompt reuses the parse. Editing a rule reuses the
parse. Editing nothing reuses everything.

`PipelineResult.outcomes` states `EXECUTED` or `REUSED` per stage, so the cost of a run is readable
off the run (FR-004, SC-004).

## 4. Failure

A stage failure ends the run and returns everything the preceding stages produced (FR-004), with
`failed_stage` set and `failure_class` carrying the error's **class name** — never its message, which
can quote the document.

A failure is attributed to the stage whose layer declared the error, not to the stage that was
running when it surfaced (FR-005). A grounding error raised during validation is a grounding error.

Retries are permitted for provider and network calls only, and that policy already lives in the
layers that make those calls; the pipeline adds none of its own (FR-010).

## 5. The store

```python
from docdoc.artifacts import FileArtifactStore, NullArtifactStore

store = FileArtifactStore(root)      # content-addressed, append-only
store = NullArtifactStore()          # the default: every get misses
```

```text
get(artifact_id, *, model, format_version) -> model | None
put(artifact_id, payload, *, stage, input_artifact_id, processor_id,
    processor_version, options_hash, format_version) -> None
derivation(artifact_id) -> DerivationRecord | None
clear(*, stage=None) -> int
```

The store is generic over what it stores: the caller names the model (research R3). It imports no
layer above the kernel.

There is **no default root**. `NullArtifactStore` is the default because the artifacts hold extracted
values and the blobs hold whole documents, and where those land is an operator's decision (FR-017,
FR-044).

`clear()` takes all of it or one stage, and nothing else (FR-019). It is the supported recovery path
from a failed integrity check: cleared deliberately by a human, never overwritten by the run that
found the fault.

Artifacts are immutable and the store is append-only — enforced by FR-062, not merely intended.
Garbage collection is out of scope; what this milestone owes a future collector is that every
artifact records its stage and its input identity, so reachability is computable by walking the store
alone (FR-022).

`run(..., verify=True)` executes every stage and still writes, so FR-062's conflict check fires on
results that would otherwise have been read back (FR-064). This is the only way a processor whose
output drifted without a version bump becomes visible on demand rather than by luck.

## 6. Integrity, and which misses are errors

| Condition | Behaviour |
|---|---|
| Nothing stored under the id | miss; execute |
| Incompatible `artifact_format_version` | miss; execute; log the mismatch (FR-015) |
| `content_id` does not match the payload | **`ArtifactError`**; do not execute (FR-014) |
| `put` for an id already present, same content | no-op (FR-062) |
| `put` for an id already present, **different** content | **`ArtifactError`** naming both; never overwrite (FR-062) |
| Concurrent `put` of identical content | both succeed; no lock required (FR-062) |
| Root unwritable, absent, or full | run without reuse; log once; never fail the run (FR-063) |
| `put` fails on a run whose stages succeeded | log; report the stage `EXECUTED`; return the result (FR-063) |

The third row is the one that matters: an `artifact_id` is a hash of a stage's *inputs*, so it cannot
detect a corrupted payload. `content_id` — `content_id_for(canonical_json(payload))` — can, and does.

A partially written artifact is never readable as a complete one (FR-016).

## 7. Explaining an identity

```python
store.derivation(artifact_id)   # -> DerivationRecord | None
```

Names the stage, the input artifact id, the processor and its version, and the **names** of the
inputs folded into the options hash. Carries no payload, no extracted value, no prompt body, no
credential (FR-025). Walking `input_artifact_id` reaches the source `blob_id`.

A derivation is read from the record a write left behind, so an id produced by a run with no store
has none, and `derivation()` returns `None` rather than reconstructing one (FR-023).

This exists because ADR-0003 accepted unreadable cache keys on the condition that something would
explain them.

## 8. Observability

One `pipeline.stage` event per stage, carrying `request_id`, `processing_id`, `step_id`, duration,
outcome, and `reused`. Where a provider answered, the provider, model, and token usage come from the
layer that called it — the pipeline does not re-derive them.

Content, values, credentials, and prompt bodies never appear (FR-043). The existing per-layer
`observe.py` modules keep emitting their own events; this adds correlation, not a second system.

Counters: stages executed and stages reused. Observability changes no result, no identity, and no
verdict (FR-049).

## 9. What this layer will not do

- **Reimplement a stage.** It sequences them. Every rule about what a stage *means* stays in that
  stage's layer (FR-003).
- **Change a result to make it cacheable.** A cached result that differs from a computed one is a
  bug in this milestone, not a trade-off.
- **Fall back.** A store miss is not a fallback; a corrupted artifact is not recovered from.
- **Persist a run history, a job row, or a queue entry.** A job is a view over the store.
- **Decide what happens next.** Routing on a verdict, review, and acceptance thresholds are not here
  and are not anywhere yet.
