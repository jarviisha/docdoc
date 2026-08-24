# Artifacts

An append-only, content-addressed store of stage results, and the chain that makes partial reuse
correct rather than merely fast.

## The chain

```text
artifact_id = sha256(input_artifact_id + processor_id + processor_version + options_hash)
```

```text
blob_id
  → parse artifact       (processor: parser)      == document_id, per ADR-0002
  → extraction artifact  (processor: extractor)
  → grounding artifact   (processor: grounder)
  → validation artifact  (processor: validator)   == processing_id
```

The chain composes, so each stage transitively inherits every input that can affect it. Change the
prompt and the extract stage gets a different identity — so it and everything downstream are computed
afresh, while everything upstream is reused. Nothing is deleted and nothing is marked stale:
invalidation is a consequence of a new identity rather than an act performed on an old one.

The run's `processing_id` **is** the terminal artifact id. There is no second run identifier, because
a second identifier for the thing this already identifies is how two ids start disagreeing.

## Two hashes, because one cannot do the job

Every stored envelope carries both, and this is the single easiest thing here to get wrong.

| Field | Hashes | Answers |
|---|---|---|
| `artifact_id` | the stage's **inputs** | *which* result is this |
| `content_id` | the stored **payload** | are the bytes intact |

An `artifact_id` is not derived from the payload. Rehashing a payload and comparing it to the
artifact id would always fail — a store carrying only the artifact id **cannot detect corruption at
all**. `content_id` is `content_id_for(canonical_json(payload))`, and it is what makes the integrity
check possible.

## Which misses are errors

Four read outcomes, and only one of them is an error.

| Condition | Behaviour |
|---|---|
| nothing stored under the id | miss; execute |
| incompatible `artifact_format_version` | **miss**; execute; log the mismatch |
| `content_id` does not match the payload | **`ArtifactError`**; do not execute |
| store root unreadable, absent, or full | run without reuse; log once; never fail the run |

The second and third rows are the pair worth understanding together.

A **format mismatch is a miss** because a version bump is an expected event on upgrade. Making it
fatal would mean every run fails after a release until somebody clears a directory by hand.

A **content mismatch raises** because that is corruption, and recomputing over it would hide a
failing disk behind a slightly slower run. The supported recovery is `docdoc store clear`, performed
deliberately by a person — never by the run that found the fault silently overwriting the evidence
of it.

## Writing never overwrites

| Condition | Behaviour |
|---|---|
| `put` for an id already present, same content | no-op |
| `put` for an id already present, **different** content | **`ArtifactError`** naming both; never overwrite |
| concurrent `put` of identical content | both succeed; no lock required |

The conflicting write is the interesting one. Two writes of one identity disagreeing means either
corruption or **a processor whose output moved without its version moving** — the failure ADR-0003
assigns to human review because the system generally cannot detect it. This is the one place it can,
and the evidence exists for exactly one moment, so the store refuses to discard it.

`run(..., verify=True)` — `docdoc extract --verify-cache` — executes every stage and still writes, so
that check fires on results that would otherwise have been read back. Without it, a drifted processor
is only ever caught by a cache miss that happens not to occur.

Concurrency is benign without coordination because the entry is immutable and content-addressed: the
store links a completed temporary file into place, which is atomic *and* refuses an existing target,
so a racing writer is told rather than silently overwritten and no reader can ever see a half-written
artifact.

## There is no default location

The store is **off unless configured**. Artifacts hold extracted values and blobs hold whole source
documents, so where they land is an operator's decision rather than one docdoc makes on their behalf.
`NullArtifactStore` is the default and misses on every read, which is what makes "the store is
optional" true by construction rather than by a flag being checked correctly at every call site.

Both stores are created readable only by the owning account. Blobs are the more sensitive of the two
and the easier to overlook: an artifact holds the values extracted from a document, and a blob holds
the document.

With no store configured, every run recomputes every stage and produces **identical results**.
Nothing about correctness depends on the store being present.

## Explaining an identity

ADR-0003 accepted that cache keys "cannot be computed by hand or eyeballed in logs" on one explicit
condition: that something would explain them.

```bash
docdoc explain sha256:3a1ede… --chain --store ./store
```

The output names the stage, the input artifact id, the processor and its version, and the **names**
of the inputs folded into the options hash — walking back to the source blob in four hops. It carries
no payload, no extracted value, no prompt body, and no credential: it explains identities, not
documents. "prompt_hash" is a name; the prompt is a document.

A derivation is *read* from the record a write left behind. A run with no store configured produces
identities that were never recorded, and the tool says so rather than reconstructing something
plausible — a reconstruction would be a guess wearing the costume of a record.

## What is out of scope

Garbage collection, retention policy, and eviction. What this milestone owes a future collector is
one checkable thing rather than a promise: **every artifact records its own stage and the identity of
its input**, so reachability from a set of roots is computable by walking the store alone. An
artifact lacking that could never be collected safely.

Also out of scope: distributed or shared caching, cache warming, and cross-host coordination. Two
docdoc versions may share one store and are safe to when the artifact-format versions and processor
versions agree — which is exactly what those versions are for, and the conflicting-write check is
what catches the case where that assumption was wrong.

## See also

- [The pipeline](pipeline.md) — who computes these identities, and when.
- [Identity](identity.md) — `blob_id`, `document_id`, and why spans anchor to the second.
- ADR-0003 — the chain and the per-stage folded inputs.
- ADR-0010 — the on-disk layout, the format version, and the job model.
