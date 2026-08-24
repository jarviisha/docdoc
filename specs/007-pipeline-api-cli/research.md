# Phase 0 Research: Pipeline, Artifact Store, CLI, and HTTP API

**Feature**: `007-pipeline-api-cli` | **Date**: 2026-08-22

Fourteen decisions. Four of them (R2, R3, R7, R9) were reached by reading the existing code rather
than by preference, and each of those four would have been decided wrongly by a plausible guess.

---

## R1 — Where the new layers go in the enforced chain

**Decision.** Four packages, inserted into `pyproject.toml`'s `import-linter` layers contract as:

```text
api, cli  >  recording  >  evaluation  >  pipeline  >  validation  >  grounding
          >  extraction  >  ingest  >  artifacts  >  kernel
```

`artifacts` sits directly above `kernel`. `pipeline` sits directly above `validation`. `api` and
`cli` are siblings at the top, held apart by a second `independence` contract. Principle X's prose
is amended to this chain in the same change, as the principle itself requires (FR-055).

**Rationale.** `artifacts` depends on `pydantic` and two kernel helpers and nothing else, so the
lowest position that is true is the honest one. `pipeline` must sit above every stage it drives —
`validation` is the highest — and inserting it directly there leaves the existing `recording >
evaluation` edge, which Milestone 6 argued for at length, exactly as it was. `recording` ends up
above `pipeline`, which is what FR-009 needs: the recorder calls the pipeline, never the reverse.

A total order forces `evaluation > pipeline` even though evaluation imports nothing from the
pipeline. That is a harmless artefact of the contract type, and the alternative — a `forbidden`
contract per pair — trades one readable list for a dozen rules nobody will maintain.

`api` and `cli` cannot be ordered against each other honestly: neither should import the other. An
`independence` contract says that, where a position in the layer list would have implied a
permission.

**Alternatives considered.** *`artifacts` inside `pipeline`* — rejected in Complexity Tracking.
*`cli` below `api`* — would let the HTTP layer reuse the CLI's rendering, which sounds like reuse and
is actually a coupling between two presentation layers with different audiences; the shared thing is
the result model, which both already import.

---

## R2 — When the parse-stage identity becomes knowable

**Decision.** The parse cache lookup happens **after routing and parser selection, before the parser
runs**. `docdoc.ingest` grows a plan/execute split: a planning call returns the routed verdict, the
selected parser, the canonical options and their hash — everything `document_id_for` needs — and a
separate call executes it. `parse()` keeps its exact current signature and becomes the composition
of the two, so no existing caller changes.

**Rationale.** This was checked against the code, not assumed. `kernel.document.Document.build`
derives identity as:

```python
document_id_for(blob_id=…, parser_id=…, parser_version=…, options_hash=…)
```

Three of those four are properties of the *chosen parser*, and `ingest.parse._route()` chooses by
first calling `assess_text_layer(file)`, which reads the file. So `document_id` is not derivable
from bytes plus a request; it is derivable the moment selection finishes, which is one function call
before the expensive work.

The cost this leaves on the table is honest and small: a cached parse still pays the local
text-layer assessment. It does **not** pay the parser, which on the cloud path is the billable call
this whole milestone exists to stop repeating. Keeping the assessment also keeps gate 6 true — the
text-layer verdict is computed and recorded on every run, cached or not, so a cached document never
arrives with an unexplained routing decision.

**Alternatives considered.** *A pre-routing request key* (`blob_id` + capability request + rule id →
`document_id`, held in an index) — rejected because the selection it stands in for depends on which
parsers are *installed*, so the key would change when an optional dependency is added and stay
stale when one is removed. An identity that moves with the environment is not an identity, and this
project has an ADR (0002) about exactly that failure. *Never caching the parse* — rejected: it is
the stage ADR-0003 names first and the only one that can cost money per call.

---

## R3 — What the store may know about what it stores

**Decision.** `ArtifactStore` is generic. Callers `put(artifact_id, payload_model, format_version=…)`
and `get(artifact_id, model=ExtractionResult, format_version=…)`. The store imports `pydantic`,
`kernel.content_id_for`, and `kernel.canonical_json` — nothing else.

**Rationale.** Deserialising a stored result requires the model class. If the store imported the four
result types it would depend on four layers, and the next stage anyone adds widens it again. Passing
the class at the call site inverts that: the store stays a typed byte store, and the knowledge of
which stage produces which model lives in `pipeline.stages`, which is the module whose job that is.

