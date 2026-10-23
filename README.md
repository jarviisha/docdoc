# docdoc

An open-source **Intelligent Document Processing service** that you host yourself.

docdoc turns unstructured documents into structured, validated, traceable data while preserving
source-level provenance through the entire pipeline. The differentiator is not that it can call an
LLM — everyone can do that. It is that every extracted value can answer:

> **Where did this come from?**

You run it from its Docker image. Clients reach it through the HTTP API; the people who run the
deployment use the operations console in a browser and a handful of commands inside the container.
docdoc is not a Python library and makes no promise about importing its packages
([ADR-0027](docs/adr/0027-service-only-surface.md)).

## Quickstart

You need Docker with Compose, and Node.js 24+ to build the console once. No API key and no cloud
account.

**1. Clone, build the console, and start the stack.**

```bash
git clone https://github.com/jarviisha/docdoc.git && cd docdoc
(cd ui && npm ci && npm run build)        # the console's assets; compose mounts ui/dist
docker compose -f packaging/docker/compose.yml up -d --build
docker compose -f packaging/docker/compose.yml exec api docdoc migrate
```

This starts the API on `http://localhost:8000`, an extraction worker, an OCR worker, Postgres and an
S3-compatible object store. `docdoc migrate` creates the run database's tables. Nothing applies
them on startup, so run it once now and again after each upgrade.

If port 5432 is already taken on your machine (by your own Postgres, say), `up` fails on the
`postgres` service. Set `DOCDOC_PG_PORT=55433` in `packaging/docker/.env` and run `up` again; only
the host side of the port changes.

**2. Create a console account and sign in.**

```bash
docker compose -f packaging/docker/compose.yml exec api docdoc console-user add admin
```

It asks for a password twice; it must be at least 12 characters. Open
`http://localhost:8000/console` and sign in as `admin`.

**3. See a document's text and where each word sits. No model is involved.**

In the console, open **Submit**. Leave "Submit as" on **OCR**, choose the tenant (a fresh stack has
one, `default`), choose `tests/fixtures/pdf/digital_invoice.pdf` from your clone, and press
**Submit**. The run opens. Once it has succeeded, the page shows with a box around every word, and
the recognised text sits beside it. A digital PDF is read by the native PDFium parser, offline.

**4. Extract fields, check them, and measure quality.**

Open **Submit** again. Set "Submit as" to **Extraction**, choose the same tenant, the schema
`invoice@1` and the same file, and press **Submit**. When the run succeeds it shows each extracted
field beside the page; hover a field to see where on the page it was read.

The stack's default model adapter is `echo`. It returns canned answers from
`tests/fixtures/echo/invoice@1.json` without calling any model, so the whole flow can be walked
offline. Those answers were written for a different invoice: some match the sample (the invoice
number `INV-001`) and some do not (the total is `1240.00`; the PDF says `228.00`).
That is useful here. Mark `invoice_number` **Correct**. Mark `total` **Wrong**, enter `228.00`
under "Should be" and a short reason (a wrong check needs one), and press **Save check**.

Then open **Quality**, choose the tenant and the `invoice` schema, and press **Re-run on checked
documents**. docdoc re-runs every document you checked with the current configuration and scores
the results against your checks. The report shows, per field, how many were right.

### Use a real model

Copy `packaging/docker/.env.example` to `packaging/docker/.env` and set both lines:

```bash
DOCDOC_MODEL_ADAPTERS=gemini
GEMINI_API_KEY=your-key
```

Then run `docker compose -f packaging/docker/compose.yml up -d` again. Compose reads `.env` from
the directory that holds `compose.yml`, and recreates the containers whose settings changed. Submit
the extraction again, and the values come from the model. Re-run Quality to compare.

Scans and photos need a parser that does OCR: Azure Document Intelligence, Google Cloud Vision, or
an OCR endpoint you run. See [ingest](docs/concepts/ingest.md) for the settings each needs.

### Stop it

```bash
docker compose -f packaging/docker/compose.yml down       # keeps the data
docker compose -f packaging/docker/compose.yml down -v    # deletes it too
```

## Configuration

Every setting is an environment variable. In the compose stack you set them in
`packaging/docker/.env`; `compose.yml` forwards each one to the API and both workers, and lists
them with their reasons. These are the ones a deployment decides first:

