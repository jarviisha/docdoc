# ADR-0015: Deletion Over a Content-Addressed Store

- **Status**: Accepted
- **Date**: 2026-09-04
- **Implements**: Milestone 10 (`specs/010-operations-and-corrections/spec.md`), FR-001 – FR-016, FR-086, FR-103
- **Relates to**: [ADR-0003](0003-content-addressed-artifact-chain.md) (artifact chain), [ADR-0010](0010-artifact-store-and-job-model.md) (store layout, §5 immutability), [ADR-0013](0013-asynchronous-run-model.md) (the run record), [ADR-0014](0014-tenant-scoping-and-store-namespacing.md) (namespacing, and the warning this ADR answers)
- **Principles engaged**: VIII (Reproducibility, provenance, and versioning), X (Layer direction), XI (MVP discipline)

## Context

Milestone 9 wrote `expires_at` on every run row and gave no code the job of reading it. That was
deliberate — a sweep is one feature and it belongs whole to one milestone — and it leaves this one
with a database that only grows, an object store that only grows, and no way for a deployment to
answer a customer who asks for their data to be removed.

Deletion is harder here than in most systems, and for a reason the project built on purpose.

**Identity is derived from content.** `artifact_id` is derived from a stage's inputs (ADR-0003), so
two runs of the same tenant over the same document and schema **share every artifact**. That sharing
is not incidental: it is what makes a second submission free, and Milestone 9's SC-005 measures it.
It follows that "delete this document" cannot mean "delete the artifacts it produced", because some
surviving run may be the reason one of them exists.

**And the store holds more than runs.** `docdoc extract` writes artifacts from the command line. A
library caller writes them. The recorder writes them while producing a prediction set. **None of
those has a run row.** Any deletion rule expressed in terms of "runs that reference this" has to
decide what it means about content no run has ever referenced, and the obvious reading destroys it.

Three further constraints arrive with the problem:

- **Principle VIII says provenance MUST NOT be silently overwritten**, and that artifacts are
  immutable. A retention sweep deletes both. This cannot be waved through.
- **ADR-0014 §3 makes the default tenant's namespace the store root itself**, and its Consequences
  section says what that implies for this milestone in as many words: *"The default tenant is the one
  whose deletion is dangerous. Its prefix is the store root, so a naive 'delete tenant'
  implementation in Milestone 10 would delete everything."*
- **A sweep dies halfway.** Whatever rule is chosen has to be safe to re-run from any point, because
  the alternative is an operator deciding whether it is safe, once, under pressure.

## Decision

### 1. The deletion set is a difference, never a complement

```text
candidates = ⋃ stage_outcomes[].artifact_id  over the runs being removed
survivors  = ⋃ stage_outcomes[].artifact_id  over that tenant's remaining runs
delete       candidates − survivors
```

An artifact becomes a candidate **only by having been named by a run that is being removed.** Content
that no run ever named is not reachable by this rule at all, which is what protects everything the
command line, the library, and the recorder ever wrote.

The rejected form is worth stating precisely, because it is the one a reasonable implementer writes:
"delete every artifact no surviving run references." It is a *complement*, it is one word away in
English and a catastrophe away in behaviour, and it deletes every CLI-written artifact in the store
on its first run. `tests/unit/test_sweep_is_a_difference.py` exists to fail if anyone converts one
into the other.

### 2. Survivorship is read, not derived

A run row's `stage_outcomes` already carries an `artifact_id` per stage
(`src/docdoc/runs/model.py`). The set of artifacts a run holds is therefore **recorded**, and the
sweep needs no parser, no schema registry, no adapter, and no options hash.

Re-deriving the chain from `blob_id` and `schema_identity` was considered and is not merely more
expensive — it is **wrong**. Derivation needs the parser version and options hash that were in effect
when the run executed, and a reconfigured deployment no longer has them. It would silently compute a
different set and miss artifacts, which is the worst available failure in a routine whose whole job
is to remove things.

### 3. Deletion order is fixed: content, then tombstone, then row

Artifacts and blobs first. The tombstone second. The run row last.

A crash between any two steps leaves the run still present with its content partly gone, and the next
sweep recomputes the difference from what remains and completes the work. The reverse order loses the
list of what to delete: a removed row is a removed `stage_outcomes`, and the artifacts it named
become unreachable garbage that no later sweep can identify.

This is what makes idempotence true rather than asserted, and it is why the sweep needs no journal,
no two-phase commit, and no compensating transaction.

### 4. A tombstone, and it is not a run status

A removed run leaves four fields: `run_id`, `tenant_id`, `deleted_at`, `policy`. Nothing else.