**Alternatives considered.** *A registry mapping stage → model inside the store* — same import
problem wearing a hat. *Storing untyped JSON and letting callers parse* — loses the round-trip
guarantee the contract tests need, and moves validation to every call site.

---

## R4 — Store layout, atomicity, and integrity

**Decision.** A filesystem store rooted at a configured directory:

```text
<root>/blobs/<aa>/<full-hash>            submitted source bytes, by blob_id
<root>/artifacts/<aa>/<full-hash>.json   one envelope per artifact id
```

Two-character fan-out on the hash, because a flat directory of a hundred thousand entries is slow on
several filesystems and free to avoid. Writes go to a temporary file in the same directory followed
by an atomic replace, so a partially written artifact is never readable as a complete one (FR-016).

Every envelope carries the payload, the `artifact_format_version` it was written under, the
`artifact_id` it is stored against, and a `content_id` — `content_id_for(canonical_json(payload))`.
On read the store recomputes the content id and refuses on mismatch (FR-014).

**Rationale.** The content id is not optional bookkeeping, and the reason is easy to miss: an
`artifact_id` under ADR-0003 is a hash of a stage's *inputs*, not of its output. Rehashing the
payload and comparing it to the artifact id would always fail. Detecting corruption needs a second,
genuinely content-derived hash, and the kernel already has the helper — made public at commit
`b66f687` because three layers needed it, and this is the fourth.

**Alternatives considered.** *SQLite* — a real option, and rejected under Principle XI: it buys
transactions this store does not need, since artifacts are immutable and content-addressed and the
only write is an atomic create. *No integrity check* — rejected; a store that can return a corrupted
artifact as a cache hit turns a disk fault into a wrong extraction, silently.

---

## R5 — Which cache misses are recoverable and which are errors

**Decision.** Two different behaviours, deliberately:

| Condition | Behaviour |
|---|---|
| No artifact stored under this id | **Miss** — execute the stage. |
| Artifact written under an incompatible `artifact_format_version` | **Miss** — execute the stage, and log that a format-version mismatch caused it. |
| Stored content does not match its recorded content id | **Raise `ArtifactError`** — do not execute, do not recompute. |
| A write arrives for an id already present, with different content | **Raise `ArtifactError`** — never overwrite (added by the caching checklist pass; FR-062). |
| Store root unwritable or absent | **Degrade** — run without reuse, log once; a run must still be able to produce a correct result. |

**Rationale.** A format bump is an expected event on upgrade and every deployment will hit it; making
it fatal would mean an upgrade breaks every run until someone clears a directory by hand. Corruption
is not expected, and recomputing over it hides a failing disk behind a slightly slower run. The
distinction is between "this artifact does not apply to me" and "this artifact is not what it claims
to be", and only the second is a lie.

The fourth row was added by the caching checklist pass and is the more interesting one. Two writes of
one identity disagreeing is the *observable symptom* of the failure ADR-0003 hands to human review —
a processor whose output moved while its version did not. The store cannot detect that in general,
but it can detect it here, and refusing to overwrite turns a silent stale-reuse into a loud error at
the one moment the evidence exists.

**Alternatives considered.** *Fatal on version mismatch* — rejected above. *Silently repair by
overwriting the corrupt entry* — rejected: it destroys the evidence of the fault, in a store whose
whole value is that a stored result can be trusted. *Last write wins on a conflicting put* — the same
objection, and it would make the store's append-only claim false.

---

## R6 — The artifact format version is not the package version

**Decision.** `ARTIFACT_FORMAT_VERSION`, an integer per artifact kind, declared next to the stage
that produces it and bumped when the stored model's shape changes incompatibly. It is unrelated to
the docdoc release version.

**Rationale.** Tying reuse to the package version invalidates every artifact on every release,
including releases that change a docstring — which makes the store useless exactly when a team most
wants it. Tying it to nothing is FR-015's failure mode: an old envelope parsed into a new model,
missing fields quietly defaulted, and a result that is wrong in a way no test will find. An explicit
integer that a reviewer must bump is the same obligation ADR-0003 already places on processor
versions, and it fails the same way — visibly, on a human.

**Alternatives considered.** *Hash the model's JSON schema and use that* — attractive, and it removes
the human step, but pydantic's schema output changes across pydantic releases, so every dependency
bump would invalidate every artifact. Recorded as a plausible later refinement if it is ever pinned.

---

## R7 — The job model, and why nothing needs a queue

