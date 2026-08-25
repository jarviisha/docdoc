# ADR-0010: Artifact Store Layout, Format Versioning, and the Synchronous Job Model

- **Status**: Accepted
- **Date**: 2026-08-22
- **Implements**: ADR-0003 (per-stage content-addressed artifact chain), Milestone 7
- **Principles engaged**: III (Determinism), VIII (Reproducibility), X (Layer direction), XI (Scale through boundaries)

## Context

ADR-0003 defined an artifact chain and promised partial reuse: change a prompt, and the parse is
reused rather than repeated. Nothing has ever stored an artifact, so the promise has been text.
`docdoc/recording/record.py` recorded that as a known limitation in its own module docstring, and
every evaluation run re-parsed every document because of it.

Building the store raises four questions ADR-0003 did not answer, each of which a later reader would
otherwise have to reconstruct from code.

## Decision

### 1. A filesystem store, not a database

```text
<root>/blobs/<aa>/<full-hash>            submitted source bytes, by blob_id
<root>/artifacts/<aa>/<full-hash>.json   one envelope per artifact id
```

Two-character fan-out on the hash, because a flat directory of a hundred thousand entries is slow on
several filesystems and free to avoid. Writes go to a temporary file in the same directory followed
by an atomic replace, so a partially written artifact is never readable as a complete one.

The sanctioned stack permits PostgreSQL "only where persistence is genuinely required". Artifacts are
immutable and content-addressed, and the only write is an atomic create — there is no transaction to
run, no row to update, and no query beyond a key lookup. SQLite was a real candidate and is rejected
on the same ground: it buys guarantees this store does not need.

There is **no default root**. The store is off unless configured. Artifacts carry extracted values
and blobs carry whole source documents, so where they land is an operator's decision, and both are
created readable only by the owning account.

### 2. Two hashes, because one cannot do the job

Every envelope carries both:

| Field | Hashes | Answers |
|---|---|---|
| `artifact_id` | the stage's **inputs**, per ADR-0003 | *which* result is this |
| `content_id` | the stored **payload** | are the bytes intact |

This is the single easiest thing to get wrong here. An `artifact_id` is not derived from the payload,
so rehashing a payload and comparing it to the artifact id would always fail — a store carrying only
the artifact id cannot detect corruption at all. `content_id` is `content_id_for(canonical_json(payload))`,
reusing the kernel helper made public at commit `b66f687`.

### 3. `artifact_format_version` is an integer per artifact kind, not the package version

Bumped when the stored model's shape changes incompatibly: a field removed, renamed, or retyped, or
a new field whose absence is not answerable from what was stored. Adding a field whose default is
never load-bearing is compatible and must not move it.

Tying reuse to the docdoc release version would invalidate every artifact on every release, including
releases that change a docstring — making the store useless exactly when a team most wants it. Tying
it to nothing means an old envelope parses into a new model with fields quietly defaulted, and a
result that is wrong in a way no test finds.

Hashing the model's JSON schema instead would remove the human step, and is rejected only because
pydantic's schema output moves across pydantic releases; recorded here as a plausible refinement if
that is ever pinned.

### 4. Four read outcomes, and only one of them is an error

| Condition | Behaviour |
|---|---|
| Nothing stored under the id | miss; execute the stage |
| Incompatible `artifact_format_version` | miss; execute; log the mismatch |
| `content_id` does not match the payload | **raise**; do not execute, do not recompute |
| Root unwritable, absent, or full | run without reuse; log once; never fail the run |

A format bump is an expected event on upgrade and every deployment will hit one; making it fatal
means an upgrade breaks every run until somebody clears a directory by hand. Corruption is not
expected, and recomputing over it hides a failing disk behind a slightly slower run. The distinction
is between *this artifact does not apply to me* and *this artifact is not what it claims to be*, and
only the second is a lie.

### 5. A write for an existing id never overwrites

Identical content is a no-op. **Divergent content raises**, naming both.

This is the one place the system can see the failure ADR-0003 hands to human review — a processor
whose output moved while its version did not. In general it is undetectable; here the evidence
exists, and refusing to overwrite turns a silent stale reuse into a loud error at the moment it does.
A run mode that executes every stage and still writes makes the check fire on demand rather than by
luck.

