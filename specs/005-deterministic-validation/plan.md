# Implementation Plan: Deterministic Validation

**Branch**: `005-deterministic-validation` | **Date**: 2026-08-18 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/005-deterministic-validation/spec.md`

## Summary

Build docdoc's validation stage: the code that takes the located result Milestone 4 produced and answers
whether it is acceptable — field by field, rule by rule, with a verdict a consumer can act on and a
record of every obligation that was checked.

Five pieces of machinery. An **enumeration walk** that turns a schema plus a value tree into an explicit
list of checks, so "how many obligations did this result carry?" has a number rather than an impression.
A **constraint evaluator** for the eight keys Milestone 3 declared, hashed into schema identity, and
deliberately never applied. A **closed rule vocabulary** — four kinds, declared as schema data and
evaluated by one generic engine — so that `total == sum(line_items)` is deterministic code without
becoming a per-document-type code path. A **linear-time pattern dialect** of docdoc's own, because the
stdlib engine cannot satisfy a time bound and a timeout would make verdicts depend on machine speed. And
a **three-state verdict** whose middle state exists so that a run where nothing ran cannot report the
same word as a run where everything ran and passed.

The stage is narrow in a specific way: it judges the result against what the schema *declared*, and
nothing else. It does not repair, does not re-ask the model, does not decide what happens to an invalid
result, and does not read the document — every location its findings carry was computed by Milestone 4
and is copied through, so two stages cannot disagree about where a value is.

**There is no kernel change at this milestone.** The one change outside the new package is additive:
`Schema` gains a `rules` field, hashed only when non-empty, so every schema hash committed at Milestone 3
stays exactly as it is (research.md R4).

The thing to know before reading further: this milestone writes a small regular-expression engine. That
is the kind of decision Principle XI exists to stop, so it is argued from measurements in research.md R2
rather than from taste, and it is contained by a differential property test that uses the stdlib as the
oracle for *what* matches while docdoc's engine supplies the guarantee about *how long it may take*.

## Technical Context

**Language/Version**: Python >= 3.11 (unchanged from Milestones 1–4)

**Primary Dependencies**: **None added.** The base install stays `pydantic` + `rapidfuzz`. Stdlib used:
`decimal` (all arithmetic), `datetime` (temporal comparisons), `enum`, `hashlib` via the kernel's
`options_hash_for`, `logging`, `time` (a monotonic clock read once, for the log event's duration only),
`typing`. `re` is used **in tests** as the differential oracle for the pattern dialect, and not in
`src/` — a distinction the plan states because it looks like an inconsistency otherwise.

The rejected alternative was `google-re2`, and the reason it was rejected is a correctness argument
before a weight one: its dialect version would have to enter `options_hash`, making two machines with
different wheels capable of different verdicts under one artifact id (research.md R2).

**Storage**: N/A. `ExtractionResult` + `GroundingResult` + `Schema` in, `ValidationResult` out. The
artifact identity is computed and exposed; nothing is persisted or cached — the same deferral Milestones
3 and 4 made.

**Testing**: `pytest` + `hypothesis`, `mypy --strict`, `ruff`, `import-linter`. Four tiers, **all
offline** (research.md R12): unit, property, contract, and a `perf`-marked tier. This feature adds **no
provider tier and no test that skips** — the second milestone in a row of which that is true. The
repository's existing 11 skips are Milestone 2's and Milestone 3's live provider tests and are untouched.

**Target Platform**: Cross-platform library; CPython 3.11+ on Linux, macOS, Windows. Everything here must
behave identically on all three: there is no model call to excuse a difference, and the pattern dialect
being docdoc's own removes the last third-party engine that could have varied.

**Project Type**: Single Python library, `src/` layout, published to PyPI

**Performance Goals** — enforced by `tests/perf/test_validation_perf.py` (marked `perf`). Derived in
research.md R8 from measured unit costs rather than asserted:

| Operation | Target | Basis |
|---|---|---|
| Validate 200 values, ~3 checks each, 20 rules | < 50 ms (SC-020) | ≈ 8 ms derived; ~6× headroom |
| One pattern check, typical value | — | **8.84 µs** measured (`pattern_dialect@1`) |
| `(a+)+` against 10,000 characters | < 100 ms | **17.2 ms** measured; CPython's `re` is ~1.2 s at *24* characters |
| Sum over a 200-entry repeating group + comparison | — | **26.4 µs** measured |
| Tolerance comparison, `Decimal` | — | **688 ns** measured |

One row in R8's table is **not** measured: constructing the result's pydantic models, estimated at
1.6–2.4 ms of the 8 ms. That is what the `perf` tier is for. If it dominates, the fix is a lighter
`CheckOutcome` representation, not a relaxed bound.

**Constraints**: The entire feature runs with no credentials, no network, and no `Document` (FR-005,
FR-007). No clock and no randomness on the verdict path, which extends to iteration order: the
enumeration walk fixes the order, and the suite runs under two `PYTHONHASHSEED` values. All arithmetic
in `Decimal`; a `float` from a `number` field enters as `Decimal(str(v))` and never as `Decimal(v)`
(research.md R3). Values never reach logs, while findings carry them by design — the boundary is
restated in research.md R9 because it is easy to read the logging rule as covering both.

**Scale/Scope**: Extraction results to a few hundred values; schemas to a few dozen rules. Fifteen modules in
one new package, one additive field on `Schema`, one `import-linter` layer added, no kernel change. The
committed documents from Milestone 2 and the `echo` adapter from Milestone 3 supply every input, plus
hand-built schemas carrying each constraint key and each rule kind.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluated against constitution v1.2.0. **Initial: PASS. Post-design re-check: PASS.**

| # | Gate (constitution principle) | Status |
|---|-------------------------------|--------|
| 1 | **Kernel purity (I)** | **PASS** — no kernel change and no new dependency of any kind. The validation package imports `Span` and `Geometry` to carry locations through and nothing else from the kernel; `tests/unit/test_kernel_purity.py` must keep passing **unedited** |
| 2 | **Provenance preservation (I, VIII)** | **PASS** — every result records the document identity, both upstream artifacts, the schema identity and hash, the vocabulary and dialect versions, the enabled rules, the policy, and the validator's id and version (data-model §10). Re-validating produces a new result; nothing is overwritten (FR-052). Locations are copied from the grounding outcome, never recomputed (FR-038) |
| 3 | **Grounding integrity (II)** | **PASS** — this stage reads the recorded grounding status and never recomputes, upgrades, or downgrades it (FR-006). An ungrounded value stays distinguishable and now becomes *reportable*: FR-034 turns "no located evidence" into a finding rather than leaving it as a field nobody reads. Fuzzy and exact scores are never compared (FR-037) |
| 4 | **Determinism (III)** | **PASS** — no clock, no randomness, no network, no provider state on the verdict path. The finding order is total by construction (FR-043, VAL-28), the enumeration walk fixes iteration order, and the pattern engine is linear-time and docdoc's own, so no third-party version can move a verdict (research.md R2). The rejected timeout-based bound is called out precisely because it would have broken this gate |
| 5 | **Provider isolation (IV)** | **PASS** — no provider SDK is involved at all, and the base install gains nothing. `google-re2` was declined; had it been adopted it would not have violated this gate, but it would have put a native binary's version inside `options_hash` |
| 6 | **Text-first (V)** | **N/A** — no parsing, no recognition, and no document is read |
| 7 | **Schema-driven (VI)** | **PASS — and this gate is why rules are data.** A per-document-type validator or a registry of Python callables keyed by schema is exactly the "InvoiceService" this principle forbids. Rules are declared in the schema, drawn from a closed vocabulary, and evaluated by one generic engine that never looks at a document type (FR-026, research.md R4). The existing "no document-type code path" test is extended to the new package |
| 8 | **Validation separation (VII)** | **PASS — this gate is the feature.** Extraction answers what the model found; grounding answers where it is; this stage answers whether it is acceptable, as a separate stage with its own artifact and its own output. `sum(line_items) == total` is deterministic code over declared data, never a prompt instruction (FR-026). Failures are structured and field-addressable, never a silent correction and never a bare boolean (FR-039, FR-042). The value Milestone 4 refused to judge — a claim that resolves while the number disagrees — is a finding here, which closes the boundary from the other side |
| 9 | **No silent fallback (VIII)** | **PASS** — a mismatched artifact pair or schema is refused with both sides named (FR-002), never validated anyway. A check that cannot run is `not_evaluated` with a closed reason code and forces `incomplete` (FR-010, FR-041); it is never quietly counted as passed. An absent operand is never substituted with zero (FR-031). A schema whose rule cannot work fails at load, not at verdict time (FR-056) |
| 10 | **Measurability (IX)** | **PASS** — the result carries per-outcome and per-severity counts that reconcile (FR-012), so validation rate is computable without re-running. The *rate itself*, and any target for it, is Milestone 6's question; this milestone makes it measurable and claims no number. Findings carry field paths and locations, which is the shape Milestone 6's annotations will need |
| 11 | **Layer direction (X)** | **PASS with the Milestone 4 refinement continued** — `docdoc.validation` is added to the layers contract above `docdoc.grounding`. Principle X's chain names neither; ADR-0003's stage chain names both, and is finer-grained and consistent with it (research.md R1). The one upward-looking change, `Schema.rules`, is data in the layer below and is *recognised* there rather than evaluated — the split Milestone 3 already established for `constraints` (EXT-4) |
| 12 | **MVP discipline (XI)** | **PASS, with the milestone's one real tension recorded** — no persistence, no cache, no DAG, no routing engine, nothing from the Deferred Technology list. The tension is the pattern engine: writing one is exactly what this principle warns against. It is justified in Complexity Tracking below with the measurements that make the simpler options unusable, and the vocabulary is deliberately four kinds rather than an expression language |
| 13 | **Kernel test rigor (XII)** | **PASS** — no span, geometry, or kernel operation semantics change, so the Milestone 1 property suite applies unchanged and must stay green. New property tests cover the pattern dialect against the stdlib oracle, decimal tolerance against `Fraction`, the totality of the finding order, and the reconciliation of counts (research.md R12) |
| 14 | **Open decisions** | **PASS** — no BLOCKING decision gates this milestone; all eight were resolved by 2026-08-17. ADR-0003's Validate row and ADR-0008's bump table are **followed, not reinterpreted**, with one recorded extension: the grounding policy is folded into `options_hash` although the ADR's row predates it, under that ADR's own rule about omitted inputs (research.md R7). `TODO(GOLDEN_DATASET_LICENSING)` gates Milestone 6 and `TODO(PRE_1_0_VERSIONING)` gates first release; neither is touched |

### Design decisions that refine the spec

Recorded so reviewers see them here rather than discovering them in code.

1. **`pattern_dialect@1` is docdoc's own linear-time subset, not a dependency.** The spec left FR-024's
   mechanism open. CPython's `re` takes **1,183 ms** on `^(a+)+$` against 24 characters and doubles per
   character, so no length precondition rescues it; a timeout would make verdicts machine-dependent.
   `google-re2` is linear and 1.45× faster than docdoc's matcher on typical values, but it is a native
   C++ extension with no musl wheels, and its dialect version would have to enter `options_hash`.
   Measured both ways in research.md R2.

2. **The grounding policy is folded into `options_hash`, extending ADR-0003's Validate row.** The row
   names `validator_version` and the rule set; the policy did not exist when it was written and it
   changes verdicts. The ADR's own rule — an omitted input that can change the output is a correctness
   bug — settles it. **Recommended follow-up**: a clarifying amendment to ADR-0003 if accepted at review.

3. **A `number` field's `float` is converted through `Decimal(str(v))`, and the docs say `number` is
   lossy by declaration.** FR-022 forbids validation from *introducing* binary floating point; it cannot
   recover precision Milestone 3's `conform` already discarded. Stating this plainly beats a guarantee
   the type system contradicts (research.md R3).

4. **FR-018's shape check survives as a refusal, and costs nothing.** The checklist raised that it may be
   unreachable given `conform` and FR-002. The enumeration walk traverses schema and values together
   anyway, so the check is an `if` in a walk that had to happen; the alternative is assuming an upstream
   invariant and producing a confident verdict over a mismatched tree (research.md R11).

5. **Finding order follows schema declaration order, while `schema_hash` sorts by name.** Deliberate:
   order is presentation and may follow the file; identity must not (research.md R5).

6. **The pattern-dialect check runs at the entry to `validate()`, not at schema load.** FR-056 says a
   pattern outside the dialect must never reach verdict time, and the task list asked
   `extraction/loader.py` to enforce it. That is not implementable: `docdoc.extraction` may not import
   `docdoc.validation`, and moving the engine down would put it beneath the only layer that uses it —
   satisfying the letter of "at load" by breaking Principle X. Every declared pattern is therefore
   compiled before the first check is enumerated, and one outside the dialect raises `SchemaError`
   naming the field and the construct. Found by `/speckit-converge`, which also found that the fault
   was previously escaping as a bare `PatternSyntaxError` — a `ValueError`, contradicting FR-054.

## Project Structure

### Documentation (this feature)

```text
specs/005-deterministic-validation/
├── plan.md              # This file
├── research.md          # Phase 0 output — R1…R12
├── data-model.md        # Phase 1 output — VAL-1…VAL-30
├── quickstart.md        # Phase 1 output
├── contracts/
│   └── validation-api.md
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/docdoc/
├── kernel/                    # unchanged
├── ingest/                    # unchanged
├── extraction/
│   ├── schema.py              # + RuleSpec, RuleKind, Schema.rules (data only, recognised not applied)
│   ├── loader.py              # + load-time authoring checks: kind, id uniqueness, operand paths, scoping
│   └── identity.py            # + "rules" folded into schema_hash only when non-empty (FR-053)
├── grounding/                 # unchanged
└── validation/                # new package, new import-linter layer
    ├── __init__.py            # validate(), the public surface of contracts/validation-api.md
    ├── errors.py              # ValidationError
    ├── severity.py            # Severity alone — breaks the options/result import cycle
    ├── enumerate.py           # the schema × value-tree walk that produces the check list
    ├── constraints.py         # the eight recognised constraint keys
    ├── pattern.py             # pattern_dialect@1: parser, NFA construction, linear-time matcher
    ├── numeric.py             # Decimal entry points, tolerance comparison
    ├── options.py             # ValidationOptions, GroundingPolicy — identity-bearing
    ├── rules.py               # the four rule kinds
    ├── grounding_policy.py    # status → severity, reading Milestone 4's record
    ├── verdict.py             # counts, severity resolution, verdict derivation, finding order
    ├── record.py              # the one internal record both public views derive from
    ├── result.py              # CheckOutcome, Finding, ValidationResult, provenance models
    ├── identity.py            # VALIDATOR_ID/VERSION, options_hash, artifact_id
    └── observe.py             # one structured event, no values

