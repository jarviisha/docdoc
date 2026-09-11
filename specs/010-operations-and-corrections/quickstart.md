# Quickstart: Validating Retention, Credentials, Limits, Delivery, and the Human Loop

**Feature**: `010-operations-and-corrections` | **Date**: 2026-09-04 |
**Last run end to end**: 2026-09-09

How to prove this milestone works, in the order the proofs get harder. Every scenario maps to a
success criterion in [spec.md](./spec.md); the criterion is the thing being checked and the commands
are one way to check it.

**Scenario 0 is the one that matters most**, and it is the only one that needs no configuration at
all — because the claim this milestone makes first is that a deployment which enables none of it sees
nothing change.

> **Every command below has been run against the four-container composition** (T176, 2026-09-09).
> That pass found eleven things this document got wrong — a `curl` form that returns `415`, four
> variable names nothing reads, two flags that do not exist, a scenario that could not work at all
> because it never enabled authentication, and a response field that disagreed with the contract. All
> are corrected here. What a guide nobody has run looks like is exactly this, which is why the task
> exists.

## Prerequisites

The same four containers Milestone 9 brought up. **No fifth container** (FR-116, SC-025):

```bash
uv sync --all-extras

# `DOCDOC_PG_PORT` because 5432 is the port a developer already running Postgres has taken.
DOCDOC_PG_PORT=55432 \
DOCDOC_EXTRAS=api,postgres,s3,otel,pdf \
  docker compose -f packaging/docker/compose.yml up -d --build   # api, worker, postgres, minio

docker compose -f packaging/docker/compose.yml exec api docdoc migrate
docker compose -f packaging/docker/compose.yml exec api docdoc migrate --check   # exits 0
```

`docdoc migrate` applies this milestone's migrations beside Milestone 9's, in order, idempotently
(Milestone 9 FR-078). Nothing is applied by a process starting. A second `migrate` prints `nothing to
apply`.

**`psql` runs in the container**, not on the host — nothing here assumes you have a client installed:

```bash
PSQL="docker compose -f packaging/docker/compose.yml exec -T postgres psql -U docdoc -d docdoc"
```

**Documents.** The commands below use `tests/fixtures/pdf/digital_invoice.pdf`, which is in the
repository. There is no `datasets/public/` directory; that path in an earlier draft named nothing.

Everything runs with no cloud account. The OTLP endpoint in scenario 6 is the only external thing,
and scenario 6 tells you how to skip it.

---

## Scenario 0 — Nothing changes until you ask (SC-002)

Bring the composition up with **no** new setting and run a document exactly as Milestone 9 did.

```bash
# The route reads the RAW BODY and decides the type from the bytes. `curl -F` sends multipart and
# gets 415: "unrecognized file signature; docdoc decides the type from the bytes".
BLOB=$(curl -sS --data-binary @tests/fixtures/pdf/digital_invoice.pdf \
        localhost:8000/v1/documents | jq -r .blob_id)

RUN=$(curl -sS -X POST "localhost:8000/v1/documents/$BLOB/runs?schema=invoice@1" | jq -r .run_id)
sleep 5   # the worker claims and executes; this is asynchronous on purpose
curl -sS "localhost:8000/v1/runs/$RUN" | jq
```

**Expected**: the Milestone 9 body, plus `"priority": "ordinary"`, and **no** `routing` key. All four
stages `executed`, and a `processing_id` the unchanged job route serves. Nothing is swept, nothing is
counted, nothing is refused, nothing is exported, nothing is delivered.

```bash
$PSQL -tAc "select 'tombstones='||count(*) from run_tombstones;"   # 0
$PSQL -tAc "select 'counters='||count(*) from limit_counters;"     # 0
$PSQL -tAc "select 'callbacks='||count(*) from callbacks;"         # 0
$PSQL -tAc "select 'corrections='||count(*) from corrections;"     # 0
```

**Why this is scenario 0**: every other capability here is a way to break an existing deployment, and
FR-101 says none of them may. If this scenario fails, stop.

---

## Enabling authentication — required by scenarios 1 to 8

Scenarios 1 onwards use administrative and tenant credentials, and **none of them can work until
authentication is on**. With it off there is one implicit tenant with empty scopes, so every
`/v1/admin` route correctly answers `404` — including to a credential that carries `admin`. An
earlier draft of this document jumped straight to scenario 1 and could not have worked.

Authentication is turned on by the **key file**, and a table-issued credential resolves through the
chain behind it (ADR-0016 §7). So a deployment needs a file with at least one entry, reachable inside
the container:

```bash
BOOT=ddk_bootstrap_change_me
HASH=$(printf '%s' "$BOOT" | sha256sum | cut -d' ' -f1)
printf '{"keys":[{"sha256":"%s","tenant_id":"ops"}]}\n' "$HASH" > /tmp/keys.json
docker cp /tmp/keys.json docdoc-api-1:/tmp/keys.json      # or mount it; see compose.yml

DOCDOC_PG_PORT=55432 DOCDOC_API_KEYS_FILE=/tmp/keys.json \
  docker compose -f packaging/docker/compose.yml up -d api worker

curl -sS -o /dev/null -w '%{http_code}\n' localhost:8000/v1/schemas                          # 401
curl -sS -o /dev/null -w '%{http_code}\n' localhost:8000/v1/schemas -H "Authorization: Bearer $BOOT"  # 200
```

---

## Scenario 1 — A key is revoked and stops working, with nothing restarted (SC-006, SC-008)

```bash
# The first admin credential is a command-line act, never a route (FR-032).
docker compose -f packaging/docker/compose.yml exec -T api docdoc credential issue --tenant ops --admin
# → ddk_… (printed once; there is no command and no route that will show it again)

export ADMIN=ddk_…
KEY=$(curl -sS -X POST localhost:8000/v1/admin/credentials \
        -H "Authorization: Bearer $ADMIN" -H 'content-type: application/json' \
        -d '{"tenant_id":"acme","label":"ci"}' | jq -r .key)
ID=$(curl -sS "localhost:8000/v1/admin/credentials?tenant_id=acme" \
        -H "Authorization: Bearer $ADMIN" | jq -r '.credentials[-1].credential_id')

curl -sS -o /dev/null -w '%{http_code}\n' localhost:8000/v1/schemas -H "Authorization: Bearer $KEY"  # 200
curl -sS -o /dev/null -w '%{http_code}\n' -X DELETE \
  "localhost:8000/v1/admin/credentials/$ID" -H "Authorization: Bearer $ADMIN"                        # 204
sleep 31                                                              # DOCDOC_RUN_CREDENTIAL_TTL_SECONDS
curl -sS -o /dev/null -w '%{http_code}\n' localhost:8000/v1/schemas -H "Authorization: Bearer $KEY"  # 401
```

**Expected**: `200`, then `401` — from the same API process, never restarted. Confirm that:

```bash
docker inspect -f '{{.State.StartedAt}}' docdoc-api-1   # the same before and after
```

This is the defect Milestone 9 documented and did not fix: deleting a compromised key from the *file*
changes nothing until a restart, and that is still true of the file.

**The process that served the revocation refuses immediately**, before the TTL, because it drops its
own cache entry as a courtesy. The TTL is the bound for *every other* process, and it is the number
the documentation states.

**Rotation with no failed request** (SC-008): issue a second key, use both, revoke the first. Neither
call is refused during the overlap.

**Then check nothing leaked** (SC-007):

```bash
docker compose -f packaging/docker/compose.yml logs --no-color | grep -c "$KEY"   # 0
$PSQL -tAc "select * from credentials;" | grep -c "$KEY"                          # 0
```

---

## Scenario 2 — A limit refuses at the door and aborts nothing (SC-009)

```bash
DOCDOC_PG_PORT=55432 DOCDOC_API_KEYS_FILE=/tmp/keys.json DOCDOC_LIMIT_CONCURRENT_RUNS=2 \
  docker compose -f packaging/docker/compose.yml up -d api

for i in 1 2 3; do
  curl -sS -o /tmp/r$i.json -w '%{http_code} ' -X POST \
    "localhost:8000/v1/documents/$BLOB/runs?schema=invoice@1" -H "Authorization: Bearer $KEY"
done; echo
```

**Expected**: `202 202 429`. The `429` body names `concurrent_runs` and carries `Retry-After: 30`:

```json
{"error":"limit_exceeded","limit":"concurrent_runs","observed":2,"allowed":2,"retry_after_seconds":30}
```

**And no third run row exists** (FR-044) — a refusal costs one counter read:

```bash
$PSQL -tAc "select count(*) from runs where tenant_id='acme';"   # 2, not 3
```

**And the two that were accepted still finish.** Lower the limit to `1` while they run: nothing is
aborted, and only the *next* submission is refused (FR-045, FR-046).

**The token budget is one submission late, on purpose** (FR-047): set
`DOCDOC_LIMIT_TOKENS_PER_PERIOD` below what one run consumes, submit, and watch that run complete and
the *next* one be refused. A budget that could abort in flight would reintroduce the paid-and-
discarded failure Milestone 9 was built to remove.