Concurrent writes of identical content both succeed. No lock, no lease, no coordinator: atomic
replacement of an immutable, content-addressed entry is what makes the race benign.

### 6. A job is a synchronous run, identified by its terminal artifact

The run happens inside the HTTP request. On success the response carries the terminal artifact id —
ADR-0003's `processing_id` — as the job id, together with the result itself. A failed run produces no
terminal artifact and therefore no job; it returns a typed error in the same response, carrying the
stage outcomes and the results the completed stages produced. `GET /v1/jobs/{id}` is a store lookup,
with a closed status set of `succeeded`, `unavailable`, and `unknown`, and **no** `pending`.

The obvious reading of "job status" is asynchronous, and it is wrong twice. The deferred-technology
list forbids the queue it would need; and a job id that *is* the terminal artifact id cannot be
issued before the run, because that id is not knowable until the stages feeding it have run.
Synchronous execution dissolves the problem rather than working around it.

Principle XI is satisfied on its own terms — local synchronous execution must be able to *become*
API → queue → workers without rewriting the domain model. Nothing here would have to change but the
transport: the pipeline is already a function from inputs to a result, and the store is already the
place a worker would write to.

## Consequences

- **The parse cache lookup must sit after routing.** `document_id` needs `parser_id` and
  `parser_version`, which routing decides by reading the file, so `ingest.parse()` splits into a
  planning call and an execution call. A cached parse still pays the local text-layer assessment and
  skips the parser — including the billable service-backed one. It also means the text-layer verdict
  is computed and recorded on every run, cached or not, which keeps Principle V's decision inspectable.
- **The store never imports a result model.** The caller names the model at the call site, so
  `docdoc.artifacts` depends on `pydantic` and two kernel helpers and sits directly above the kernel.
- **Garbage collection stays out of scope**, and what this ADR owes a future collector is one
  checkable thing: every artifact records its stage and its input identity, so reachability from a set
  of roots is computable by walking the store alone.
- **A stale result now requires a hash collision or an unbumped processor version**, and the second
  has a symptom for the first time.

## Amendment, 2026-08-24 — what a job lookup can honestly answer

**Status**: Accepted. Amends §6 above; nothing else in this ADR changes.

The interface checklist (`specs/007-pipeline-api-cli/checklists/interfaces.md`, CHK019) found §6 and
the HTTP contract requiring a distinction this store cannot make. As first written, an id that was
never produced was to be reported `unknown` and one whose artifact had been cleared `unavailable` —
but the store is content-addressed and append-only, `clear()` leaves no tombstone, and nothing records
what the store was never asked to hold. Both are the same observation: *not here*. A status set that
claimed otherwise would have been decided by whoever implemented the endpoint.

Three decisions follow, and the through-line is that each replaces an answer the system would have had
to invent with one it can actually derive.

1. **`unavailable` covers every well-formed absent id**, cleared or never produced, and says so rather
   than guessing. **`unknown` is reserved for an id that is not a well-formed artifact identity** — a
   syntactic judgement, available without any history. The set is closed at three, still with no
   `pending`.

2. **A run's response carries the result, not only its identity.** §6 as written returned a job id, and
   a caller then fetched the result from the store. But the store is off unless configured (§1), and a
   write may degrade without failing the run — so in both cases the run succeeded, the result existed
   for a moment, and the response threw away the only copy. An identity-only response is a receipt the
   caller frequently cannot redeem. The job endpoints keep their purpose: retrieval later, by someone
   holding an id and not a result.

3. **A failed run's response carries the partial results.** ADR-0003's chain gives a failed run no
   terminal artifact and therefore no job, so there is no later fetch in which the completed stages'
   output could surface. FR-004 requires those results not be discarded; without this they were
   discarded at the boundary while the library dutifully preserved them.

**Consequence.** The store gains nothing and loses nothing — no tombstone, no job table, no state.
What changed is that two response bodies now carry what the store was previously assumed to hold for
them, which is also what makes the HTTP interface usable with no store at all.
