# docdoc

An open-source **Intelligent Document Processing engine**.

docdoc turns unstructured documents into structured, validated, traceable data while preserving
source-level provenance through the entire pipeline. The differentiator is not that it can call an
LLM — everyone can do that. It is that every extracted value can answer:

> **Where did this come from?**

**Status:** Milestones 1–9 implemented — kernel, parsers, extraction, grounding, validation,
evaluation, the pipeline and HTTP interface, the grounding viewer, and asynchronous runs. An extracted
value can be **located**, **checked**, and **measured**: docdoc will tell you that an invoice's stated
total does not equal the sum of its lines, point at the place on the page the total was read from, and
tell you how often it gets that right against a golden set. Since Milestone 9 it will also accept that
invoice without holding your connection for the several minutes a long scan takes — see
[Roadmap](#roadmap).

## What it does today

Hand it a PDF and ask where a value physically sits:

```python
from docdoc.ingest import CapabilityRequest, parse

document = parse(
    pdf_bytes,
    require=CapabilityRequest(media_type="application/pdf", geometry=True),
)

(span,) = document.find("INV-001")
document.page_for(span)  # (0,)  -> page 1
document.locate(span)  # (Geometry(page_index=0, bbox=BBox(0.45, 0.1, 0.63, 0.13)),)
```

Note that no provider is named. You ask for the capabilities you need; which parser supplies them
is configuration. And the routing decision is on the result, per page:

```python
document.provenance.text_layer.rule_id          # 'text-layer@1'
document.provenance.text_layer.text_layer_usable  # True
document.provenance.text_layer.pages            # per-page verdicts and character counts
```

### Extract structured values against a versioned schema

A schema is **data**, not code. Adding a document type is adding two files; there is no
`InvoiceService` and no `if schema.name == "invoice"` anywhere in the engine.

```python
from docdoc.extraction import SchemaRegistry, default_adapter, extract

registry = SchemaRegistry.from_paths(["schemas/"])
result = extract(
    document,
    schema="invoice@1",
    registry=registry,
    adapter=default_adapter(),   # configuration picks it; this line names no provider
)

result.value_at("total").value          # Decimal('1240.00')  — not a float
result.value_at("total").claimed_text   # '1,240.00'  — byte-faithful, as the model returned it
result.value_at("due_date").present     # False — an explicit absence, not an error
```

Note the identity pair. `invoice@1` is the contract a consumer pins to; `schema_hash` answers a
different question, and both are recorded:

```python
result.provenance.schema_identity   # 'invoice@1'      — did the contract change?
result.provenance.schema_hash       # 'sha256:37a9f6…' — did anything result-affecting change?
result.artifact_id                  # the extraction's own content-addressed identity
```

Rewording a field description moves the hash and not the version, which invalidates the extraction
and **reuses the parse**. [ADR-0008](docs/adr/0008-schema-evolution-policy.md) has the bump rules.

**Extraction resolves no grounding, and that is a stage boundary rather than a gap.** Every value
leaves extraction with `grounding=None`, because grounding is its own stage with its own artifact
under [ADR-0003](docs/adr/0003-content-addressed-artifact-chain.md). What extraction supplies is the
byte-faithful `claimed_text`; `docdoc.grounding.ground()` resolves it:

```python
from docdoc.grounding import ground

located = ground(document, result)
total = located.outcomes["total"]
total.status                                        # 'exact'
document.text[total.span.start:total.span.end]      # 'TOTAL             1,240.00'
total.pages, total.geometry                         # (0,), (Geometry(...),)
located.counts.grounding_rate                       # 1.0
```

Grounding is deterministic and entirely offline — no network, no credentials, no model call, enforced
by an import contract. It answers *where*, never *whether*: a value that disagrees with the text it
resolved to is a **validation** finding. And `model_confidence` is stored verbatim, labelled
UNTRUSTED, and routes nothing ([ADR-0004](docs/adr/0004-confidence-semantics.md)).

Then ask whether the result is acceptable:

```python
from docdoc.validation import validate

result = validate(extraction, located, schema)
result.verdict                                      # Verdict.INVALID
[(f.field_path, f.reason, f.expected, f.actual) for f in result.findings]
# [('total', 'sum_mismatch', '1420.00', '1240.00')]
result.findings[0].span, result.findings[0].pages   # copied from grounding, never recomputed
```

Validation is a separate stage with its own artifact, because a validator built into a prompt is
unverifiable and cannot be regression-tested (Principle VII). Cross-field rules are **data in the
schema** evaluated by one generic engine — there is no `InvoiceValidator` — and the verdict has three
states, because a run where nothing could be checked must not report the same word as one where
everything was checked and passed.

And finally, ask how often it gets that right:

```python
from docdoc.evaluation import evaluate, load_golden_set, load_prediction_set

golden = load_golden_set("datasets/mvp/manifest.json")
report = evaluate(golden, load_prediction_set("datasets/mvp/predictions"))

report.metrics.micro["field_accuracy"].value        # 0.9286
report.metrics.micro["field_accuracy"].numerator    # 26
report.metrics.micro["field_accuracy"].denominator  # 28
report.partial.covered_labels                       # 28 of 48 — the restricted tier was skipped
report.report_id                                    # "sha256:c3b4bc8e…"
```

Every metric states its numerator and denominator, and a metric with an empty denominator is `None`
rather than `0.00` — a rate of zero reads as total failure, and an unasked question is not one. A
document that crashes has its labelled fields counted as *missing* and stays in every denominator, so
failing on the hard documents can never raise a score.

Run the examples, which need no infrastructure and no credentials at all:

```bash
uv sync --all-extras
uv run python examples/build_document.py                       # kernel only
uv run python examples/parse_pdf.py tests/fixtures/pdf/digital_invoice.pdf
uv run python examples/extract_invoice.py                      # extraction, offline
uv run python examples/ground_invoice.py                       # grounding, offline
uv run python examples/validate_invoice.py                     # validation, offline
uv run python examples/evaluate_golden_set.py                  # evaluation, offline
uv run python examples/compare_reports.py                      # regression detection, offline
uv run python examples/run_pipeline.py                         # all four stages plus reuse, offline
```

[`examples/serve_api.md`](examples/serve_api.md) covers running the HTTP interface.

### From the command line

Everything above, without writing a script. `pip install docdoc` gives you the command — the CLI is
`argparse`, so it costs no dependency.

```bash
docdoc parse    invoice.pdf                        # route, parse, report what came back
docdoc extract  invoice.pdf --schema invoice@1     # the whole pipeline
docdoc inspect  invoice.pdf --schema invoice@1     # every value, its verdict, and where it was found
docdoc inspect  --result sha256:3a1e…              # the same, read back from the store
docdoc explain  sha256:3a1e… --chain               # why an identity is that value
docdoc eval     manifest.json --predictions ./p    # score a golden set
docdoc store    clear --stage extract              # all of it, or one stage
```

Two rules make up most of the contract. With `--json`, standard output carries **exactly one** JSON
document and nothing else; diagnostics go to standard error in both forms. And the exit code
distinguishes the two things a single non-zero code confuses:

| Code | Meaning |
|---|---|
| `0` | the run completed and the document is valid |
| `1` | the run completed and the document is **invalid** — a real result, not an error |
| `2` | the run could not complete: a typed docdoc error |
| `64` | the invocation itself was wrong |

A script that treats "this invoice is wrong" as "docdoc is broken" is the outcome a single non-zero
code guarantees.

### Over HTTP

```bash
pip install docdoc[api]
uvicorn --factory docdoc.api.app:create_app
```

```text
POST /v1/documents                        → the blob identity
GET  /v1/documents/{blob_id}              → its size and media type
POST /v1/documents/{blob_id}/extract      → the job identity, and the result
POST /v1/extract                          → the result, storing nothing
GET  /v1/jobs/{job_id}                    → succeeded | unavailable | unknown
GET  /v1/jobs/{job_id}/result             → the stored result
```

A result fetched over HTTP and the same run performed in-process agree on every value, verdict,
location, and identity — asserted by a contract test that runs both and compares.

Three more accept work without holding the connection open, and need a database:

```text
POST   /v1/documents/{blob_id}/runs       → 202 and a run_id, before any stage runs
GET    /v1/runs/{run_id}                  → queued | running | succeeded | failed | cancelled
DELETE /v1/runs/{run_id}                  → requests cancellation
```

A `run_id` is not a `job_id`. A run is an *attempt* and exists the moment the request is accepted; a
`processing_id` is a *result* and is the terminal artifact's identity, so it cannot exist until the
stages feeding it have run. Submitting one document twice gives you two run ids and one processing
id, and that is the answer rather than a collision. A succeeded run names its `processing_id`, and
the unchanged job routes serve the result — see [runs](docs/concepts/runs.md).

Two more are outside `/v1` and outside authentication, served by the API and every worker alike:

```text
GET /healthz    → 200, always; touches no database, no store, no provider
GET /readyz     → 200, or 503 naming the dependency it cannot reach
```

Readiness is strict: a process that cannot reach the run-state database reports not ready even
though the synchronous routes would still work. That withdraws working capacity on purpose, because
no orchestrator's probe can express "route half the traffic here".

**Authentication exists, and it is off by default.** Point `DOCDOC_API_KEYS_FILE` at a key file and
every route except the two health routes requires `Authorization: Bearer <key>`; each key resolves
to exactly one tenant, and a tenant sees only its own blobs, runs, and results. The file holds
SHA-256 hashes rather than keys, so a leak of it is not a set of working credentials:

```json
{"keys": [{"sha256": "…", "tenant_id": "acme"}]}
```

> **A deployment that has not enabled it is exactly as exposed as it was before.** The default is the
> *compatible* one, not the safe one: it exists so that upgrading breaks nothing, and it means
> security is opt-in. With no key file configured there is no authentication on any route, one
> implicit tenant owns everything, and anyone who can reach the service can spend your model provider
> budget. Put it behind your own gateway, or turn this on. See
> [ADR-0014](docs/adr/0014-tenant-scoping-and-store-namespacing.md).

### Installing

```bash
pip install docdoc          # every deterministic layer and the CLI, no provider SDK
pip install docdoc[pdf]     # native PDF text path
pip install docdoc[azure]   # geometry-capable cloud path, for scans and images
pip install docdoc[gcv]     # image OCR for JPEG and PNG — no tables, no PDFs
pip install docdoc[google]  # the LLM adapter for extraction
pip install docdoc[api]     # the HTTP interface
```

### Configuration

Which schemas exist, which provider answers, and which model it uses are deployment decisions, so none
of them appears in your code:

```sh
export DOCDOC_SCHEMA_PATHS=/etc/docdoc/schemas   # where default_registry() looks; os.pathsep-separated
export DOCDOC_MODEL_ADAPTERS=gemini              # adapter preference order, comma-separated
export DOCDOC_GEMINI_MODEL=gemini-3.5-flash      # which model answers
export GEMINI_API_KEY=...                        # or GOOGLE_API_KEY
export DOCDOC_STORE_ROOT=/var/lib/docdoc         # where artifacts and blobs land — no default
export DOCDOC_STORE_URL=s3://bucket/prefix       # …or an object store, which is what lets two
                                                 #   workers reuse each other's artifacts
export DOCDOC_RUN_DATABASE_URL=postgresql://…    # run state, for asynchronous runs — no default
```

`DOCDOC_RUN_DATABASE_URL` is needed only by the asynchronous run routes and the worker, and it has
no default for the same reason `DOCDOC_STORE_ROOT` has none. A deployment that uses neither needs no
database at all, and the library and the command line need one in no configuration. Apply the schema
explicitly with `docdoc migrate`; nothing applies it on startup, because several workers booting at
once would be several processes altering one table.

`DOCDOC_STORE_URL` is the object-store form of `DOCDOC_STORE_ROOT`, and it wins when both are set —
a deployment that named both meant the more specific one. It takes
`s3://bucket[/prefix][?endpoint_url=…]`; the query parameter is what MinIO, R2, and every other
S3-compatible store needs and AWS does not, and keeping it in the URL makes "where the store is" one
value rather than three that can disagree. **Run more than one worker and you need this**, or a
shared filesystem: with a private root per worker every lookup misses, every run is correct, and you
pay for every parse twice with nothing anywhere reporting a problem.

Three configure asynchronous runs, and only the worker reads the last two:

```sh
export DOCDOC_API_KEYS_FILE=/etc/docdoc/keys.json # turns authentication on; absent means off
export DOCDOC_RUN_LEASE_SECONDS=90                # how long a worker's claim holds
export DOCDOC_RUN_MAX_ATTEMPTS=3                  # claims before a run is abandoned
```

One more matters only when you enable authentication over a store that already has content in it:

```sh
export DOCDOC_DEFAULT_TENANT=acme         # who owns everything written before tenants existed
```

Content written before Milestone 9 sits at `<root>/blobs/…` with no tenant segment, and it stays
there — nothing is copied or moved. Every other tenant lives under `<root>/t/<tenant_id>/`. This
names the one whose namespace *is* the root, and it must be the same value in the API, in every
worker, and in `docdoc migrate`, because it describes the store's layout rather than one
invocation's behaviour. `docdoc migrate` records it and refuses to change it afterwards: moving it
once content exists would leave that content at a path nothing looks at, and the symptom is not an
error but correct answers plus a silent re-payment for every parse.

Five more exist and are rarely worth touching:

```sh
export DOCDOC_ECHO_FIXTURES=./fixtures    # canned answers for the offline `echo` adapter
export DOCDOC_MATCH_VIEW_CACHE=8          # folded views held in memory; LRU, default 8
export DOCDOC_MAX_REQUEST_BYTES=33554432  # HTTP request body cap, applied while reading
export DOCDOC_MAX_DOCUMENT_BYTES=52428800 # largest document accepted, before any parse
export DOCDOC_MAX_PAGES=1000              # page-count limit, checked as soon as it is known
```

`DOCDOC_ECHO_FIXTURES` is the one to know about: with `DOCDOC_MODEL_ADAPTERS=echo` it makes the whole
pipeline runnable offline against committed answers. The echo adapter is **never** selected
automatically — no configuration that merely fails to name a usable adapter can land on it — because
auto-selecting a fixture adapter would turn a missing credential into a stream of confident,
fabricated extractions.

Every setting that can change what a command does gains a flag of the same meaning — `--schema-path`,
`--adapter`, `--echo-fixtures`, `--store`, `--store-url`, `--max-document-bytes`, `--max-pages` — so
there is no second vocabulary to learn. An explicit flag beats the environment, which beats the
default, per setting. Three live only on the subcommands that can use them:
`--run-database-url` on `docdoc migrate` and `docdoc worker`, and `--lease-seconds` and
`--max-attempts` on `docdoc worker` alone. A flag that did nothing to the command carrying it would
be the second vocabulary arriving from the other direction.

Four have no flag on purpose: `DOCDOC_MAX_REQUEST_BYTES` caps an HTTP request body and the command
line reads none; `DOCDOC_MATCH_VIEW_CACHE` bounds a per-process cache that a single run fills with
one entry; `DOCDOC_API_KEYS_FILE` names a credential file and enables an HTTP concern the command
line and the library do not have; and `DOCDOC_GEMINI_MODEL` and the credentials are per-provider — a
credential especially, since `argv` is readable by every process on the host.

`DOCDOC_STORE_ROOT` has **no default**, and that is deliberate. Artifacts hold extracted values and
blobs hold whole source documents, so where they accumulate is your decision rather than ours. With
none set, every run recomputes every stage and produces identical results; with one set, changing a
prompt reuses the parse instead of paying the cloud parser again.

Every one has a sensible default and every one can be overridden by an explicit argument — configuration
is the default, not a cage. `DOCDOC_SCHEMA_PATHS` is the exception worth knowing: with neither it nor an
explicit path, the registry is **empty**, because docdoc ships no schema of its own. A schema is your
data, not ours.

> **Licence note.** `docdoc[pdf]` installs [PyMuPDF](https://pymupdf.readthedocs.io/), which is
> **AGPL-3.0** (or a paid commercial licence). docdoc itself is Apache-2.0 and the extra is opt-in,
> so docdoc's own distribution is unaffected — but if you embed `docdoc[pdf]` in a closed-source
> pipeline, the AGPL applies to you. Know this before you install it, not after.
> See [ADR-0001](docs/adr/0001-parser-and-ocr-strategy-in-mvp.md).

## Three principles that shape everything

**1. Source location is a first-class concern.** A document is never reduced to a `str`. The
canonical representation preserves pages, tokens, character spans, normalized geometry, and
ingestion provenance, so any text range resolves back to a page and a bounding box.

**2. Grounding is computed, never self-certified.** An extracted value is not a string — it
carries its source spans, page, geometry, and grounding status. An LLM may propose a quote;
docdoc alone decides whether that quote resolves to a real span. Ungrounded values stay
distinguishable from grounded ones at every layer.

**3. The MVP stays small.** No Kafka, no Temporal, no Kubernetes, no vector database, no workflow
engine. The core library is usable with no database, no object store, and no running service.
Scale comes later through stable boundaries, not premature infrastructure. The execution model
may change; the core contracts must not.

These are three of twenty in the [project constitution](.specify/memory/constitution.md), which
governs every specification, plan, and code change in this repository.

## Architecture

Dependencies flow strictly downward, and the rule is machine-checked in CI:

```text
API, CLI → Recording → Evaluation → Pipeline → Validation → Grounding
         → Extraction → Ingest → Artifacts → Kernel
```

The chain above is the one `pyproject.toml` enforces, which is the only one worth writing down. Two
positions are not the ones a reader would guess, and both are deliberate. **`Artifacts` sits directly
above the kernel**, because it stores whole result models without importing one — the caller names
the model — so it depends on `pydantic` and two kernel helpers and nothing else. **`Pipeline` sits
directly above `Validation`**, the highest stage it drives, which yields `Recording → Pipeline`: the
recorder *calls* the pipeline rather than holding a second copy of the stage order. **`API` and `CLI`
are siblings, not a stack** — neither may import the other, which an ordered position cannot express,
so an `independence` contract states it instead.

**`Recording` sits above `Evaluation` deliberately**: the recorder
produces a prediction set and the scorer consumes one, so the data flows one way and the layers do
too — which is what makes `evaluation → recording` a build failure rather than a sentence in a
specification. Producing a prediction set needs a provider; scoring one must not.

The **kernel** is the bottom layer and depends on nothing above it. Its only runtime dependency is
`pydantic`; the base install adds `rapidfuzz` for grounding's approximate matching (ADR-0005). It performs no file, network, clock, or random access, so identical inputs always
produce identical outputs — enforced by an AST scan and a runtime audit hook, not by convention.

Identity is two-level ([ADR-0002](docs/adr/0002-blob-and-document-identity.md)):

| | Derived from | Identifies |
|---|---|---|
| `blob_id` | the original bytes | the **source file** |
| `document_id` | blob + parser id + version + options | **one specific parse** of it |

Spans and geometry anchor to `document_id`. Two parsers over the same bytes share a `blob_id` and
get different `document_id`s, so one parse's positions can never be silently applied to another.

## Installation

```bash
pip install docdoc          # kernel only — no provider SDKs, no OCR engine
```

Provider integrations ship as optional extras and are never pulled in unless you ask for them.

## Development

```bash
uv sync --all-extras
uv run pytest tests/unit tests/property      # fast
HYPOTHESIS_PROFILE=thorough uv run pytest    # what CI runs
uv run mypy src/docdoc/kernel                # strict
uv run ruff check . && uv run lint-imports   # lint + layer boundaries
```

The kernel's correctness rests on one property, verified across thousands of generated cases:

```text
locate(span) == merge(partition(document)).locate(span)
```

Cutting a document apart and reassembling it must not change where anything came from. No
higher-layer work merges while that property is failing or absent.

## Roadmap

| Milestone | Scope | Status |
|---|---|---|
| 1 | Kernel: Document IR, `locate` / `find` / `slice` / `merge`, identity | **Done** |
| 2 | Parsers: native PDF text path, one geometry-capable cloud provider | **Done** |
| 3 | Schema-driven extraction, one LLM adapter | **Done** |
| 4 | Deterministic grounding: exact → fuzzy → ungrounded | **Done** |
| 5 | Validation: schema, field, and cross-field rules | **Done** |
| 6 | Evaluation: golden dataset, field accuracy, grounding rate | **Done** |
| 7 | Pipeline, artifact store, CLI, and HTTP API | **Done** |
| 8 | Read-only grounding viewer, and the two endpoints that reach it | **Done** |
| 9 | Asynchronous runs, shared object storage, health routes, tenant scoping | **Done** |

Milestone 8 adds no guarantee — no stage, no provider, no change to any value docdoc produces. What it
changes is who can see the guarantees the first seven built.

Milestone 9 adds none either, and that is the claim it is measured on: a result obtained through a
worker and the same result obtained synchronously agree on every value, verdict, location, and
identity, and the golden-set metrics do not move by a digit. What it buys is that a 400-page scan no
longer holds an HTTP connection for the several minutes it takes.

## The browser viewer

```bash
pip install 'docdoc[api,ui]'
uvicorn --factory docdoc.api.app:create_app --port 8000    # then open /ui/
```

Read-only: it shows which page and which rectangle each extracted value came from, lists every value
docdoc could **not** place rather than hiding it, and edits nothing. Five things worth knowing before
you run it anywhere real:

- **It is unauthenticated by default, and anyone who can reach it can spend your model provider
  budget.** Milestone 9 added authentication and left it off, so this sentence is true of every
  deployment that has not set `DOCDOC_API_KEYS_FILE`. Until then it belongs on a trusted network;
  docdoc ships no `serve` command and cannot pick a safer bind address for you.
- **With authentication on, the viewer does not work** — and it fails at the door rather than after
  the page renders. `/ui` requires a credential like everything else except the two health routes,
  and a browser has no way to send a bearer token, so the interface is unavailable to it. That is
  the honest outcome rather than a regression: before, the shell loaded and then every `/v1` call it
  made was refused. A viewer that works under authentication needs a session mechanism this
  milestone deliberately does not add.
- **A run continues after you close the page.** There is no cancel, because closing a browser stops the
  waiting and not the work. A proxy in front of it must allow a request duration at least as long as
  your slowest extraction, or it will terminate runs you have already paid for.
- **Pages are shown selectively** — those carrying located values — because a deployment accepts
  1000-page documents by default. Every other page is reachable, and the interface says so on screen.
- **No accessibility conformance level is claimed.** What is guaranteed instead: every fact the overlay
  conveys is in the value list, the list works without a pointer, and no distinction depends on colour.
- **The rendered interface carries no automated test.** Its decisions are tested as pure functions; the
  screen is not. [How the viewer works](docs/concepts/viewer.md) says exactly what that costs.

A related change to the API: **`POST /v1/extract` runs a document with no store configured** and writes
nothing. A deployment that previously refused every extraction for want of storage now serves them
(ADR-0012).

## Documentation

- [Constitution](.specify/memory/constitution.md) — the governing principles
- [Architecture decisions](docs/adr/) — fourteen accepted ADRs, and no open constitutional decisions
- [Document concepts](docs/concepts/document.md)
- [Identity model](docs/concepts/identity.md)
- [Kernel API contract](specs/001-kernel-document-ir/contracts/kernel-api.md)
- [Ingest API contract](specs/002-ingest-parser-layer/contracts/ingest-api.md)
- [How ingest works](docs/concepts/ingest.md) — the two paths and the text-layer decision
- [How extraction works](docs/concepts/extraction.md) — the two identities, and the stage boundary with grounding
- [How grounding works](docs/concepts/grounding.md) — the three states, the match view, and why the two scores are not comparable
- [How validation works](docs/concepts/validation.md) — the three verdicts, rules as data, and why docdoc has its own regex dialect
- [How evaluation works](docs/concepts/evaluation.md) — the six outcomes, the denominators, the two tiers, and how to add a document to the golden set
- [How the viewer works](docs/concepts/viewer.md) — the three geometry states, why no coordinate is transformed, and what carries no automated test
- [The pipeline](docs/concepts/pipeline.md) — the four stages, the reuse decision, what a cached parse still pays for, and why a job needs no queue
- [Artifacts](docs/concepts/artifacts.md) — the chain, the two hashes, which misses are errors, and the one symptom of a missed version bump
- [Runs](docs/concepts/runs.md) — the two identities, the lifecycle, why redelivery is mostly "resume", and the exact limits of cancellation
- [Grounding API contract](specs/004-deterministic-grounding/contracts/grounding-api.md)
- [Validation API contract](specs/005-deterministic-validation/contracts/validation-api.md)
- [Extraction API contract](specs/003-schema-driven-extraction/contracts/extraction-api.md)
- [Pipeline and store API contract](specs/007-pipeline-api-cli/contracts/pipeline-api.md)
- [CLI contract](specs/007-pipeline-api-cli/contracts/cli.md) — commands, output forms, exit codes
- [HTTP API contract](specs/007-pipeline-api-cli/contracts/http-api.md) — endpoints, statuses, error shapes
- [HTTP API additions](specs/008-grounding-viewer/contracts/http-api-additions.md) — the storeless run and the schema listing
- [View-model contract](specs/008-grounding-viewer/contracts/view-model.md) — the viewer's tested surface, and what it does not cover
- [Run routes and authentication](specs/009-asynchronous-runs/contracts/runs-http-api.md) — the three run routes, the two health routes, and the exemption list
- [The runs layer](specs/009-asynchronous-runs/contracts/runs-layer.md) — the queue protocol, the worker loop, store namespacing, and the transition events
- [Contributing](CONTRIBUTING.md)

## License

[Apache License 2.0](LICENSE). Chosen for its explicit patent grant, which matters for a project
intended to be embedded in enterprise document pipelines — see
[ADR-0007](docs/adr/0007-apache-2-license.md).
