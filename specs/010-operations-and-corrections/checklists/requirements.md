# Specification Quality Checklist: Retention, Credentials, Limits, Delivery, and the Human Loop

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-09-04
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

Three items were checked with a qualification, and the qualification is recorded rather than
smoothed over, because each is the kind of thing `/speckit-analyze` has caught in this repository
before.

**"No implementation details" — three requirements name technology deliberately.** FR-017 and FR-019
name OTLP, FR-060 and FR-061 name loopback ranges and redirects, and FR-098 names the existing
`forbidden` import contract. Each is a *prohibition or an interface the constitution already fixes*,
not a design choice this spec is making: OTLP is what "OpenTelemetry where practical" resolves to,
the destination policy is a security boundary that is meaningless stated abstractly, and a
prohibition with no automated guard is a comment. This follows the precedent Milestone 9 set with its
own FR-026, which said so in its own text.

**"Requirements are testable" — FR-028's propagation bound is a number the spec does not fix.** It is
testable as written (the bound must be configurable, documented as a number, and observed), and the
value belongs in `/speckit-plan` alongside the mechanism that achieves it. Fixing it here would
choose the mechanism by choosing the number.

**Scope is bounded, and the boundary is wide.** This milestone carries seven deferred items, two of
which (routing, corrections) are product features rather than operational ones. The spec states this
as its own risk in "One milestone, two halves", answers Milestone 9's stated objection to mixing
them, restates the objection's criterion as SC-001, and records the 10a/10b split as recommended and
not taken with the reason. A reviewer who takes the split can do it along the Requirements section
boundaries, which are drawn to permit it. Flagged here because "wide but bounded" and "unbounded" are
easy to confuse in a checklist tick.

Eight scope-defining questions are answered under Clarifications → Session 2026-09-04, each recorded
with its cost. Three were answered by informed decision while the spec was drafted — erasure depth
(full cascade within the tenant), what a limit counts and when it refuses (three counters at
submission, token budget one submission late), and where corrections live (a table plus tenant-scoped
routes, viewer unchanged). Five were asked and answered in a `/speckit-clarify` session on the same
day: where the new mutable state lives (tables in the existing run database, which ends Milestone 9's
"the database is a dependency of asynchrony only"), what a removed run's status says (nothing —
`RunStatus` stays closed at five and a tombstone is a separate thing), who executes unrequested work
(the worker, in a bounded maintenance tick; credentials by cache lifetime), who sets priority (the
client, under a per-tenant ceiling), and the routing outcome set (`automatic` and `review`, and no
third).

Two of those five superseded or amended a shipped milestone's requirement, and both are recorded in
Dependencies rather than applied quietly: `specs/009` FR-006's five-state closed set is **preserved**
by FR-004a, and FR-024's claim ordering is **amended** by FR-091 with its default intact.