**`RunStatus` gains no member.** Milestone 9 closed it at five and left `expired` out because a state
no code path could reach is a state that lies; putting it back now that a code path exists would swap
one lie for another, because the value would carry none of a run's other fields. A `Run` with a
status and nothing else is not a run. So a removed run is gone *as a run*, and the tombstone is a
different thing living in a different table — which also leaves Milestone 9's two `CHECK` constraints
on `runs` exactly as they are.

`policy` is what distinguishes ageing out (`retention`) from erasure on request
(`erasure:tenant`, `erasure:document`). No second status carries that distinction, because the
tombstone already has to record it.

### 5. Erasure by tenant is a prefix operation — except for the default tenant

ADR-0014 §5 put the tenant above the fan-out precisely so that this milestone would not have to scan,
and for a named tenant that is exactly what happens: `delete_prefix(tenant_id)`.

**The default tenant is refused.** ADR-0014 called this "the correct semantics" and it is — the
default tenant *does* own the root — and that is precisely why it cannot be the default *behaviour*:
an operator erasing "the default tenant" in a deployment that never enabled authentication is not
erasing a customer, they are erasing everything docdoc holds.

*Corrected 2026-09-04, while implementing §7.* This paragraph first said the prefix path "would
remove every other tenant's subtree". **It would not**, and the layout is the reason: artifacts live
at `<base>/artifacts/` and blobs at `<base>/blobs/`, so the default tenant's prefix delete removes
`<root>/artifacts/` and `<root>/blobs/` and leaves `<root>/t/` untouched. Other tenants survive by
construction, which is a point in ADR-0014 §1's favour and was worth getting right rather than
overstating.

What it *does* remove is everything written before authentication was ever enabled, everything
`docdoc extract` wrote from the command line, everything a library caller produced, and everything
the recorder wrote — which in a deployment that never enabled authentication is all of it. That is
the same hazard as §1's, arriving by a different route, and it is why the guard stands.

So:

- `DELETE /v1/admin/tenants/default` is refused, naming why. There is no URL that empties the root.
- `docdoc erase --tenant default` uses the §1 difference — that tenant's run-derived content, and
  nothing else.
- `docdoc erase --tenant default --purge-store-root` does the other thing, once, typed deliberately,
  on a command line.

`delete_prefix` raises `RetentionError` unless `allow_store_root=True`, and that argument is passed at
exactly one call site in the codebase.

### 6. What is never swept

A run that is not terminal. A run under an unexpired lease. A run whose result carries a live
correction (FR-013). The first two protect work in flight; the third is the one place this
milestone's two halves touch, and it exists because a policy that deletes a result somebody corrected
is a data-loss bug that only appears once both features are present.

### 7. The stores gain deletion, and that is a public break

`ArtifactStore` and `BlobStore` gain `delete` and `delete_prefix`. Under ADR-0011 a `0.x` minor may
break any public API provided the changelog names what moved, and this one does. The two surfaces
that get a deprecation path instead — the kernel's identity derivations and the on-disk artifact
format — are untouched.

`delete` returns whether something was there, so counts report what was removed rather than what was
attempted, without a second existence check that would race.

## Consequences

**Deleting is not overwriting, and *silently* is where Principle VIII is satisfied.** No artifact's
bytes are ever rewritten; no `artifact_id` is reused for different content; ADR-0010 §5's refusal to
overwrite is untouched. What replaces a run is a record saying it was here, when it went, and under
which rule. The owning tenant is told that; every other tenant is told what it would be told about an
identifier that never existed, which is Milestone 9's FR-066 unchanged.

The alternative — removing the row outright — makes "was this ever here?" unanswerable to the only
party entitled to ask, and that is the reading of Principle VIII that would actually breach it.

**An artifact that is deleted and later recomputed derives the same identity**, because
content-addressing promises exactly that. Deletion costs money and time on the next run; it does not
cost correctness.

**The sweep is more expensive than dropping rows**, and that is the price of §1. It reads
`stage_outcomes` for the batch and for the tenant's survivors, and it is bounded per invocation so a
year of accumulated rows makes progress across many ticks rather than holding one transaction open.

**Nothing here is a reference count.** No mutable structure sits beside the immutable store, which is
the shape ADR-0010 §1 rejected for artifacts and which no crash-safe sweep wants to maintain.

**A deployment running no worker sweeps nothing.** The tick lives in the worker (FR-114), so a
synchronous-only deployment that configures retention and then observes nothing being removed needs
to be told why — and the operator documentation says so rather than leaving it to be discovered.
