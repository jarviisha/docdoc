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
| `POST /v1/documents/{blob_id}/runs` | **yes** — and a run-state database |
| `GET /v1/runs/{run_id}` | a run-state database |
| `DELETE /v1/runs/{run_id}` | a run-state database |
| `GET /healthz`, `GET /readyz` | no — `/healthz` touches nothing at all |

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

## Running a worker alongside the API

The routes above are synchronous: the run happens inside the request, and a
400-page scan holds the connection for the several minutes it takes. Three more
routes accept a document and return before any stage executes, and they need a
second process to do the work and a database to record it.

```bash
pip install 'docdoc[api,postgres]'
export DOCDOC_RUN_DATABASE_URL=postgresql://docdoc:docdoc@localhost:5432/docdoc

docdoc migrate                      # explicit, idempotent, never applied on boot
docdoc worker --health-port 8001    # blocks until signalled
```

Same image, same configuration vocabulary, different entry point. The worker
reads `DOCDOC_SCHEMA_PATHS`, `DOCDOC_STORE_ROOT`, `DOCDOC_MODEL_ADAPTERS` and the
rest exactly as the API does — there is no second set of variable names to keep
in step.

```bash
RUN=$(curl -sS -X POST "$BASE/documents/$BLOB/runs?schema=invoice@1" | jq -r .run_id)
curl -sS "$BASE/runs/$RUN" | jq '{status, attempts, processing_id}'
curl -sS -X DELETE "$BASE/runs/$RUN" | jq .status     # requests cancellation
```

A succeeded run names a `processing_id`, and the **unchanged** job routes above
serve the result. A `run_id` is an attempt and a `processing_id` is a result;
submitting one document twice gives two of the first and one of the second.

Three things to get right, in order of how expensive they are to get wrong:

- **`docdoc migrate` is a step you run, never something a process does on boot.**
  With several workers starting at once, an implicit migration is several
  processes altering one table, and the schema a deployment ends up with depends
  on which container won. `docdoc migrate --check` exits non-zero when anything
  is pending, which is what a rollout gates on.
- **Scale by replica count, not by a flag.** There is no `--concurrency`: a
  worker runs one document at a time, because PyMuPDF and `rapidfuzz` hold the
  GIL in bursts and a threaded worker lets one long parse starve a sibling's
  heartbeat until that sibling loses a lease it is still executing.
- **More than one worker needs a shared store.** Point every process at the same
  `DOCDOC_STORE_URL` (or a shared filesystem). With a private `DOCDOC_STORE_ROOT`
  per worker everything still *works* — every result correct, nothing logged, no
  metric moved — and every worker re-parses every document. The only evidence is
  the bill.

`--health-port` is optional and makes the worker answer the same `/healthz` and
`/readyz` the API does, so one orchestrator configuration covers both process
types. See [`docs/concepts/runs.md`](../docs/concepts/runs.md).

## Health and readiness

Two routes, outside `/v1` and outside authentication, served by the API and by
any worker given a `--health-port`:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/healthz   # 200, always
curl -sS http://127.0.0.1:8000/readyz | jq .
# {"status": "not_ready", "unmet": ["run-state-database"]}
```

`/healthz` returns a constant and touches nothing — a liveness probe that checked
a dependency would restart every replica for a fault none of them has.

`/readyz` reaches the run-state database and the store, caches the outcome for
two seconds, and names what it cannot reach. It is **strict**: a process that
cannot reach the database reports not ready even though the synchronous routes
above would still serve every request correctly. That withdraws working capacity
on purpose — no orchestrator's readiness probe can express "route the synchronous
half here", so a per-capability signal would be one nothing could consume.

Point your container runtime's health check at `/healthz` and your orchestrator's
readiness probe at `/readyz`. Using `/readyz` for both makes a database outage a
restart loop.

## Authentication

**Off by default, and a deployment that has not enabled it is exactly as exposed
as it was before.** That default is the *compatible* one rather than the safe
one: it exists so upgrading breaks nothing, and it means security is opt-in.

```bash
export DOCDOC_API_KEYS_FILE=/etc/docdoc/keys.json
```

```json
{"keys": [{"sha256": "<sha256 of the key>", "tenant_id": "acme"}]}
```

Then every route except `/healthz` and `/readyz` requires
`Authorization: Bearer <key>`, each key resolves to exactly one tenant, and a
tenant sees only its own blobs, runs, and results — cross-tenant reads return
responses byte-identical to those for an identifier that never existed.

**Every route** means every one: `/ui`, `/docs`, `/redoc` and `/openapi.json`
included. The two probes are the whole exemption list, because kubelet and an
ELB target group issue a bare request and requiring a key there would make an
authenticated deployment permanently unhealthy. One consequence is worth knowing
before you turn it on: a browser cannot send a bearer token, so **the viewer is
unavailable on an authenticated deployment** — it fails at the door instead of
loading and then failing on every call it makes.

The file holds hashes and never keys, so a leak of it is not a set of working
credentials. There is no flag and there never will be: `argv` is readable by
every process on the host.

Enabling it over a store that already has content in it needs one more thing.
Pre-existing content sits at `<root>/blobs/…` with no tenant segment and stays
there — nothing is copied or moved — so name the tenant it belongs to:

```bash
export DOCDOC_DEFAULT_TENANT=acme     # in the API, in every worker, and in `docdoc migrate`
```

`docdoc migrate` records that answer and refuses to change it afterwards, because
moving it once content exists would leave everything at a path nothing looks at —
and the symptom is not an error but correct answers plus a silent re-payment for
every parse.

**With authentication off there is no authorisation, no quota, and no rate
limiting either.** Put docdoc behind whatever your deployment already uses, on a
private network or a loopback interface.

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
none. Three things to know before exposing it.

**With no key file configured it is unauthenticated, so anyone who can reach it can spend your
provider budget.** That is the default and it is unchanged from Milestone 8 — authentication exists
now, and a deployment that has not enabled it is exactly as exposed as it always was.

**With a key file configured the viewer does not work at all.** `/ui` requires a credential like
everything except the two probes, and a browser has no way to send a bearer token, so the interface is
unavailable to it. That is the honest failure rather than a new limitation: before, the page loaded
and then every `/v1` call it made was refused. A viewer usable under authentication needs a session
mechanism this milestone deliberately does not add.

**A run continues after the page is closed**, because closing a browser stops the waiting and not the
work. If a proxy sits in front, allow a request duration at least as long as your slowest extraction,
or it will terminate runs you have already paid for.

The interface belongs on a trusted network. docdoc ships no `serve` command, so it cannot pick a safer
bind address for you.

## See also

- [`contracts/http-api.md`](../specs/007-pipeline-api-cli/contracts/http-api.md) — the full contract
- [`contracts/http-api-additions.md`](../specs/008-grounding-viewer/contracts/http-api-additions.md) — the two newer endpoints
- [`docs/concepts/viewer.md`](../docs/concepts/viewer.md) — how the viewer works, and what carries no test
- [`docs/concepts/pipeline.md`](../docs/concepts/pipeline.md) — why a job needs no queue
- [`docs/concepts/runs.md`](../docs/concepts/runs.md) — the two identities, redelivery, and the limits of cancellation
- [`examples/submit_async_run.py`](submit_async_run.py) — submit and poll to completion, runnable
- [`examples/view_grounding.md`](view_grounding.md) — a PDF to a rectangle, offline
- [`examples/run_pipeline.py`](run_pipeline.py) — the same thing in-process