```sh
DOCDOC_SCHEMA_PATHS=/schemas                   # where schemas live; os.pathsep-separated
DOCDOC_MODEL_ADAPTERS=gemini                   # adapter preference order, comma-separated
DOCDOC_GEMINI_MODEL=gemini-3.5-flash           # which model answers
GEMINI_API_KEY=...                             # or GOOGLE_API_KEY
DOCDOC_STORE_URL=s3://bucket/prefix            # where artifacts and blobs land…
DOCDOC_STORE_ROOT=/var/lib/docdoc              # …or a directory instead; the URL wins if both are set
DOCDOC_RUN_DATABASE_URL=postgresql://…         # run state, accounts, credentials, corrections
```

`DOCDOC_SCHEMA_PATHS` has no default worth relying on: with none, the registry is **empty**,
because docdoc ships no schema of its own. A schema is your data. The `schemas/` directory in this
repository holds examples, and compose mounts it at `/schemas`.

`DOCDOC_STORE_URL` takes `s3://bucket[/prefix][?endpoint_url=…]`; the query parameter is what MinIO,
R2, SeaweedFS and every other S3-compatible store needs and AWS does not. **Run more than one worker
and you need a shared store**: with a private directory per worker every lookup misses, every run is
correct, and you pay for every parse twice with nothing anywhere reporting a problem.

Apply the database schema explicitly with `docdoc migrate`. Nothing applies it on startup, because
several workers booting at once would be several processes altering one table.

Three configure asynchronous runs, and only the worker reads the last two:

```sh
DOCDOC_API_KEYS_FILE=/secrets/keys.json   # turns authentication on; absent means off
DOCDOC_RUN_LEASE_SECONDS=90               # how long a worker's claim holds
DOCDOC_RUN_MAX_ATTEMPTS=3                 # claims before a run is abandoned
```

OCR runs are queued (ADR-0022), and the queue is bounded twice. Both refusals carry `Retry-After`:

```sh
DOCDOC_OCR_QUEUE_PER_TENANT=100   # one consumer's OCR runs queued or running → else 429
DOCDOC_OCR_QUEUE_TOTAL=500        # every consumer's together → else 503, "overloaded"
DOCDOC_OCR_RETENTION_HOURS=24     # how long a result is kept after its run finishes
```

All three are on with no configuration. OCR results are swept even when
`DOCDOC_RUN_RETENTION_DAYS` is unset; extraction runs are not.

Two configure retention, and **both are unset by default, which means nothing is ever removed**:

```sh
DOCDOC_RUN_RETENTION_DAYS=30   # how long a run is kept; unset means kept forever
DOCDOC_RUN_SWEEP_BATCH=500     # runs removed per pass
```

A sweep removes the run **and the artifacts and blobs no surviving run of that tenant still
names**. Content written outside a run, such as by `POST /v1/documents/{blob_id}/extract`, is never
touched. The worker sweeps between claims; a deployment running **no** worker sweeps nothing, and
`docdoc sweep` is how it removes anything at all.

One configures how quickly a revoked credential stops working:

```sh
DOCDOC_RUN_CREDENTIAL_TTL_SECONDS=30   # how long a resolution is cached
```

**This is a number and not "immediately", because "immediately" is not something you can test.**
Each process that authenticates caches what a credential resolved to for this long, so revoking one
takes effect everywhere within one lifetime, with no restart.

Six configure per-tenant limits, and all are absent by default, which means nothing is counted and
nothing is refused:

```sh
DOCDOC_LIMIT_SUBMISSIONS_PER_MINUTE=60     # fixed window
DOCDOC_LIMIT_CONCURRENT_RUNS=25            # counted from the runs table, never stored
DOCDOC_LIMIT_RUNS_PER_PERIOD=10000         # fixed window, 30 days
DOCDOC_LIMIT_TOKENS_PER_PERIOD=5000000     # the budget refuses the *next* submission
DOCDOC_LIMITS_FILE=/secrets/limits.json    # per-tenant overrides, and priority ceilings
DOCDOC_PRIORITY_CEILING=ordinary           # the highest priority a client may be granted
```

A refusal is `429`, it names which limit and when to come back, and it creates no run. See
[limits](docs/concepts/limits.md).

Six configure webhook delivery, and the first is what turns it on at all:

```sh
DOCDOC_DELIVERY_SECRETS_FILE=/secrets/webhook-secrets.json
DOCDOC_DELIVERY_MAX_ATTEMPTS=6             # then the delivery comes to rest at `failed`
DOCDOC_DELIVERY_TIMEOUT_SECONDS=10
DOCDOC_DELIVERY_BACKOFF_SECONDS=30         # doubled per attempt, capped at a day
DOCDOC_MAINTENANCE_DELIVERY_BATCH=32       # deliveries attempted per tick
DOCDOC_DELIVERY_ALLOW_PRIVATE=false        # permits http and private addresses; a guard
```

**A deployment that registers no callbacks performs no outbound request at all.** Destinations are
validated at registration *and again before every attempt*, because validating once is defeated by
DNS rebinding. See [delivery](docs/concepts/delivery.md).

Two export traces of what docdoc already logs. The image does not include the exporter by default,
so build it with the `otel` extra added:

```sh
DOCDOC_EXTRAS=api,postgres,s3,google,azure,gcv,ocr,pdf,otel   # read by `up --build`
DOCDOC_OTLP_ENDPOINT=http://collector:4318/v1/traces
DOCDOC_OTLP_HEADERS='authorization=Bearer …'
```

One configures routing, and it is unset by default; with no policy there is no `routing` block on a
run at all:

```sh
DOCDOC_ROUTING_POLICY=/secrets/routing.json
```

A routing decision reads grounding status and score, validation severity and verdict, and schema
requiredness. It does **not** read `model_confidence`. See [routing](docs/concepts/routing.md).

Two bound the worker's unrequested work, and one names who owns content written before tenants
existed:

```sh
DOCDOC_MAINTENANCE_INTERVAL_SECONDS=60     # how often a tick runs
DOCDOC_MAINTENANCE_BUDGET_MS=5000          # how long one may take
DOCDOC_DEFAULT_TENANT=acme                 # must match in the API, every worker, and migrate
```

`docdoc migrate` records `DOCDOC_DEFAULT_TENANT` and refuses to change it afterwards: moving it once
content exists would leave that content at a path nothing looks at.

Five more exist and are rarely worth touching:

```sh
DOCDOC_ECHO_FIXTURES=/fixtures      # canned answers for the offline `echo` adapter
DOCDOC_MATCH_VIEW_CACHE=8           # folded views held in memory; LRU, default 8
DOCDOC_MAX_REQUEST_BYTES=33554432   # HTTP request body cap, applied while reading
DOCDOC_MAX_DOCUMENT_BYTES=52428800  # largest document accepted, before any parse
DOCDOC_MAX_PAGES=1000               # page-count limit, checked as soon as it is known
```

The echo adapter is **never** selected automatically. The compose stack names it, which is an
explicit local choice. No configuration that merely fails to name a usable adapter can land on it,
because auto-selecting a fixture adapter would turn a missing credential into a stream of
confident, fabricated extractions.

The worker also accepts the settings that change what it does as flags of the same meaning
(`docdoc --help`). An explicit flag beats the environment, which beats the default.

## API routes

All under `/v1`. Documents and results:

```text
POST /v1/documents                        → the blob identity
GET  /v1/documents/{blob_id}              → its size and media type
POST /v1/documents/{blob_id}/extract      → the job identity, and the result, inside the request
POST /v1/extract                          → the result, storing nothing
GET  /v1/jobs/{job_id}                    → succeeded | unavailable | unknown
GET  /v1/jobs/{job_id}/result             → the stored result
GET  /v1/schemas                          → the schemas this deployment has
GET  /v1/parsers                          → the parsers this deployment has configured
```

Runs accept work without holding the connection open:

```text
POST   /v1/documents/{blob_id}/runs       → 202 and a run_id, before any stage runs
POST   /v1/ocr                            → 202 and a run_id; OCR happens on a worker
GET    /v1/runs                           → this tenant's runs, newest first
GET    /v1/runs/{run_id}                  → queued | running | succeeded | failed | cancelled
GET    /v1/runs/{run_id}/result           → an OCR run's Document, once it has succeeded
DELETE /v1/runs/{run_id}                  → requests cancellation
```

A `run_id` is not a `job_id`. A run is an *attempt* and exists the moment the request is accepted;
a `processing_id` is a *result*, the terminal artifact's identity, and the job routes serve an
extraction run's result by it. Submitting one document twice
gives you two run ids and one processing id. See [runs](docs/concepts/runs.md), and
[`examples/submit_async_run.py`](examples/submit_async_run.py) for a client.

Callbacks, corrections and quality:

```text
POST   /v1/callbacks                      → register a destination; 422 if the policy refuses it
DELETE /v1/callbacks/{callback_id}        → 204, idempotent
GET    /v1/runs/{run_id}/delivery         → what became of the notification
POST   /v1/runs/{run_id}/corrections      → record a check: a value is correct, or wrong
GET    /v1/runs/{run_id}/corrections      → this tenant's, and nobody else's
GET    /v1/answers                        → the checked documents and fields, per schema
POST   /v1/evaluations?schema=            → 202; re-run the checked documents, score them
GET    /v1/evaluations                    → this tenant's reports, newest first
GET    /v1/evaluations/{evaluation_id}    → one report, with its per-field table
```

[`examples/receive_webhook.py`](examples/receive_webhook.py) verifies a delivery's signature. See
[quality](docs/concepts/quality.md) for what a report holds.

Administration, which requires the `admin` scope. A caller without it gets `404` rather than `403`:

```text
POST   /v1/admin/credentials              → issue; the only time a key is readable
GET    /v1/admin/credentials              → what has been issued; no key, no digest
DELETE /v1/admin/credentials/{id}         → revoke, on every process, with no restart
DELETE /v1/admin/tenants/{tenant_id}      → erase a customer's data
DELETE /v1/admin/documents/{blob_id}      → erase one document and the runs over it
```

Two are outside `/v1` and outside authentication, served by the API and every worker alike:

```text
GET /healthz    → 200, always; touches no database, no store, no provider
GET /readyz     → 200, or 503 naming the dependency it cannot reach
```

The console speaks only `/v1/console/*`, which answers only to a console session. See
[the console](docs/concepts/console.md).

**There is no route that assigns a correction to a reviewer, no queue of work, no review state, and
no `PATCH` on a result.** Principle IX permits the correction model and forbids the review platform,
and a contract test asserts each of those absences by name.

**Authentication exists, and it is off by default.** Point `DOCDOC_API_KEYS_FILE` at a key file and
every `/v1` route requires `Authorization: Bearer <key>`; each key resolves to exactly one tenant,
and a tenant sees only its own blobs, runs and results. The file holds SHA-256 hashes rather than
keys, so a leak of it is not a set of working credentials:

```json
{"keys": [{"sha256": "…", "tenant_id": "acme"}]}
```

**Revoking a key from the file still requires restarting the process.** The file is read once, at
startup. Delete a compromised key from it and the key keeps working until every process holding the
old mapping has restarted. For anything you may need to revoke, issue credentials instead:
`docdoc credential issue --tenant acme` stores one in the run database and prints it once, and
`docdoc credential revoke <id>` stops it working on every process within
`DOCDOC_RUN_CREDENTIAL_TTL_SECONDS`, with no restart.

> **A deployment that has not enabled authentication is exposed.** With no key file and no issued
> credential there is no authentication on any `/v1` route, one implicit tenant owns everything,
> and anyone who can reach the service can spend your model provider budget. Put it behind your own
> gateway, or turn this on. See [ADR-0014](docs/adr/0014-tenant-scoping-and-store-namespacing.md)
> and [`examples/serve_api.md`](examples/serve_api.md).

## Operating

The image carries one command, `docdoc`, for the people who run the deployment. Run it inside a
container, for example `docker compose -f packaging/docker/compose.yml exec api docdoc …`:

| Command | What it does |
|---|---|
| `docdoc migrate` | applies the run database's schema; records the default tenant |
| `docdoc worker --kind extraction\|ocr` | claims and runs queued runs; the compose stack starts one of each |
| `docdoc sweep` | removes expired runs and what only they named |
| `docdoc credential issue\|list\|revoke` | manages API credentials; `issue --admin` makes the first admin key |
| `docdoc console-user add\|passwd\|disable\|list` | manages console accounts |
| `docdoc erase` | erases a tenant's data, or one document's |
| `docdoc store clear` | empties the artifact store, or one stage of it |

Each takes `--help`. The guides: [rotating a credential](examples/rotate_credential.md),
[erasing a tenant](examples/erase_tenant.md), and [serving the API](examples/serve_api.md).

The operations console, at `/console`, is the browser face of the same work: list and inspect runs,
submit a document, check extracted fields and measure quality, issue and revoke credentials, and
erase a tenant or a document. You sign in with an account made by `docdoc console-user add`; the
session is a server-side cookie that ends after 15 minutes idle. **It is not a review platform**:
there is no reviewer assignment, review queue, workload, review state or disposition, and a build
check greps for those words ([ADR-0019](docs/adr/0019-operations-console-boundary.md)). See
[the console](docs/concepts/console.md).

