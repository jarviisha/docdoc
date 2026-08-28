# Specification Quality Checklist: Asynchronous Runs, Shared Storage, and Tenant Scoping

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-28
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

### Iteration 1 — 2026-08-28

Two items failed and were fixed before this record was written; both are recorded because the
correction is the useful part.

1. **"No implementation details" initially failed.** The first draft named PostgreSQL, S3,
   `SELECT ... FOR UPDATE SKIP LOCKED`, Docker, and MinIO throughout the Requirements section. Those
   are real decisions and they belong in the spec — but as *rationale in the argued prose sections*,
   not inside a functional requirement. FR-016 now says two workers must never execute the same run
   concurrently, which is testable against any mechanism; the choice of mechanism is argued under "Why
   this needs no constitutional amendment" and will be fixed by `/speckit-plan` and ADR-0013. FR-026
   ("no broker, coordinator, or scheduler process") is the one place a technology constraint survives,
   stated as a prohibition rather than a selection, because it is a constitutional boundary the spec
   must carry.

2. **"Success criteria are technology-agnostic" initially failed on two criteria.** An earlier SC-002
   read "run submission returns before the pipeline executes", which is a statement about internals
   and unmeasurable from outside; it is now a latency percentile measured with the worker pool
   stopped. An earlier SC-005 counted "S3 GET requests"; it now counts parser invocations, which is
   the fact anyone actually cares about and is true under any store.

### Iteration 2 — 2026-08-28

All three [NEEDS CLARIFICATION] markers resolved by the user, recorded in the spec's Clarifications
session. Each answer widened the spec rather than merely filling a blank, so the ripple is recorded
here:

- **Q1 → per-tenant namespacing.** Added FR-084 (namespaced stores), FR-085 (namespacing changes
  location, never identity — two tenants still derive the same `processing_id` independently), FR-086
  (reuse within a tenant only, unobservable across them). Added **SC-017**, which is the criterion the
  answer exists for: SC-008 makes cross-tenant *responses* identical, and SC-017 makes their *cost and
  timing* identical, because an existence oracle leaks through an invoice that no status code
  controls. Qualified SC-005 and User Story 3's first scenario with "same tenant" — without that word
  they asserted reuse the design now forbids.

- **Q2 → authentication off by default, one implicit tenant.** Added FR-088 (default-off behaves
  exactly as Milestone 8) and FR-089 (explicit, idempotent migration assigns pre-existing content to a
  configured tenant; no inference, nothing left unreachable). Amended FR-059 from unconditional to
  conditional, and FR-081 so the README states the honest version — authentication exists, it is off
  by default, and a deployment that has not enabled it is exactly as exposed as before. Added
  **SC-018** for upgrade compatibility.

- **Q3 → strict readiness.** Added FR-087, which carries the uncomfortable half explicitly: a node
  with no database withdraws working synchronous capacity, on purpose, and the operator documentation
  must say so. Route-scoped readiness moved to Out of Scope rather than left unmentioned, because a
  reader who wants it should find a decision rather than a silence.

### Iteration 3 — 2026-08-28 (`/speckit-clarify`, run after plan and tasks)

Four questions asked and answered. All sixteen items still pass; nothing regressed. Re-evaluated
because clarification ran **out of order** — after `/speckit-plan` and `/speckit-tasks` rather than
before — so every answer had to be propagated into plan, research, contracts, data model, and task
list rather than into the spec alone.

- **Q1 resolved a contradiction, not an ambiguity.** FR-084, R12, and T055 said tenant namespacing was
  unconditional while SC-018 required pre-existing content to stay readable with authentication at its
  default — and existing content carries no prefix. Implementing either one as written would have made
  the other false. FR-084a now fixes the default tenant's namespace as the store root. `/speckit-analyze`
  had not caught this.
- **Q2** fixed the worker concurrency model at one run per process (FR-025 rewritten, R9a added).
- **Q3** gave the withdrawn-schema case a defined outcome (FR-091 added, FR-020 and FR-038 amended).
- **Q4** put a `run.transition` event in scope (FR-092, FR-093, R10a added).

### Outstanding

None. All sixteen items pass.

**Two acknowledged exceptions to "no implementation details", both deliberate and both prohibitions
rather than selections.** FR-026 forbids a broker, coordinator, or scheduler; FR-025 forbids executing
runs in threads, subprocesses, or an event loop. Each names a mechanism because each carries a
correctness or constitutional constraint that a mechanism-neutral phrasing would lose — a threaded
worker can starve a heartbeat into losing a live lease, and the deferred-technology list is
constitutional. Recorded here so a future reader finds a decision rather than a lapse. `/speckit-clarify` is not required — the full clarification surface was
resolved across the two 2026-08-28 sessions recorded in the spec.

One item is worth carrying into `/speckit-plan` as a note rather than a defect: **FR-026 is the only
functional requirement that names technology**, and it does so as a prohibition ("no broker,
coordinator, or scheduler process") rather than a selection. That is deliberate — it is a
constitutional boundary from the deferred-technology list, and a plan that satisfies every other
requirement while introducing a broker would satisfy the spec's letter and break the project's rules.