---

## Scenario 3 — One tenant's backlog does not delay another's run (SC-010)

```bash
for i in $(seq 1 200); do
  curl -sS -o /dev/null -X POST "localhost:8000/v1/documents/$BLOB/runs?schema=invoice@1" \
    -H "Authorization: Bearer $BULK_KEY"     # tenant `bulk`
done
time curl -sS -X POST "localhost:8000/v1/documents/$BLOB2/runs?schema=invoice@1" \
    -H "Authorization: Bearer $KEY"          # tenant `acme`, one run
```

**Expected**: `acme`'s run is claimed within a bounded number of claims — not after `bulk`'s 200. The
bound is the number of tenants with queued work, not the size of anyone's backlog, because the claim
ranks each tenant's queue independently (research R10).

**Then check starvation** (FR-089): with `DOCDOC_RUN_STARVATION_SECONDS=10`, an ordinary run older
than that is claimed ahead of a newly submitted urgent one.

**And the ceiling** (SC-026):

```bash
curl -sS -X POST "localhost:8000/v1/documents/$BLOB/runs?schema=invoice@1" \
  -H "Authorization: Bearer $KEY" -H 'content-type: application/json' \
  -d '{"priority":"urgent"}' | jq -c '{status, priority}'
```

**Expected**: `{"status":"queued","priority":"ordinary"}` on a deployment whose ceiling is ordinary.
The run is **accepted at the ceiling** and the response says what it was granted. It is not refused —
an operator lowering a ceiling must not break a client that changed nothing.

A *malformed* priority is different and is refused, because a client asking for `"high"` believes it
is asking for something:

```bash
curl -sS -X POST "…/runs?schema=invoice@1" -H "Authorization: Bearer $KEY" \
  -H 'content-type: application/json' -d '{"priority":"high"}'
# 422 {"error":"invalid_priority","detail":"priority is `ordinary` or `urgent`"}
```

---

## Scenario 4 — A signed delivery arrives, and a broken receiver breaks nothing (SC-011, SC-012)

Delivery needs a **signing secret in configuration** — the table holds only a digest, so a
registration whose secret the deployment does not hold is refused rather than accepted and left
undeliverable (FR-066):

```bash
printf '{"secrets":["whsec_test"]}\n' > /tmp/webhook-secrets.json
docker cp /tmp/webhook-secrets.json docdoc-api-1:/tmp/webhook-secrets.json
docker cp /tmp/webhook-secrets.json docdoc-worker-1:/tmp/webhook-secrets.json

# `ALLOW_PRIVATE` because the receiver below is on loopback. It relaxes the SSRF guard; a real
# deployment does not set it.
DOCDOC_PG_PORT=55432 DOCDOC_API_KEYS_FILE=/tmp/keys.json \
DOCDOC_DELIVERY_SECRETS_FILE=/tmp/webhook-secrets.json DOCDOC_DELIVERY_ALLOW_PRIVATE=true \
  docker compose -f packaging/docker/compose.yml up -d api worker

WEBHOOK_SECRET=whsec_test python examples/receive_webhook.py &

CB=$(curl -sS -X POST localhost:8000/v1/callbacks -H "Authorization: Bearer $KEY" \
      -H 'content-type: application/json' \
      -d '{"url":"http://host.docker.internal:8787/hook","secret":"whsec_test"}' | jq -r .callback_id)

curl -sS -X POST "localhost:8000/v1/documents/$BLOB/runs?schema=invoice@1" \
     -H "Authorization: Bearer $KEY" -H 'content-type: application/json' \
     -d "{\"callback_id\":\"$CB\"}"
```

**Expected**: one delivery, whose `X-Docdoc-Signature` verifies against `whsec_test` — the example
receiver prints the run and its state, and refuses anything whose signature or timestamp does not
check out. The payload carries identities and a terminal state, and no document text, no value, and
no provider message (FR-054).

**Now break the receiver** and confirm the run is unaffected:

```bash
kill %1
curl -sS "localhost:8000/v1/runs/$RUN/delivery" -H "Authorization: Bearer $KEY" | jq
```

**Expected**: attempts climbing with backoff, the run still `succeeded`, and the delivery coming to
rest at `failed` after the attempt limit. `last_error` is a **class name**, never the receiver's
response body.

**And the destination policy** (SC-013), with `DOCDOC_DELIVERY_ALLOW_PRIVATE` **unset**:

```bash
curl -sS -X POST localhost:8000/v1/callbacks -H "Authorization: Bearer $KEY" \
  -H 'content-type: application/json' \
  -d '{"url":"https://127.0.0.1/hook","secret":"whsec_test"}'
```