## Architecture

**Three principles shape everything.**

1. **Source location is a first-class concern.** A document is never reduced to a `str`. The
   canonical representation preserves pages, tokens, character spans, normalized geometry, and
   ingestion provenance, so any text range resolves back to a page and a bounding box.
2. **Grounding is computed, never self-certified.** An LLM may propose a quote; docdoc alone
   decides whether that quote resolves to a real span. Ungrounded values stay distinguishable from
   grounded ones at every layer.
3. **The MVP stays small.** No Kafka, no Temporal, no Kubernetes, no vector database, no workflow
   engine. Scale comes through stable boundaries, not premature infrastructure.

These are three of twenty in the [project constitution](docs/constitution.md), which governs every
specification, plan, and code change in this repository.

**Four process types**, from one image: the API, an extraction worker, an OCR worker, and
`docdoc migrate`, with Postgres for run state and an S3-compatible store for blobs and artifacts.
Retention, delivery and counter expiry happen in the worker's loop between claims, not in a
scheduler.

**A run is four stages**, each with its own content-addressed artifact
([ADR-0003](docs/adr/0003-content-addressed-artifact-chain.md)): parse, extract, ground, validate.
A schema is data, not code; adding a document type is adding two files under `schemas/`.
Extraction returns the model's byte-faithful `claimed_text`; grounding, deterministic and offline,
resolves it to a span, a page and a box; validation checks the values against the schema's rules and
returns one of three verdicts. Changing a prompt reuses the parse instead of paying the parser again.

Dependencies flow strictly downward, and the rule is machine-checked in CI by `import-linter`:

```text
API, CLI → Recording → Evaluation → Pipeline → Validation → Grounding
         → Extraction → Ingest → Artifacts → Kernel
```

`API` and `CLI` are siblings, not a stack: neither may import the other. Provider SDKs appear only
inside adapter directories. The **kernel** is the bottom layer; its only runtime dependency is
`pydantic`, and it performs no file, network, clock, or random access, so identical inputs always
produce identical outputs. That is enforced by an AST scan and a runtime audit hook.

Identity is two-level ([ADR-0002](docs/adr/0002-blob-and-document-identity.md)):

| | Derived from | Identifies |
|---|---|---|
| `blob_id` | the original bytes | the **source file** |
| `document_id` | blob + parser id + version + options | **one specific parse** of it |

Spans and geometry anchor to `document_id`, so one parse's positions can never be silently applied
to another.

## Development

```bash
make install    # uv sync --all-extras, and the console's npm deps
make test       # the Python suite
make lint       # ruff, mypy strict, layer boundaries
make ui         # the console: typecheck, tests, build
make dev        # API, OCR worker and the console's dev server against a local Postgres
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the rules that are not negotiable.

## Documentation

- [Constitution](docs/constitution.md) — the governing principles
- [Architecture decisions](docs/adr/) — the accepted ADRs
- [Document concepts](docs/concepts/document.md) and the [identity model](docs/concepts/identity.md)
- [How ingest works](docs/concepts/ingest.md) — the parsers, and the text-layer decision
- [How extraction works](docs/concepts/extraction.md) — the two identities, and the stage boundary with grounding
- [How grounding works](docs/concepts/grounding.md) — the three states, and the match view
- [How validation works](docs/concepts/validation.md) — the three verdicts, and rules as data
- [The pipeline](docs/concepts/pipeline.md) and [artifacts](docs/concepts/artifacts.md) — the stages, the chain, and reuse
- [Runs](docs/concepts/runs.md) — the lifecycle, redelivery, and cancellation
- [Quality](docs/concepts/quality.md) and [evaluation](docs/concepts/evaluation.md) — checks, reports, and the golden set
- [The operations console](docs/concepts/console.md)
- [Credentials](docs/concepts/credentials.md), [limits](docs/concepts/limits.md), [retention](docs/concepts/retention.md), [delivery](docs/concepts/delivery.md), [routing](docs/concepts/routing.md)
- [HTTP API contract](specs/007-pipeline-api-cli/contracts/http-api.md) and [run routes](specs/009-asynchronous-runs/contracts/runs-http-api.md)
- [Contributing](CONTRIBUTING.md)

## License

[Apache License 2.0](LICENSE). Chosen for its explicit patent grant, which matters for a project
intended to be embedded in enterprise document pipelines — see
[ADR-0007](docs/adr/0007-apache-2-license.md).
