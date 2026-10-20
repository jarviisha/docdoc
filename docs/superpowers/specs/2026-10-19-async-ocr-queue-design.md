# Async OCR through a bounded queue

Status: approved. Date: 2026-10-19.

## Intent

The OCR gateway (`2026-10-19-ocr-gateway-design.md`) shipped `POST /v1/ocr` as a synchronous,
storeless route and said: *"Async OCR-only runs come later, if the sync route proves too slow."*
That moment has come, for a reason that is not speed but how consumers work.

Consumers of docdoc (mmerp first, others later) **cannot make their users sit and wait for OCR**.
A user pushes a batch of documents, possibly continuously, walks away, and looks at a results list
somewhere else. So docdoc must accept a document immediately, OCR it later at the pace the OCR
hosts can bear, and hand the result back when asked.

Accepting immediately must not mean accepting without limit. The queue is bounded, per consumer
and overall, and when it is full docdoc **says so honestly** ("the system is overloaded, please
send again later") instead of accepting work it will drop or holding a connection open.

## Current state (verified 2026-10-19)

- `POST /v1/ocr` (`src/docdoc/api/app.py:1275`) reads the body, runs `ingest_parse` in the
  threadpool, and returns `Document.model_dump` JSON. No job id, nothing stored, no limiter. With
  the example endpoint config (`timeout_s` 300, `deadline_s` 1800) one request can hold a
  connection for 30 minutes.
- An async run model already exists (ADR-0013, spec 009): the `runs` table in Postgres
  (queued/running/succeeded/failed/cancelled, lease, attempts, idempotency key), claimed with
  `SELECT … FOR UPDATE SKIP LOCKED` (`src/docdoc/runs/queue.py`), executed by `docdoc worker`
  (one run per process; scale by replicas). Routes: `POST /v1/documents/{blob_id}/runs` → 202,
  `GET /v1/runs/{id}`, `DELETE` to cancel, webhooks (ADR-0018).
- A run today **always** needs `blob_id` and `schema_identity` (`src/docdoc/runs/model.py:265`):
  there is no OCR-only run kind.
- Tenant limiter (`src/docdoc/runs/limits.py`): per-tenant `submissions_per_minute`,
  `concurrent_runs` (counted live from `runs` where status in queued/running), and token budgets,
  configured from a limits file with a `default` plus per-tenant overrides. Refusal is 429 with
  `Retry-After`. Applied only in `submit_run`. No deployment-wide cap exists.
- Retention (ADR-0015, `src/docdoc/runs/retention.py`, `maintenance.py`): every run has
  `expires_at`; the worker's maintenance tick sweeps expired runs and deletes the artifacts and
  blobs only they referenced. `expires_at` is stamped at submission (30 days by
  `DEFAULT_RETENTION`), but the sweep only runs when `DOCDOC_RUN_RETENTION_DAYS` is set; unset
  means nothing is swept, and the shipped compose leaves it unset.
- Auth and limits are off in the shipped compose (`DOCDOC_API_KEYS_FILE`, `DOCDOC_LIMITS_FILE`
  empty), so every caller there is the default tenant.
- `/v1/ocr` today needs no database or object store.
- Blobs are stored content-addressed in the object store (SeaweedFS since 013) via
  `POST /v1/documents`.
- mmerp does **not** call `/v1/ocr` yet; it still calls the Tesseract kit on .27 directly.

## Goals

1. **Async OCR submission.** A consumer sends a document plus the same choices `/v1/ocr` takes
   today (`parser`, `language`) and gets `202` with a run id at once. The OCR runs on a worker.
2. **Results retrievable later.** The consumer gets status and, once done, the OCR result (the same
   serialized `Document` `/v1/ocr` returns today) by polling the run, and by webhook where the
   consumer has one registered, using the existing run and delivery machinery.
3. **Bounded per consumer.** Each consumer has a configurable cap on how many OCR documents it may
   have waiting or running. Over the cap → refused immediately with **429** + `Retry-After`, body
   saying that consumer's queue is full. One consumer filling its share never refuses another.
4. **Bounded overall.** A configurable deployment-wide cap on OCR documents waiting or running,
   summed over all consumers. Over it → refused immediately with **503** + `Retry-After`, body
   saying the system is overloaded, send again later.
5. **Short retention, configurable.** OCR results and the uploaded file are kept for a configurable
   period, **default 1 day**, then cleaned up by docdoc itself. The consumer never has to delete.
6. **All OCR goes through the queue.** The synchronous `POST /v1/ocr` is removed (or answers with
   the async behaviour); there is no path that bypasses the caps.

## Non-goals

- No new infrastructure: no Redis, broker, or workflow engine (ADR-0013, constitution deferred
  list). The queue is the existing `runs` table; the main compose stays api, worker(s), postgres,
  object storage (a second worker service of the same image is not new infrastructure).
- No priority between consumers or between documents. Order within the queue stays what the claim
  query does today.
- No change to how OCR itself is performed (adapter, splitting, kits, provider naming from
  ADR-0021).
- No async variant of `/v1/extract` (ADR-0012 stands).
- Not fixing, in this project, the separate bug that `/v1/extract` runs the pipeline inside
  `async def` and blocks the event loop. Track it as its own issue.

## Anti-goals (a failure even if it works)

- **Silent drops.** A document that got `202` must end as succeeded, failed, or cancelled with a
  visible status. Never vanish.
- **Dishonest refusals.** Refusal says which cap was hit (consumer vs system) and gives a
  `Retry-After`. No generic 500, no hanging connection, no accepting then discarding.
- **One consumer starving the others** through the queue they share.
- **Caps that can be overshot under concurrent submissions** by more than a trivial race margin.
  (Builder decides how strict; see below.)
- **Retention reaching into extraction data.** The 1-day OCR period must not shorten retention of
  extraction runs or delete artifacts a surviving run still references (ADR-0015 difference rule).

## Decided by the user

- Consumers push and leave; nobody waits on OCR synchronously.
- The waiting queue must be bounded.
- When full, docdoc refuses honestly: "system overloaded, please send again later".
- The cap is configurable **per consumer**.
- There is **also a global cap**.
- OCR results are retained for a configurable period, **default 1 day**, then cleaned up.
- **Every** OCR request goes through the queue; the sync route does not remain.
- **OCR gets its own workers.** A worker can be restricted to a run kind (e.g.
  `docdoc worker --kind ocr` / `--kind extraction`; unrestricted remains possible), and the main
  compose runs an OCR worker service separate from the extraction worker, so an OCR backlog never
  delays extraction and OCR workers scale to the OCR hosts' capacity on their own. This is
  separation by kind, not priority; the "no priority" non-goal stands.

## Claude's calls (check these)

- **429 for a consumer's cap, 503 for the global cap**, both with `Retry-After`, so the caller can
  tell "my share is full" from "docdoc is full". Retry hint follows the existing
  `concurrent_runs` reasoning (short, honest, ~30 s) unless the builder finds a better signal.
- **"Consumer" = tenant**, the existing unit of the limits file and credentials. The per-consumer
  cap is a new key in the existing limits book (`default` + `tenants` overrides); the global cap is
  a deployment setting. Counted live from `runs` like `concurrent_runs`, never stored.
- **A slot is held while queued or running**; succeeded/failed/cancelled free it.
- **Default per-consumer cap ~100**, global default chosen by the builder relative to it; both
  overridable. Both caps apply with no configuration (unlike the existing limits, which are off
  unless configured): the per-consumer default applies to every tenant, including the single
  default tenant of an auth-off deployment. The shipped compose stays auth-off; per-consumer caps
  become distinct once an operator configures keys and a limits file.
- **OCR and extraction are counted separately.** OCR runs count only toward the OCR caps; the
  existing `concurrent_runs`/`submissions_per_minute`/token limits filter to extraction runs, so an
  OCR backlog never blocks a tenant's extraction submissions and vice versa.
- **OCR is a new run kind on the existing `runs` table**, not a parallel queue. The run carries
  the OCR choices (parser, language) instead of a schema identity; the worker dispatches on kind.
- **Submission is one call with the file in the body**, like `/v1/ocr` today, so mmerp does not
  need a separate upload step. The file is stored as a blob (content-addressed, as today) so the
  worker can read it.
- **Retention period is a deployment setting for OCR runs**, separate from
  `DOCDOC_RUN_RETENTION_DAYS`, default 1 day, and **on by default** (the user asked for cleanup
  after a day): the maintenance tick sweeps expired OCR runs even when run retention is unset.
  This departs from "every maintenance step is off unless configured" and the ADR says so.
  Per-consumer retention is not added.
- **The day counts from the run finishing**, not from submission, so a deep queue cannot sweep a
  result before the consumer could fetch it or the webhook was delivered.
- **`/v1/ocr` path:** reusing the path with new 202 semantics vs a new path and deleting the old
  one is the builder's call; either way the sync behaviour is gone and docs/ADRs say so.

## Constraints

- Constitution and ADRs: ADR-0013 (run model, SKIP LOCKED, at-least-once, cancel at stage
  boundaries), ADR-0015 (deletion is a difference), ADR-0018 (webhooks), ADR-0021 (caller-named
  parsers, 422 on unknown name, no fallback), ING-7 (one complete `Document` or an exception; no
  silent partials). Removing a public route and adding a run kind need an ADR (next number 0022)
  amending the OCR gateway design's "no async OCR" non-goal.
- Request-time checks that exist today (body size 413, page cap, unknown parser 422) still happen
  **at submission**, before a slot is taken, wherever they can be checked without OCR.
- At-least-once: a redelivered OCR run may call the OCR host twice; that is acceptable, but the
  result must be identical in shape and the run must end in exactly one terminal state.
- **OCR now requires the run database and object store.** A deployment without
  `DOCDOC_RUN_DATABASE_URL` that used `/v1/ocr` loses it (503). This follows from "every OCR goes
  through the queue"; the ADR names it as a breaking change.
- The worker processes one run per process; throughput is worker replicas × OCR host capacity.
  The caps bound queue length, not speed.

## Left to the builder

- Schema/migration shape for the run kind; whether OCR output is stored as an artifact (likely,
  so retention and delivery work unchanged).
- Exact response bodies and error codes in docdoc's error model.
- How strictly caps hold under concurrent submissions (count-then-insert in one transaction, an
  advisory lock, or an accepted small overshoot documented with a `ponytail:` note).
- Whether mmerp-facing docs/examples (`examples/submit_async_run.py`) get an OCR variant.
- Test plan, including a cap-under-concurrency test and a retention test proving extraction runs
  are untouched.