tests/
├── unit/
│   ├── test_schema_authoring_errors.py        # FR-025, FR-029, FR-056, SC-014 — refused at load
│   ├── test_rules_in_schema_hash.py           # FR-053, SC-019 — rules hashed only when declared
│   ├── test_check_enumeration.py              # FR-008, FR-013, FR-018 — the walk and its refusals
│   ├── test_required_checks.py                # FR-014 … FR-017 — presence, entries, absent groups
│   ├── test_constraints.py                    # FR-019 … FR-023 — the eight keys, exactly compared
│   ├── test_constraint_key_coverage.py        # SC-005 — no key may ship unenforced
│   ├── test_pattern_dialect_rejections.py     # FR-024, FR-056 — the dialect's edges and budgets
│   ├── test_rules.py                          # FR-026 … FR-030, FR-032 — the four kinds
│   ├── test_rule_not_evaluated.py             # FR-031, VAL-17 — an absent operand is not zero
│   ├── test_grounding_policy.py               # FR-034 … FR-038, SC-011 — evidence, read not re-decided
│   ├── test_verdict_derivation.py             # FR-040 … FR-042 — three states, no boolean
│   ├── test_no_repair.py                      # FR-004, FR-044, SC-006 — nothing fixed, nothing raised
│   ├── test_model_confidence_routes_nothing.py # FR-045, SC-012 — the untrusted field routes nothing
│   ├── test_validation_answers_whether.py     # Principle VII — the mirror of Milestone 4's boundary test
│   ├── test_findings_are_addressable.py       # FR-039, VAL-21 — prose carries nothing extra
│   ├── test_validation_refusals.py            # FR-002, SC-015 — three mismatches, none validated
│   ├── test_validation_logging.py             # FR-057, FR-058, SC-021 — counts in logs, values in findings
│   ├── test_finding_order_is_total.py         # FR-043, SC-013 — order independent of hash seed
│   ├── test_validation_reads_no_document.py   # FR-005, FR-052 — no document, no mutation
│   └── test_validator_version_snapshot.py     # FR-050, VAL-2, SC-018 — the change detector
├── property/
│   ├── test_pattern_dialect.py                # the differential oracle; `re` is right, we are fast
│   ├── test_decimal_semantics.py              # FR-022, FR-030 — against `Fraction`, not against itself
│   └── test_counts_reconcile.py               # FR-012, SC-002 — counts add up for any schema
├── contract/
│   └── test_validation_identity.py            # FR-047 … FR-049, SC-016, SC-017 — what moves the id
├── integration/
│   └── test_validate_invoice.py               # US1 and US2 end to end over a committed document
└── perf/
    └── test_validation_perf.py                # SC-020 and the adversarial pattern case

