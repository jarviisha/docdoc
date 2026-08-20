# Implementation Plan: Golden-Set Evaluation

**Branch**: `006-golden-set-evaluation` | **Date**: 2026-08-20 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/006-golden-set-evaluation/spec.md`

## Summary

Build docdoc's evaluation stage: the code that takes a golden set and a recorded prediction set and
answers whether any of this is any good, and whether the last change made it worse. Milestone 4 made
the grounding rate computable and set no target. Milestone 5 did the same for validation. Both deferred
the same question to here.

Five pieces of machinery. A **golden set** that is two tiers of versioned data with an identity that
moves whenever anything metric-relevant in it moves. A **comparison** that resolves every labelled field
to exactly one of six closed outcomes under a versioned comparator, with a type-identity gate in front
of it because `True == 1` in Python and FR-024 forbids exactly that match. A **metric definition** —
`metric_definitions@1` — whose numerators and denominators are pinned data rather than formulas in
code, because the denominators are the part a future contributor will want to "improve" when a number
looks bad. A **report** that is byte-identical for fixed inputs, states every numerator and denominator,
and is content-addressed. And a **comparison between reports** that names which provenance fields
differed, so that a metric change is attributable rather than merely observed.

The stage is narrow in a specific way: it reads recorded facts and derives nothing. It does not re-run
the pipeline, does not re-ground, does not re-validate, does not open a document, and does not ask a
model whether a predicted value *means* the same as the expected one — that last one is the failure
Principle II forbids for grounding, and it is no more acceptable when the subject is accuracy.

**There is no kernel change at this milestone**, and no change to extraction, grounding, or validation
at all. This is the first milestone that adds only new packages. That is not an accident of scope: the
spec's Out of Scope forbids changing any earlier stage's behaviour to improve a metric, because a
milestone that both measures and improves can report honestly on neither.

The thing to know before reading further: this milestone adds **two** packages where one would look
tidier, and the reason is that FR-003 requires recording to not be part of evaluation. Inside one
package that sentence is a naming convention. As two layers it is the `import-linter` contract, and it
fails the build. The second thing to know is that the usual proof of "this layer touches no network"
does not work here, for a reason that took measuring to find — research.md R1.

## Technical Context

**Language/Version**: Python >= 3.11 (unchanged from Milestones 1–5)

**Primary Dependencies**: **None added.** The base install stays `pydantic` + `rapidfuzz`. Stdlib used:
`json` (the dataset format, research.md R4), `decimal` and `datetime` (typed comparison), `enum`,
`hashlib` via the kernel's `canonical_json` / `options_hash_for`, `logging`, `pathlib`, `re` (field-path
decomposition for ordering — over paths **docdoc itself generated**, never over an authored pattern or
a document value), `time` (a monotonic read for the log event's duration only), `typing`.

`YAML` was the rejected alternative for the dataset format, and it was rejected on a correctness
argument before a convenience one: the dataset identity is a hash over canonical JSON, so authoring in
JSON removes a conversion step where two representations could disagree about what was hashed
(research.md R4).

**Storage**: N/A for the library. `GoldenSet` + `PredictionSet` in, `EvaluationReport` out. The report's
identity is computed and exposed; nothing is persisted, cached, or written — the same deferral
Milestones 3, 4, and 5 made. The golden set lives in `datasets/` at the repository root, which is
outside `[tool.hatch.build.targets.wheel]`'s `packages`, so FR-059 ("the base install MUST NOT require
the golden set") is satisfied by the build configuration that already exists rather than by a new rule.

**Testing**: `pytest` + `hypothesis`, `mypy --strict`, `ruff`, `import-linter`. Four tiers, **all
offline**. This feature adds **no provider tier and no test that skips** — the third milestone in a row
of which that is true. The repository's existing 11 skips are Milestone 2's and Milestone 3's live
provider tests and are untouched. `docdoc.recording` is exercised against the `echo` adapter, which is
precisely what that adapter exists for and why it is never auto-selected.

**Target Platform**: Cross-platform library; CPython 3.11+ on Linux, macOS, Windows. Everything here
must behave identically on all three — there is no model call to excuse a difference, and unlike
Milestone 5 there is not even a pattern engine, so the only platform hazards are iteration order and
hash seed. Both are addressed by a total order (EVA-26) run under two `PYTHONHASHSEED` values.

**Project Type**: Single Python library, `src/` layout, published to PyPI

**Performance Goals** — enforced by `tests/perf/test_evaluation_perf.py` (marked `perf`). Derived in
research.md R13 from measured unit costs:

| Operation | Target | Basis |
|---|---|---|
| Score the gate-5 target dataset (50 docs, 500 labels), excluding load | < 50 ms | ≈ 2 ms derived; ~25× headroom |
| Same, end to end **including** load | < 500 ms | not derived — see below |
| `Decimal == Decimal` | — | **34.8 ns** measured |
| Frozen pydantic model, 8 fields | — | **1.98 µs** measured |
| Field-path decomposition | — | **1.85 µs** measured; cached per outcome |
| Sorting 500 outcomes | — | **0.040 ms** measured |
| `options_hash_for(options)` | — | **6.87 µs** measured |

One row is deliberately **not** derived: loading. Reading and validating a manifest, 500 labels, and
500 prediction artifacts is I/O plus pydantic validation, and it will dominate the 2 ms of arithmetic
by an order of magnitude. It is bounded separately and it is what the `perf` tier measures. If it
dominates beyond the bound, the fix is a leaner on-disk prediction form, not a relaxed bound. This is
the discipline Milestone 5 applied when it declined to assert an unmeasured model-construction cost —
and it is worth noting that the row Milestone 5 flagged as unmeasured is the one measured here.

**Constraints**: Scoring runs with no credentials, no network, no provider, no database, and no object
store (FR-007), and is the only thing the quality gates read, so a scorer that could reach a network is
a metric nobody can reproduce. No clock and no randomness on the scoring path — `Correction.timestamp`
is data an annotator supplies, not a clock the scorer reads, and the only `time` call is the log
event's duration. All comparison is on typed values with a type-identity gate (EVA-12a). Document text,
field values, and label values never reach logs; findings and outcomes carry them by design, subject to
tier-driven redaction. The boundary is the one Milestone 5's `observe.py` documents and is restated in
research.md R12 because "values never appear anywhere" is the wrong reading of FR-057 and would make
outcomes useless.

**Scale/Scope**: Datasets to a few hundred documents and a few thousand labels. Two new packages, no
kernel change, no change to any existing layer, two `import-linter` layers added, one new top-level
`datasets/` directory. The `echo` adapter from Milestone 3 and the committed documents from Milestone 2
supply every input the recording path needs.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluated against **constitution v1.3.0**. **Initial: PASS. Post-design re-check: PASS.**

| # | Gate (constitution principle) | Status |
|---|-------------------------------|--------|
| 1 | **Kernel purity (I)** | **PASS** — no kernel change and no new dependency. The new packages import `BBox`, `Span`, `Geometry`, `canonical_json`, `options_hash_for`, and `DocdocError` from the kernel and nothing else; `tests/unit/test_kernel_purity.py` must keep passing **unedited** |
| 2 | **Provenance preservation (I, VIII)** | **PASS** — every report records all seventeen items FR-040 lists, and a run that cannot record one is **refused rather than reported** (EVA-21a), which is stronger than recording a null. Locations are copied from the grounding outcome, never recomputed. Re-evaluating produces a new report and overwrites nothing (FR-044) |
| 3 | **Grounding integrity (II)** | **PASS** — the grounding rate reuses Milestone 4's definition and its recorded counts and is never recomputed from outcomes (FR-033). Ungrounded stays distinguishable, and a *correct but ungrounded* value is `CORRECT` for accuracy and `ungrounded` for the rate — two facts, two numbers, never merged (EVA-15b). No model is asked to judge anything (FR-008) |
| 4 | **Determinism (III)** | **PASS** — no clock, no randomness, no network, no provider state on the scoring path. The outcome order is total by construction (EVA-26) and the suite runs under two `PYTHONHASHSEED` values. Scoring is deterministic **even though what it scores was not**: the model that produced a prediction is probabilistic; the comparison of that prediction against a label is arithmetic, and that boundary is what puts this stage in the deterministic core at all |
| 5 | **Provider isolation (IV)** | **PASS, with the caveat that makes it real** — `docdoc.evaluation` touches no provider and the base install gains nothing. `docdoc.recording` reaches a provider only through the existing `ModelAdapter` and registry, naming no SDK type. The caveat: a forbidden-imports contract **cannot prove** this, because `adapters/gemini.py` imports `google.genai` inside a function and the registry imports the adapter inside a function, so the provider is invisible to static analysis. The contract is kept for its preventive value and a runtime test supplies the proof (research.md R1, quickstart Scenario 8) |
| 6 | **Text-first (V)** | **N/A** — no parsing and no recognition. The scorer never opens a document; it compares labels against recorded predictions. `docdoc.recording` delegates to `docdoc.ingest`, which already owns the text-layer decision and is unchanged |
| 7 | **Schema-driven (VI)** | **PASS — and this gate is why entry keys live in the dataset.** A golden set that could only describe invoices would reintroduce, in data, the document-type coupling this principle forbids in code (FR-013). The repeating-group alignment key is declared by the golden set and **never** by the schema: a key in the schema would move `schema_hash` under ADR-0008, and FR-004 would then refuse every label already written against it, so the act of improving alignment would invalidate the dataset it was meant to measure |
| 8 | **Validation separation (VII)** | **PASS** — this stage judges nothing about a document. It reuses Milestone 5's verdicts and counts rather than recomputing them (FR-034), so that "the extraction was wrong" and "validation caught it" remain two independently visible facts. Neither suppresses the other: a validator rejecting a wrong value is a success of validation, not an improvement in extraction accuracy |
| 9 | **No silent fallback (VIII)** | **PASS** — mismatched schema identities, unknown documents, and incomparable reports are refused with both sides named (FR-004, FR-005, FR-046), never scored anyway. An empty denominator is `None`, **never `0.0`** (FR-032). An unevaluated document is named and stays in every denominator (FR-005). `NOT_ASSESSABLE` is never collapsed into `DISAGREES` (EVA-14b). A restricted bundle short of its declared label count is refused, because a bundle silently short of its declaration is a smaller denominator wearing a full report's clothes |
| 10 | **Measurability (IX)** | **PASS — this gate is the feature.** All five constitutionally required metrics, at dataset, document, and field-path level, each stating its numerator and denominator, each traceable to the individual comparisons that produced it. Corrections carry the seven required fields and are promotable into dataset signal without becoming a review platform (FR-054). **This milestone still claims no quality target**, per its own Assumptions: it makes accuracy measurable and regressions visible, and what a good field accuracy is for a given schema is a property of a deployment |
| 11 | **Layer direction (X)** | **PASS with the Milestone 4/5 refinement continued, two layers this time** — `docdoc.evaluation` above `docdoc.validation`, and `docdoc.recording` above `docdoc.evaluation`. Principle X's chain names none of the four; ADR-0003's stage chain names three and is finer-grained and consistent with it, the same reading Milestones 4 and 5 recorded. Evaluation is **not** a stage in that chain — it consumes the validation artifact and produces nothing a document-processing stage consumes — which is why its identity is named `report_id` and not `artifact_id` (EVA-24) |
| 12 | **MVP discipline (XI)** | **PASS, with the milestone's one real tension recorded** — no persistence, no cache, no queue, no worker, no CLI, no HTTP, no review UI, nothing from the Deferred Technology list, and no new dependency. The tension is `docdoc.recording`: a package no contributor runs, in a milestone whose headline property is that contributors can run everything. It is justified in Complexity Tracking below |
| 13 | **Kernel test rigor (XII)** | **PASS** — no span, geometry, or kernel operation semantics change, so Milestone 1's property suite applies unchanged and must stay green. Three new property tests where the invariant is universal rather than exemplary: metric reconciliation, order totality, and identity sensitivity in **both** directions — an identity that moves on everything is as useless as one that moves on nothing (research.md R14) |
| 14 | **Open decisions** | **PASS — and this is the gate that was failing when planning was first attempted.** `TODO(GOLDEN_DATASET_LICENSING)` named this milestone explicitly and is now resolved by **ADR-0009** with the amendment to **constitution v1.3.0**, which also states gate 5's target size. It was decided in an explicit clarification session and recorded in an ADR, which is what the precedence rule requires; it was not settled by whichever dataset landed first. `TODO(PRE_1_0_VERSIONING)` gates first public release and is untouched |

### Design decisions that refine the spec

Recorded so reviewers see them here rather than discovering them in code.

1. **`metric_definitions@1` pins denominators the spec named but did not define.** Principle IX names
   "coverage" without saying what it counts. Two readings were live; the adopted one is answer rate over
   value labels, which reconciles exactly with missing rate and the unevaluated rate over one
   denominator. The rejected one — evaluation completeness — is already carried more precisely by the
   partial declaration, and would read 1.0 on every healthy run. Full table and the reconciliation
   identity in research.md R2.

2. **A macro-average reports how many documents it averaged over.** A document with an undefined metric
   cannot enter a mean, and excluding it silently makes the macro number describe an unstated subset —
   FR-015's failure mode at a different scale, invisible unless the count travels with the number
   (research.md R3, EVA-18a).

3. **Comparison gates on type identity before equality.** `True == 1`, `Decimal(1) == True`, and
   `1 == 1.0` are all true in Python, and `isinstance` does not help because `bool` subclasses `int`.
   Without `type(a) is type(b)` a boolean label silently matches an integer prediction and the report
   calls it correct — which is precisely the cross-type coercion FR-024 forbids (EVA-12a).

4. **Location agreement uses containment, not IoU.** A human labelling a value draws a loose box;
   docdoc's geometry is tight on the tokens. IoU punishes exactly that pairing, so a perfectly located
   box can score below threshold while being completely right. The rule asks the question actually
   being tested — is what docdoc found inside what the human pointed at — at a threshold of 0.5,
   versioned `page_box@1`. IoU becomes the better measure if labels ever become machine-tight, and gets
   a `page_box@2` (research.md R9).

5. **The manifest commits label *counts* for the restricted tier, whose labels it cannot commit.**
   Without them a checkout knows how many restricted documents exist but not how many labels they
   carry, so FR-015's "covered fraction" would have to be guessed. Committing integers makes a partial
   report state its own denominator exactly, and is the mechanism behind SC-016 (research.md R7,
   EVA-5a).

6. **The report's identity is `report_id`, not `artifact_id`.** The formula's shape is deliberately
   ADR-0003's, because the argument for content-addressing is the same one. The name is not, because
   evaluation is not in that chain and a reader who went looking would not find it (EVA-24).

7. **The usual proof of "this layer reaches no network" does not work here.** Found while designing the
   contract, not while writing code: the forbidden-imports contracts that Milestones 4 and 5 rely on
   pass for `docdoc.evaluation` whether or not the recorder sits inside it, because the provider import
   is dynamic. The load-bearing contract here is the docdoc-internal edge `evaluation ⊁ recording`, and
   the property itself is asserted by a runtime test (research.md R1).

## Project Structure

### Documentation (this feature)

```text
specs/006-golden-set-evaluation/
├── plan.md              # This file
├── research.md          # Phase 0 output — R1…R14
├── data-model.md        # Phase 1 output — EVA-1…EVA-30
├── quickstart.md        # Phase 1 output — nine scenarios, none of which skip
├── contracts/
│   └── evaluation-api.md
├── checklists/
│   └── requirements.md
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/docdoc/
├── kernel/                    # unchanged
├── ingest/                    # unchanged
├── extraction/                # unchanged
├── grounding/                 # unchanged
├── validation/                # unchanged
├── evaluation/                # new package, new import-linter layer
│   ├── __init__.py            # evaluate(), compare(), promote(), the loaders
│   ├── errors.py              # EvaluationError
│   ├── tiers.py               # Tier, DocumentOrigin — no imports, breaks cycles
│   ├── golden.py              # GoldenSet, GoldenDocument, EntryKeySpec, load + refusals
│   ├── labels.py              # Label, ExpectedLocation, Expectation
│   ├── predictions.py         # DocumentPrediction, PredictionSet, load + refusals
│   ├── comparators.py         # comparators@1, the type-identity gate
│   ├── alignment.py           # positional@1 / keyed@1, GroupOutcome
│   ├── location.py            # page_box@1, containment, the three-valued result
│   ├── outcomes.py            # FieldOutcome, FieldOutcomeKind, the closed set
│   ├── ordering.py            # path_key, the total order
│   ├── definitions.py         # metric_definitions@1 — numerators and denominators as data
│   ├── metrics.py             # MetricValue, micro/macro, DatasetMetrics
│   ├── score.py               # evaluate() — the walk that produces outcomes
│   ├── report.py              # EvaluationReport, PartialDeclaration, provenance models
│   ├── redact.py              # tier-driven disclosure
│   ├── compare.py             # Comparison, judgements, provenance diffing
│   ├── corrections.py         # Correction, promote()
│   ├── identity.py            # SCORER_ID/VERSION, options_hash, report_id
│   └── observe.py             # one structured event, no values
└── recording/                 # new package, new import-linter layer, ABOVE evaluation
    ├── __init__.py            # record_predictions()
    └── record.py              # parse -> extract -> ground -> validate, failures recorded

