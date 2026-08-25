# Public Contract: HTTP Interface

**Feature**: `007-pipeline-api-cli` | **Date**: 2026-08-22

FastAPI behind the `docdoc[api]` extra, confined to `docdoc.api` (research R13). Single node,
synchronous, no queue, no database (research R7).

## 1. Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/documents` | Submit source bytes. Returns the **blob identity**. |
| `GET` | `/v1/documents/{blob_id}` | That blob's metadata: identity, size, media type. |
| `POST` | `/v1/documents/{blob_id}/extract` | Run the pipeline. Returns a job identity **and the result** (FR-067). |
| `GET` | `/v1/jobs/{job_id}` | The job's status. |
| `GET` | `/v1/jobs/{job_id}/result` | The full result. |

**Which of these need a store** (FR-068). Submission and the two job endpoints do: submission has
nowhere to put the bytes without one, and a job lookup is definitionally a store lookup. Running an
extraction and reading what it produced do not, because the run's response carries the result. With
no store configured, `POST /v1/documents` is refused with an explicit error naming the setting that
would fix it — accepting bytes the deployment cannot keep, and handing back an identity that will
never resolve, is the worse answer.

## 2. Why submission returns a blob identity

The founding sketch had this endpoint return a `document_id`. Under ADR-0002 a `document_id`
identifies *one parse* of a file, and at submission no parse has happened or even been chosen.
Returning a blob id under that name would hand callers an identifier whose spans and geometry anchor
to nothing.

So the field is named for what it is. Identical bytes submitted twice yield the same identity and one
stored blob (FR-021).

## 3. Why the job model needs no queue

The run happens inside the `POST …/extract` request. On success the response carries the **terminal
artifact id** as the job id, which is ADR-0003's `processing_id` and therefore covers every
result-affecting input transitively (FR-007, FR-033) — **and the result itself, in full** (FR-067).

Returning only an identity would be a receipt the caller often cannot redeem. With no store
configured the terminal artifact is never written (FR-017), and after a degraded write it is written
nowhere (FR-063); in both cases the run succeeded, the result exists, and the only copy of it is the
one this response either carries or throws away. The job endpoints are for *later* retrieval by
someone who has the id and not the result.

A failed run produces no terminal artifact and therefore no job. It returns a typed error in the same
response — carrying **the stage outcomes and the results the completed stages produced** (FR-066).
Since there is no job to fetch afterwards, this response is the only place a partial result can
appear, and FR-004's "MUST NOT discard partial results" is a promise about the library that this is
what makes true at the boundary. The command line says the same thing with exit code `2`.

`GET /v1/jobs/{id}` is a store lookup. Status is drawn from a closed set of **three**:

| Status | When |
|---|---|
| `succeeded` | The terminal artifact is in the store. |
| `unavailable` | The id is well-formed and the artifact is not in the store (FR-035, FR-036). |
| `unknown` | The id is not a well-formed artifact identity, so no run could have produced it. |

There is no `pending`. Fabricating one for an id nobody issued is how a client waits forever.

**`unavailable` does not distinguish "never produced" from "produced and since cleared", and that is
deliberate.** The store is content-addressed and append-only, `clear()` leaves no tombstone, and
nothing anywhere records what the store was never asked to hold — so the two are one observation, and
a status that claimed to tell them apart would be inventing the difference. `unknown` is reserved for
the judgement that *can* be made without history: whether the id is syntactically an artifact id at
all. An earlier draft of this contract assigned `unknown` to "never produced" and `unavailable` to
"cleared", which asked the store for a fact it does not hold.

A result reported `unavailable` MUST NOT be silently recomputed: the inputs may have moved since, and
returning a different result under the same id would break the one promise the identity makes
(FR-036).

## 4. Equality with the library

A result fetched over HTTP and the same run performed in-process agree on every value, verdict,
location, and identity (FR-034, SC-010). The interface serialises a result; it does not produce a
different one. This is asserted by a contract test that runs both and compares, not by inspection.

## 5. Limits

| Limit | Where enforced |
|---|---|
| Request body size | Here, while reading, before the body is buffered. |
| Document size | `ingest.Limits`, before any parse or transmission (research R10). |
| Media type allowlist | `ingest.Limits`, from the bytes — never from a client-declared type. |

An oversized or disallowed submission is refused before any parser runs and before any provider is
contacted, and leaves no temporary file behind (FR-039, FR-041, SC-009).

## 6. Errors

Every failure is a stable, provider-neutral, typed docdoc error naming the stage at fault (FR-037).

| Error | Status |
|---|---|
| `UnsupportedDocumentError` | 415 (type) / 413 (size) |
| `SchemaError` | 400 — unknown or unresolvable schema |
| `ParserCapabilityError` | 422 — no available parser satisfies the request |
| `ProviderError`, `ModelProviderError` | 502 |
| `ExtractionError`, `GroundingError`, `ValidationError` | 422 |
| `ArtifactError` | 500 — a stored artifact failed its integrity check |
| `PipelineError` | 500 |

The body carries the error class name, the stage, docdoc's own message, and structured detail — and,
where the failure happened mid-run, the per-stage `outcomes` and the `results` the completed stages
produced (FR-066):

```json
{
  "error": {"class": "ValidationError", "stage": "validate", "message": "...", "detail": {}},
  "outcomes": [{"stage": "parse", "status": "executed", "artifact_id": "sha256:..."}],
  "results": {"parse": {...}, "extract": {...}, "ground": {...}}
}
```

It MUST NOT carry a provider's error text, which may quote the document it choked on. Note that
`results` legitimately contains extracted values: they are the caller's own document coming back, on
the response to the caller's own request. That is a different thing from a log line, and FR-043's
prohibition is about logs. The one rule that holds in both places is that a **provider's** message
never appears.

Note that a document failing validation is **not** an error: the run succeeded and the result carries
an invalid verdict, exactly as the library returns it.

## 7. What the HTTP interface will not do

- **Require itself.** The library is fully usable without it, and its dependencies stay out of the
  base install (FR-038, FR-053).
- **Authenticate, authorise, meter, or rate-limit.** Out of scope by the spec's Assumptions; a
  deployment puts docdoc behind its own gateway. Stated so it is a decision rather than an oversight.
- **Queue, defer, retry across requests, call back, stream, or batch.** All out of scope.
- **Hold state of its own.** Blobs and artifacts are in the store; there is no job table and no
  session.
