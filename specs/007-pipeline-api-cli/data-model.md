# Phase 1 Data Model: Pipeline, Artifact Store, CLI, and HTTP API

**Feature**: `007-pipeline-api-cli` | **Date**: 2026-08-22

Every model here is a frozen pydantic model unless stated otherwise, matching every layer below.
Nothing in this milestone redefines a result type from Milestones 1–6; they are carried, stored, and
serialised as they are.

---

## `docdoc.artifacts`

### `ArtifactEnvelope`

What one stored artifact looks like on disk. The store's only opinion about its contents is that
they round-trip.

| Field | Type | Notes |
|---|---|---|
| `artifact_id` | `str` | The id this envelope is stored under. `sha256:…`, matching `ID_PATTERN`. |
| `stage` | `Stage` | Which stage produced it. Recorded for `explain`, never used to pick a model. |
| `input_artifact_id` | `str \| None` | The previous link in the chain. `None` only for the parse stage, whose input is a blob. |
| `processor_id` | `str` | Stable identity of the producer — parser id, extractor id, grounder id, validator id. |
| `processor_version` | `str` | Moves when the producer's output moves for fixed inputs (ADR-0003). |
| `options_hash` | `str` | The stage's folded options, per ADR-0003's table and its Milestone 5 amendment. |
| `artifact_format_version` | `int` | The stored model's shape. Not the package version (research R6). |
| `content_id` | `str` | `content_id_for(canonical_json(payload))`. The integrity check (research R4). |
| `payload` | `dict` | The serialised result model. |

**Invariants**

- `content_id` recomputed from `payload` on every read must equal the stored value, or
  `ArtifactError` (FR-014).
- `artifact_id` is *not* derivable from `payload`; it is a hash of inputs. This is why `content_id`
  exists and is the single easiest thing to get wrong here.
- Immutable once written. There is no update path.

### `ArtifactStore` *(protocol)*

```text
get(artifact_id, *, model, format_version) -> model | None
put(artifact_id, payload, *, stage, input_artifact_id, processor_id,
    processor_version, options_hash, format_version) -> None
derivation(artifact_id) -> DerivationRecord | None
clear(*, stage=None) -> int
```

