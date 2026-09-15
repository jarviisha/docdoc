# Specification Quality Checklist: The Operations Console

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-09-11
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

Four items were checked with a qualification, recorded here rather than smoothed over, because each
is the kind of thing `/speckit-analyze` has caught in this repository before.

**"No implementation details" — several requirements name browser mechanisms deliberately.** FR-007
names `localStorage`, `sessionStorage`, IndexedDB, and the Cache API; FR-008 names referrer headers
and query strings. These are *prohibitions*, and a prohibition stated abstractly ("the credential
must not be persisted") is one no check can enforce and one a future author can satisfy in their own
opinion. Naming the mechanisms is what makes SC-004 a measurement instead of an intention, and it
follows the precedent `specs/008` FR-032 set and `specs/010`'s checklist recorded for its own FR-060.

**"No implementation details" — the spec names existing routes and ADR sections throughout.** This is
the house convention rather than a lapse: Milestone 11 adds no capability, so almost every
requirement is a statement about *which already-shipped contract the console must use rather than
duplicate*. FR-027 and FR-029 are only meaningful as references, because their whole content is
"reuse this, do not build a second one". A version of them that avoided naming the routes would
permit the exact outcome they exist to forbid.

**"Requirements are testable" — FR-015's page size and FR-001's path are not fixed here.** Both are
testable as written (a documented default and a documented maximum must exist and be observed; the
path must differ from the viewer's), and both are choices for `/speckit-plan`. Fixing a number here
would choose a mechanism by choosing a number, which is the objection `specs/010`'s checklist raised
about its own propagation bound.

**"Scope is clearly bounded", and this is the milestone where that item does the most work.** The
scope is defined by a constitutional line that was drawn eight days before the spec was written, so
the boundary is stated three times and in three forms: as a principle (who the surface is about), as
five nouns a build check greps for, and as thirteen Out of Scope items. SC-006 is what makes the
fence a measurement. A reviewer should read "bounded" here as *enforced*, not merely *described* —
that difference is the whole subject of ADR-0019 §2.

Nine scope-defining questions are answered under Clarifications → Session 2026-09-11, each with its
cost stated. Four were settled while the spec was drafted: the credential mechanism (pasted, held in
memory, no login route — the reload cost is accepted, not deferred), corrections (no face in this
milestone, and ADR-0019 §6 leaves which milestone builds one open), the cross-tenant view (not built,
because "one new read" is the claim this milestone is measured on), and where the console lives
relative to the viewer (sibling sources, separate entry, viewer guard re-pointed rather than
relaxed).

Five more were asked and answered in a `/speckit-clarify` session on the same day, and three of them
changed the spec rather than confirming it:

- **The run detail shows no part of a result** (FR-022a). This resolved a real tension between the
  original FR-022 and FR-037, and it is why SC-016 is now one check with no exception list.
- **The link does not reach the viewer** (FR-022b). Asked because FR-022a said it did; the viewer
  holds a two-path allow-list and no route serves a stored document's bytes, so the link had nothing
  on the other side of it. The cost of building one is now recorded in Out of Scope instead of being
  discovered during implementation.
- **The credential is discarded after a bounded idle** (FR-009a, SC-003a). The one place this
  milestone chose the larger mechanism, and the reason is in the clarification: the console erases
  tenants, and the identifier FR-030 asks an operator to re-type is on screen for anyone to copy.
- **Accessibility mirrors `specs/008` FR-059** (FR-045a–c, SC-014a) — labelled, keyboard-operable,
  no conformance level claimed. A gap in the first draft, not a decision it had made.
- **Revoking the key in use is allowed and not identified as such** (FR-026a). Identifying it needs a
  route returning the caller's credential identity, which is the second new read SC-003 forbids.

One requirement narrows a shipped milestone's guarantee rather than preserving it verbatim, and it is
recorded in Dependencies rather than applied quietly: **`specs/008` FR-029's read-only check narrows
from this repository's UI to the viewer's own sources** (FR-040, ADR-0019 §5). That is the claim
Milestone 8 actually made; what changes is that the tree now contains something else, so inheriting
the check by directory would have silently widened it into a falsehood.
