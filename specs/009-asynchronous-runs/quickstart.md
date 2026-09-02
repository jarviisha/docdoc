# Quickstart: Validating Asynchronous Runs

**Feature**: `009-asynchronous-runs` | **Date**: 2026-08-28

How to prove this milestone works, in the order the proofs get harder. Every scenario below maps to a
success criterion in [spec.md](./spec.md); the criterion is the thing being checked, and the commands
are one way to check it.

## Prerequisites

```bash
uv sync --all-extras
docker compose -f packaging/docker/compose.yml up -d     # api, worker, postgres, minio
```

No cloud credentials are needed except a model provider key, and not even that for scenarios 1–3,
which run against the offline `echo` adapter:

```bash
export DOCDOC_MODEL_ADAPTERS=echo
export DOCDOC_ECHO_FIXTURES=./tests/fixtures/echo
export DOCDOC_SCHEMA_PATHS=./schemas
```

Migrations are explicit and never applied by a process starting (FR-078):

```bash
docdoc migrate            # idempotent; safe to re-run
docdoc migrate --check    # exits non-zero if anything is pending
```

---

## 1. The milestone works at all

**Checks SC-001** — the criterion the milestone exists to satisfy.

```bash
BLOB=$(curl -sX POST localhost:8000/v1/documents --data-binary @invoice.pdf | jq -r .blob_id)
RUN=$(curl -sX POST "localhost:8000/v1/documents/$BLOB/runs?schema=invoice@1" | jq -r .run_id)

# poll until terminal
curl -s "localhost:8000/v1/runs/$RUN" | jq '{status, processing_id}'
```

**Expected**: the submission returns immediately with `status: "queued"` and **no `processing_id`
field at all** — absent, not null. Polling reaches `succeeded` and names a `processing_id`, from which
the unchanged job route serves the result.

The real assertion is the comparison, and it belongs in a test rather than a terminal:

```bash
uv run pytest tests/contract/test_async_matches_sync.py
```

That test runs the same document through both paths and compares every value, verdict, location, and
identity. If it fails, nothing else here matters.

## 2. Submission does not wait for the work

**Checks SC-002.**

```bash
docker compose stop worker
time curl -sX POST "localhost:8000/v1/documents/$BLOB/runs?schema=invoice@1"
```

**Expected**: returns in well under 200 ms with the worker stopped — proving the response is not the
run. Restart the worker and the queued run is claimed and completes with no resubmission.

## 3. The same document twice

**Checks FR-005 and the identity argument of ADR-0013 §1.**

Submit the same blob and schema twice. **Expected**: two different `run_id`s, one identical
`processing_id`, and — on a counter, not a stopwatch — zero parser invocations on the second.

## 4. Killing a worker mid-run

**Checks SC-003, SC-004, SC-011.**

```bash
uv run pytest tests/integration/test_redelivery.py
```

Kills the worker at each of the four stage boundaries. **Expected**: every run completes on
redelivery with a `processing_id` identical to the uninterrupted run's, and the billable-stage count
exceeds the uninterrupted count by at most one — by zero when the kill falls between stages.

This is where the artifact chain earns its keep: the redelivered attempt reuses every completed
stage's artifact, so "redelivery" is mostly "resume".

## 5. A document that kills the worker every time

**Checks SC-006.**

```bash
uv run pytest tests/integration/test_poison_run.py
```

**Expected**: the run comes to rest at `failed` with `error_class: "RunAbandonedError"` after three
attempts, and terminates at most three worker processes. Without the attempt limit this test never
ends, which is the point of having one.

## 6. Two workers, one store, no double billing

**Checks SC-005.**

```bash
docker compose up -d --scale worker=2
uv run pytest tests/integration/test_shared_store_reuse.py
```

**Expected**: a document already parsed by one worker and resubmitted by the **same tenant** is
claimed by the other and reuses the parse — parser invocations: zero. Run this against a filesystem
store with private roots and it fails, which is why the shared store is in this milestone and not the
next one.

## 7. Tenant isolation, including the part a status code cannot deliver

**Checks SC-008 and SC-017.**

```bash
export DOCDOC_API_KEYS_FILE=./tests/fixtures/keys.json   # two tenants
uv run pytest tests/contract/test_tenant_isolation.py
```

**Expected**, in two halves:

- Tenant B's request for tenant A's `run_id`, `blob_id`, or `processing_id` returns a response
  **byte-identical** to one for an identifier that never existed.
- Tenant B submitting a document tenant A has already processed invokes the parser and the model
  adapter **exactly as many times as a first-ever submission**. This is SC-017, and it is the reason
  the store is namespaced per tenant: without it, a stopwatch and an invoice reveal what the status
  code conceals.

## 8. Losing the database

**Checks SC-009.**

```bash
docker compose stop postgres
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/healthz     # 200
curl -s localhost:8000/readyz | jq                                  # 503, names the dependency
curl -s -o /dev/null -w '%{http_code}\n' \
     -X POST "localhost:8000/v1/documents/$BLOB/runs?schema=invoice@1"   # 503, retryable
```

**Expected**: liveness passes, readiness fails naming `run-state-database`, and submission is refused
rather than accepted and lost. Note that the synchronous routes would still work — readiness fails
anyway, and that is deliberate (FR-087).

## 9. Cancellation, including what it does not do

**Checks SC-015.**

Cancel a **queued** run: it never executes. Cancel a **running** one: the response is `200` with
`status: "running"`, and the run stops before the *next* stage. A provider call already in flight
completes and is billed.

If you expected the second case to return `cancelled` immediately, the contract disagrees on purpose
— see [runs-http-api.md](./contracts/runs-http-api.md#delete-v1runsrun_id).

## 10. Nothing else moved

**Checks SC-010, SC-012, SC-013** — the criteria that prove the scope held.

```bash
uv run python examples/evaluate_golden_set.py        # bit-identical to pre-milestone output
git diff --stat main -- src/docdoc/kernel src/docdoc/ingest \
    src/docdoc/extraction src/docdoc/grounding src/docdoc/validation   # empty

uv pip install -e .                                   # base install, no extras
uv run pytest tests/unit tests/property               # green with no database and no bucket
```

**Expected**: golden-set metrics do not move by a digit; the diff against those five layers is empty;
the offline suite passes with neither `docdoc[postgres]` nor `docdoc[s3]` installed. This milestone
touches no stage, so a moved metric is not a regression to investigate — it is evidence the scope
escaped.