examples/
└── validate_invoice.py        # the quickstart's 30-second version
```

**Structure Decision**: One new package, `src/docdoc/validation/`, added to the `import-linter` layers
contract above `docdoc.grounding`, with a forbidden-imports contract mirroring grounding's. The only
changes outside it are three additive edits in `docdoc.extraction` that declare rules as data — the layer
that owns schemas keeps owning what a well-formed schema is, and the layer above owns what a rule means
(research.md R1, R4).

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| **Writing a regular-expression engine** (`pattern_dialect@1`, ~180 lines plus a parser) against Principle XI's "every abstraction needs a present-tense reason" | FR-024 requires a hard bound on pattern evaluation, and FR-056 requires the dialect to be documented and versioned because it decides verdicts | **stdlib `re`**: measured 1,183 ms on `^(a+)+$` at 24 characters, doubling per character — unusable for 30–60 character field values, and a length precondition cannot fix it. **A timeout**: makes the verdict depend on machine speed, so one artifact id could describe two verdicts (Principle III). **`google-re2`**: sound and 1.45× faster on typical values, but a native C++ extension with no musl wheels and glibc ≥ 2.28 manylinux tags, and its own version would have to enter `options_hash` — moving the definition of what a pattern *means* outside docdoc. **Dropping `pattern`**: it is a recognised constraint key since Milestone 3, and a declared constraint that is never enforced is the exact defect this milestone exists to end |

Contained by: a closed, documented subset; load-time rejection of everything outside it; and a Hypothesis
differential test that makes `re.fullmatch` the oracle for correctness while docdoc's engine supplies
only the time bound (research.md R2, R12).

## Phase outputs

- **Phase 0** — [research.md](research.md): R1 layer placement, R2 the pattern engine (the spec's one
  carried-forward unknown, resolved with measurements), R3 decimal semantics over an upstream `float`,
  R4 rule declarations and `schema_hash` stability, R5 deterministic check enumeration, R6 three
  verdicts, R7 the grounding policy in identity, R8 SC-020 derived rather than asserted, R9 errors and
  observability reuse, R10 not-evaluated reason codes, R11 FR-018's fate, R12 the testing strategy.
- **Phase 1** — [data-model.md](data-model.md) (VAL-1…VAL-30 and the error model),
  [contracts/validation-api.md](contracts/validation-api.md) (the public surface and what the layer will
  not do), [quickstart.md](quickstart.md) (eight runnable scenarios, none of which skip).
- **Phase 2** — `tasks.md`, produced by `/speckit-tasks`. Not created here.
