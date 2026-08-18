# Specification Quality Checklist: Deterministic Grounding

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-18
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

- Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`.

### Validation record (2026-08-18)

Two issues were found on the first pass and fixed before the checklist was marked complete:

1. **Named dependency in the requirements.** The first draft named the approximate-matching library
   and the specific similarity measure inside the functional requirements. Both are implementation
   choices that ADR-0005 already fixes; the requirements now say "a pinned similarity measure" and the
   concrete choice is carried in the Dependencies section as a constraint inherited from the ADR, where
   `/speckit-plan` will bind it.
2. **An unqualified round-trip invariant.** ADR-0006 states the offset-map round trip as an identity.
   Taken literally that is unsatisfiable for a range whose boundary falls inside a character the
   comparison form deletes, such as a soft hyphen. FR-017 and SC-006 now state the identity for ranges
   whose boundaries survive and require a containing range otherwise — never a narrower or moved one.
   This strengthens the testable invariant rather than weakening it, and should be confirmed in review
   as the intended reading of the ADR.

### Carried forward for `/speckit-plan` — resolved 2026-08-18

- **The candidate-window slack.** Marked open because no value is fixed in the constitution or the ADRs.
  Resolved by [research.md R4](../research.md): it is not a free parameter. `k = floor((1 - t) · m / t)`
  falls out of the scorer's own definition and was verified exactly for claim lengths 1–59. A larger
  slack generates only provably-below-threshold candidates; a smaller one breaks the candidate filter's
  completeness proof. Getting it wrong is not cosmetic — the independent-constant version measured
  1373 ms against the derived version's 53 ms on one value.
- **The alternatives limit.** Bound to five, per ADR-0005 (data-model GRD-12).
- **In-process derivation of the comparison form.** Confirmed as planned (research.md R9), matching
  Milestone 3's deferral of the artifact store.

### Raised by the plan, for review

- [research.md R1](../research.md) departs from ADR-0005's literal `extraction/grounding.py` placement,
  putting grounding in its own package and its own `import-linter` layer. The ADR's binding decision, per
  its title, is that fuzzy matching lives *outside the kernel*; the module path is the illustration. If
  accepted, ADR-0005 should carry a clarifying amendment.
- [research.md R6](../research.md) records dash folding as a measured gap that this milestone
  deliberately does not close, because ADR-0006 pins the `v1` transformation list.
