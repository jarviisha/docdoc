# ADR-0026: Tenant Quality Evaluation

- **Status**: Accepted
- **Date**: 2026-10-23
- **Amends**: [ADR-0023](0023-console-login-and-cross-tenant-operations.md) §4 (page images are
  served for succeeded extraction runs as well as OCR runs)
- **Relates to**: [ADR-0008](0008-schema-evolution-policy.md) (schema versions),
  [ADR-0014](0014-tenant-scoping-and-store-namespacing.md) (tenant scoping),
  [ADR-0015](0015-deletion-over-a-content-addressed-store.md) (erasure),
  [ADR-0019](0019-operations-console-boundary.md) §2 (the five nouns, which still hold word for
  word)
- **Design**: `docs/superpowers/specs/2026-10-22-tenant-quality-evaluation-design.md`
- **Principles engaged**: IX (evaluation and human correction are product features; the review
  platform fence), XI (MVP discipline)
- **Superseded usage**: the `docdoc eval` command named below was removed in [ADR-0027](0027-service-only-surface.md); `datasets/mvp` stays, scored from tests.

## Context

The only quality measure docdoc had was `docdoc eval` over a committed golden set of four synthetic
invoices. It says nothing about a tenant's own documents, or whether a change (another model, a
prompt edit, a parser upgrade, a new schema version) helped. Every tenant needs to ask "does docdoc
get our fields right, and did my last change make it better?" without preparing dataset files.

## Decision

A person looks at an extraction run's fields beside the page and marks each field they choose
**correct**, or **wrong** with the right value (or "not present"). Those checks are the tenant's
**answers**. An **evaluation** re-runs every checked document with the deployment's current
configuration, scores the results against the answers with `docdoc.evaluation` (unchanged), and
stores a **report** recording the configuration used. Tenants use Bearer routes (`/v1/answers`,
`/v1/evaluations`); the operator uses the same capabilities cross-tenant under `/v1/console`, and a
check made there counts as the tenant's, recorded with the operator as who checked.

### 1. Checking and Quality are a correction surface, not a review platform

ADR-0019 §2 holds word for word. Each noun is avoided like this:

- **Reviewer assignment.** No run, document, or field belongs to a person. The person chooses which
  run to open; nothing is given to them.
- **A review queue.** There is no list of documents waiting to be checked, and no ordering of work.
  The Quality area lists reports, not pending items.
- **Workload.** Nothing counts checks per person, and nothing balances anything. Counts are of
  documents and fields in an answer set, per tenant and schema.
- **A review state.** Runs gain no checked/unchecked status; the run status set stays the closed
  five. A field is checked or not only in the sense that an answer exists for it.
- **A disposition.** Correct and wrong are statements about a *value* (a correction, Principle IX
  and ADR-0017's family of facts), not outcomes of a review. Nothing is approved, rejected, or
  escalated; no route or identifier uses those words, and the UI says "Correct" and "Wrong".

A changed mind is a new check, never an edit or delete. The contract test that bans PATCH and the
review words gains an explicit reviewed list of the new routes, not a pattern.

### 2. Answers never expire

A check is stored as an answer that lives until the same field is checked again (the newest check on
any run of that document wins; one answer per tenant, schema name, document and field path), or the
document or tenant is erased. A run that carries a live answer is pinned against the retention
sweep for as long as the answer lives. `DOCDOC_CORRECTION_RETENTION_DAYS` is **retired**: an expiry
would have made report history break on its own. Pre-1.0 (ADR-0011): no migration.

### 3. Erasure and reports

A report describes a computation that happened, so it outlives the documents it scored.

- Erasing a **document** deletes its checks, removes it from every stored answer set and report
  (its outcomes, values and locations), and keeps the report's aggregates and `report_id`. The
  evaluation counts it in `erased_documents`, and the drill-down shows it as erased. Concurrent
  erases of one evaluation both stick.
- Erasing a **tenant** deletes its checks and evaluations.
- Values a tenant sees are joined at read time from the stored answers and each re-run's own
  artifacts. Tenant documents are `Tier.RESTRICTED`, so the stored report carries hashes, not values.

### 4. Schema versions: restamping

The library refuses to score, or compare reports, across a schema identity or hash change
(ADR-0008). It is left alone. Before each evaluation the answer set is **restamped** to the newest
registered version of the schema name: every document gets that version's identity and hash, and
labels for field paths that no longer exist are dropped from the stamped copy, not from storage.
New fields count as unchecked until someone checks them. The stamped set has its own
`golden_set_id`, so reports across a schema change are visibly not comparable, and the console marks
the change ("schema changed: invoice@1 → invoice@2") without calling `compare()` across it.

### 5. Cost and concurrency

An evaluation runs through the ordinary run queue and reuses unchanged stages from the
content-addressed store, so an unchanged configuration costs nothing, and the console states the
document count before starting. It is refused with `409 no_answers` when there are no answers, `409
evaluation_in_progress` while another for the same tenant and schema is unfinished, and `429` when
the tenant's run limits are reached. A document whose run fails counts as failed in the report.

An evaluation that cannot be scored at all (its schema version removed or edited between start and
settle, or `evaluate` refusing the pair) is **failed**: it records the error class, never a message,
once with its settle time, and stops holding its tenant and schema, so the next start is not refused.
Its status reads `failed` and the console marks it "Could not be scored". A console start is logged
with the operator, tenant, schema and outcome.

### 7. Typed values, reasons, and provenance

`comparators@1` gates on type, so a check typed as text could never match a typed extraction. A
corrected value is typed with the run's schema when it is recorded (text parsed for number, integer
and boolean, then the library's `carry`), and refused with `422 invalid_value` naming only the field
path when the declared type cannot carry it. Before scoring, label values and extracted values are
typed by the schema as the dataset loaders type them (`schema_facts` + `carry`); the library is not
changed. A wrong check needs a reason, as the library's `Correction` requires; the console says so
before saving. Each report summary carries a trimmed `provenance` (model and parser ids and
versions, schema hashes, prompt hashes) so a report says which configuration produced it.

### 6. Amendment to ADR-0023 §4

ADR-0023 §4 scoped the page-image route to OCR runs. Checking an extracted value needs the page it
was read from, so the route now also serves **succeeded extraction runs**. It is still console-only,
session-authenticated, and tenant-scoped in the query; it renders a page the deployment already
stored and is not a new place a result is kept.

## Consequences

- The constitution's console paragraph is widened to permit checking, starting an evaluation (a
  paid action), and showing evaluation reports, and to say page images are served for succeeded
  extraction runs (v1.13.0).
- A new `evaluations` table (migration `0012`, with `failure` added by `0013`) and answer columns
  (migration `0011`); both are purged by the tenant and document erasure paths.
- The `docdoc eval` CLI and `datasets/mvp` are untouched; retiring them is a later project.

## Alternatives considered

- **Answers that expire with correction retention.** Rejected: reports would lose their basis.
- **Rewriting stored reports on erasure.** Rejected: aggregates describe a computation that
  happened; only per-document detail is removed.
- **Teaching the library to score across schema versions.** Rejected: ADR-0008's refusal is the
  point; restamping adapts the input instead.
- **A "documents to check" list.** Rejected: it is a review queue by another name.