datasets/                      # NOT in the wheel — FR-059 by build config, not by rule
└── mvp/
    ├── manifest.json          # documents, tiers, origins, entry keys, declared counts
    ├── documents/             # public tier only
    ├── labels/                # <document_id>.json
    └── predictions/           # public tier only, committed and replayed

tests/
├── unit/
│   ├── test_golden_set_authoring_errors.py   # FR-014, SC-021 — refused at load, named
│   ├── test_document_provenance_required.py  # FR-011, EVA-2a/2b — no basis, no admission
│   ├── test_ordering.py                      # EVA-26 — entry 2 before entry 10, and total
│   ├── test_comparators.py                   # FR-023, FR-024, EVA-12a — True is not 1
│   ├── test_entry_alignment.py               # FR-020, FR-021 — positional, keyed, count mismatch
│   ├── test_location_agreement.py            # FR-038, EVA-14 — three values, containment
│   ├── test_metric_definitions.py            # SC-003 — hand-computed literals
│   ├── test_undefined_is_not_zero.py         # FR-032, SC-005 — every empty denominator
│   ├── test_failures_count.py                # FR-037, SC-006 — crashing lowers the score
│   ├── test_unlabeled_excluded.py            # FR-036, SC-007 — counted, never in a denominator
│   ├── test_evaluation_refusals.py           # FR-004, FR-005, FR-046, SC-010, SC-013
│   ├── test_partial_reports.py               # FR-015, SC-016 — exact covered fraction
│   ├── test_redaction.py                     # FR-056, SC-018 — tier-driven, not caller-driven
│   ├── test_evaluation_logging.py            # FR-057, FR-061, SC-017 — counts in logs, values in outcomes
│   ├── test_scoring_is_offline.py            # FR-007 — socket patched to raise
│   ├── test_inputs_unchanged.py              # FR-006, SC-008 — byte-identical before and after
│   ├── test_no_model_is_asked.py             # FR-008 — the adapter is never constructed
│   ├── test_no_rederivation.py               # FR-002, FR-044 — reads, never recomputes
│   ├── test_model_confidence_unread.py       # FR-028 — untrusted upstream, unread here
│   ├── test_grounding_rate_reused.py         # FR-033 — Milestone 4's counts, not a second rate
│   ├── test_scores_never_averaged.py         # FR-039 — exact and fuzzy never pooled
│   ├── test_base_install_excludes_evaluation_data.py  # FR-059 — datasets/ outside the wheel
│   ├── test_comparison_judgements.py         # FR-045, FR-047, FR-049, SC-014, SC-015
│   ├── test_corrections.py                   # FR-050 … FR-053, SC-019, SC-020
│   ├── test_evaluation_has_no_document_type_code.py  # Principle VI, extending Milestone 3's test
│   └── test_scorer_version_snapshot.py       # EVA-24, the change detector
├── property/
│   ├── test_metrics_reconcile.py             # SC-004, EVA-17c — the identity, for any dataset
│   ├── test_report_determinism.py            # FR-043, SC-009 — two hash seeds
│   └── test_report_identity_sensitivity.py   # FR-042, SC-012 — both directions
├── contract/
│   └── test_evaluation_boundaries.py         # FR-058, SC-024 — layer direction, both new packages
├── integration/
│   ├── test_evaluate_public_tier.py          # US1, US3, SC-001, SC-022 — end to end from a checkout
│   └── test_record_and_score.py              # US4 path, against the echo adapter
└── perf/
    └── test_evaluation_perf.py               # the < 50 ms and < 500 ms bounds

