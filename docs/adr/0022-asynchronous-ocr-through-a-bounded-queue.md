# ADR-0022: OCR Is a Run Kind, Accepted Into a Bounded Queue

- **Status**: Accepted
- **Date**: 2026-10-20
- **Amends**: [ADR-0021](0021-caller-named-parsers-and-ocr-endpoints.md) and the OCR gateway design's
  "no async OCR" non-goal (`POST /v1/ocr` no longer answers in the request); the constitution's
  Development Compose sentence — see the Sync Impact Report for v1.10.0 → v1.11.0
- **Relates to**: [ADR-0013](0013-asynchronous-run-model.md) (the run model this reuses),
  [ADR-0015](0015-deletion-over-a-content-addressed-store.md) (the sweep this narrows by kind),
  [ADR-0018](0018-webhook-delivery-semantics.md) (callbacks, unchanged)
- **Design**: `docs/superpowers/specs/2026-10-19-async-ocr-queue-design.md`
- **Principles engaged**: XI (MVP Discipline and Scale Through Boundaries), VIII (no silent drop)

## Context

The OCR gateway (ADR-0021) shipped `POST /v1/ocr` as a synchronous, storeless route and said
async OCR would come "if the sync route proves too slow". The reason it came is not speed. The
systems calling docdoc — mmerp first — cannot make their users wait on OCR: a user pushes a batch,
possibly continuously, and reads results somewhere else. A synchronous route holds a connection for
as long as the slowest page takes (thirty minutes with the example endpoint deadline), and nothing
bounds how many such connections arrive at once.

The run model already solves holding nothing open (ADR-0013). What it lacked was a run that is OCR
alone — every run required a schema — and any bound on how much work may be waiting.

## Decision

### 1. OCR is a run kind on the existing `runs` table

`runs.kind` is `extraction` or `ocr` (migration 0008). An OCR run carries no `schema_identity`, and
carries the caller's `ocr_parser` and `ocr_language` instead; a check constraint and the model both
enforce that the schema belongs to extraction alone. Every existing row and every caller that names
no kind is `extraction`. The worker dispatches on kind: an OCR run executes the parse stage alone
(`docdoc.pipeline.parse_only`), which shares the pipeline's parse-and-cache path, so the `Document`
is filed as the parse-stage artifact and its id is the run's `processing_id`. A redelivered OCR run
finds it filed and calls no OCR host.

### 2. Every OCR request goes through the queue

`POST /v1/ocr` answers `202` with the run and performs no OCR. The synchronous behaviour is removed,
not kept beside it, so nothing bypasses the caps below. **This is a breaking change**: OCR now needs
the run database and an object store, and a deployment without `DOCDOC_RUN_DATABASE_URL` answers
`503` (not retryable, naming the setting). `GET /v1/runs/{run_id}/result` serves an OCR run's
`Document` once it has succeeded; an extraction run's result stays at
`GET /v1/jobs/{processing_id}/result`.

Everything that can be refused without OCR is refused at submission and before a slot is taken:
an oversized body (413), an unknown or incapable parser and an undeclared language (422).

### 3. Two caps, both on by default, refused honestly

- **Per consumer (tenant)**: OCR runs queued or running, default 100
  (`DOCDOC_OCR_QUEUE_PER_TENANT`, or `ocr_queue` per tenant in the limits file). Over it: **429**,
  `Retry-After: 30`, the limit refusal shape every other limit uses.
- **Per deployment**: the same count over every tenant, default 500 (`DOCDOC_OCR_QUEUE_TOTAL`).
  Over it: **503**, `Retry-After: 30`, `"error": "system_overloaded"` — "the system is overloaded;
  please send again later".

Two status codes so a caller can tell "my share is full" from "docdoc is full". Both are counted
live from `runs`, never stored. A count that fails is a transient 503, never read as zero. OCR runs
count only toward these caps; the extraction limits (`concurrent_runs` and the rest) count only
extraction runs, so neither backlog refuses the other.

The count and the insert are two statements, so N concurrent submissions can overshoot a cap by
up to N−1. That margin is accepted and marked in the code; an advisory lock is the upgrade if it
ever matters.

### 4. OCR results are kept a day after finishing, by default

`DOCDOC_OCR_RETENTION_HOURS`, default 24. The deadline is stamped at submission and **re-anchored
to the run's finish**, so a deep queue cannot sweep a result before anybody could fetch it. The
worker's tick sweeps expired OCR runs **even when `DOCDOC_RUN_RETENTION_DAYS` is unset** — the one
exception to "every maintenance step is off unless configured", made because the user asked for
OCR results to be cleaned up after a day. With run retention off, the sweep is narrowed to OCR and
never touches an extraction run. Survivorship is still computed over every surviving run of the
tenant (ADR-0015 §1), so a blob an extraction run names outlives the OCR run that shared it.

### 5. Workers are separated by kind, not prioritised

`docdoc worker --kind ocr|extraction` claims only that kind; no flag claims any kind, as before. The
composition runs `worker` (extraction) and `ocr-worker` (OCR), so an OCR backlog never delays
extraction and OCR workers scale to the OCR hosts' capacity on their own. This is separation, not
priority: within a kind the claim order is unchanged. Two worker services needed constitution
v1.11.0, recorded as an amendment rather than a reading of the old sentence.

## Consequences

- mmerp submits, polls or receives a callback, and fetches — three calls where there was one — and
  is told the truth when docdoc is full.
- OCR throughput is `ocr-worker` replicas × OCR host capacity. The caps bound the queue's length,
  not its speed.
- A deployment that used `/v1/ocr` with no database loses OCR until it configures one.

## Alternatives considered

- **Keep the synchronous route beside the queue.** Rejected: it would be a path around both caps,
  and the consumers this serves cannot use it anyway.
- **A separate OCR queue table, or Redis.** Rejected: ADR-0013 and the deferred-technology list;
  the `runs` table already has leases, redelivery, cancellation, callbacks, and retention.
- **Priority instead of separate workers.** Rejected: a priority still lets one kind's backlog sit
  in front of the other's whenever the favoured kind has work.
