# The pipeline

Four stages, in one place, for one document.

```text
parse → extract → ground → validate
```

Until Milestone 7 that sequence was written out inside one private function of the recording layer —
a package that exists to serve evaluation, so the order in which docdoc processes a document lived in
the module least likely to be read by somebody asking what the order is. It is now
`docdoc.pipeline.run`, and the recorder calls it.

```python
from docdoc.pipeline import run

result = run(source, schema="invoice@1", registry=registry, adapter=adapter)
print(result.validation.verdict, result.processing_id)
```

Synchronous, in-process, and usable with no service, no database, no object store, and no network
beyond whatever the configured adapter and parser need themselves.

## It sequences; it does not reimplement

Every rule about what a stage *means* stays in that stage's layer. The pipeline decides what runs, in
what order, what is reused, and what is recorded — and nothing else. A behaviour reachable only
through the pipeline is a bug, and the test that keeps it honest regenerates the committed prediction
set and compares it byte for byte with the one on disk.

It is also **not a DAG engine** and must not become one: no stage graph, no conditional stage, no
user-supplied stage. The MVP has four stages and they are these four.

## The reuse decision

Before each stage the pipeline computes that stage's artifact id **from inputs known before the stage
runs**, and asks the store. A hit is returned instead of executing, and the returned result is
required to be indistinguishable from the executed one.

That "before it runs" is the whole difficulty. Each layer already derives its own artifact id at the
end of its work, which is enough to *record* an identity and useless for *reusing* one: by the time
the id exists, the cloud parser has been billed and the model has answered. So:

- **Parse** splits in two. `plan_parse` routes the file and selects a parser — which is what
  `document_id` folds — and `execute_plan` runs it. A cached parse skips the parser, including a
  billable service-backed one, and still pays the local text-layer assessment. That is deliberate:
  the assessment is what selects the parser, so a cached document can never arrive carrying a routing
  decision this run did not make, and Principle V's decision stays inspectable on a hit.

- **Extract** is predicted rather than derived. Its options hash folds the model that *answered*,
  which Milestone 3 chose on purpose — a request naming an alias and the model that served it are
  different computations, and folding the requested name would let them share one content address.
  So the pipeline computes the id the run *will* produce if the provider answers with the model it
  was asked for, and stores the result only when the prediction came true. A provider that
  substitutes a model gets a correct result and no cache entry.

- **Ground** and **validate** are deterministic and take no provider, so their identities are
  knowable outright.

### What reuse looks like

| Change | Parse | Extract | Ground | Validate |
|---|---|---|---|---|
| nothing | reused | reused | reused | reused |
| the prompt, or the schema | reused | executed | executed | executed |
| the model | reused | executed | executed | executed |
| a validation rule | reused | reused | reused | executed |

Nothing is ever deleted or marked stale. The store is append-only, and invalidation is a consequence
of a **new identity** rather than an act performed on an old one — which is why running an old schema
again after a new one reuses everything.

### A retyping step you would not expect

A stored extraction comes back with its `Decimal` values as strings and its dates as strings, because
JSON has neither type and `ExtractionResult.values` is `dict[str, Any]`. Left alone, validation would
compare a total against a sum of *strings* and reach a different verdict than the run that produced
the artifact — silently, and looking exactly like a model that cannot read numbers. So a reused
extraction is retyped against its schema, using `conform`'s single copy of the coercion rules.

## Failure

A stage failure ends the run and **returns everything the preceding stages produced**. It is not
raised: it is recorded on the result, with the stage that failed and the error's *class name* —
never its message, which can quote the document.

The stage is attributed to the layer that **declared** the error, not to the one that was executing
when it surfaced. A grounding error raised during validation is a grounding error, and attributing it
to validation would send whoever reads the report to the wrong code.

Two errors do escape rather than being recorded: `PipelineError`, because sequencing itself failed,
and `ArtifactError`, because a corrupt payload or a refused divergent write is a fault in the
deployment rather than in the document. Reporting "extraction failed" for a bad disk would send a
reader to the wrong code just as surely.

Retries are permitted for provider and network calls only, and that policy lives in the layers that
make those calls. The pipeline adds none of its own: there is no transient failure mode in a
deterministic computation.

## Why a job needs no queue

The HTTP interface runs the pipeline inside the request. That is not a simplification of an
asynchronous design — it is what the identity model permits.

A job id that *is* the terminal artifact id cannot be issued before the run, because that id is not
knowable until the stages feeding it have finished. An asynchronous design would need a second
identifier issued up front, which is the failure ADR-0008 exists to prevent in another guise.
Synchronous execution dissolves the problem instead of working around it: by the time there is
something to hand back, the id exists.

Principle XI is satisfied on its own terms. Local synchronous execution must be able to *become*
API → queue → workers without rewriting the domain model, and nothing here would have to change but
the transport: the pipeline is already a function from inputs to a result, and the store is already
the place a worker would write to.

## What it records

One `pipeline.stage` event per stage, carrying the request id, the processing id, the step id, the
duration, the outcome, and whether the stage was reused — plus, where a provider answered, the
provider, the model, and the token usage, taken from the layer that made the call rather than
re-derived.

Content, values, credentials, and prompt bodies never appear. Durations, timestamps, request ids,
retry counts, and transport settings enter no identity, no artifact, and no verdict.

## See also

- [Artifacts](artifacts.md) — the chain, the two hashes, and what a store read may and may not do.
- [Identity](identity.md) — blob, document, and the two-level model everything above rests on.
- ADR-0003 — the per-stage content-addressed chain.
- ADR-0010 — the store's layout and the synchronous job model.
