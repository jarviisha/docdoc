---

description: "Task list for Milestone 10 — Retention, Credentials, Limits, Delivery, and the Human Loop"
---

# Tasks: Retention, Credentials, Limits, Delivery, and the Human Loop

**Input**: Design documents from `/specs/010-operations-and-corrections/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: Not optional. Three of this milestone's requirements are *only* meaningful as tests —
SC-001 (golden-set metrics bit-identical with every capability enabled), SC-017 (a routing decision
that does not move when `model_confidence` alone changes), and the R1 hazard (a sweep that must not
delete what the command line wrote). Each of those is a claim about what does **not** happen, and a
claim of that shape has no other form.

**Organization**: Grouped by user story so each is independently implementable and testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete work)
- **[Story]**: US1–US8 from spec.md. Setup, Foundational, and Polish carry no label

## Path Conventions

Single project: `src/docdoc/`, `tests/` at repository root, per plan.md's Structure Decision.

## The 10a / 10b cut line, if it is taken

plan.md records the two-halves risk and the recommendation not taken. **If the split is taken later,
the cut is at Phase 10.** Phases 1–9 and 11 are the operational half (10a); Phase 10 — routing and
corrections — is the product half (10b). Two tasks exist specifically to make that cut clean:

- **T041** defines `CorrectionStore.pinned_runs` and a null implementation in Foundational, so the
  retention sweep can honour FR-013 before corrections exist. This is the one place the halves touch,
  and it is a protocol rather than a dependency.
- **T145** replaces the null implementation with the real one and is the *only* task in Phase 10 that
  Phase 3 cares about.

Nothing else crosses. Ship 10a without Phase 10 and the sweep behaves exactly as specified, because
no correction pins anything when none can exist.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: The packaging, the contracts, and the skeletons — so that nothing later has to stop and
add a dependency or a guard.

- [X] T001 Add the `otel` optional extra (`opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`) to `[project.optional-dependencies]` in `pyproject.toml`, with a comment stating that it is observability infrastructure the constitution names rather than a provider SDK, that it is imported inside `telemetry.bridge()`, and that webhook delivery deliberately adds **no** dependency (R5, R6)
- [X] T002 Add a `forbidden` contract in `pyproject.toml` named "outbound HTTP is confined to delivery and telemetry (FR-099)". import-linter has no allow-list, so express it the way the tool actually works: `source_modules` enumerates the deterministic layers **and the `docdoc.runs` submodules other than `delivery`** — `docdoc.runs.retention`, `.keys`, `.limits`, `.routing`, `.corrections`, `.maintenance`, `.queue`, `.postgres`, `.worker`, `.model`, `.observe` — with `forbidden_modules = ["http", "socket", "urllib", "opentelemetry"]` — **`http` and not `http.client`: import-linter rejects subpackages of external packages, which the first draft discovered by failing to load**. Enumerating is deliberate: a contract on `docdoc.runs` as a whole plus `ignore_imports` for the two exceptions passes when a *new* module reaches the network, and this one fails until somebody adds it here on purpose. A prohibition with no automated guard is a comment
- [X] T003 Correct the layers-contract comment in `pyproject.toml` that reads "the total order then forces `runs > evaluation`, **which `runs` does not import**" — after T041 it does. Same rule Milestone 9 applied to the `artifacts` comment: a comment that lies is worse than one that is absent
- [X] T004 [P] Create the module skeletons in `src/docdoc/runs/`: empty `retention.py`, `keys.py`, `limits.py`, `delivery.py`, `routing.py`, `corrections.py`, `maintenance.py`
- [X] T005 [P] Create `src/docdoc/telemetry/__init__.py` as an empty module. **Amended during implementation:** it *is* declared in the layers contract, sharing `evaluation`'s position. This task said it should gain no position because it imports nothing of docdoc's; `tests/unit/test_layer_boundaries.py` failed on the first run and gave the better reason — an undeclared package is unconstrained. Constitution v1.8.0 carries the Principle X amendment, and making it a module rather than a package to pass the test was rejected as the fix FR-096a warns about
- [X] T006 [P] Register an `otel` pytest marker in `pyproject.toml` beside the existing `postgres` and `s3` markers, so the offline suite excludes exporter-dependent tests by default, and document the command in `CONTRIBUTING.md`
- [X] T007 Extend `packaging/docker/compose.yml` with the new environment variables **only** — no fifth service. SC-025 asserts four containers; a collector, if an operator wants one, is theirs (FR-023)

**Checkpoint**: `import-linter` passes with the new contract, and the offline suite is green with no new extra installed.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The governance that gates code, the two protocol extensions, the schema, and the four
error types. **No user story work begins until this phase completes.**

### Decisions that gate code — these block everything below

- [X] T008 Write and merge constitution **v1.8.0** in `.specify/memory/constitution.md`: amend the "multi-tenant billing" paragraph to distinguish **counting in order to refuse** from **metering in order to bill**, stating that invoicing, pricing, and metering-for-billing remain deferred; and add `RetentionError`, `CredentialError`, `LimitExceededError`, `DeliveryError` to the error-model list. Include the SYNC IMPACT REPORT block with the bump rationale, as every prior amendment does (FR-102, plan.md gate 12). **Gate 12 is FAIL until this merges**
- [X] T009 Write `docs/adr/0015-deletion-over-a-content-addressed-store.md` and mark it Accepted: survivorship as a **set difference** and never a complement, the CLI-artifact hazard it avoids, the tombstone and why it is not a run status, the tenant-prefix operation, and the default tenant's exception. Add its row to `docs/adr/README.md` (FR-103, R1, R2)
- [X] T010 [P] Write `docs/adr/0016-credential-lifecycle.md` and mark it Accepted: the credentials table, the administrative scope on `Principal`, the cache-lifetime propagation bound and why it is not instant, and the **supersession of `specs/009` FR-061** — which forbade route-based mutation *in that milestone*. Add its row to `docs/adr/README.md` (FR-104, R11, R12)
- [X] T011 [P] Write `docs/adr/0017-confidence-routing-policy.md` and mark it Accepted: which signals a decision may read, why `model_confidence` is not among them (Principle II, ADR-0004), how the policy is versioned, and why the outcome set has exactly two members. Add its row to `docs/adr/README.md` (FR-105)
- [X] T012 [P] Write `docs/adr/0018-webhook-delivery-semantics.md` and mark it Accepted: at-least-once, HMAC-SHA256 with the timestamp inside the signed material, one delivery per run as a constraint, and the resolve-validate-pin destination policy including why re-validation before each attempt is what closes DNS rebinding. Add its row to `docs/adr/README.md` (FR-106, R7, R8)

### The store protocols gain deletion

- [X] T013 Extend the `ArtifactStore` Protocol in `src/docdoc/artifacts/store.py` with `delete(artifact_id, *, tenant_id) -> bool` and `delete_prefix(*, tenant_id, allow_store_root=False) -> int`, documenting that `delete` returns whether something was there so a sweep can count what it removed without a racing existence check (R3, FR-012)
- [X] T014 Implement both methods on `FileArtifactStore` in `src/docdoc/artifacts/store.py`, and on `NullArtifactStore` as no-ops returning `False` and `0` — a store that holds nothing deleted nothing, and raising would make the null store the only one a sweep must branch for
- [X] T015 [P] Extend `BlobStore` in `src/docdoc/artifacts/blobs.py` with the same pair over `blob_id`
- [X] T016 [P] Implement both pairs on `S3ArtifactStore` and `S3BlobStore` in `src/docdoc/artifacts/s3.py`, using paginated delete for the prefix operation
- [X] T017 Make `delete_prefix` raise `RetentionError` when `tenant_id` is the default tenant unless `allow_store_root=True`, in `src/docdoc/artifacts/store.py`, `blobs.py`, and `s3.py`. The default tenant's prefix **is the store root** (ADR-0014 §3) — the docstring must say so, and must say that this is the guard ADR-0014's Consequences section asked for by name (R2)
- [X] T018 Add a `CHANGELOG.md` entry under Unreleased naming the `ArtifactStore` and `BlobStore` protocol change as a public break, per ADR-0011's rule that a `0.x` minor may break any public API but must ship an entry naming what moved

### The schema

- [X] T019 Write `src/docdoc/runs/migrations/0003_retention.sql`: the `run_tombstones` table (four columns, exactly), and the `runs_expiring` partial index on `expires_at` restricted to the three terminal states — the index Milestone 9's `0001_runs.sql` named in a comment and deliberately did not create
- [X] T020 [P] Write `src/docdoc/runs/migrations/0004_credentials.sql`: the `credentials` table with `digest` unique **deployment-wide** rather than per tenant, `scopes` as `text[]`, and `credentials_by_tenant`
- [X] T021 [P] Write `src/docdoc/runs/migrations/0005_limits.sql`: `limit_counters` keyed on `(tenant_id, kind, window_start)`, and the `runs_tenant_active` partial index that serves the concurrency count
- [X] T022 [P] Write `src/docdoc/runs/migrations/0006_delivery.sql`: `callbacks`, and `deliveries` with the `UNIQUE (run_id)` constraint that makes FR-058's ordering guarantee a constraint rather than worker discipline, plus the `deliveries_due` partial index
- [X] T023 [P] Write `src/docdoc/runs/migrations/0007_corrections.sql`: the `corrections` table with its own `expires_at`, and the three `runs` columns — `priority smallint NOT NULL DEFAULT 0`, `tokens_used integer`, `callback_id uuid`. Add **no** `CHECK` change and **no** new status value; `RunStatus` stays closed at five (FR-004a)
- [X] T024 Verify `docdoc migrate` applies 0003–0007 in order and that `--check` reports them as pending beforehand, changing nothing in `src/docdoc/cli/commands/migrate.py` — the runner was built to take more files and this task is to prove it, not to modify it

### Errors, the principal, and the model

- [X] T025 [P] Add `RetentionError`, `CredentialError`, `LimitExceededError`, and `DeliveryError` to `src/docdoc/runs/errors.py`, each subclassing the existing `RunError` so every current handler keeps working. `LimitExceededError` carries the limit, observed, allowed, and retry hint; none carries a credential, a document, a provider message, or a receiver's response body (FR-100)
- [X] T026 Add `scopes: frozenset[str] = frozenset()` to `Principal` in `src/docdoc/api/auth.py`, keeping the dataclass frozen and keeping it free of any name or credential. `KeyRing` constructs principals with the default, so a Milestone 9 deployment gains no administrative access by upgrading (FR-038, R12)
- [X] T026a Extend `src/docdoc/runs/identity.py` with `new_credential_id()`, `new_delivery_id()`, `new_correction_id()`, `new_erasure_id()`, `new_key_secret()`, and `window_start(now, interval)`. It is already the only module in `docdoc.runs` permitted to import `uuid`, `time`, `datetime`, `random`, or `secrets`, and `tests/unit/test_runs_clock_confinement.py` enforces that over **every** module in the package — so the seven modules this milestone adds get their instants and identifiers as parameters, exactly as `queue.py` does (FR-096a)
- [X] T026b Add `tests/unit/test_clock_guard_was_not_widened.py` asserting that `PERMITTED` in `tests/unit/test_runs_clock_confinement.py` still names exactly `identity.py`. The guard's own docstring records that it was silently disabled once by a change written to make it pass; FR-095 forbids relaxing it and this is what makes that requirement falsifiable rather than a sentence
- [X] T027 [P] Add `Priority` (a closed two-member set, ordinary and urgent, stored as a `smallint`) and the `priority`, `tokens_used`, `callback_id` fields to `Run` in `src/docdoc/runs/model.py`. **Do not touch `RunStatus`** — add a module-level comment saying so and pointing at FR-004a, because this is the file where a sixth state would be added by someone who had not read the clarification
- [X] T028 [P] Add `Tombstone` to `src/docdoc/runs/model.py`: four fields, frozen, `extra="forbid"`. A tombstone that could carry a fifth field is a way of retaining what was deleted
- [X] T029 Add `mapping for 410` and `mapping for 429` to `src/docdoc/api/errors.py`'s `status_for`, so a tombstone hit and a limit refusal reach the HTTP layer as distinct outcomes rather than as an existing code with a different body

### Foundational tests

- [X] T030 [P] Write `tests/unit/test_run_status_set_is_closed.py` asserting `RunStatus` has exactly the five members Milestone 9 named, **by name**, with a failure message pointing at FR-004a and at `runs/model.py`'s comment (SC-023)
- [X] T031 [P] Write `tests/unit/test_default_tenant_prefix_is_guarded.py`: `delete_prefix` for the default tenant raises without the flag, and does not raise with it, over the file store and a fake S3 (R2)
- [X] T032 [P] Write `tests/unit/test_store_delete_reports_presence.py`: `delete` returns `True` once and `False` afterwards, over `FileArtifactStore`, `NullArtifactStore`, and `BlobStore`
- [X] T033 [P] Write `tests/unit/test_principal_scopes_default_empty.py`: a principal from the file-backed `KeyRing` has empty scopes, so upgrading grants no admin (FR-038)
- [X] T034 [P] Write `tests/unit/test_new_errors_carry_no_content.py`: each of the four new errors, constructed with hostile inputs, exposes no credential, document text, provider message, or receiver body

**Checkpoint**: constitution v1.8.0 merged, four ADRs Accepted, `docdoc migrate` applies seven migrations, offline suite green — **including `test_runs_clock_confinement.py`, which is what will go red first if T026a was skipped**.

---

## Phase 3: User Story 1 — Runs stop accumulating (Priority: P1)

**Goal**: A configured retention period causes expired runs and the content derived from them to be
removed automatically, idempotently, and without touching anything a surviving run needs.

**Independent test**: configure a short period, create runs, advance the clock, sweep twice — the
first pass removes, the second removes nothing, and surviving runs still complete without re-invoking
a billable provider.

- [X] T035 [P] [US1] Implement `SweepReport` in `src/docdoc/runs/retention.py`: counts by kind and **no identifiers of what was deleted**, because a report listing what it erased is a way of retaining it (FR-012)
- [X] T036 [US1] Implement the candidate query in `src/docdoc/runs/postgres.py`: terminal runs whose `expires_at` has passed, excluding runs under an unexpired lease, ordered and limited by batch size (FR-003, FR-015)
- [X] T037 [US1] Implement `retention.survivors()` in `src/docdoc/runs/retention.py`: the union of `stage_outcomes[].artifact_id` over the tenant's **remaining** runs, read from the row rather than re-derived — the ids are already recorded and re-derivation would need a parser version the deployment may no longer have (R1)
- [X] T038 [US1] Implement `retention.sweep()` in `src/docdoc/runs/retention.py` as the **set difference** `candidates − survivors`, never a complement. The docstring must state the hazard in full: a complement deletes every artifact `docdoc extract` ever wrote from the command line, everything a library caller produced, and every artifact the recorder wrote, because none of those has a run row (R1)
- [X] T039 [US1] Implement the deletion order in `src/docdoc/runs/retention.py` — artifacts and blobs, then the tombstone, then the run row — and document that the reverse loses the list of what to delete and that a crash between any two steps is completed by the next sweep (FR-002)
- [X] T040 [US1] Make an unreachable store abort the sweep **before** any run row is removed, in `src/docdoc/runs/retention.py`, logging once. Removing the row while the content survives loses the only record of what to delete (plan.md gate 9)
- [X] T041 [US1] Define the `CorrectionStore` Protocol with `pinned_runs(run_ids, *, at)` in `src/docdoc/runs/corrections.py`, and a `NullCorrectionStore` returning an empty set. **This is the only place the milestone's two halves touch** — the sweep can honour FR-013 before corrections exist, and Phase 11 replaces the null implementation
- [X] T042 [US1] Call `pinned_runs` from `retention.sweep()` in `src/docdoc/runs/retention.py` and exclude pinned runs from the batch (FR-013)
- [X] T043 [US1] Implement blob deletion in `src/docdoc/runs/retention.py`: a blob is removed when no surviving run of that tenant names it, by the same difference rule as artifacts
- [X] T044 [US1] Implement `docdoc sweep [--once]` in `src/docdoc/cli/commands/sweep.py`, resolving the database and store from the same configuration vocabulary the worker uses, with no second set of variable names (FR-107)
- [X] T045 [US1] Add `DOCDOC_RETENTION_PERIOD` and `DOCDOC_SWEEP_BATCH` (default 500) to `src/docdoc/api/settings.py` and the CLI config, following the existing precedence rule. A deployment configuring no period sweeps nothing (FR-014, FR-049)
- [X] T046 [US1] Emit a `retention.swept` structured event from `src/docdoc/runs/observe.py` carrying counts by kind and no content (FR-012)
- [X] T046a [US1] Implement `maintenance.tick()` in `src/docdoc/runs/maintenance.py` with **one step only** — a single retention sweep batch — bounded in wall-clock time and checked between items, with no thread, subprocess, or event loop, because Milestone 9's FR-025 forbids them in the worker and its heartbeat argument reaches maintenance identically (R15, FR-114)
- [X] T046b [US1] Call `maintenance.tick()` from the worker loop between claims in `src/docdoc/runs/worker.py`, skipping when less than `DOCDOC_MAINTENANCE_INTERVAL` (default 60 s) has passed. This is what makes US1's "on a schedule" true; without it retention exists only as a command somebody has to remember to run (FR-114, FR-117)

### Tests for User Story 1

- [X] T047 [P] [US1] Write `tests/unit/test_sweep_is_a_difference.py` — **the most important test in this milestone**: write artifacts via a CLI-style extraction with no run row, sweep, and assert they survive. Then assert the second extraction reuses rather than re-parses, counted on a parser invocation counter (R1)
- [X] T048 [P] [US1] Write `tests/unit/test_sweep_is_idempotent.py`: a second pass over the same state removes zero, and a pass interrupted at each of the three deletion steps and resumed reaches the same end state (SC-003, FR-002)
- [X] T049 [P] [US1] Write `tests/unit/test_sweep_spares_shared_artifacts.py`: an expiring run and a surviving run sharing an artifact — the artifact survives and the surviving run still completes with zero provider invocations
- [X] T050 [P] [US1] Write `tests/unit/test_sweep_never_touches_live_runs.py`: queued, running, and leased runs are never candidates regardless of `expires_at` (FR-003)
- [X] T051 [P] [US1] Write `tests/unit/test_tombstone_holds_four_fields.py`: the model rejects a fifth, and the policy name distinguishes `retention` from `erasure:*` (FR-004)
- [X] T052 [US1] Write `tests/integration/test_sweep_bounded.py`: a batch limit is honoured and a backlog larger than one batch makes progress across ticks (FR-015)
- [X] T053 [US1] Write `tests/integration/test_sweep_store_unavailable.py`: with the store unreachable, zero run rows are removed and the degradation is logged once

**Checkpoint**: `docdoc sweep` is safe, idempotent, and provably does not delete what the command line wrote.

---

## Phase 4: User Story 2 — Erase one customer's data because they asked (Priority: P1)

**Goal**: Erasure by tenant and by document removes source bytes, derived content, and records —
without reaching another tenant's content and without emptying the store root by accident.

**Independent test**: two tenants submit byte-identical documents; erasing one leaves the other's runs
retrievable and its artifacts reusable, measured on a parser invocation counter.

- [X] T054 [US2] Implement `retention.erase(tenant_id=…)` in `src/docdoc/runs/retention.py` using `delete_prefix` for a non-default tenant — the prefix operation ADR-0014 §5 made possible by putting the tenant above the fan-out (FR-006)
- [X] T055 [US2] Make `retention.erase` fall back to the set-difference path when the tenant is the default one, in `src/docdoc/runs/retention.py`. Its prefix is the store root, so the prefix path would remove everything written before authentication was enabled and everything the command line ever wrote. **It leaves other tenants alone** — `<root>/t/` is outside `<root>/artifacts/`; corrected 2026-09-04 during implementation of T013–T017 (R2, ADR-0015 §5)
- [X] T056 [US2] Implement `retention.erase(blob_id=…)` in `src/docdoc/runs/retention.py`: the blob, the artifacts no surviving run of that tenant names, and the runs that referenced it — by the same difference rule (FR-005)
- [X] T056a **Amended after the same review**: the purge ran only on the prefix path, so erasing the *default* tenant — the one case ADR-0015 §5 forces onto the set difference — left every row behind. It now runs on both paths, never on a retention sweep (which removes one run, not a customer's configuration), and scoped by `run_id` for a document erasure. Delete the tenant's `corrections` rows as part of both erasure paths in `src/docdoc/runs/retention.py`, and its `callbacks`, `deliveries`, and `limit_counters` rows with them. `corrections.payload` holds the predicted and corrected **values** a reviewer stated, so it is the one new table that can carry document-derived content — an erasure that leaves it behind answers "erased" about data that is still there (FR-086, FR-005)
- [X] T057 [US2] Cancel in-flight runs of a tenant before its erasure proceeds, in `src/docdoc/runs/retention.py`. Erasure must never leave a worker writing artifacts into a namespace that has just been removed (FR-010)
- [X] T058 [US2] Make erasure idempotent in `src/docdoc/runs/retention.py`: a second call removes nothing and succeeds, and erasing a tenant that never existed succeeds having removed nothing. An operator running it twice under time pressure is the normal case (FR-009)
- [X] T059 [US2] Implement `docdoc erase --tenant | --document [--purge-store-root]` in `src/docdoc/cli/commands/erase.py`. `--purge-store-root` is the only path to `allow_store_root=True` in the codebase and exists at no HTTP route
- [X] T060 [US2] Add `DELETE /v1/admin/tenants/{tenant_id}` and `DELETE /v1/admin/documents/{blob_id}` to `src/docdoc/api/app.py`, both requiring the `admin` scope, both returning `202` with an erasure identifier, and both **refusing the default tenant** with the message contracts/operations-http-api.md specifies
- [X] T061 [US2] Add the tombstone lookup to `GET /v1/runs/{run_id}` in `src/docdoc/api/app.py`: `410` with `deleted_at` and `policy` for the owning tenant, `404` for everyone else and for an absent identifier. The two must differ **in kind**, not in a field of one shape (FR-011)

### Tests for User Story 2

- [X] T062 [P] [US2] Write `tests/integration/test_erase_tenant.py`: two tenants, byte-identical documents, one erased — the other's runs resolve and its artifacts reuse, on a counter rather than on elapsed time (SC-004)
- [X] T063 [P] [US2] Write `tests/integration/test_erase_default_tenant_refused.py`: the route refuses, the CLI without the flag uses the difference path, and the CLI with the flag empties the root. This is ADR-0014's predicted bug, as a test (R2)
- [X] T064 [P] [US2] Write `tests/contract/test_erased_run_responses.py`: `410` to the owner carrying time and policy, `404` to another tenant **byte-identical** to a never-existent identifier (SC-005)
- [X] T065 [P] [US2] Write `tests/integration/test_erase_is_idempotent.py`: twice, and against a tenant that never existed (FR-009)
- [X] T065a [P] [US2] Write `tests/integration/test_erase_leaves_no_row.py`: after erasing a tenant, every table this milestone adds holds zero rows for it — `corrections`, `callbacks`, `deliveries`, `limit_counters`, and `runs` — while `run_tombstones` holds exactly one per removed run (FR-086)
- [X] T066 [US2] Write `tests/integration/test_erase_cancels_in_flight.py`: a running run is cancelled before its tenant's namespace goes (FR-010)

**Checkpoint**: a deletion request can be honoured, proved, and repeated — and cannot take the deployment with it.

---

## Phase 5: User Story 3 — Revoke a credential without restarting (Priority: P1)

**Goal**: Keys are issued, listed, rotated, and revoked at runtime, and a revocation reaches every
running process within a bound stated as a number.

**Independent test**: issue a key, use it, revoke it, and confirm the next request is refused by an
API process that was never restarted.

- [X] T067 [P] [US3] Implement `Credential` in `src/docdoc/runs/keys.py`: identifier, tenant, scopes, label, timestamps, revocation state — and **no key and no digest**, so a listing cannot leak either (FR-030)
- [X] T068 [US3] Define the `KeyStore` Protocol in `src/docdoc/runs/keys.py` with `resolve`, `issue`, `revoke`, `list_for`, per contracts/operations-layer.md
- [X] T069 [US3] Implement `PostgresKeyStore` in `src/docdoc/runs/keys.py`: `issue` generates the key, stores `sha256(key)` reusing `api/auth.py:digest_of`, and returns the plaintext **once** as the first element of the tuple — the only place in the codebase a key exists outside a caller's memory (FR-025, FR-026)
- [X] T070 [US3] Implement revocation in `src/docdoc/runs/keys.py` as an `UPDATE` setting `revoked_at`, never a `DELETE`, and refuse to reissue a revoked identifier (FR-036). "This key was revoked on the 4th" is the answer an incident review needs
- [X] T071 [US3] Implement `CachedKeyStore` in `src/docdoc/runs/keys.py`: hits cached for `DOCDOC_CREDENTIAL_TTL` (default 30 s), **misses not cached** so issuance takes effect immediately, and the cache holding digests and principals and never a key (R11)
- [X] T072 [US3] Make `KeyRing` in `src/docdoc/api/auth.py` satisfy `resolve` and raise `CredentialError` for the three mutating methods, so the Milestone 9 file ring keeps working unchanged (FR-038)
- [X] T073 [US3] Implement the two-source resolution order in `src/docdoc/api/auth.py`: file ring first, key store second. A deployment mid-migration keeps its file keys working while table-issued keys start to, and no key silently stops working because a table appeared
- [X] T074 [US3] Implement the `admin` scope check as a FastAPI dependency in `src/docdoc/api/app.py`, returning **`404`** for a non-admin principal rather than `403` — an ordinary tenant learning that an administrative surface exists at a URL is a disclosure with no upside
- [X] T075 [US3] Add `POST /v1/admin/credentials`, `GET /v1/admin/credentials`, and `DELETE /v1/admin/credentials/{id}` to `src/docdoc/api/app.py` per contracts/operations-http-api.md, refusing `scopes: ["admin"]` on the create route (FR-032)
- [X] T076 [US3] Implement `docdoc credential issue|revoke|list [--admin]` in `src/docdoc/cli/commands/credential.py`. `--admin` is the **only** way the first administrative credential comes into existence; a route that could mint one is a route that needs no credential
- [X] T077 [US3] Write `last_used_at` best-effort and explicitly not in the request's transaction, in `src/docdoc/runs/keys.py`, with a docstring saying why: exactness would put a write on every authenticated request
- [X] T078 [US3] Emit a `credential.operation` audit event from `src/docdoc/runs/observe.py` naming actor, operation, credential identifier, and tenant — and never the credential (FR-035)
- [X] T079 [US3] Add `DOCDOC_CREDENTIAL_TTL` to `src/docdoc/api/settings.py` and state it in the operator documentation **as a number**, not as "immediately" (FR-028)

### Tests for User Story 3

- [X] T080 [P] [US3] Write `tests/integration/test_revocation_without_restart.py`: `200`, revoke, wait one TTL, `401` — from the same process, never restarted. This closes the defect Milestone 9 documented in task T117 (SC-006)
- [X] T081 [P] [US3] Write `tests/integration/test_rotation_has_no_gap.py`: two active keys during an overlap, zero refusals (SC-008)
- [X] T082 [P] [US3] Write `tests/unit/test_credential_cache_ttl.py`: a hit is cached, a miss is not, and issuance is visible immediately while revocation takes up to the TTL (R11)
- [X] T083 [P] [US3] Write `tests/unit/test_secrets_never_leave.py`: over logs, run records, error bodies, telemetry attributes, webhook payloads, and `argv` — zero occurrences of **either** an API key **or** a callback signing secret, both seeded with distinctive strings. One test for both because the surfaces are identical and two tests would drift; FR-066 additionally requires that no route return a signing secret after registration, asserted here against `GET /v1/callbacks` and the registration response itself (SC-007, FR-033, FR-066)
- [X] T084 [P] [US3] Write `tests/contract/test_admin_surface_is_invisible.py`: a tenant key gets `404` on every admin route, never `403`
- [X] T085 [P] [US3] Write `tests/unit/test_one_refusal_for_four_causes.py`: absent, malformed, unrecognised, and revoked credentials produce one indistinguishable response (FR-034). Assert in the same file that every credential resolves to **exactly one** `tenant_id` and that `admin` is a scope on a principal rather than a second tenant — Milestone 9's FR-060 preserved, and FR-037's statement that a capability is not an identity
- [X] T086 [US3] Write `tests/contract/test_milestone_9_key_file_unchanged.py`: a deployment configured exactly as Milestone 9 configured it authenticates identically and reaches no admin route (FR-038, SC-002)

**Checkpoint**: the security defect Milestone 9 shipped with is closed, and closing it broke nothing.

---

## Phase 6: User Story 4 — Keep one tenant from consuming the deployment (Priority: P2)

**Goal**: Four limits refuse at submission, and one tenant's backlog cannot delay another's run beyond
a bound.

**Independent test**: drive one tenant past each limit and confirm the refusals name the limit while a
second tenant keeps being accepted and claimed.

- [X] T087 [P] [US4] Implement `LimitVerdict` in `src/docdoc/runs/limits.py`: allowed, or a refusal carrying limit, observed, allowed, and retry hint — the four fields the `429` body needs (FR-043)
- [X] T088 [US4] Define the `Limiter` Protocol and implement `NullLimiter` (allows everything, and is the default) in `src/docdoc/runs/limits.py`, so a deployment configuring no limits counts nothing (FR-049)
- [X] T089 [US4] Implement `PostgresLimiter.check` for the submission-rate and period-count limits in `src/docdoc/runs/limits.py` using one `INSERT … ON CONFLICT DO UPDATE` per check (R9)
- [X] T090 [US4] Implement the concurrency limit in `src/docdoc/runs/limits.py` as a `COUNT(*)` over `runs` against `runs_tenant_active`, **not** as a stored counter — the table already knows, and a counter beside it is a fact that can drift (R9)
- [X] T091 [US4] Implement `record_tokens` in `src/docdoc/runs/limits.py`, called by the worker in the transaction that records the terminal state, and implement the token-budget check against the accumulated window (R9, FR-042)
- [X] T092 [US4] Add the token total to the worker's finish path in `src/docdoc/runs/worker.py`, reading usage from `PipelineResult` and writing `runs.tokens_used` in the same statement as the terminal state
- [X] T093 [US4] Wire the limiter into `POST /v1/documents/{blob_id}/runs` in `src/docdoc/api/app.py` **before** any store access, returning `429` with `Retry-After` and creating no run, consuming no queue position, and touching no store (FR-044)
- [X] T094 [US4] Implement the per-tenant fair claim in `src/docdoc/runs/postgres.py`: the CTE of R10 with `row_number() OVER (PARTITION BY tenant_id ORDER BY priority DESC, created_at)`, ordered `starved DESC, rank, priority DESC, created_at`, keeping `FOR UPDATE SKIP LOCKED` on the outer statement so Milestone 9's FR-016 is untouched
- [X] T095 [US4] Add `DOCDOC_LIMIT_*` and `DOCDOC_STARVATION_BOUND` (default 1 h) to `src/docdoc/api/settings.py`, per-tenant with a deployment-wide default, following the existing precedence rule (FR-050)
- [X] T096 [US4] Emit a `limit.refused` structured event from `src/docdoc/runs/observe.py` carrying tenant, limit, and observed value, and no content (FR-052)
- [X] T097 [US4] Document the fixed-window boundary burst — up to twice the limit across a boundary — in `docs/concepts/limits.md`, stating that the bound which actually protects the deployment is the worker pool size and is unaffected

### Tests for User Story 4

- [X] T098 [P] [US4] Write `tests/fixtures/limiter.py` — `InMemoryLimiter` satisfying the same Protocol, so window policy is testable with no database
- [X] T099 [P] [US4] Write `tests/unit/test_limit_windows.py`: each of the four limits, including the documented boundary burst asserted rather than avoided (R9)
- [X] T099a [P] [US4] Write `tests/integration/test_limit_is_not_per_process.py`: two `PostgresLimiter` instances against one database, standing in for two API replicas, share one budget — the second refuses at the limit the first consumed. FR-051 exists because an in-process counter grants the full limit once per replica, and a deployment that scales out silently multiplies every limit it configured
- [X] T100 [P] [US4] Write `tests/contract/test_limit_refusal_shape.py`: `429` names the limit, carries `Retry-After`, and is distinguishable from `401` in kind (FR-043)
- [X] T101 [P] [US4] Write `tests/integration/test_limit_creates_no_run.py`: a refused submission leaves zero rows and touches no store (FR-044)
- [X] T102 [P] [US4] Write `tests/integration/test_limit_aborts_nothing.py`: lowering a limit below current usage refuses new work and leaves runs in flight alone (FR-045, FR-046)
- [X] T103 [US4] Write `tests/integration/test_token_budget_is_one_late.py`: the run that exceeds the budget completes; the next is refused (FR-047)
- [X] T104 [US4] Write `tests/integration/test_fairness_bound.py`: 200 queued runs for one tenant, one for another — the second is claimed within a bound proportional to the number of tenants, not to the backlog (SC-010)
- [X] T104a [US4] Write `tests/perf/test_claim_latency.py`: measure claim latency at queue depths of 100, 1 000, and 10 000 across ten tenants, and assert a budget with the reason stated in the file, as `tests/perf/` already does elsewhere. R10 accepts that the window function sorts the eligible set rather than walking `runs_claimable` in order; plan.md says that cost is "measured rather than assumed", and a deployment with a very deep queue is exactly the one that asked for fairness
- [X] T105 [US4] Write `tests/unit/test_claim_order_default.py`: with no priorities and one tenant, claim order is ascending creation time, exactly as Milestone 9 specified (FR-091)

**Checkpoint**: a shared deployment survives one noisy customer, and a quiet one still gets served.

---

## Phase 7: User Story 5 — Be told when a run finishes (Priority: P2)

**Goal**: A registered callback receives one signed delivery per terminal run, retried on failure,
and a hostile or broken destination affects nothing else.

**Independent test**: register a callback, complete a run, verify the signature; then break the
receiver and confirm the run is unaffected while the delivery backs off and comes to rest.

- [X] T106 [P] [US5] Implement `Callback` and `Delivery` in `src/docdoc/runs/delivery.py`, with `last_error` typed as a **class name** and never a receiver's response body
- [X] T107 [US5] Implement the destination policy in `src/docdoc/runs/delivery.py`: `getaddrinfo`, refuse if **any** resolved address is loopback, link-local, private, multicast, reserved, or unspecified, and refuse non-HTTPS unless explicitly allowed. Every address, not the first — a host resolving to one public and one private address is the attack (R8, FR-060)
- [X] T108 [US5] Implement the pinned connection in `src/docdoc/runs/delivery.py`: connect to the validated address, present the original hostname in `Host` and in TLS SNI, and **do not follow redirects**. Re-validate before every attempt; validating once at registration fails to DNS rebinding (R8, FR-061)
- [X] T109 [US5] Implement signing in `src/docdoc/runs/delivery.py`: `X-Docdoc-Signature: t=<unix>,v1=<hex>`, HMAC-SHA256 over `f"{t}.{body}"`, stdlib only. The timestamp is inside the signed material so a captured delivery is not valid forever (R7, FR-055)
- [X] T110 [US5] Implement the payload builder in `src/docdoc/runs/delivery.py` carrying run identity, terminal state, failing stage and error class, processing identity where one exists, routing outcome where configured, and the delivery identifier — and nothing else (FR-054)
- [X] T111 [US5] Implement `enqueue`, `due`, and `attempt` on `PostgresDeliverer` in `src/docdoc/runs/delivery.py`, with backoff and an attempt limit after which the delivery comes to rest at `failed` (FR-057)
- [X] T112 [US5] Enqueue a delivery from the worker's finish path in `src/docdoc/runs/worker.py` when the run carries a `callback_id`, in the same transaction as the terminal state so a crash cannot lose the notification
- [X] T112a [US5] Add the due-deliveries step to the existing `maintenance.tick()` in `src/docdoc/runs/maintenance.py`, ordered before the sweep batch and sharing the same budget. The tick was built in T046a with one step; this adds the second rather than introducing a scheduler
- [X] T113 [US5] Add `POST /v1/callbacks`, `DELETE /v1/callbacks/{id}`, and `GET /v1/runs/{run_id}/delivery` to `src/docdoc/api/app.py`, with the `422` body naming the class of refusal and **not** the addresses it resolved to
- [X] T114 [US5] Accept `callback_id` on the run submission body in `src/docdoc/api/app.py`, returning `404` for an unknown or another tenant's callback
- [X] T115 [US5] Add `DOCDOC_DELIVERY_*` settings (attempt limit, timeout, backoff base, allowed private destinations) to `src/docdoc/api/settings.py`

### Tests for User Story 5

- [X] T116 [P] [US5] Write `tests/support/webhook_receiver.py` — a stdlib HTTP receiver that records and verifies. Mocking the transport would test nothing about R8
- [X] T117 [P] [US5] Write `tests/unit/test_signature_covers_timestamp.py`: a body altered by one byte fails, and a replayed delivery is detectable from the signed timestamp (SC-011)
- [X] T118 [P] [US5] Write `tests/unit/test_destination_policy.py`: every resolved address is checked and not just the first; loopback, link-local, private, multicast, reserved, and unspecified all refused; redirects not followed (SC-013)
- [X] T119 [P] [US5] Write `tests/unit/test_delivery_payload_carries_no_content.py`: over a run seeded with distinctive strings (FR-054)
- [X] T120 [US5] Write `tests/integration/test_delivery_signed_and_retried.py`: one delivery, verified; then a failing receiver, backoff, and rest at the attempt limit (SC-011, SC-012). Assert across all attempts that `delivery_id` is **identical every time** — FR-056 makes it the value a receiver deduplicates on, so an id regenerated per attempt turns at-least-once delivery into at-least-once *processing* on the receiver's side
- [X] T121 [US5] Write `tests/integration/test_delivery_failure_touches_no_run.py`: the run stays `succeeded`, no worker blocks, and no other tenant's deliveries are delayed (FR-063)
- [X] T122 [US5] Write `tests/integration/test_one_delivery_per_run.py`: the `UNIQUE (run_id)` constraint makes overlap impossible rather than unlikely (FR-058)
- [X] T123 [US5] Write `tests/integration/test_polling_still_works.py`: with delivery failed, the run's terminal state is still readable by polling — a lost delivery loses no information (FR-065)
- [X] T123a [P] [US5] Write `tests/unit/test_no_callback_no_socket.py`: with no callback registered, run a full submit-claim-finish cycle with `socket.socket` patched to raise, and require it to succeed. FR-064 says a deployment registering no callbacks performs no outbound request; the graph proves nothing about a module that opens a connection at runtime, which is why `tests/unit/test_scoring_is_offline.py` already does exactly this for evaluation

**Checkpoint**: a client can stop polling, and a broken receiver cannot reach the deployment.

---

## Phase 8: User Story 6 — See a run in a tracing backend (Priority: P2)

**Goal**: The events Milestone 9 already emits reach an operator's collector, carrying identifiers and
nothing else, without displacing anything already installed.

**Independent test**: point at a collector, execute one run, find one trace with a span per stage and
per transition; then unset the endpoint and confirm the offline suite passes with the extra absent.

- [X] T124 [US6] Add `set_observer` / `observer` to `src/docdoc/runs/observe.py`, mirroring `pipeline/observe.py` exactly — one slot, same signature, return value ignored, a raising observer cannot fail a run. Today the module only calls `logging`, so `run.transition` could not reach an exporter at all (R4)
- [X] T125 [US6] Implement `bridge(endpoint, headers)` in `src/docdoc/telemetry/__init__.py`, importing `opentelemetry` **inside** the function behind `docdoc[otel]` (R5, FR-019)
- [X] T126 [US6] Map `pipeline.stage` and `run.transition` payloads to spans in `src/docdoc/telemetry/__init__.py`, correlated by run identity and by processing identity where one exists (FR-021)
- [X] T127 [US6] Install the bridge from `src/docdoc/api/app.py` and `src/docdoc/runs/worker.py` **only when the observer slot is empty**, logging once which happened. `pipeline/observe.py` documents the single slot as a decision; an exporter that took it silently would be a regression of somebody else's observability (R4)
- [X] T128 [US6] Make exporter failure non-fatal in `src/docdoc/telemetry/__init__.py`: no run fails, no stage blocks, and the failure is reported once per outage rather than once per event, matching the pattern `pipeline/observe.py` already uses (FR-022)
- [X] T129 [US6] Add `DOCDOC_OTLP_ENDPOINT` and `DOCDOC_OTLP_HEADERS` to `src/docdoc/api/settings.py`. Unset means nothing is exported and no telemetry dependency is required (FR-018)

### Tests for User Story 6

- [X] T130 [P] [US6] Write `tests/unit/test_runs_observer_slot.py`: install, replace, remove; a raising observer does not fail a transition
- [X] T130a [P] [US6] Write `tests/unit/test_emission_points_unchanged.py`: capture the `pipeline.stage` and `run.transition` payloads with no bridge installed, install the bridge, capture again, and require them byte-identical — same fields, same values, same order. FR-024 forbids export from changing an existing emission point, and an exporter that enriches a payload "harmlessly" is how a deployment's log parsing breaks at the same moment its tracing starts working
- [X] T131 [P] [US6] Write `tests/integration/test_telemetry_does_not_displace.py`: with an observer already installed, the bridge is not installed and the log says so (R4)
- [X] T132 [P] [US6] Write `tests/integration/test_telemetry_leaks_nothing.py`: over a document seeded with distinctive strings, zero occurrences in any span attribute (SC-015)
- [X] T133 [P] [US6] Write `tests/integration/test_telemetry_collector_down.py`: runs succeed, failures reported once per outage (SC-016)
- [X] T134 [US6] Write `tests/contract/test_offline_without_otel.py`: with `DOCDOC_OTLP_ENDPOINT` unset and the extra not installed, the full offline suite passes (SC-014, SC-021)

**Checkpoint**: the four stages and five states are visible in somebody else's tools, and invisible when nobody asked.

---

## Phase 9: User Story 8 — Let an urgent run past a batch (Priority: P3)

**Goal**: A client can order its own work within a ceiling its operator sets, and nothing starves.

**Independent test**: queue a batch and one urgent run — the urgent one is claimed next; then age an
ordinary run past the bound and confirm it goes ahead of a newer urgent one.

**Depends on**: Phase 6 (T094 built the claim query this phase configures).

- [X] T135 [US8] Accept `priority` on the run submission body in `src/docdoc/api/app.py`, clamp it to the tenant's ceiling, and **echo the granted value always** — including when none was requested — so a client can see what it got without reading a ceiling it cannot (FR-087a)
- [X] T136 [US8] Make an over-ceiling request **accepted at the ceiling** rather than refused, in `src/docdoc/api/app.py`. An operator lowering a ceiling must not break a client that changed nothing (FR-087a)
- [X] T137 [US8] Add `DOCDOC_PRIORITY_CEILING` per tenant with a deployment-wide default to `src/docdoc/api/settings.py`, reachable through no route — a tenant that could raise its own ceiling is self-service escalation past another tenant's queue (FR-087b)
- [X] T138 [US8] Verify priority affects no value, verdict, location, or identity, by passing it nowhere below `Runs` (FR-093)

### Tests for User Story 8

- [X] T139 [P] [US8] Write `tests/unit/test_claim_order.py`: priority preferred, starvation bound wins over priority, per-tenant rank ahead of both, and the no-configuration default collapsing to creation time (R10, FR-088, FR-089, FR-091)
- [X] T140 [P] [US8] Write `tests/contract/test_priority_ceiling.py`: an over-ceiling request is accepted at the ceiling and the response states the granted priority (SC-026)
- [X] T141 [US8] Write `tests/integration/test_priority_does_not_starve.py`: an ordinary run past the bound is claimed ahead of a newer urgent one (FR-089)

**Checkpoint**: ordering exists, and nothing waits forever.

---

## Phase 10: User Story 7 — Route the doubtful, take the correction back (Priority: P2)

**Goal**: A completed result carries a versioned routing outcome computed from trusted signals only,
and a reviewer's correction can be recorded against it without changing it.

**Independent test**: run a partially grounded document, read the decision and its reasons, submit a
correction, and confirm the result hashes identically before and after.

**This is the 10b half.** If the split is taken, this phase and T145 move to Milestone 11 and nothing
else changes.

- [X] T142 [P] [US7] Implement `RoutingOutcome` (exactly two members), `RoutingReason`, and `RoutingDecision` in `src/docdoc/runs/routing.py` — frozen, `extra="forbid"`, with a module comment saying why there is no third outcome: `reject` is a disposition the caller owns and `retry` would re-enter the pipeline (FR-067, FR-067a)
- [X] T143 [US7] Implement `RoutingPolicy` as data loaded from configuration in `src/docdoc/runs/routing.py`, carrying a version string that every decision records (FR-070)
- [X] T144 [US7] Implement `decide(result, policy)` in `src/docdoc/runs/routing.py` as a **pure function** reading grounding status and score, validation severity and verdict, and schema requiredness — and reading `model_confidence` nowhere (FR-068, FR-069, FR-073)
- [X] T145 [US7] Implement `PostgresCorrectionStore` in `src/docdoc/runs/corrections.py` satisfying the Protocol T041 defined, including the real `pinned_runs`, and replace `NullCorrectionStore` in the sweep's wiring. **This is the only task in this phase that Phase 3 depends on**
- [X] T146 [US7] Import `Correction` from `docdoc.evaluation.corrections` in `src/docdoc/runs/corrections.py` and store it whole as `jsonb`, lifting `run_id`, `processing_id`, and `field_path` out for querying only. The model is Milestone 6's and is not redefined (FR-077, R13)
- [X] T147 [US7] Add `POST /v1/runs/{run_id}/corrections` and `GET /v1/runs/{run_id}/corrections` to `src/docdoc/api/app.py`, tenant-scoped in the query rather than checked after the fetch, with `410` for an erased run and `409` for one with no result (FR-079, FR-082)
- [X] T148 [US7] Take `annotator` from the request body and never from the credential, in `src/docdoc/api/app.py`. The person who reviewed a value and the key that submitted it are different facts (FR-085)
- [X] T149 [US7] Add the `routing` block to `GET /v1/runs/{run_id}` in `src/docdoc/api/app.py`, absent rather than null when no policy is configured (FR-075, FR-076)
- [X] T150 [US7] Implement correction export in a form the existing promotion path accepts, in `src/docdoc/runs/corrections.py`, so a correction becomes dataset signal without a second model (FR-081)
- [X] T151 [US7] Add `DOCDOC_ROUTING_POLICY` and `DOCDOC_CORRECTION_RETENTION_PERIOD` to `src/docdoc/api/settings.py`, and make correction retention independent of run retention with the longer winning (FR-013)

### Tests for User Story 7

- [X] T152 [P] [US7] Write `tests/unit/test_routing_ignores_model_confidence.py` — **the negative test that is the requirement**: vary `model_confidence` alone across a fixture set and assert the decision does not move (SC-017, Principle II)
- [X] T153 [P] [US7] Write `tests/unit/test_routing_is_pure.py`: no clock, no store, no network, no artifact written, and identical results under one policy version producing identical decisions (FR-071, FR-073)
- [X] T154 [P] [US7] Write `tests/unit/test_routing_outcome_set.py`: exactly two members, asserted by name, and the set not configurable (FR-067)
- [X] T155 [P] [US7] Write `tests/unit/test_routing_policy_edit_is_not_retroactive.py`: editing a policy alters no existing decision (FR-074)
- [X] T156 [P] [US7] Write `tests/integration/test_correction_changes_nothing.py`: the result, its artifacts, and its identities hash identically before and after (SC-018)
- [X] T157 [P] [US7] Write `tests/integration/test_correction_moves_no_metric.py`: golden-set metrics unchanged absent an explicit promotion (SC-018, FR-080)
- [X] T158 [US7] Write `tests/integration/test_correction_pins_a_run.py`: a run carrying a live correction survives the sweep, and goes when the correction's own retention allows (SC-019, FR-013)
- [X] T159 [US7] Write `tests/contract/test_no_review_platform.py`: assert that no route exists for assignment, reviewer queues, workload, or review state, and that the viewer has no write path (FR-083, FR-084)

**Checkpoint**: doubtful results are marked as such, corrections come back, and nothing about the result moved.

---

## Phase 11: Polish & Cross-Cutting Concerns

- [X] T160 [P] Write `docs/concepts/retention.md`: the sweep, the set difference and the hazard it avoids, the tombstone, erasure, and the default tenant's exception — stating each capability's limits where it states its behaviour (FR-109)
- [X] T161 [P] Write `docs/concepts/credentials.md`: issuance, the once-only plaintext, rotation with overlap, revocation and its bound **as a number**, the administrative scope, and the lockout that is possible and documented (FR-109)
- [X] T162 [P] Write `docs/concepts/limits.md`: the four limits, refusal at submission, the token budget being one submission late, and the fixed-window boundary burst (FR-109)
- [X] T163 [P] Write `docs/concepts/delivery.md`: at-least-once, signing and how to verify, ordering, the destination policy, and that polling remains the record (FR-109)
- [X] T164 [P] Write `docs/concepts/routing.md`: which signals a decision reads, why `model_confidence` is not one, the two outcomes, versioning, and that a decision is not a disposition (FR-109)
- [X] T165 [P] Write `examples/erase_tenant.md`, `examples/rotate_credential.md`, and `examples/receive_webhook.py` — the three runnable examples FR-110 requires
- [X] T166 [P] Write `examples/routing/default.json` and `examples/corrections/total.json`, referenced by quickstart.md scenarios 5
- [X] T167 Update the README: the roadmap table gains Milestone 10 as Done; the configuration section gains every new variable; and the authentication warning is revised to state what is now true — credentials can be issued and revoked at runtime, **the viewer still does not work under authentication**, and a deployment that has enabled none of this is exactly as exposed as Milestone 9 left it (FR-111)
- [X] T168 Update the README's "database" claim and `docs/concepts/runs.md` to record that Milestone 9's assumption "the database is a dependency of asynchrony only" ends here: revocation, limits, delivery, and corrections each need it, and a deployment enabling none of them still needs none (FR-113)
- [X] T169 Write the `CHANGELOG.md` entry for Milestone 10 under Unreleased, in the style of Milestone 9's: what was added, what it costs, and what it deliberately does not do
- [X] T170 Flip `specs/010-operations-and-corrections/spec.md`'s **Status** from Draft to Implemented, in the same change as T167 — the transition the spec template wires to the roadmap task so it rides on a step the milestone already has to do
- [X] T171 Write `tests/integration/test_golden_set_unmoved.py`: golden-set metrics bit-identical with every capability enabled and with all disabled, and a result produced each way agreeing on every value, verdict, location, and identity (**SC-001** — the criterion the milestone exists to satisfy)
- [X] T172 Write `tests/contract/test_milestone_9_deployment_unchanged.py`: quickstart scenario 0 as a suite — no sweeping, no counting, no refusing, no exporting, no routing, no delivering, and zero rows in the new tables (SC-002)
- [X] T173 Write `tests/unit/test_no_fifth_process.py` and `tests/unit/test_maintenance_is_bounded.py`: four process types and four compose services (SC-025), and a maintenance tick delaying a claim by at most the budget plus one delivery timeout with zero leases lost (SC-024)
- [X] T173a Write `tests/unit/test_protected_layers_untouched.py`: the milestone's diff against `main` touches **zero** files under `src/docdoc/kernel/`, `ingest/`, `extraction/`, `grounding/`, `validation/`, and zero lines of `artifacts/derivation.py` and `artifacts/envelope.py`'s identity derivation (SC-020, FR-094). Assert in the same file that `tests/unit/test_kernel_purity.py` and the determinism audit hook are unmodified (FR-095). plan.md's gate 1 already cites SC-020 as its evidence, and until this exists that citation points at nothing
- [X] T174 *Withdrawn 2026-09-04, moved to T046a.* It implemented `maintenance.tick()` in the Polish phase, which left User Story 1's promise that runs "age out on their own, on a schedule" undeliverable until the last phase of the milestone. The number is retained rather than reused, because a task identifier that changes meaning is worse than a gap in a sequence
- [X] T175 *Withdrawn 2026-09-04, split into T046b and T112a.* Same reason
- [X] T175a Document in `docs/concepts/runs.md` that a deployment running no worker sweeps nothing and delivers nothing, while its credentials still revoke on time because that is a cache lifetime and not a scheduled task (FR-117)
- [X] T175b Add the expired-limit-counter step to `maintenance.tick()` in `src/docdoc/runs/maintenance.py`. A counter table that only grows is the failure this milestone exists to prevent
- [X] T176 Run every quickstart.md scenario end to end against the four-container composition and correct any step that does not work as written. A validation guide nobody has run is a guide that lies

  **Done 2026-09-09.** The port conflict recorded here on the first attempt was
  not one: `compose.yml` already parameterises the published Postgres port
  precisely because "5432 is the port a developer already running Postgres has
  taken", so `DOCDOC_PG_PORT=55432 docker compose up` stands the composition up
  beside an unrelated database without touching it. The earlier refusal was
  wrong about the facts and the composition was never at risk.

  Four containers came up healthy, `docdoc migrate` applied 0003–0007 and
  reported `nothing to apply` on a second run, and the scenarios were executed.
  **Eleven defects found, every one of them in the documentation or the
  configuration rather than in the code the suite covers** — which is the shape
  of finding this task exists for, because a green suite cannot see any of them.

  Verified live, end to end: SC-002 (a run through all four stages with no
  `routing` key and zero rows in every new table), SC-003 (sweep removes 3, second
  pass removes 0), SC-005 (`410` to the owner with `deleted_at` and `policy`;
  `404` to everyone else, `cmp`-identical to a never-existent identifier),
  SC-006 (`200` → revoke → `401` from an API process whose `StartedAt` did not
  move), SC-007 (zero occurrences of either key in the logs or the table),
  SC-009 (`202 202 429` naming `concurrent_runs`, carrying `Retry-After`, and
  leaving 2 run rows rather than 3), SC-013 (a loopback destination refused
  `422` naming the category and no address), SC-026 (`urgent` accepted at the
  ceiling as `"ordinary"`, granted as `"urgent"` when the ceiling is raised, and
  `"high"` refused `422`), the R1 hazard (a CLI extraction's artifacts all
  `reused` after a sweep), and ADR-0014's predicted bug (erasing the root-owning
  tenant refused `409`, and erasure idempotent against a tenant that never
  existed).

  Not executed live: scenario 3's 200-run fairness measurement, scenario 5's
  correction round trip, and scenario 6's collector — each needs a fixture or a
  service the composition does not carry, and each is covered by a test.

  ### What running it found

  1. **`curl -F file=@…` returns `415` on every upload.** The route reads the raw
     body and decides the type from the bytes; multipart arrives as a form and is
     rejected as an unrecognised signature. This is the **first command in
     scenario 0**, so the guide failed on its own opening line. `--data-binary @…`
     is correct.
  2. **`datasets/public/invoice-01.pdf` does not exist.** `datasets/` contains
     `mvp/` and no PDFs. Every scenario named a document that is not there.
  3. **Scenario 1 could not work at all.** It uses `/v1/admin` routes, and the
     composition it tells you to bring up has authentication **off** — so every
     one of those routes correctly answers `404`, including to a credential that
     carries `admin`. The guide never said to enable it. A whole section is now
     devoted to doing so, because the key file is what turns authentication on and
     a table-issued credential resolves through the chain behind it.
  4. **`DOCDOC_DEFAULT_TENANT` was not forwarded by `compose.yml`.** It is read by
     `docdoc.artifacts.paths` in the api, in every worker, and in `docdoc migrate`,
     and it describes the store's layout — so it must be identical in all of them.
     A deployment upgrading a store that already held pre-tenant content would be
     told by `migrate` that it disagreed with itself and had no way to answer from
     the composition. Now forwarded.
  5. **`DOCDOC_LIMIT_TOKENS_PER_MONTH` is read by nothing** (`…_PER_PERIOD`).
  6. **`DOCDOC_STARVATION_BOUND` is read by nothing**, and `10s` is not a value it
     would take (`DOCDOC_RUN_STARVATION_SECONDS=10`).
  7. **`DOCDOC_RETENTION_PERIOD=1h` is read by nothing**, and the setting takes
     whole days (`DOCDOC_RUN_RETENTION_DAYS=1`).
  8. **`DOCDOC_CREDENTIAL_TTL` is read by nothing** (`DOCDOC_RUN_CREDENTIAL_TTL_SECONDS`).
  9. **`docdoc sweep --once` does not exist.** The flags are `--retention-days`,
     `--batch`, and `--all`.
  10. **`docdoc extract --document FILE` does not exist**; the file is positional.
      This appears in the R1 check, which is the most important command in the
      document.
  11. **`psql` was assumed on the host.** It is not installed there and does not
      need to be — the container has it.

  ### The one code defect it found

  **`priority` crossed the wire as a number.** A submission asks for
  `"urgent"` by name and the response answered `10`;
  `contracts/operations-http-api.md` says the name in three places. The
  asymmetry is not cosmetic: the ceiling is deliberately readable through no
  route (FR-087b), so a client receiving `10` has to guess a mapping it cannot
  check, and a third priority class would silently break every client that had
  hard-coded two.

  Fixed by `Priority.label` / `Priority.from_label`: the **name** crosses the
  wire on both the submission response and the run state, and the `IntEnum`
  value stays in the column the claim query orders by. Verified live — `0` and
  `10` in the table, `"ordinary"` and `"urgent"` in the body.

---

## Corrections made during implementation, 2026-09-09

Recorded here rather than left in a diff, on the same terms as the corrections
Phases 1 to 6 already carry.

- **T117 was marked complete and its file did not exist.**
  `tests/unit/test_signature_covers_timestamp.py` is now written. A ticked box
  with nothing behind it is worse than an unticked one, because the next reader
  stops looking.

- **`install_bridge` moved twice, and `lint-imports` moved it both times.** It
  was first written in `docdoc.telemetry`, which sits *below* `docdoc.runs` and
  therefore cannot read the observer slot it installs into. Moving it into
  `runs/observe.py` broke the "outbound HTTP is confined" contract instead,
  through `postgres → observe → telemetry → opentelemetry`. The shape that
  holds is the third one: the function takes the bridge **factory** as a
  parameter and the two front ends compose them, because the composition of "a
  slot" and "an exporter" belongs to the layer that has both. Neither contract
  was weakened and no `ignore_imports` entry was added.

- **`packaging/docker/compose.yml` named eight settings nothing reads.**
  `DOCDOC_RETENTION_PERIOD`, `DOCDOC_SWEEP_BATCH`, `DOCDOC_CREDENTIAL_TTL`,
  `DOCDOC_STARVATION_BOUND`, `DOCDOC_DELIVERY_ATTEMPT_LIMIT`,
  `DOCDOC_DELIVERY_TIMEOUT`, `DOCDOC_CORRECTION_RETENTION_PERIOD`, and
  `DOCDOC_MAINTENANCE_INTERVAL` are corrected to the names in `identity.py`. An
  operator exporting any of them got silence.

- **`DOCDOC_MAINTENANCE_INTERVAL` gained its unit**, becoming
  `DOCDOC_MAINTENANCE_INTERVAL_SECONDS`, which corrects
  `contracts/operations-layer.md`. This project's rule is that a duration
  carries its unit in its name — the same reason `DOCDOC_RUN_LEASE_SECONDS` is
  not `DOCDOC_RUN_LEASE`.

- **`docdoc worker` configured none of this.** The CLI never passed a retention
  period, a sweep batch, a deliverer, or a maintenance interval, so T046b's tick
  ran with nothing to do in every real deployment. It now reads all of them from
  the environment, each defaulting to off.

- **The signing secret is a `SecretBook` read from a file.** `data-model.md`
  says `callbacks.secret_digest` is "not enough to sign with" and that the
  secret comes from configuration; T106–T111 did not say where. A file of
  secrets matched by digest is what makes a per-destination secret (FR-055) and
  "no route returns it" (FR-066) both true. Registration with an unconfigured
  secret is refused rather than accepted and left undeliverable.

- **Eight test files were consolidated into three**, and plan.md's tree is
  updated to say so: the four delivery integration files became
  `test_delivery_postgres.py`, the two correction ones became
  `test_corrections_postgres.py`, and the three telemetry-installation ones
  became `test_telemetry_installation.py`. Each set needed one identical fixture
  and separate files would have been separate copies of it. Phases 3 to 6 made
  the same call for the same reason.

- **T132 and T133 are unit tests, not integration tests.** Both were planned
  against a live collector, which would have put SC-015 — the check that no
  document content leaves the deployment — in the suite that only runs where
  infrastructure exists. `telemetry.span_for` is a pure function so the leak
  check runs offline, and `bridge(tracer=…)` is a narrow seam so "reported once
  per outage" is testable without an outage.

- **`limits.LimitPolicy` accepts `priority_ceiling` and ignores it.** A ceiling
  is a per-tenant policy in the same file the four limits use, and giving
  `LimitPolicy` a fifth field the limiter never reads would have been a second
  source of truth. It is read by the HTTP layer, which is the only thing that
  grants a priority.

---

## Dependencies

```text
Phase 1 (Setup)
  └─> Phase 2 (Foundational)  ← T008 constitution v1.8.0 and T009–T012 ADRs gate ALL code
        ├─> Phase 3  (US1 retention)   P1
        │     └─> Phase 4  (US2 erasure)      P1   — reuses the difference and the CLI
        ├─> Phase 5  (US3 credentials)  P1        — independent of Phases 3, 4
        ├─> Phase 6  (US4 limits + fairness) P2   — T094 builds the claim query
        │     └─> Phase 9  (US8 priority)     P3   — configures what T094 built
        ├─> Phase 7  (US5 delivery)     P2        — independent
        ├─> Phase 8  (US6 telemetry)    P2        — independent
        └─> Phase 10 (US7 routing + corrections) P2 — T145 is Phase 3's only dependency here
              └─> Phase 11 (Polish)