**Expected**: `422`, naming the class of refusal and **not** the addresses it resolved to:

```json
{"error":"destination_refused",
 "detail":"a callback destination must not resolve to a loopback, link-local, private, multicast, reserved, or unspecified address"}
```

`http://169.254.169.254/…` is refused too — for the scheme first, because https is required before
any address is resolved. Repeat with a hostname that resolves to one public and one private address:
also `422`, because every resolved address is checked and not just the first.

---

## Scenario 5 — Doubtful results route to review, and a correction comes back (SC-017, SC-018)

```bash
docker cp examples/routing/default.json docdoc-api-1:/tmp/routing.json
DOCDOC_PG_PORT=55432 DOCDOC_API_KEYS_FILE=/tmp/keys.json DOCDOC_ROUTING_POLICY=/tmp/routing.json \
  docker compose -f packaging/docker/compose.yml up -d api worker
```

Run a document that grounds partially, then:

```bash
curl -sS "localhost:8000/v1/runs/$RUN" -H "Authorization: Bearer $KEY" | jq .routing
```

**Expected**: `{"outcome":"review","policy_version":"default@1","reasons":[…]}` — two possible
outcomes and no third, a version on every decision, and reasons naming field, signal, and what was
observed. `observed` is a **string** and never a score: grounding scores are not comparable across
tiers (ADR-0004), so a number here would invite a comparison that means nothing.

**The test that matters is the negative one** (SC-017): re-run with only `model_confidence` altered in
the adapter fixture. The decision must not move. Principle II forbids routing on a model's
self-report, and `tests/unit/test_routing_ignores_model_confidence.py` is how that is checked rather
than asserted.

**Then record a correction and prove it changed nothing** (SC-018):

```bash
PID=$(curl -sS "localhost:8000/v1/runs/$RUN" -H "Authorization: Bearer $KEY" | jq -r .processing_id)
BEFORE=$(curl -sS "localhost:8000/v1/jobs/$PID/result" -H "Authorization: Bearer $KEY" | sha256sum)
curl -sS -X POST "localhost:8000/v1/runs/$RUN/corrections" -H "Authorization: Bearer $KEY" \
     -H 'content-type: application/json' -d @examples/corrections/total.json
AFTER=$(curl -sS "localhost:8000/v1/jobs/$PID/result" -H "Authorization: Bearer $KEY" | sha256sum)
test "$BEFORE" = "$AFTER" && echo "unchanged"
```

**And prove it moves no metric** until an explicit promotion:

```bash
# `docdoc eval`, and the manifest is POSITIONAL. There is no `docdoc evaluate`,
# no `--dataset`, and no `--report` — score before and after and compare.
docdoc eval manifest.json --predictions ./predictions --json > /tmp/before.json
# … record the correction …
docdoc eval manifest.json --predictions ./predictions --json > /tmp/after.json
diff <(jq .metrics /tmp/before.json) <(jq .metrics /tmp/after.json)    # empty
```

---

## Scenario 6 — A run appears in a tracing backend (SC-014, SC-015, SC-016)

```bash
DOCDOC_PG_PORT=55432 DOCDOC_OTLP_ENDPOINT=http://host.docker.internal:4318/v1/traces \
  docker compose -f packaging/docker/compose.yml up -d api worker
```

**Expected**: one trace per run, with a span per stage and per run transition, correlated by run and
processing identity. **No fifth container** — the collector is yours; the composition adds none
(FR-023).

**Check the log line on startup.** It says which of four things happened — `unconfigured`,
`installed`, `occupied`, or `unavailable`. If something already occupies the observer slot, docdoc
says so and **does not displace it** (research R4): `pipeline/observe.py` documents one slot as a
decision, and an exporter that quietly took it would be a silent regression of somebody else's
observability.

**Then check the spans carry nothing** (SC-015): grep the collector's output for strings seeded into
a document — expect zero hits. `tests/unit/test_telemetry_leaks_nothing.py` does this offline, over
the function that decides what becomes a span attribute.

**Skipping this scenario is a valid outcome** (SC-014, second half): with `DOCDOC_OTLP_ENDPOINT`
unset, the offline suite passes with `docdoc[otel]` not installed at all.

---

## Scenario 7 — Runs stop accumulating (SC-003)

```bash
$PSQL -tAc "update runs set expires_at = now() - interval '1 day' where tenant_id='acme';"

# There is no `--once`. The command sweeps one batch and exits; `--all` loops until nothing is left.
docker compose -f packaging/docker/compose.yml exec -T api docdoc sweep --retention-days 1 --json
docker compose -f packaging/docker/compose.yml exec -T api docdoc sweep --retention-days 1 --json
```