examples/
├── evaluate_golden_set.py     # the quickstart's 30-second version
└── compare_reports.py         # the regression story, US2
```

**Structure Decision**: Two new packages rather than one, added to the `import-linter` layers contract
as `docdoc.recording > docdoc.evaluation > docdoc.validation`. Nothing in any existing layer changes —
this is the first milestone that is purely additive, which the spec's Out of Scope requires rather than
merely permits. The golden set lives at `datasets/` outside the wheel, so FR-059 falls out of the
existing build configuration.

**One scope boundary stated rather than implied.** This milestone builds the machinery and a fixture
dataset large enough to exercise every code path. It does **not** deliver a golden set at gate 5's
target size of 50 documents and 500 labelled fields — that is dataset authoring and generator work, not
implementation work, and pretending otherwise would put a number in `tasks.md` that no task produces.
The gate stays advisory until the dataset reaches that size, which is exactly what constitution v1.3.0
now says and what this milestone deliberately does not flip.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| **`docdoc.recording`** — a second new package, which no contributor runs, in a milestone whose headline property is that contributors can run everything (Principle XI: every abstraction needs a concrete, present-tense reason) | FR-003 requires a path that produces a prediction set, and it is the **only** path available for the restricted tier, whose predictions carry document content and cannot be committed. Without it, "how was the committed prediction set produced?" has no answer in the repository, and refreshing it after a prompt or model change becomes an act nobody can reproduce | **A script under `examples/` or `tools/`**: it needs the same `PredictionSet` model and the same typed errors, and — decisively — `prediction_set_id` folds `recorder_id` and `recorder_version` (EVA-10, research.md R6). A script has no version, so the prediction set's identity would carry a hole exactly where FR-040 and FR-042 need it to be whole. **Folding it into `docdoc.evaluation`**: makes FR-003's "recording MUST NOT be part of evaluation" a naming convention instead of a layers contract, and removes the one statically visible edge that can be enforced at all (research.md R1). **Omitting it**: leaves the public tier's committed predictions with no reproducible provenance, which is the defect ADR-0003 exists to prevent one level down |

Contained by: the layers contract, which fails the build on `evaluation → recording`; a `perf`- and
provider-free test suite that never invokes it except against the `echo` adapter; and the fact that it
is two modules, one function, and no new dependency.

## Phase outputs

- **Phase 0** — [research.md](research.md): R1 the two packages and the proof that does not work, R2
  the metric definitions, R3 micro/macro and the denominator a macro hides, R4 JSON over YAML, R5 the
  total order across a multi-schema dataset, R6 the three identities, R7 the two tiers and declared
  counts, R8 comparators and the type gate, R9 alignment and location agreement, R10 reusing Milestones
  4 and 5, R11 comparison and attributability, R12 corrections and disclosure, R13 performance derived
  from measurements, R14 the testing strategy.
- **Phase 1** — [data-model.md](data-model.md) (EVA-1…EVA-30 and the error model),
  [contracts/evaluation-api.md](contracts/evaluation-api.md) (both packages, and what the layer will
  not do), [quickstart.md](quickstart.md) (nine runnable scenarios, none of which skip).
- **Phase 2** — `tasks.md`, produced by `/speckit-tasks`. Not created here.