**Decision.** Execution is synchronous. `POST /v1/documents/{id}/extract` runs the pipeline inside
the request. On success it returns the terminal artifact id as the job id (ADR-0003) **and the result
itself**; on failure it returns a typed error immediately, carrying the completed stages' results, and
creates no job. `GET /v1/jobs/{id}` and `GET /v1/jobs/{id}/result` are store lookups, answering
`succeeded`, `unavailable`, or `unknown`, and never pending (FR-035).

> **Amended 2026-08-24** (ADR-0010 amendment, checklist CHK019/021/022/032). This paragraph
> originally reported an id that was never produced as `unknown` and a cleared one as `unavailable`,
> and returned only a job id. An append-only store with no tombstones cannot tell those two apart, and
> a response carrying only an identity is unredeemable whenever no store is configured — which is the
> default. `unknown` now means *not a well-formed artifact id*; the result travels in the response.

**Rationale.** The obvious reading of "job status" is asynchronous, and it is wrong here twice over.
The deferred-technology list forbids the queue it would need; and more interestingly, a job id that
is the terminal artifact id *cannot* be issued before the run, because the terminal id is not
knowable until the stages that feed it have run — R2 shows even the first stage's id needs the file
routed. Synchronous execution dissolves the problem instead of working around it: by the time there
is something to hand back, the id exists.

Principle XI's requirement is satisfied on its own terms — local synchronous execution must be able
to *become* API → queue → workers without rewriting the domain model. Nothing here would have to
change but the transport: the pipeline is already a function from inputs to a result, and the store
is already the place a worker would write to.

**Alternatives considered.** *A job table with a pending state* — a database, for rows that would be
written and read in the same request. *A separate job id issued up front* — introduces a second
identifier for the thing `processing_id` already identifies, which is the failure ADR-0008 exists to
prevent in another guise.

---

## R8 — What `POST /v1/documents` returns

**Decision.** The **blob identity**, named as such. `GET /v1/documents/{id}` returns that blob's
metadata: its identity, its size, and its detected media type.

**Rationale.** The founding sketch has this endpoint return a `document_id`. Under ADR-0002 that is
not available: a `document_id` identifies *one parse* of a file, and at submission no parse has
happened or been chosen. Returning a blob id under the name `document_id` would hand callers an
identifier whose spans and geometry anchor to nothing — the exact confusion the two-level identity
exists to prevent. This is a correction to the founding document, recorded rather than papered over.

**Alternatives considered.** *Parse eagerly on submit so a `document_id` can be returned* — makes
submission expensive and picks a parser before the caller has said what they need it for.

---

## R9 — Observability is mostly already built

**Decision.** Reuse the existing per-layer `observe.py` modules. The pipeline adds three things and
no new logging system: a correlation context carrying `request_id` and `processing_id`, one
`pipeline.stage` event per stage recording `step_id`, duration, outcome, and `reused: true|false`,
and in-process counters for stages executed versus reused. Tracing is a callable hook, not a
dependency.

**Rationale.** `docdoc/{ingest,extraction,grounding,validation}/observe.py` already emit structured
events through the standard library's `logging`, already carry identities and versions, and already
enforce the no-content rule — `validation/observe.py` argues the boundary explicitly for the stage
most able to blur it. Building a second mechanism would leave two, and the new one would be the one
without the argument attached. What is genuinely missing is correlation across stages and the
executed/reused fact, which no existing module could have known about.

Not taking an OpenTelemetry dependency is a decision, not an omission: the constitution says "where
practical", the events already carry every id a span would, and a hook lets a deployment bridge them
without the base install growing a tracing stack.

**Alternatives considered.** *`prometheus_client` for counters* — a dependency, for numbers a caller
can read off the result. *`structlog`* — a dependency, for what `logging` plus a dict already does
in this codebase.

---

## R10 — Limits are mostly already built too

**Decision.** Reuse `ingest.Limits`, which already carries `allowed_media_types` and a size cap and
is enforced by `SourceFile.from_bytes` and re-checked unconditionally inside `parse()` before any
routing or transmission. The pipeline threads it through; the HTTP layer adds exactly one thing
ingest cannot know about — a request-body cap enforced while reading, before the body is buffered.

**Rationale.** FR-039 and FR-040 read like new work and are mostly a wiring job. The one genuinely
new requirement is that an oversized *request* must be refused before it is read into memory, which
is a transport concern and belongs in the transport layer.

**Alternatives considered.** *A second limits type in the pipeline* — two vocabularies for one
concept, and the FR-031 prohibition on exactly that.

---

## R11 — The match-view cache stays in memory

