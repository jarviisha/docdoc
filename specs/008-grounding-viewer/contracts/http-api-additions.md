# Public Contract: HTTP Interface Additions

**Feature**: `008-grounding-viewer` | **Date**: 2026-08-25

Two endpoints added to the interface of Milestone 7, plus the static assets. Everything else about
that contract — single node, synchronous, no queue, no database — is unchanged, and
`specs/007-pipeline-api-cli/contracts/http-api.md` is corrected in the same change (§5 below).

## 1. Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/extract` | Run the pipeline over submitted bytes. Returns **the result and no job**. Needs no store. |
| `GET` | `/v1/schemas` | The identities this deployment has configured. |

## 2. `POST /v1/extract`

```
POST /v1/extract?schema=invoice@1
Content-Type: application/pdf
<raw document bytes>
```

The body is the document. `schema` is a query parameter carrying a concrete `name@version`, the same
form `POST /v1/documents/{blob_id}/extract` accepts and the same form `GET /v1/schemas` returns —
one vocabulary, not three.

**It writes nothing.** No blob, no artifact, no temporary file outliving the request. The route passes
a null artifact store **unconditionally**, so a deployment *with* a store configured gets the same
non-persistence as one without: the endpoint decides whether a run persists, not the configuration
(FR-008, SC-008). This is the property most likely to be broken by a later "optimization" that reuses
the deployment's store because it happens to be there, and SC-008 exists to fail when that happens.

**It returns no job identity.** A storeless run produces no terminal artifact, and ADR-0003's
`processing_id` *is* the terminal artifact id — so there is nothing to hand back and nothing to fetch
later. This is a property of the choice, not a gap in it. A caller who wants a retrievable identity
submits the document first and uses the store-backed route, exactly as before.

The success body is Milestone 7's run response **minus** `job_id`. Every value, verdict, location, and
identity agrees with the same run performed in-process and with the same run performed through the
store-backed route (FR-004, SC-006).

**Limits** are the submission path's, applied by calling the same code rather than by restating the
rule: the request body cap while reading, then `SourceFile.from_bytes` and `check_limits`, with the
media type detected **from the bytes** and never from a client-declared `Content-Type` (FR-005). An
oversized or disallowed document is refused before any parser runs, before any provider is contacted,
and leaves no temporary file (Milestone 7's FR-039, FR-041).

**Errors** are Milestone 7's, unchanged, with the same statuses and the same body shape — including
the per-stage `outcomes` and the `results` completed stages produced when a run fails partway
(FR-006). No new error class is introduced.

**Exposure.** A deployment configured without a store previously refused every extraction request over
HTTP, because submission was refused and extraction needed a `blob_id`. After this endpoint it serves
them. An operator who read that refusal as a closed door is entitled to learn it has opened (FR-063),
and the documentation says so rather than leaving it to be discovered on an invoice.

## 3. `GET /v1/schemas`

```json
{ "schemas": [ { "identity": "invoice@1" }, { "identity": "receipt@2" } ] }
```

Sorted, and drawn from `SchemaRegistry.identities()` — which is already the exact set `resolve()`
accepts, so a listed identity is runnable without translation (FR-010).

**No paths, and nothing about where a schema came from** (FR-011). Not merely trimmed for tidiness:
this endpoint is unauthenticated like the rest of the interface, and a filesystem layout is not
something to hand out for free.

**An empty list is success**, not an error (FR-012). A deployment with no schemas configured is validly
configured — it just has nothing to offer — and the viewer names the setting that populates it rather
than reporting a fault.

The listing carries no field descriptions. `describe()` exists and returns them; offering them here
would publish schema internals over an unauthenticated endpoint to serve a choice that needs only a
string.

## 4. Static assets

Served from the same origin as the API, so no cross-origin configuration is introduced anywhere
(FR-034). Present only when the `ui` extra is installed; absent otherwise, and the API is fully
functional without them.

When the extra is installed but no built assets are found, the application says so and names the step
that fixes it, distinguishing the two cases it can be in (FR-037): a checkout where the interface has
not been built, and an installation where the asset distribution is missing. It never serves a blank
page and never fails obscurely.

## 5. The correction to Milestone 7's contract

`specs/007-pipeline-api-cli/contracts/http-api.md` §1 currently reads:

> Running an extraction and reading what it produced do not [need a store], because the run's response
> carries the result.

That is false against the code and always was: `POST /v1/documents/{blob_id}/extract` refuses without a
store (`src/docdoc/api/app.py:218`), and it cannot do otherwise, because its input is a `blob_id` and a
`blob_id` exists only after a submission. The correction states which routes need a store and why, and
names `POST /v1/extract` as the one that needs none — making the sentence true rather than deleting it
(FR-007, SC-012).

The correction is part of this milestone's change, not a follow-up. A contract that disagrees with the
code is worse than one that is silent, because it is trusted.
