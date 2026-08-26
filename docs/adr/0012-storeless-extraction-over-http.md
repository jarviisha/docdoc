# ADR-0012: Extraction Over HTTP Without a Store

- **Status**: Accepted
- **Date**: 2026-08-25
- **Supersedes**: the store-coupling described in `specs/007-pipeline-api-cli/contracts/http-api.md` §1
- **Principles engaged**: IV (Provider-agnostic adapters), VIII (No silent fallback), XI (MVP discipline)

## Context

The library has been able to run a pipeline over bytes with no store since Milestone 7 — `run()`
takes a `store` and accepts a null one, and FR-017 says so. The HTTP interface could not.

The coupling is not incidental to any one endpoint; it is a consequence of the only input shape on
offer. `POST /v1/documents/{blob_id}/extract` takes a `blob_id`. A `blob_id` exists only after a
submission. Submission is refused outright without a store, because accepting bytes a deployment
cannot keep and handing back an identity that will never resolve is the worse answer (FR-068). So
**over HTTP, every document came to rest on disk before anything could read it**, and no
configuration could change that.

Two things made this worth reopening rather than documenting.

**The project had already rejected exactly this, elsewhere.** The `gcv` adapter declines Google
Vision's asynchronous API — and therefore declines PDF support on that parser — because it "requires
Cloud Storage buckets for input and output, which is a storage dependency and a place for document
content to come to rest outside the process". The same objection applies to docdoc's own interface
with more force, because there the resting place is not a third party's design but ours.

**The contract already claimed otherwise.** `http-api.md` §1 read: *"Running an extraction and
reading what it produced do not [need a store], because the run's response carries the result."* That
sentence was false against the code on the day it was written, and could not have been made true by
any endpoint whose input is a `blob_id`.

The occasion was Milestone 8's browser viewer, which holds the document in the browser and needs
somewhere to send it that does not require the deployment to have configured storage first. But the
defect predates the viewer and would have outlived it.

## Decision

### 1. `POST /v1/extract` takes the bytes and writes nothing

The body is the document; `schema` is a query parameter carrying a concrete `name@version`. The run
happens inside the request, as every run does. The response carries the full result.

### 2. It passes a null artifact store **unconditionally**

Not "when no store is configured". A deployment *with* a store gets the same nothing written.

Whether a run persists is a property of **the endpoint the caller chose**, never of how the
deployment happens to be set up. Stated this way because the alternative is a plausible future
change: the store is right there on the deployment object, and reusing it would look like an
optimisation and read like one in review. `tests/integration/test_storeless_extract.py` fails when
that happens, and its docstring says so.

### 3. A storeless run has no job, and the field is absent rather than null

ADR-0003 makes `processing_id` the terminal artifact id. A run that writes no terminal artifact has
no such id — there is nothing to hand back and nothing to fetch later.

`job_id: null` would say the same thing and invite a caller to send it to `GET /v1/jobs/{id}`, which
would answer `unknown` about an identity nobody issued. Omitting the field ends that conversation one
step earlier. A caller who wants a retrievable identity submits the document first and uses the
store-backed route, which is unchanged.

### 4. The old contract is corrected rather than weakened

Two ways existed to end the disagreement between §1 and the code. The sentence could have been
weakened to match, documenting the store coupling as intended. It was not, because the sentence
described the better system and the library already implemented it.

## Consequences

**A deployment configured without a store now serves extractions it previously refused.** This is a
change in exposure, not only in capability: the interface has no authentication, so anyone who can
reach it can now run documents through the deployment's model provider and spend its budget, on a
deployment where every such request used to be refused for want of storage. An operator who read that
refusal as a closed door is entitled to hear from us that it has opened, which is why Milestone 8's
FR-063 makes saying so a requirement rather than a courtesy.

**The two routes must not drift.** `POST /v1/extract` and `POST /v1/documents/{blob_id}/extract` are
one pipeline behind two doors; SC-006 asserts they agree on every value, verdict, location, and
identity. The response models share a base class so that the storeless one differs in exactly the
field it is defined to omit, and in nothing that drifted.

**Nothing is deprecated.** The store-backed route keeps its behaviour, its job identity, and its
reuse guarantees. This adds a path; it removes none.

## Alternatives considered

**Weaken the contract to match the code.** Cheapest, and it would have recorded as intentional a
coupling the project rejects elsewhere on principle. Rejected on that inconsistency alone.

**Relax the store check on the existing route.** Considered and rejected as incoherent: that endpoint
takes a `blob_id`, which exists only after a submission, so removing the guard converts a clear error
into an obscure 404 without opening any path.

**Let the viewer submit first and require a store.** Would have left the contract false and forced
every viewer deployment to configure storage — putting whole documents at rest to serve an interface
whose entire design keeps them in the browser.