Two implementations: `FileArtifactStore` (research R4's layout) and `NullArtifactStore`, which
misses on every `get` and discards every `put`. The null store is the default and there is no default
root, which is what makes FR-017 true by construction rather than by a flag being checked correctly
everywhere.

`clear(stage=None)` accepts all-of-it or one stage. Those two subsets and no query language
(FR-019).

**State transitions**: absent → present. There is no other transition — in particular no
present → present, because a `put` over an existing id is a no-op when the content matches and an
`ArtifactError` when it does not (FR-062). `clear()` returns to absent.

**Invariant**: no stored artifact is ever overwritten. This is what makes "append-only" a property
rather than a description, and it is the only mechanical symptom available for a processor whose
output moved without its version moving.

### `BlobStore`

Submitted source bytes, addressed by `blob_id`. `put` is idempotent: identical bytes yield one entry
(FR-021). Separate from the artifact store because blobs are opaque bytes with no envelope, no
processor, and no format version.

### `DerivationRecord`

The answer to "why is this id this value", assembled from an envelope. Carries `artifact_id`,
`stage`, `input_artifact_id`, `processor_id`, `processor_version`, `options_hash`, and the *names* of
the inputs folded into that options hash. Carries **no** payload, no extracted value, no prompt body
(FR-025). A chain is a list of these, walked back to a `blob_id`.

---

## `docdoc.pipeline`

### `Stage` *(enum)*

`PARSE`, `EXTRACT`, `GROUND`, `VALIDATE`. Fixed at four; the spec's Assumptions rule out adding,
removing, or configuring one. Note that `docdoc.evaluation` already defines a `Stage` enum with the
same four members for recording failures — the pipeline's is the one the store and the contracts use,
and the evaluation one is left alone rather than merged, since changing it would move
`prediction_set_id` and invalidate the committed public tier for no gain.

### `StageOutcome`

| Field | Type | Notes |
|---|---|---|
| `stage` | `Stage` | |
| `status` | `EXECUTED \| REUSED \| SKIPPED \| FAILED` | `SKIPPED` means an earlier stage failed. |
| `artifact_id` | `str \| None` | `None` when the stage failed or was skipped. |
| `duration_ms` | `int` | Wall time. Never enters an identity (gate 4). |
| `failure_class` | `str \| None` | The typed error's **class name**. Never its message, which can quote the document — the rule `recording/record.py` already holds and this milestone inherits. |

### `PipelineResult`

| Field | Type | Notes |
|---|---|---|
| `outcomes` | `tuple[StageOutcome, ...]` | One per stage attempted, in order. |
| `document` | `Document \| None` | Carried or fetched — see research's open question. |
| `extraction` | `ExtractionResult \| None` | |
| `grounding` | `GroundingResult \| None` | |
| `validation` | `ValidationResult \| None` | |
| `processing_id` | `str \| None` | The terminal artifact id (ADR-0003, FR-007). `None` if the run failed before the terminal stage. |
| `provenance` | `RunProvenance` | |
| `failed_stage` | `Stage \| None` | |

**Invariants**

- A failed run keeps every result the preceding stages produced (FR-004). Losing them is the exact
  failure `recording/record.py` was written to avoid, and the reason it is being rewritten rather
  than replaced.
- `processing_id` is present exactly when `validation` is present.
- Every `artifact_id` in `outcomes` is either present in the store or was produced by an `EXECUTED`
  stage in this run.

### `RunProvenance`

`pipeline_id`, `pipeline_version`, `request_id`, the schema identity and hash, and the per-stage
processor identities and versions. `pipeline_version` is folded into the terminal artifact id and
into nothing else (ADR-0003).

### `RunLimits`

An alias for the existing `ingest.Limits`, threaded through. Not a new type (research R10).

---

## `docdoc.api`

### `SubmittedDocument`

`blob_id`, `size_bytes`, `media_type`. Deliberately **not** called `document_id` (research R8): a
`document_id` identifies one *parse*, and at submission no parse has happened or even been chosen, so
returning a blob id under that name would hand a caller an identifier whose spans anchor to nothing.

**Realised as two types rather than one** — `SubmissionResponse` and `BlobMetadata` — because they
differ in one field and the difference is real. A submission has just seen the bytes, so its
`media_type` is known; a metadata read of a blob already in the store may not be able to re-detect
one, so there it is optional. Collapsing them would mean either an optional field on the response
that is never absent, or a required field on the lookup that cannot always be filled.

### `Job`

Not a stored row. A view assembled from the store: `job_id` (= terminal artifact id), `status`, and
where available the result. There is no `PENDING`, because a synchronous run that has not finished
has not returned a job id (research R7).

`status` is drawn from a closed set of **three** (FR-035 as amended, ADR-0010's amendment of
2026-08-24):

| Status | When |
|---|---|
| `succeeded` | the terminal artifact is in the store |
| `unavailable` | the id is well-formed and is not in the store |
| `unknown` | the id is not a well-formed artifact identity, so no run could have produced it |

**`unavailable` deliberately does not distinguish "never produced" from "produced and since
cleared".** The store is content-addressed and append-only, `clear()` leaves no tombstone, and
nothing records what the store was never asked to hold — so the two are one observation, and a status
claiming to tell them apart would be inventing the difference. `unknown` is reserved for the
judgement that *can* be made without history: whether the id is syntactically an artifact id at all.

This section said `SUCCEEDED` and `UNAVAILABLE` until 2026-08-24. The two-member set came from an
earlier draft in which `unknown` meant *never produced* — a distinction an append-only store cannot
make. Recorded rather than quietly corrected, because the earlier reading is the one a reader would
otherwise reconstruct.

### `ErrorBody`

`error` — carrying `class` (the docdoc error class name), `stage`, `message`, and `detail` — plus
`outcomes` and `results` where the failure happened mid-run (FR-066).

The message is docdoc's own, never a provider's, which may quote the document it choked on (FR-037).

**The partial results are not optional detail.** A failed run produces no terminal artifact and
therefore no job to fetch later, so this response is the *only* place the completed stages' output
can appear — without it, FR-004's "MUST NOT discard partial results" would be honoured in the library
and defeated one layer out. `results` legitimately carries extracted values: it is the caller's own
document returning on the caller's own request, which is a different thing from a log line, and
FR-043's prohibition is about logs.

The parsed document is represented by its identity rather than inline. It carries every token and
bounding box — the largest thing in the run — and the parse stage's `artifact_id` is already in
`outcomes` for a caller who wants it.

This section gave `type`, `stage`, `message`, `detail` and no partial results until 2026-08-24; the
field also serialises as `class`, since `type` is not a keyword worth fighting and `class` is what the
contract's example shows.

---

## Relationships

```text
blob_id ──put──> BlobStore
   │
   └─ parse ──> document_id ─┐
                             ├─ extract ──> extraction_artifact_id ─┐
                             │                                      ├─ ground ──> grounding_artifact_id ─┐
                             │                                      │                                     ├─ validate ──> processing_id
                             │                                      │                                     │
        each link stored as one ArtifactEnvelope ────────────────────────────────────────────────────────┘
        each link explainable as one DerivationRecord
```

The chain is ADR-0003's, unchanged. What this milestone adds is that each arrow is now a lookup
before it is a computation.

---

## What is deliberately not modelled

- **A job queue, a job table, a run history.** A job is a view over the store (research R7).
- **A cache-statistics model.** Counters are integers on the result and in the log events; a model
  for them would be a schema for two numbers.
- **A user, a tenant, a quota, an API key.** Out of scope by the spec's Assumptions.
- **A second `Stage` enum, a second limits type, a second logging payload shape.** Each already
  exists one layer down and is reused (research R9, R10).
- **`ArtifactRef` as a type of its own.** The spec's Key Entities names it — "an artifact's identity
  together with the stage that produced it and the identity of its input — the edge of the chain,
  which is what makes a derivation explainable" — and it is **subsumed into `ArtifactEnvelope`**,
  whose `artifact_id`, `stage`, and `input_artifact_id` are exactly those three fields with exactly
  that meaning. Recorded here rather than built, because a separate reference type would be a second
  place the edge is written down, and the two would eventually disagree about a chain that has one
  authority. FR-022's guarantee — that reachability from a set of roots is computable by walking the
  store alone — is met by the envelope carrying the edge, and is what
  `test_explain.py::test_the_chain_reaches_the_source_blob_in_four_hops` walks.
  - Added 2026-08-25. The subsumption was always the design; it was the only entity in the spec's
    list with no referent in this document, while `RunLimits`, the `Stage` enum, and the logging
    payload each had one. An omission in a list of deliberate decisions reads as an oversight, which
    is the whole reason that list exists.