**Decision.** A bounded in-process cache in `grounding/view.py`, keyed on `(document_id,
match_view_version)`. No filesystem, no store dependency, no change to `ground()`'s signature. The
bound is a maximum entry count with least-recently-used eviction, configurable, with a documented
default — "bounded" was the word this decision originally used, and FR-020 now requires a number
behind it.

**Rationale.** ADR-0006 says the view is cached by `document_id` + `match_view_version`, and
`view.py` already has `_view_id_for(document_id, version)` — the key exists and nothing uses it for
caching. Persisting the view was considered and buys very little: when a run repeats, the *grounding
artifact itself* is cached and the view is never built at all. The case the memory cache actually
serves is the one persistence would not — several extractions ground against the same document
inside one process, which is what an evaluation sweep does 48 times.

Keeping it in memory also keeps `docdoc.grounding` free of file access, which its forbidden-imports
contract and its "deterministic all the way down" claim both depend on.

**Alternatives considered.** *Store the view as an artifact* — real work, marginal benefit, and it
would put I/O inside the layer whose headline property is that it has none.

---

## R12 — argparse, and the CLI ships in the base install

**Decision.** `argparse` from the standard library. A `docdoc` console entry point in
`[project.scripts]`. No extra, no new dependency.

**Rationale.** FR-053 requires that the base install acquire no command-line framework. The reading
that puts the CLI behind an extra satisfies it; the reading that uses the standard library satisfies
it *and* makes the command available to everyone who typed `pip install docdoc`. The founding
document's reason for wanting a CLI at all — "a developer should not need to deploy five services
just to test docdoc" — argues against making them find a second install line. argparse handles
subcommands, mutually exclusive groups, and help text, which is the whole surface here.

**Alternatives considered.** *Typer or Click* — nicer to write, and both are a dependency in the base
install or a second install step for the feature meant to lower the barrier. *A `docdoc[cli]`
extra* — the extra exists to keep something out; with argparse there is nothing to keep out.

---

## R13 — FastAPI, behind `docdoc[api]`

**Decision.** `fastapi` and `uvicorn` under a new `docdoc[api]` extra, confined to `docdoc.api`, and
added to the forbidden-imports contracts of the deterministic layers alongside the provider SDKs.

**Rationale.** The constitution's Principle X names FastAPI in the list of things the domain model
must stay free of, and its development-compose line names an `api` service — the stack anticipates
this choice. Request and response validation is pydantic, which the project already uses for every
model, so the request shapes are the same kind of object as everything else.

**Alternatives considered.** *`http.server` from the standard library* — no dependency, and hand-rolled
routing, hand-rolled validation, hand-written OpenAPI, for a service that must map a dozen typed
errors onto status codes. The dependency is confined to one package behind an opt-in extra, which is
the arrangement Principle IV already sanctions for every provider SDK.

---

## R14 — The versioning policy that closes `TODO(PRE_1_0_VERSIONING)`

**Decision.** ADR-0011 will state a `0.x` policy: while the major version is `0`, a **minor** bump
may break any public API, a **patch** bump never breaks one, and every breaking change ships with a
changelog entry naming what moved and what to do about it. Two surfaces get an explicit deprecation
path rather than a silent break, because things outside the repository are pinned to them: the
kernel's identity derivations (`blob_id`, `document_id`, the artifact chain) and the on-disk artifact
format. Nothing is promised stable before `1.0.0` beyond that.

**Rationale.** The decision has been open since the founding, gated on "before first public release",
and this is the last milestone able to carry it. The narrow promise is the honest one: the kernel API
is still expected to churn, so a broad stability claim would be broken within a release. But identity
derivations and the artifact format are different in kind — a change to either invalidates data that
already exists on someone else's disk, and that deserves a warning rather than a version number.

**Alternatives considered.** *Leave it open* — the constitution permits it, since the gate is the
release rather than this milestone. Rejected because a decision that outlives the last plan capable
of carrying it does not get made later; it gets made accidentally, by the first release that ships
without it.

---

## Open questions carried into implementation

None blocking. Two things to watch and decide with code in front of us:

- **Where the store root defaults to.** FR-044 forbids a shared or world-readable default, and the
  artifacts contain extracted values. A per-user cache directory is the obvious answer; the mode bits
  on it are worth being deliberate about rather than inheriting.
- **Whether `PipelineResult` should carry the parsed `Document`.** It is large, and callers who want
  it can fetch it from the store by `document_id`. Deciding this while writing the CLI's `inspect`
  command, which is the consumer that would tell us.
