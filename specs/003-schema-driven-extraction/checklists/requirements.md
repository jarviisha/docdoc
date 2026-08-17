# Specification Quality Checklist: Schema-Driven Extraction

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-17
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
- No provider and no model is named anywhere in the spec. The constitution's MVP stack fixes that
  there is exactly *one* LLM adapter; binding it to a concrete provider belongs to `/speckit-plan`,
  not here.
- "Provider SDK" and "HTTP interface" appear as category names rather than named technologies,
  matching the usage already established in the Milestone 2 spec.
- The three highest-risk assumptions were resolved by `/speckit-clarify` on 2026-08-17, together with
  two gaps the ambiguity scan surfaced. All five are recorded under `## Clarifications` in the spec:
  1. **One window per extraction** — confirmed, with the kernel's existing `slice` named as the
     documented escape hatch (FR-046). Windowing stays deferred and is not foreclosed: Milestone 1
     already built `slice`, `merge`, and the `origin` ranges that survive both, for exactly this.
  2. **No grounding tier resolved here** — confirmed and given a structural justification rather than
     an effort-based one (FR-047): ADR-0003 makes grounding its own stage with its own artifact, so no
     grounding input may enter the extract stage's options hash. The cost accepted is that every value
     is still ungrounded when this milestone ships.
  3. **Repeating groups in scope, bounded to one level** (FR-048), rejected at registration when
     exceeded. Keeps `invoice@1` expressible while bounding the recursion that would otherwise run
     through schema, response shape, conformance check, and result type at once.
  4. **Schemas are declarative data files** (FR-049, FR-050), hashed with the canonical serialization
     ADR-0002 already defines rather than a second convention invented for schemas.
  5. **A performance criterion over the deterministic work only** (SC-021), measured with the model
     call excluded.
- One judgment call a reviewer may want to revisit: the spec now names `slice`, `merge`, `find`, and
  `origin` — docdoc's own public kernel surface, shipped in Milestone 1. These are read as domain
  vocabulary for this project's consumers rather than as implementation details, on the same footing
  as the Milestone 2 spec referencing the kernel's construction rules. No third-party framework, no
  provider, and no model is named anywhere.
- FR-006 and the Out of Scope section together carry Principle VII's separation: field constraints are
  *declared* in the schema and hashed into its identity per ADR-0008, but *enforced* by Milestone 5.
  Extraction checks shape and type parseability only. This split is the item most likely to be
  misread during planning, because it makes a schema constraint something this feature stores and
  deliberately does not act on.