```

**The maintenance tick is built where it is first needed.** T046a creates it in Phase 3 with one step,
because User Story 1's story text promises automatic ageing and a tick that arrives in Polish makes
that promise false for the length of the milestone. T112a adds the delivery step in Phase 7 and T175b
the counter step in Polish — each phase extends a loop that already runs rather than introducing a
scheduler, which is what keeps FR-116's "no fifth process type" true by construction.

## Parallel Opportunities

- **Phase 2**: T010, T011, T012 (three ADRs) run in parallel; T019–T023 (five migrations) run in parallel; T030–T034 (five tests) run in parallel
- **Phase 3**: T047–T051 run in parallel once T038 lands
- **Phase 5**: T080–T085 run in parallel once T075 lands
- **Phases 5, 7, 8** are independent of one another and of Phases 3–4 — three developers, three phases, no contention
- **Phase 6, 7, 8**: T099a, T123a, T130a are each independent of their phase's implementation tasks and of one another
- **Phase 11**: T160–T166 (five documents, two example sets) run in parallel

## Implementation Strategy

**MVP is Phase 2 + Phase 3.** A deployment that can sweep is a deployment that stops accumulating,
and it is the only item on Milestone 9's deferred list that gets worse with time.

**Second increment: Phase 5.** It closes a documented security defect — a revoked key that keeps
working until a restart — and it is what any operator interface will eventually need.

**Third: Phase 4**, which reuses Phase 3's difference and adds the ability to answer a deletion
request.

**Then 6, 7, 8 in any order**, by whichever pressure the deployment is feeling: noisy tenants,
polling clients, or blind operators.

**Phase 10 last, and separable.** It is the product half, it is the only part whose review needs a
product opinion rather than an operational one, and Phase 3 depends on exactly one of its tasks.

**Gate reminder**: T008 must merge before any code task in any phase. Constitution v1.8.0 is what
makes gate 12 pass, and plan.md records it as FAIL until then.

---

## Phase 12: Convergence

Appended 2026-09-09 by `/speckit-converge`, after `/speckit-implement` closed T176.

**Every item here is a gap in *evidence*, not in behaviour.** The implementation satisfies all 122
functional requirements and all eight user stories; the P1 stories were additionally verified end to
end against the four-container composition. What these tasks add is the regression coverage that
makes six of those claims survive the next change, plus one command-line invocation that is still
wrong in the quickstart.

**Three of them exist because consolidation lost an assertion.** Phases 3 to 11 merged eight planned
test files into three, which was the right call — each set needed one identical fixture — but the
merge dropped the `410`/`404` contract, the callback-secret leak check, and the audit event. A
consolidation that keeps the fixtures and loses the assertions is the failure mode this phase is
looking for, and it found three.

- [X] T177 Write `tests/contract/test_erased_run_responses.py` asserting the two outcomes differ **in kind**: `410` to the owning tenant carrying `deleted_at` and `policy`, and `404` to every other tenant that is **byte-identical** to the response for an identifier that never existed — compared with `cmp`-style equality on the body rather than by reading both, because "byte-identical" is the claim. `run_erased` currently appears in no test file at all; the behaviour was verified by hand on 2026-09-09 and has no regression test per SC-005, FR-011 (missing)

- [X] T178 Extend `tests/unit/test_credential_never_logged.py` (or add the companion T083 named) to seed a **callback signing secret** with a distinctive string and assert zero occurrences across logs, run records, error bodies, telemetry attributes, delivery payloads, and `argv` — and that the registration response is the only place it is ever accepted, never returned. The existing file covers API keys only, so the second half of the surface FR-066 names is untested per FR-066, SC-007 (missing)

- [X] T179 Write a contract test asserting the `routing` block is **present and correctly shaped** on `GET /v1/runs/{run_id}` when a policy is configured: `outcome` one of exactly two values, a `policy_version` on every decision, and `reasons` naming field, signal, and observed — with `observed` a **string** and never a score. Only the absent case is asserted today, so `_routing_for` reaches the HTTP surface with no test behind it per FR-076, FR-075, FR-072 (missing)

- [X] T180 Write a test that a credential issue and a credential revoke each emit one `credential.operation` audit event naming actor, operation, credential identifier, and tenant — and **never the credential**. The event is emitted by `_audit` in `src/docdoc/api/app.py` and asserted nowhere; it appears in the suite only as an example of an event the span bridge deliberately drops per FR-035 (missing)

- [X] T181 Add `require_otlp_endpoint()` to `tests/infra.py` beside `require_database` and `require_s3_endpoint`, and write one `otel`-marked test that an executed run produces a trace correlatable to the run by run identity and to the result by processing identity. The marker is registered in `pyproject.toml` and `DOCDOC_TEST_OTLP_ENDPOINT` is defined in `tests/infra.py`, and **zero tests carry the marker** — a marker that marks nothing is the same defect as a documented variable nothing reads, which this milestone already corrected eight of per SC-014, FR-021, FR-017 (missing)

- [X] T182 Correct `quickstart.md` scenario 5: it names `docdoc evaluate --dataset … --report …` and the command is `docdoc eval MANIFEST --predictions DIR`. **This survived the T176 correction pass** — eleven other commands in that document were run and fixed and this one was not, which is exactly how the guide came to need T176 in the first place. Run it rather than reading it per quickstart scenario 5, FR-109 (contradicts)

- [X] T183 Extend `tests/unit/test_no_fifth_process.py` to assert SC-024's second half: that a maintenance tick delays a worker's next claim by at most the budget plus one delivery timeout **with zero leases lost** — a heartbeat must not be delayed into losing a lease the worker still holds. The budget bound is asserted; the lease half appears only in prose per SC-024, FR-114 (partial)

### What Phase 12 found

**T179 found a production bug, and it was the reason to write the test.**
`_routing_for` in `src/docdoc/api/app.py` assembled a `PipelineResult` out of two
artifacts it had read back from the store — with `outcomes=()` and
`provenance=validation.provenance`. Those are two different models:
`PipelineResult.provenance` is a `RunProvenance` and a validation artifact
carries a `ValidationProvenance`. Pydantic rejected it, so **`GET
/v1/runs/{run_id}` raised on every succeeded run as soon as a routing policy was
configured** — the entire feature, unreachable, on the one route that serves it.

Nothing caught it because nothing exercised it. The unit tests call `decide`
directly with fixtures; the live pass on 2026-09-09 ran eight quickstart
scenarios and scenario 5 — the one that configures a policy — was among the three
it did not.

The fix is not a cast. `decide` was annotated as taking a `PipelineResult` and
reads exactly two of its attributes, so the route was fabricating six it did not
have in order to satisfy a type. `routing.Routable` now names the two, and
`routing.StoredResult` carries them — which is the same refusal `_stored_result`
already makes one function away: *"reporting stage statuses for work this request
did not do would be fiction."* A retrieval is not a run, and a type satisfied by
a fiction is a type that stops catching things.

**The other six were evidence gaps, and three had one cause.** Phases 3–11
consolidated eight planned test files into three. That was right — each set
needed one identical fixture — but the merge silently dropped three assertions:
the `410`/`404` contract (`run_erased` appeared in no test at all), the
callback-secret leak check, and the credential audit event. A consolidation that
keeps the fixtures and loses the assertions is worth watching for; it is
invisible in a diff and in a green suite.

**And the `otel` marker marked nothing.** T006 registered it in Phase 1 "so the
offline suite excludes exporter-dependent tests by default", `tests/infra.py`
defined `DOCDOC_TEST_OTLP_ENDPOINT`, and no test carried either — the same
"registered and describes nothing" defect this milestone already corrected eight
instances of in `compose.yml`. It now selects four tests and skips them with a
reason naming which of the two prerequisites is missing.

Offline suite after Phase 12: **3490 passed, 9 skipped** (up 32), `ruff check`
and `ruff format --check` clean, 12/12 import contracts kept.

---

## Phase 13: Convergence

Appended 2026-09-09 by a second `/speckit-converge`, after Phase 12 closed. **One
finding, and it is CRITICAL.**

This pass did the thing the previous two deferred: it ran the `postgres`- and
`s3`-marked suites, which had never been executed. 71 of 73 passed, the two skips
were correct, the whole s3 suite passed, and all 23 of this milestone's new
Postgres tests passed. One test failed, and it is Milestone 9's.

- [X] T184 **CRITICAL** — Move the claim's eligibility predicate inside the scan that carries `FOR UPDATE ... SKIP LOCKED` in `src/docdoc/runs/postgres.py`, so that two concurrent workers cannot be handed the same run per FR-092, Milestone 9 FR-016, SC-006 (contradicts)

  **The evidence.**
  `tests/integration/test_run_queue_postgres.py::test_two_workers_racing_never_receive_the_same_run`
  fails **18 times in 20**. Eight workers against ten queued runs return as few as
  five distinct `run_id`s. A direct probe shows a row left at `attempts = 2`:
  two `UPDATE`s landed on one run, and two workers each hold what they believe is
  the lease.

  Sequential claims are correct — a second claim of the only queued run returns
  `None` — so the fault needs true concurrency, which is why every offline test
  and both prior convergence passes missed it.

  **The cause.** T094 moved the eligibility predicate into the `eligible` CTE:

      eligible AS (
          SELECT ... FROM runs LEFT JOIN served ...
           WHERE (runs.status = 'queued'
                  OR (runs.status = 'running' AND runs.lease_until < %(now)s))
             AND runs.attempts < %(max_attempts)s
      ),
      candidate AS (
          SELECT runs.run_id, runs.status AS from_state
            FROM runs JOIN eligible ON eligible.run_id = runs.run_id
           ORDER BY ...
             FOR UPDATE OF runs SKIP LOCKED
           LIMIT 1
      )

  `FOR UPDATE` makes Postgres re-evaluate a row after acquiring its lock, but it
  re-applies only the quals **of the scan that carries the clause**. Here that
  scan's only qual is the join on `run_id`. So when another transaction claims a
  run and commits, this statement locks the row, rechecks it, finds the id still
  matches, and claims it again — `status` and `lease_until` are never re-tested,
  because they live one CTE away.

  Milestone 9's statement had the predicate in the same scan as the lock, so the
  recheck rejected a concurrently-claimed row. That is the property that was lost.

  **plan.md reasoned this through and reached the wrong answer**, which is worth
  keeping rather than quietly correcting. Its "Re-check after Phase 1 design"
  section asks *"does the claim query's window function break Milestone 9's `SKIP
  LOCKED` guarantee?"* and answers: *"It does not, and the reason is where the
  clause sits. The CTE ranks and orders candidates; `FOR UPDATE SKIP LOCKED`
  still applies to the single row the outer statement selects, so two workers
  still cannot claim one run."* The clause does still apply. What moved is the
  **predicate it rechecks against**, and that is the half the argument did not
  consider.

  **What it costs.** One document parsed and sent to a model provider twice,
  concurrently, by two workers each believing it holds the lease — the failure
  the run queue exists to prevent. ADR-0013 §4's redelivery-safety argument
  covers *sequential* re-execution of a deterministic pipeline; it does not cover
  two workers running one document at the same time, and `_demand_the_result_is_
  retrievable` will not catch it either.

  **And it is flaky rather than deterministic.** It passed on one full-suite run
  and failed 18/20 in isolation, so in CI it reads as an occasionally-annoying
  test rather than as a correctness bug. Whatever fix lands should be verified by
  running that test in a loop, not once.

  **Suggested shape**, not prescribed: keep `served` and the ordering terms as
  CTEs, and put the `status` / `lease_until` / `attempts` predicate back on the
  locked scan itself — the ordering may be computed from a CTE, but eligibility
  has to be re-checkable at lock time. Whatever lands, `tests/unit/
  test_claim_order.py` must still pass unchanged: fairness, priority, starvation,
  and the collapse to `created_at` under Milestone 9's defaults are all still
  required (FR-088 to FR-091).

### What this pass checked

- 122 functional requirements, 26 success criteria, 8 user stories
- **The `postgres` (73) and `s3` (11) suites, executed for the first time** —
  everything except the one test above passed, including all 23 Postgres tests
  this milestone added
- 3490 offline tests, `ruff check`, `ruff format --check`, 12/12 import contracts
- 14 constitution gates — no violation

The lesson is the same one Phases 12 and 13 both taught, and it is worth stating
once more plainly: **every defect found in the last three passes came from
running something, not from reading it.** `priority` as a number, routing raising
a `500`, and now a claim race — three bugs, none visible in a diff, none visible
to a green offline suite, all found the moment code was actually exercised.

### T184, closed 2026-09-09

**Fixed by deleting the `eligible` CTE**, not by adding to it. Its two jobs were
separated by which one has to be fresh:

- **Eligibility** — `status`, `lease_until`, `attempts` — moved onto the `runs`
  scan that carries `FOR UPDATE OF runs SKIP LOCKED`, where the post-lock recheck
  can see it. This is the correctness half.
- **Ordering** — starvation, the per-tenant deficit from `served`, priority,
  creation time — stayed as it was, reading a snapshot. It is allowed to:
  preferring the wrong tenant for one claim is a fairness wobble, and claiming a
  run somebody else owns is a document parsed and billed twice.

The result is the shape Milestone 9 had — predicate and lock in one scan — with
the ordering terms added, and one fewer CTE than before.

**Measured, not argued.**

| | before | after |
|---|---|---|
| race test, isolated | **18 failures in 20** | **0 in 30** |
| 8 workers, 10 runs | 5–7 distinct claims | 8 distinct, every row `attempts = 1` |

`tests/unit/test_claim_order.py`, `test_claim_policy.py`,
`test_limits_and_fairness_postgres.py`, and `test_priority_does_not_starve.py`
all pass unchanged, so fairness (FR-090), priority (FR-088), the starvation bound
(FR-089), and the collapse to `created_at` under Milestone 9's defaults (FR-091)
are intact.

**The test was strengthened, because its weakness is why the bug survived.** A
single round caught the defect about 70% of the time — often enough to read as a
flaky test somebody reruns, rarely enough that a full-suite run had passed with
the bug present. `test_two_workers_racing_never_receive_the_same_run` now stages
five rounds and asserts `attempts` as well as the identities, which is the
evidence that distinguishes *this* failure from a missing `SKIP LOCKED`.

That change was verified the only way worth trusting: the defect was
**reintroduced deliberately** and the strengthened test caught it **10 times out
of 10**, then the fix was restored. A regression guard nobody has watched fail is
a guard nobody should rely on.

**Final state.** 3490 offline, 71 postgres, 9 s3 — all passing; `ruff check` and
`ruff format --check` clean; 12/12 import contracts kept.

---

## Phase 14: Convergence

Appended 2026-09-09 by a third `/speckit-converge`. Two findings, both `partial`,
neither a behaviour defect — the code does what the spec asks. What is wrong is
the **evidence**: one suite that can never run, and one guardrail that does not
check the property it claims to.

This pass installed the `otel` extra, stood up an OTLP sink on 4318, and ran the
`perf` measurements against a real database. Everything else passed: 3489
offline, 71 postgres, 9 s3, 3 perf.

- [X] T185 Add `otel` to `AMBIENT_MARKS` in `tests/conftest.py`, and verify the four `otel`-marked tests **actually execute** rather than reporting a skip per SC-014, FR-017, FR-021 (partial)

  **The tests cannot run, and the skip message is misleading.** With
  `opentelemetry` installed, a collector listening on `127.0.0.1:4318`, and
  `DOCDOC_TEST_OTLP_ENDPOINT` exported, `pytest -m otel` reports **4 skipped**
  with *"no DOCDOC_TEST_OTLP_ENDPOINT configured"* — a message that is true from
  inside the test and false from outside it.

  `_hermetic_environment` in `tests/conftest.py` deletes every `DOCDOC_*`
  variable before each test unless that test carries a mark in
  `AMBIENT_MARKS = ("provider", "postgres", "s3")`. `otel` is not in that tuple,
  so the scrub removes the endpoint a moment before `require_otlp_endpoint()`
  looks for it.

  **`conftest.py` already describes this failure, for the previous marker it
  happened to:** *"a test needing a database finds its DSN in
  `DOCDOC_TEST_DATABASE_URL`, which the scrub below would otherwise delete a
  moment before the test looked for it -- leaving every infrastructure test
  permanently skipped on a correctly configured machine, and silently so."*

  So SC-014 still has no executable evidence. T181 was written to fix a marker
  that marked nothing, and produced a marker that marks four tests which cannot
  run — which is the same defect one layer along, and it is mine.

  **Verify by running them, not by reading the diff.** The fix is one tuple entry;
  the check is that `pytest -m otel` against a collector reports 4 passed.

- [X] T186 Make `tests/perf/test_claim_latency.py` assert the property its docstring claims — or correct the docstring to the cost research R10 actually accepted per T104a, plan.md performance goals, R10 (partial)

  The docstring says: *"What it is really watching for is the shape: claim
  latency must be flat in queue depth."* The assertion is `elapsed <
  BUDGET_SECONDS` at each of three depths, independently — a ceiling, not a
  shape. Nothing compares the depths to each other.

  Measured on this pass, against a real database:

  | queue depth | claim latency |
  |---|---|
  | 100 | 37 ms |
  | 1 000 | 37 ms |
  | 10 000 | 57 ms |
  | 50 000 | 107 ms |

  It is **not flat**, and it is not supposed to be: R10 accepted that the
  ordering sorts the eligible set, and plan.md records the cost as *"bounded by
  queue depth rather than table size, and measured rather than assumed"*. The
  plan confirms it — `Sort Method: external merge Disk: ~3.7 MB` at 50 000. The
  behaviour is right; the docstring overreaches.

  What that costs is real: a regression to *quadratic* would still be under one
  second at the test's maximum depth of 10 000 and would fall over in production.
  The guardrail is weaker than it says, on a query this milestone rewrote twice.

  Either assert a growth bound across depths — e.g. that 10× the depth costs
  well under 10× the time — or say plainly that this checks a ceiling and that
  the shape is R10's accepted `O(eligible)` sort. Both are defensible; claiming
  the first and doing the second is not.

### What this pass also confirmed

**Last pass's claim-query fix is not a perf regression.** The two plans, compared
at 50 000 queued runs on the same database:

| | execution time | sort |
|---|---|---|
| before (predicate in a CTE) | 115 ms | external merge, 3 976 kB |
| after (predicate on the locked scan) | **85 ms** | external merge, 3 688 kB |

Removing the `eligible` CTE made the claim both correct and slightly cheaper.

**Two things went wrong during this pass and are recorded because they cost
time.** `uv sync --extra otel --extra api --extra postgres --extra s3` dropped
the `dev` group and removed `pytest` and `ruff`; `uv sync --all-extras` restored
them, and that is the invocation the quickstart already gives. And with the extra
now installed locally the offline suite reads **3489 passed, 10 skipped** rather
than 3490/9 — `test_a_configured_endpoint_without_the_extra_is_reported_and_
fatal_to_nothing` skips itself, correctly, because it cannot be observed on a
machine that has the extra. That is the test working, not a regression.

### What Phase 14 found

**T185's verification uncovered a second bug, and it is the larger of the two.**

The task was one tuple entry: add `otel` to `AMBIENT_MARKS` so the scrub stops
deleting `DOCDOC_TEST_OTLP_ENDPOINT` before the test reads it. That worked — the
four tests went from `4 skipped` to `4 passed`.

Then the collector was checked, and it had received **nothing**. Not late,
nothing: zero batches after waiting past the batch timer.

`bridge()` built a `TracerProvider`, attached an `OTLPSpanExporter` to it, and
then took its tracer from `trace.get_tracer("docdoc")` — which reads the
**global** provider. This function never set one. So every span went to the
default no-op tracer and the configured provider, exporter attached, was
discarded on the next line.

**docdoc had exported zero spans since telemetry landed.** The whole of User
Story 6 was inert. Four `otel` tests passed throughout, because every one of
them asserts that emitting does not raise — and emitting into a no-op tracer
does not raise. `test_telemetry_leaks_nothing.py` and
`test_telemetry_collector_down.py` could not catch it either: both substitute a
tracer, so neither exercises the line that picks the wrong one.

The fix is `provider.get_tracer("docdoc")`. Deliberately **not**
`trace.set_tracer_provider(provider)`, which would also work: that call is
process-global, takes effect once, and would silently displace whatever provider
the deployment had configured — precisely the displacement this module refuses
to perform on the observer slot, refused for the same reason (research R4). A
new test asserts the global provider is untouched.

**And the guard that would have caught it now exists.**
`tests/support/otlp_collector.py` is a stdlib OTLP/HTTP sink on an ephemeral
port, mirroring `tests/support/webhook_receiver.py` — which exists because
"mocking the transport would test nothing about the destination policy". The
same argument applied here and had not been made. Two new tests point the bridge
at a collector they can read and assert a batch **arrived**; they need no
`DOCDOC_TEST_OTLP_ENDPOINT` at all, because they bring their own.

`bridge()` gained `force_flush` on the returned callable so a test can make the
batch leave without waiting on the SDK's timer. It is also the honest thing for
a worker shutting down between batch intervals, which otherwise drops its last
spans; nothing in docdoc calls it yet, and that is a smaller gap than the one it
closes.

**T186 corrected a docstring that overreached and a ceiling that under-defended.**

The prose claimed claim latency "must be flat in queue depth". It is not, it is
not meant to be, and the test never checked it — R10 accepted an `O(eligible)`
sort and `EXPLAIN` confirms an `external merge` at depth. Measured: ~40 ms at
100 and 1 000, ~50 ms at 10 000, ~107 ms at 50 000.

Worse, the ceiling was not catching what the prose implied. At 10 000 a round
trip (~35 ms) dominates and the sort is milliseconds, so a sort gone *quadratic*
projects to ~0.2 s — comfortably inside the 1 s budget, and green. At 50 000 the
same regression projects to ~3.6 s and fails. So `DEPTHS` gained a fourth entry,
and the docstring now says what the design actually provides and why the largest
depth is the load-bearing one.

**Final state.** 3489 offline, 72 postgres (the perf suite gained a case), 2
otel passing and 4 skipping for want of an operator's collector, 9 s3;
`ruff check` and `ruff format --check` clean; 12/12 import contracts kept.

---

## Running quickstart scenarios 5 and 6, 2026-09-09

The two scenarios T176 left unrun, executed against the four-container
composition. Both work now; neither worked when it started, and getting there
found **four defects** — one of them the reason scenario 6 exists.

### 1. docdoc's structured events reached nothing

`run.transition` — **0**. `credential.operation` — **0**. The telemetry startup
line — absent. A full run had been submitted, claimed, and completed, and two
credentials issued, and the container's stdout carried nothing but uvicorn's
access log.

`docdoc.*` loggers emit at INFO. In the shipped composition they had no handler
and inherited `WARNING` from a root logger that had none either, so every record
was created, formatted, and dropped. The unit tests never saw it because
`caplog` attaches its own handler — precisely what production lacked.

This made five requirements unobservable in the deployed artifact: FR-035's audit
event, FR-052's limit refusal, FR-012's deletion counts, Milestone 9's
`run.transition`, and the bridge's own install outcome. `docs/concepts/runs.md`
devotes a section to reading these, and quickstart scenario 6 tells the operator
to check a startup line that was never printed.

`docdoc.telemetry.configure_logging()` now emits one JSON object per record and
is called by both front ends. It defers to whatever exists: a `docdoc` logger
with handlers is the deployment's, a root logger with handlers means the
deployment configured logging centrally and **nothing is touched at all**, and
only when neither is true does it configure anything.

That last rule was learned by breaking it. The first version lifted the level in
the middle case too, which mutates a process-global logger that outlives the
call — under `pytest`, where root always has a handler, one test that built an
app raised `docdoc` to INFO for every test after it and
`test_a_refused_run_also_emits_exactly_one_event` began seeing three. Order-
dependent, green in isolation. The fix is that the middle case changes nothing.

### 2. `host.docker.internal` does not resolve on Linux

Scenarios 4 and 6 tell the reader to point a webhook and an exporter at that
name. Docker Desktop provides it; plain Docker on Linux does not, and this
repository's CI and most contributors are Linux. Verified inside the container:
`Name or service not known`. Both services now carry
`extra_hosts: ["host.docker.internal:host-gateway"]`, and the same command was
re-run and resolved.

### 3. There was no way to get an operator's file into a container

Three scenarios need one — a key file, webhook signing secrets, a routing
policy — and the composition mounted only `schemas/` and the echo fixtures. The
instruction I had written was `docker cp` followed by `docker compose up -d`,
which **cannot work**: changing an environment variable recreates the container
and takes the copied file with it. The API then refuses to start on an
unreadable key file, correctly, which is how this was found.

`packaging/docker/secrets/` is now mounted read-only at `/secrets`, gitignored,
with a README covering the three files.

### 4. An example test claimed an independence it did not have

`test_the_asynchronous_example_runs_with_nothing_listening` says it runs "under a
URL that is guaranteed to refuse, so the assertion does not depend on whether the
developer happens to have something on port 8000". Nothing set
`DOCDOC_EXAMPLE_URL`, so it used the example's default of `localhost:8000` — and
with the composition up and authentication on it failed with
`AuthenticationError`, which is not "nothing listening". It now points at port 9,
`discard`, and passes with the composition running.

### What the scenarios showed once they ran

**Scenario 5.** The routing block, live — the path that raised a `500` before
Phase 12: `{"outcome":"review","policy_version":"default@1","reasons":[…]}`, two
reasons naming field, signal, and observation as strings. `priority` reads
`"ordinary"`. A correction recorded `201`, and the result hashed **byte-identical
before and after** (SC-018), readable back under its own tenant.

**Scenario 6.** `telemetry.installed` from both api and worker; three
`run.transition` events across the lifecycle, correlated by `run_id`; and **three
batches of spans delivered to a collector** — the assertion that had been
impossible while the bridge exported nothing.

**Final state.** 3489 offline, 72 postgres, 11 s3, 2 otel passing and 4 skipping
for want of an operator's collector; `ruff check`, `ruff format --check`, and
12/12 import contracts clean.

### Scenarios 3 and 4, run 2026-09-09

The last two that had never been executed. Both pass; one defect found, in an
example I wrote.

**Scenario 3 — fairness (SC-010).** Two hundred runs queued for tenant `bulk`
with the worker stopped, then a single run for `acme` submitted **last**. On
restart, `acme`'s run was claimed **second**, out of a 201-deep queue. The bound
is the number of tenants with work, not the depth of anyone's backlog — which is
FR-090 measured rather than argued. The claim order read `b a`.

**Scenario 4 — delivery (SC-011, SC-012, FR-057, FR-063, FR-065).**

*Delivered*: `state: delivered`, `attempts: 1`, `last_status: 200`, and the
example receiver verified the signature **with its own implementation of the
documented rule** and printed the run and its state.

*Broken receiver*: the run reached `succeeded` and stayed there while nobody
could be told. Attempts climbed 2 → 3 → 3 → 4 across the backoff, came to rest
at `state: failed, attempts: 6`, and `last_error` read `ConnectionRefusedError`
— a class name, never a response body. The result stayed retrievable at
`GET /v1/jobs/{id}/result` → `200`, which is FR-065: polling remains the record
and a lost delivery loses no information.

**The defect: `examples/receive_webhook.py` printed nothing when backgrounded.**
`quickstart.md` says to run it as `… &`, and stdout redirected to a file or a
pipe is block-buffered — so a program whose entire purpose is showing you what
arrived showed nothing at all, not even its startup line, until it exited. It
was killed with `SIGTERM`, which it does not catch, so the buffer was discarded
and the output never appeared. The delivery's `200` proved the handler had run
and the signature had verified; the operator-visible half the quickstart promises
had not. Fixed with `flush=True`, and re-verified by running it again and
watching the line appear.

**Every quickstart scenario has now been executed end to end.** Nine of nine.