**Expected**: the first pass reports removals; the **second reports zero** (FR-002, SC-003). One
tombstone exists per removed run, carrying four columns and nothing else:

```bash
$PSQL -c "select * from run_tombstones limit 1;"
#  run_id | tenant_id | deleted_at | policy      ← and no fifth column
curl -sS -o /dev/null -w '%{http_code}\n' "localhost:8000/v1/runs/$RUN" -H "Authorization: Bearer $KEY"
```

**Expected**: `410`, with `deleted_at` and `policy`. From another tenant's key: `404`, byte-identical
to an identifier that never existed (SC-005) — compare the two bodies with `cmp` rather than reading
them, because "byte-identical" is the claim.

**Now the interruption test** (SC-003, second half): kill the worker mid-sweep, restart it, and
confirm the end state matches an uninterrupted sweep.

**And the one that catches the dangerous bug** (research R1):

```bash
docker cp tests/fixtures/pdf/digital_invoice.pdf docdoc-api-1:/tmp/inv.pdf
CLI="docker compose -f packaging/docker/compose.yml exec -T api docdoc"

# The document is a POSITIONAL argument; there is no `--document` flag.
$CLI extract /tmp/inv.pdf --schema invoice@1 --json   # writes artifacts, creates NO run row
$CLI sweep --retention-days 1 --json
$CLI extract /tmp/inv.pdf --schema invoice@1 --json   # must REUSE, not re-parse
```

**Expected**: every stage of the second extraction reports `reused`. A sweep implemented as a
*complement* — "delete every artifact no surviving run references" — deletes everything the command
line ever wrote, because CLI artifacts have no run row. The deletion set is a **difference**, and this
is how you check it.

---

## Scenario 8 — Erasing a customer, without erasing anyone else (SC-004, SC-005)

```bash
# Two tenants, byte-identical documents.
curl -sS --data-binary @tests/fixtures/pdf/digital_invoice.pdf \
  localhost:8000/v1/documents -H "Authorization: Bearer $KEY"
curl -sS --data-binary @tests/fixtures/pdf/digital_invoice.pdf \
  localhost:8000/v1/documents -H "Authorization: Bearer $OTHER"
# … run both, then:
curl -sS -X DELETE localhost:8000/v1/admin/tenants/acme -H "Authorization: Bearer $ADMIN"
```

**Expected**: `200` and a report of what went — `acme`'s blobs, artifacts, and run records gone; the
other tenant's runs still retrievable and its artifacts still reusable, verified on a parser
invocation counter rather than on elapsed time.

**Idempotence** (FR-009): repeat the erasure, and erase a tenant that never existed. Both answer
`200` having removed nothing:

```json
{"scope":"tenant","target":"neverexisted","runs":0,"artifacts":0,"blobs":0,"rows":0,"degraded":false}
```

**The guard** (research R2, FR-006) — erase whichever tenant owns the store root:

```bash
curl -sS -X DELETE localhost:8000/v1/admin/tenants/default -H "Authorization: Bearer $ADMIN"
```

**Expected**: `409`, naming why and pointing at the command line:

```json
{"error":"default_tenant_erasure_refused",
 "detail":"the default tenant's namespace is the store root, so this would remove content no run ever produced…"}
```

The default tenant's namespace is the store root (ADR-0014 §3), so a prefix erasure of it removes
everything written before authentication was enabled and everything the command line ever wrote.
ADR-0014's Consequences section predicted this milestone would get it wrong; this is the check that it
did not.

```bash
docdoc erase --tenant default            # run-derived content only, via the set difference
docdoc erase --tenant default --purge-store-root   # the other reading, typed deliberately, once
```

---

## What "done" means

| Scenario | Criteria |
|---|---|
| 0 | SC-002, SC-021, SC-022 |
| 1 | SC-006, SC-007, SC-008 |
| 2 | SC-009 |
| 3 | SC-010, SC-026 |
| 4 | SC-011, SC-012, SC-013 |
| 5 | SC-017, SC-018, SC-019 |
| 6 | SC-014, SC-015, SC-016 |
| 7 | SC-003, SC-005, SC-023, SC-024 |
| 8 | SC-004, SC-005 |

**SC-001 is not in that table and is not checked by any scenario here**, because it is checked by the
suite rather than by hand: golden-set metrics bit-identical, and a result with every capability
enabled agreeing with one produced with all of them disabled. It is the criterion the milestone exists
to satisfy, and if it fails none of the above matters.
