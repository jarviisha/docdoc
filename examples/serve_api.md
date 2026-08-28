# Running the HTTP interface

The HTTP interface ships behind an extra, because the library must stay usable
with no web framework installed at all.

```bash
pip install 'docdoc[api]'
export DOCDOC_SCHEMA_PATHS=/etc/docdoc/schemas
export DOCDOC_STORE_ROOT=/var/lib/docdoc
export DOCDOC_MODEL_ADAPTERS=gemini
export GEMINI_API_KEY=...

uvicorn --factory docdoc.api.app:create_app --host 127.0.0.1 --port 8000
```

`--factory` rather than a module-level `app`, so that importing
`docdoc.api.app` — to read the error table, say — does not read your environment
and build a deployment as a side effect.

## A whole run

```bash
BASE=http://127.0.0.1:8000/v1

BLOB=$(curl -sS --data-binary @invoice.pdf "$BASE/documents" | jq -r .blob_id)
curl -sS "$BASE/documents/$BLOB" | jq .

JOB=$(curl -sS -X POST "$BASE/documents/$BLOB/extract?schema=invoice@1" | jq -r .job_id)
curl -sS "$BASE/jobs/$JOB" | jq .
curl -sS "$BASE/jobs/$JOB/result" | jq '.validation.verdict'
```

The extract call returns the result **as well as** the identity, so that last
fetch is for somebody who has the id later — not for the caller who just paid for
the run. That matters more than it sounds: with no store configured the terminal
artifact is never written, and an identity-only response would be a receipt
nobody can redeem.

## Which endpoints need a store

| Endpoint | Needs a store |
|---|---|
| `POST /v1/documents` | **yes** — there is nowhere to put the bytes without one |
| `GET /v1/documents/{blob_id}` | **yes** |
| `POST /v1/documents/{blob_id}/extract` | no — the response carries the result |
| `GET /v1/jobs/{job_id}` | **yes** — a job lookup *is* a store lookup |
| `GET /v1/jobs/{job_id}/result` | **yes** |

There is no default store location. Artifacts hold extracted values and blobs
hold whole source documents, so where they accumulate is your decision. With
`DOCDOC_STORE_ROOT` unset, a submission is refused with an error naming the
setting rather than accepting bytes the deployment cannot keep.

## Job status

Three values, and never `pending`:

| Status | Meaning |
|---|---|
| `succeeded` | the terminal artifact is in this store |
| `unavailable` | the id is well-formed and is not in this store |
| `unknown` | the id is not a well-formed artifact identity |

`unavailable` deliberately does not distinguish *never produced* from *produced
and since cleared*. The store is content-addressed and append-only, `clear()`
leaves no tombstone, and nothing records what the store was never asked to hold —
so a status claiming to tell them apart would be inventing the difference. A
result reported `unavailable` is never silently recomputed: the inputs may have
moved, and returning a different result under the same id would break the one
promise the identity makes.

## Authentication is your gateway's job

**There is none here**, and that is a decision rather than an oversight. docdoc
has no authentication, no authorisation, no tenancy, no quota, and no rate
limiting. Put it behind whatever your deployment already uses, on a private
network or a loopback interface, and do not expose it directly.

Two limits *are* enforced, because they bound what the process will do rather
than who may ask it to:

- the request body cap (`DOCDOC_MAX_REQUEST_BYTES`, default 32 MiB), applied
  while the body is being read rather than after it is buffered;
- the document size limit and the media-type allowlist, which are
  `ingest.Limits`'s and are checked from the bytes, never from a client-declared
  `Content-Type`.

An oversized or disallowed submission is refused before any parser runs and
before any provider is contacted.

## Errors

Every failure is a stable, provider-neutral, typed docdoc error naming the stage
at fault. A **document that fails validation is not an error** — the run
succeeded and the result carries an invalid verdict, exactly as the library
returns it, with a `200`.

A failure part-way through a run returns the typed error **and** the results of
the stages that completed. That response is the only place they can appear: a
failed run produces no terminal artifact and therefore no job to fetch later.

A provider's error message never crosses the wire, because it may quote the
document it choked on.

## Running a document without a store

`POST /v1/extract` takes the bytes directly, returns the result, and **writes nothing** — no blob, no
artifact, no temporary file, and not even when a store *is* configured. Whether a run persists is a
property of the endpoint you called, never of how the deployment is set up (ADR-0012).

```bash
export DOCDOC_SCHEMA_PATHS=/etc/docdoc/schemas
export DOCDOC_MODEL_ADAPTERS=echo
unset DOCDOC_STORE_ROOT                    # nothing to configure

curl -sS "$BASE/schemas" | jq .            # what this deployment can run
curl -sS -X POST "$BASE/extract?schema=invoice@1" --data-binary @invoice.pdf | jq .verdict
```

It returns **no `job_id`**: a run that writes no terminal artifact has no identity to hand back, since
ADR-0003 makes the job id *be* the terminal artifact id. Submit the document first if you want one.

**This changes what an unconfigured deployment exposes.** Before, a deployment with no
`DOCDOC_STORE_ROOT` refused every extraction request — submission was refused, and every extracting
route took a `blob_id` that only a submission could produce. It now serves them. If you were relying on
that refusal as a closed door, it has opened.

## The browser interface

Behind a second extra, and absent unless you ask for it:

```bash
pip install 'docdoc[api,ui]'
# then open http://127.0.0.1:8000/ui/
```

Read-only: it shows which page and which rectangle each value came from and says plainly when there is
none. Two things to know before exposing it — **it is unauthenticated, so anyone who can reach it can
spend your provider budget**, and **a run continues after the page is closed**, because closing a
browser stops the waiting and not the work. If a proxy sits in front, allow a request duration at least
as long as your slowest extraction, or it will terminate runs you have already paid for.

The interface belongs on a trusted network. docdoc ships no `serve` command, so it cannot pick a safer
bind address for you.

## See also

- [`contracts/http-api.md`](../specs/007-pipeline-api-cli/contracts/http-api.md) — the full contract
- [`contracts/http-api-additions.md`](../specs/008-grounding-viewer/contracts/http-api-additions.md) — the two newer endpoints
- [`docs/concepts/viewer.md`](../docs/concepts/viewer.md) — how the viewer works, and what carries no test
- [`docs/concepts/pipeline.md`](../docs/concepts/pipeline.md) — why a job needs no queue
- [`examples/view_grounding.md`](view_grounding.md) — a PDF to a rectangle, offline
- [`examples/run_pipeline.py`](run_pipeline.py) — the same thing in-process
