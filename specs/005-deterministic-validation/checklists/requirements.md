# Specification Quality Checklist: Deterministic Validation

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
- "Non-technical stakeholders" is read as it was for Milestones 3 and 4: the consumer of this stage is
  a developer building on docdoc, and the spec states that audience explicitly at the top of the User
  Scenarios section rather than pretending to a wider one.

### Validation record (2026-08-18)

No `[NEEDS CLARIFICATION]` markers were left. Every open choice the source material did not fix was
decided from the constitution and the ADRs and recorded in Assumptions, following the pattern of
Milestones 3 and 4. The three decisions most worth challenging in review:

1. **Rules are schema data evaluated by one generic engine, not user-supplied code.** Forced by
   Principle VI (no document-type code paths) plus Principle VII (no prompt-expressed rules). If review
   wants a code-level rule plug-in in the MVP, that is a scope change, not a detail.
2. **The rule vocabulary is four closed kinds.** Deliberately smaller than an expression language.
   Widening it is a versioned act; the spec is written so that widening later costs a version bump and
   nothing else.
3. **Three verdicts rather than two.** `incomplete` exists so that "nothing failed" and "nothing ran"
   cannot share a word. A two-state verdict was rejected because it makes a vacuous pass indistinguishable
   from a real one, which is the failure mode this stage exists to prevent.

### Carried forward for `/speckit-plan` — resolved 2026-08-18

- **The bound on pattern evaluation (FR-024).** Resolved by [research.md R2](../research.md), and it was
  not a style choice: CPython's `re` takes **1,183 ms** on `^(a+)+$` against 24 characters and doubles per
  character, so a length precondition cannot rescue it, and a timeout would make the verdict depend on
  machine speed. Decision: `pattern_dialect@1`, docdoc's own documented linear-time subset — **8.84 µs**
  on a typical value, **17.2 ms** on the 10,000-character adversarial case. `google-re2` was measured
  (6.08 µs / 499 µs) and declined: it is a native extension with no musl wheels, and its dialect version
  would have to enter `options_hash`, moving the meaning of a pattern outside docdoc. Recorded in the
  plan's Complexity Tracking, because writing a regex engine is exactly what Principle XI warns against.
- **`schema_hash` stability when rules are introduced (FR-053, SC-019).** Achievable, verified by reading
  `extraction/identity.py`: `schema_hash_for` hashes an explicitly built payload, not a `model_dump`, so a
  `"rules"` key is added only when a schema declares one. The alarm already exists —
  `tests/unit/test_schema_snapshot.py` — and SC-019 means it must keep passing **unedited**.
- **SC-020's 50 ms bound.** Now derived rather than asserted ([research.md R8](../research.md)): ≈ 8 ms
  from measured unit costs, about 6× headroom. One row of that derivation — pydantic model construction
  for the check records — is estimated, not measured, and the plan says so and gives the `perf` tier the
  job of confirming it.

### Raised for review — resolved 2026-08-18

- **FR-018 may be redundant with FR-002.** Kept, as a refusal, per [research.md R11](../research.md): the
  enumeration walk traverses schema and value tree together anyway, so the check costs an `if` in a walk
  that had to happen, while the alternative is assuming an upstream invariant and producing a confident
  verdict over a mismatched tree.
- **Severity on a check that did not fail.** Confirmed intentional and documented in data-model VAL-11
  and VAL-12: `not_evaluated` carries `warning` so every record has the same shape, and the verdict takes
  `incomplete` from the *outcome*, never from the severity. A consumer filtering by severity alone sees a
  warning; a consumer branching on the verdict cannot mistake the run for a clean one.

### Raised by the implementation, for review

- **`value_absent` was removed from the not-evaluated vocabulary (VAL-17).** The data model listed it
  for a constraint on a field with no value. Building it showed it cannot exist: an optional field left
  absent would then push the verdict to `incomplete`, and nearly every real document has one — so the
  state would stop meaning "an obligation went unchecked". The rule is now that **an obligation exists
  only where it applies**: a constraint constrains a value, and absence is the requiredness check's
  subject. Recorded in `data-model.md` VAL-17 and in the `ReasonCode` docstring.
- **Milestone 3's `test_schema_hash` strategy was updated.** FR-025 makes a numeric bound on a string
  unconstructible, and that property test drew types and constraints independently. The strategy now
  draws them together — the same "respect the invariant rather than filter for it" shape its own
  docstring already used for the repetition bound.
- **`tests/unit/test_plan_tree_is_current.py` now attributes a test file to the highest layer it
  imports**, and accepts any plan listing it. Milestone 5 added two tests of *schema-layer* behaviour
  that import only `docdoc.extraction`; listing them in Milestone 3's plan would attribute this
  milestone's work to a feature that predates it.
- **`ruff`'s RUF032 autofix rewrote `Decimal(1240.10)` into `Decimal("1240.10")`** in
  `tests/property/test_decimal_semantics.py` — silently deleting the assertion that distinguishes the
  two. The float is now bound to a name, with a comment saying why. Worth knowing before the next
  `--unsafe-fixes` run.

### Decided after the seventh convergence pass

- **No standing mutation-testing tier.** The ad-hoc harness found seven real gaps across two
  passes — including two tests that passed for the wrong reason — and it is still not being
  committed. Three reasons, in order of weight: it uses string replacement on the source and
  **already rotted inside one session** (two of fourteen mutations stopped applying when
  `ruff format` moved a line, while the harness reported "caught" for mutations it never made),
  so it fails *open* — the defect class this milestone spent four phases eliminating; its output
  is already banked, since every gap it found is now pinned by a test in the suite; and a
  standing tier binds every future milestone's CI, which CONTRIBUTING says should be an ADR-level
  decision rather than something settled on a feature branch. The transferable part is the
  technique, not the script, and it is written down in CONTRIBUTING under "Check that a test
  holds what it claims". If a tier is wanted, it should use an AST-based tool and be decided at
  Milestone 6 alongside the rest of the CI budget.

### Raised by the plan, for review

- The grounding policy is folded into `options_hash` although ADR-0003's Validate row predates it and does
  not name it. Justified by that ADR's own rule about omitted inputs; if accepted, ADR-0003 should carry a
  clarifying amendment ([plan.md](../plan.md) design decision 2).
- `Schema` gains a `rules` field in the extraction layer that the validation layer evaluates. This
  continues Milestone 3's "recognised, never applied" split (EXT-4) rather than inventing one, but it is
  the milestone's only upward-looking change and deserves a reviewer's eye.
