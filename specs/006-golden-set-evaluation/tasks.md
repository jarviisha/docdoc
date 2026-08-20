---

description: "Task list for 006-golden-set-evaluation"
---

# Tasks: Golden-Set Evaluation

**Input**: Design documents from `/specs/006-golden-set-evaluation/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/evaluation-api.md](contracts/evaluation-api.md), [quickstart.md](quickstart.md)

**Tests**: **NOT optional here.** The template's constitution override names *Layer boundaries* and
*Evaluation-affecting changes* explicitly, and this feature is the second of those by definition.
Principle XII additionally requires property tests where an invariant is load-bearing, and three are:
the reconciliation of metrics against their outcomes (SC-004), the totality of the report order
(SC-009), and the sensitivity of `report_id` in **both** directions (SC-012). Test tasks below are
requirements, not suggestions.

**On the golden-set metrics task the template mandates.** Milestones 4 and 5 each recorded that there
was none, because `TODO(GOLDEN_DATASET_LICENSING)` was open and gated this milestone. It is now
resolved — ADR-0009, constitution v1.3.0 — and **this milestone builds the machinery those two
deferred**. What it does *not* build is a golden set at quality gate 5's target size of 50 documents
and 500 labelled fields: that is dataset authoring and generator work, not implementation work. T074
delivers a committed public tier large enough to exercise every code path and **states its own size**,
so the distance to the target is a number a reader can see rather than a gap nobody mentions. The gate
stays advisory, which is exactly what constitution v1.3.0 says and what this milestone deliberately
does not flip.

**Organization**: Grouped by user story so each is independently implementable and testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US4, mapping to spec.md's prioritised stories
- Exact file paths in every description

## Path Conventions

Single Python project, `src/` layout. New code lands in `src/docdoc/evaluation/` and
`src/docdoc/recording/`. **No existing layer changes** — this is the first purely additive milestone,
which the spec's Out of Scope requires rather than merely permits. The dataset lands in `datasets/` at
the repository root; tests in `tests/`.

## Where implementation and tests diverge

Four things the spec assigns to later stories must be *implemented* in US1, because `evaluate()` cannot
ship without them. Each such task says so, and each one's **dedicated adversarial test stays in the
story that owns it**:

1. **Provenance and `report_id`** (FR-040 … FR-042, US1's own) — a run that cannot record a provenance
   field is refused rather than reported, so there is no version of `evaluate()` that omits this.
2. **The `Tier` field and the redaction shape** (FR-056, tested in US3) — `FieldOutcome` cannot grow a
   `redacted` field later without moving every committed report's identity.
3. **The `partial` field on the report** (FR-015, tested in US3) — FR-001 forbids a partial report that
   is not marked partial, and a field added later would mean every US1-era report silently claimed to
   be complete.
4. **The refusals** (FR-004, FR-005) — shipping US1 without them produces confident metrics over a
   prediction set and a golden set that do not describe the same thing, which is the failure this
   stage exists to prevent.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Two package skeletons, the layer contracts that make FR-003's separation a build failure
rather than a sentence, and the fixtures every later phase reads

- [ ] T001 Create the package skeleton `src/docdoc/evaluation/__init__.py` with a module docstring stating what this layer is and what it refuses to do — no re-derivation, no model call, no document read, no network, no persistence, no decision about what should happen to a regression — mirroring the shape of `src/docdoc/validation/__init__.py`
- [ ] T002 [P] Create the package skeleton `src/docdoc/recording/__init__.py` with a module docstring stating that this is the **only** part of the feature that reaches a provider, that nothing in `docdoc.evaluation` may import it, and that scoring never requires it (FR-003, contracts §7)
- [ ] T003 Add both packages to the layers contract in `pyproject.toml` as `layers = ["docdoc.recording", "docdoc.evaluation", "docdoc.validation", "docdoc.grounding", "docdoc.extraction", "docdoc.ingest", "docdoc.kernel"]` and extend the existing comment, which already explains that higher layers are added as their milestones land. Record **why `recording` is above `evaluation`**: the recorder constructs the `PredictionSet` the scorer consumes, so the data flow supplies the ordering, and the ordering is what makes `evaluation → recording` a build failure (FR-003, FR-058, research.md R1). Must run after T001 and T002 — `import-linter` errors on a layer naming a non-existent module
- [ ] T004 Add a forbidden-imports contract for `docdoc.evaluation` in `pyproject.toml` listing `socket`, `urllib`, `http`, `httpx`, `requests`, `openai`, `anthropic`, `google`, `boto3`, `azure`, `fastapi`, `sqlalchemy`, mirroring validation's. **Add a comment stating plainly what this contract does and does not prove**: `adapters/gemini.py` imports `google.genai` inside a function and `adapter_registry.py` imports the adapter inside a function, so a package reaching a provider *through* `docdoc.extraction` is invisible to static analysis. This contract is preventive — it fires the moment someone writes `import httpx` here — and T075 is what actually asserts FR-007 (research.md R1). Depends on T003
- [ ] T005 [P] Build the golden-set fixtures in `tests/fixtures/evaluation/datasets.py`: a small well-formed golden set spanning **two schemas** (FR-013), with value labels, absence labels, a repeating group aligned positionally, a repeating group with a declared key, labels carrying an expected location and labels carrying none, and one document in the restricted tier
- [ ] T006 [P] Build the authoring-error fixtures in `tests/fixtures/evaluation/authoring_errors.py`: a duplicated `document_id`, an unresolvable field path, a label whose value the declared `FieldType` cannot carry, a `DocumentOrigin` with an empty `basis`, a `SYNTHETIC` origin missing its generator, an `EntryKeySpec` naming a non-scalar field, and duplicate key values within one side (EVA-2a, EVA-2b, EVA-4a, EVA-5b, EVA-13a)
- [ ] T007 [P] Build the prediction fixtures in `tests/fixtures/evaluation/predictions.py`: committed `ExtractionResult` + `GroundingResult` + `ValidationResult` triples produced offline with Milestone 3's `echo` adapter over Milestone 2's committed documents — one matching the labels exactly, one with a near-miss value, one with an absent value the labels say is present, one with a value the labels say is absent, one carrying an undeclared field path, one whose document failed at `GROUND`, one with no prediction at all, and one produced under a **different** `schema_hash` for the refusal tests

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The closed vocabularies, the models, the loaders, the ordering, and the three identities
every story reads. No user story can start until this phase completes

- [ ] T008 Implement `src/docdoc/evaluation/errors.py` with `EvaluationError(DocdocError)` carrying `dataset`, `document_id`, `field_path`, `expected`, and `actual` as attributes rather than only interpolated into prose, mirroring `ValidationError`. Document the line it draws: an **error** is a statement about the request, a **field outcome** is a statement about the document, and collapsing them is how a mismatched pair produces a confident report. Never retried — there is no transient failure mode in a deterministic offline computation (FR-060, EVA-30)
- [ ] T009 [P] Implement `src/docdoc/evaluation/tiers.py` with `Tier`, `OriginKind`, and `DocumentOrigin`. No imports from elsewhere in the package, so it can break the cycles `golden.py` and `redact.py` would otherwise form (EVA-1, EVA-2)
- [ ] T010 [P] Implement `src/docdoc/evaluation/labels.py` with `Expectation`, `ExpectedLocation`, and `Label`. `ExpectedLocation` reuses the kernel's `BBox` and carries a page; document why it is **never** a text offset — offsets move when a parser changes how it extracts text, so a label pinned to one turns a parser upgrade into mass mislocation that is not mislocation (EVA-7a, FR-038). `Label` states either the expected value or an asserted absence, addresses fields by the **same** field-path form extraction, grounding, and validation use, and can record who stated it and when (FR-016, FR-017, FR-018, FR-019, EVA-8)
- [ ] T011 Implement `src/docdoc/evaluation/golden.py` models: `GoldenDocument`, `EntryKeySpec`, `GoldenSet`. `declared_label_count` is required on **every** document including restricted ones whose labels are absent — this is the field that lets a partial report state its covered fraction exactly, and it is the mechanism behind SC-016. The manifest states its size as documents and labelled fields **per tier, never merged across tiers** (FR-009, EVA-3, EVA-4, EVA-5, EVA-5a). Depends on T009, T010
- [ ] T012 Implement `load_golden_set()` in `src/docdoc/evaluation/golden.py`, reading JSON manifests and label files, with every load-time refusal of EVA-5b raising `EvaluationError` naming the offender. An authoring error must surface when the golden set loads, never as a document that silently scores zero for reasons nobody can see (FR-014, SC-021). Depends on T011
- [ ] T013 [P] Write `tests/unit/test_golden_set_authoring_errors.py` asserting every fixture from T006 is refused at load with the offender named, and that the well-formed set from T005 loads unchanged. Covers FR-014 and SC-021
- [ ] T014 [P] Write `tests/unit/test_document_provenance_required.py` asserting a document whose `basis` is empty is not admitted, and that a `SYNTHETIC` document missing `generator_id` or `generator_version` is refused. FR-011 is not a formality: a document whose provenance cannot be stated must not enter the dataset, and a synthetic document whose generator is unknown cannot be regenerated, so its labels are unverifiable (EVA-2a, EVA-2b)
- [ ] T015 Implement `src/docdoc/evaluation/predictions.py` with `Stage`, `DocumentPrediction`, `PredictionSet`, and `load_prediction_set()`. `failure_reason` records the typed error's **class name only**, never a value (EVA-9). Depends on T008
- [ ] T016 Implement the prediction-set refusals in `src/docdoc/evaluation/predictions.py`: a prediction for a document the golden set does not contain is refused naming it (FR-005, EVA-10a), and a prediction whose `schema_identity` or `schema_hash` differs from the labels' is refused naming **both sides** (FR-004, EVA-10c). A label written under `invoice@1` says nothing about a result produced under `invoice@2` (ADR-0008). Depends on T015
- [ ] T017 Implement `src/docdoc/evaluation/ordering.py` with `path_key()`, decomposing a field path so entry indices type as integers and entry 2 precedes entry 10, plus the total order `(tier, document_id, path_key, field_path)`. Document why Milestone 5's declaration-order `sort_key` is **not** reused: a dataset spans schemas so there is no single declaration order, and no walk has been run because evaluation reads recorded facts (EVA-26, research.md R5). Keys are computed once per outcome and cached — decomposition is 1.85 µs measured, which is 0.93 ms per 500 fields if recomputed
- [ ] T018 [P] Write `tests/unit/test_ordering.py` asserting `line_items[2].x` precedes `line_items[10].x`, that the order is total over the T005 fixture, and that it is unchanged across a shuffled input
- [ ] T019 Implement `src/docdoc/evaluation/identity.py` with `SCORER_ID`, `SCORER_VERSION`, `options_hash_for_evaluation()`, `golden_set_id_for()`, `prediction_set_id_for()`, and `report_id_for()`, all built on the kernel's existing `canonical_json` / `options_hash_for` (ADR-0002). `prediction_set_id` folds one **validation artifact id per document** plus the failure stage for members that did not process, plus the recorder's id and version — the terminal artifact id transitively covers every earlier stage's inputs under ADR-0003, so re-deriving that set by hand would be a second, weaker copy of a guarantee the chain already gives (EVA-24, research.md R6). `golden_set_id` moves whenever any document, label, or metric-relevant metadata moves, and every report records it (FR-012, EVA-6). Depends on T011, T015
- [ ] T020 Name the report's identity field `report_id` and **not** `artifact_id` in `src/docdoc/evaluation/identity.py`, with a comment stating why: evaluation is not a stage in ADR-0003's chain — it consumes the validation artifact and produces nothing a document-processing stage consumes — so a reader who went looking for it there would not find it. The formula's *shape* is deliberately ADR-0003's, because the argument for content-addressing is the same one (EVA-24). Depends on T019
- [ ] T021 Implement `src/docdoc/evaluation/observe.py` with one structured event per run carrying identities, versions, counts, duration, and the partial flag — and **no** document text, field values, or label values (FR-057, FR-061). Document the boundary the way `src/docdoc/validation/observe.py` does: outcomes carry values by design, logs carry identities, versions, counts, and hashes. "Values never appear anywhere" is the wrong reading of FR-057 and would make outcomes useless
- [ ] T022 [P] Write `tests/contract/test_evaluation_boundaries.py` asserting the layer direction for **both** new packages: `docdoc.evaluation` may not import `docdoc.recording`, neither may be imported by any lower layer, and `lint-imports` passes. This is FR-058 and SC-024, and it is the check that makes FR-003 structural rather than conventional

**Checkpoint**: Vocabularies, models, loaders, ordering, and identity exist. User story work can begin

---

## Phase 3: User Story 1 — Know how good it actually is (Priority: P1) 🎯 MVP

**Goal**: A maintainer scores a committed prediction set against a committed golden set and gets field
accuracy, coverage, missing rate, incorrect rate, and grounding rate — at dataset, document, and
field-path level, every number stating its numerator and denominator, every number traceable to the
comparisons that produced it.

**Independent Test**: Score the T005/T007 fixtures with no credentials and no network; confirm every
metric matches a hand-computed value, that each reports its denominator, and that summing the per-field
outcomes reproduces the dataset totals exactly.

### Comparison

- [ ] T023 [US1] Implement `src/docdoc/evaluation/comparators.py` with `comparators@1` — exact typed equality per `FieldType` — and the **type-identity gate in front of it**: `equal(a, b) := type(a) is type(b) and a == b`. This is the requirement, not defensive coding: `True == 1`, `Decimal(1) == True`, and `1 == 1.0` are all true in Python, and `isinstance` is wrong because `bool` subclasses `int`. Without the gate a boolean label silently matches an integer prediction and the report calls it correct, which is precisely the cross-type coercion FR-024 forbids (EVA-12a)
- [ ] T024 [US1] Add the comparator registry in `src/docdoc/evaluation/comparators.py`, keyed by `FieldType` and versioned, so that a future leniency is a data change and an identity move rather than an `if` inside the scorer (FR-024, EVA-12b). Depends on T023
- [ ] T025 [P] [US1] Write `tests/unit/test_comparators.py` asserting `Decimal("1240.00") == Decimal("1240.0")` compares equal (trailing zeros are representation), that a `bool` label never matches an `int` prediction and vice versa, that `1` never matches `1.0`, and that no normalization, case-folding, trimming, or rounding occurs on strings (FR-023, FR-024, SC-002)
- [ ] T026 [US1] Implement `src/docdoc/evaluation/alignment.py` with `positional@1`, `keyed@1`, `EntryAlignment`, and `GroupOutcome`. The key is read from the **golden set**, never from the schema — a key in the schema would move `schema_hash` under ADR-0008 and FR-004 would then refuse every label already written against it, so the act of improving alignment would invalidate the dataset it was meant to measure (FR-020, research.md R9). Unmatched entries on either side become missing entries and spurious entries
- [ ] T027 [US1] Report the entry-count mismatch on `GroupOutcome` as its own fact in `src/docdoc/evaluation/alignment.py`, not only through the field outcomes it causes: positional alignment downstream of a missing entry produces field-level wreckage that reads as many independent errors instead of one (FR-021, EVA-16a). Depends on T026
- [ ] T028 [P] [US1] Write `tests/unit/test_entry_alignment.py` asserting positional alignment by declared order, keyed alignment where the golden set declares a key, that duplicate key values within one side are refused at load, and that a four-versus-five entry mismatch is reported as its own fact **and** produces the field outcomes it causes (FR-020, FR-021, EVA-13a)
- [ ] T029 [US1] Implement `src/docdoc/evaluation/location.py` with `page_box@1` and the three-valued `LocationAgreement`. The expected page must appear in `GroundingOutcome.pages`; where the label states a box and the outcome carries geometry, the recorded area on that page lying inside the expected box must be ≥ 0.5 of the recorded area. **Containment, not IoU** — a hand-drawn label box is loose and docdoc's geometry is tight on the tokens, so IoU punishes exactly that pairing and would fail a perfectly located value (research.md R9, EVA-14a)
- [ ] T030 [US1] Implement the `NOT_ASSESSABLE` path in `src/docdoc/evaluation/location.py`: when a label states a box and `GroundingOutcome.geometry is None` — the parser supplied none — the result is `NOT_ASSESSABLE`, **never** `DISAGREES`. Milestone 4's GRD-17 already refuses to collapse "no geometry available" into "nothing is there", and reporting a parser's silence as a grounding error would make the mislocation rate a function of which parser ran. These outcomes leave the mislocation denominator and are counted separately (EVA-14b, EVA-14c). Depends on T029
- [ ] T031 [P] [US1] Write `tests/unit/test_location_agreement.py` asserting all three values, that a same-page wrong-location value reads `DISAGREES` while a same-page correct one reads `AGREES`, that a missing-geometry case reads `NOT_ASSESSABLE` and is excluded from the mislocation denominator, and that location agreement is a **separate axis** from the field outcome — never a seventh member of the closed set (FR-038, EVA-14, SC-002)

### Outcomes and the walk

- [ ] T032 [US1] Implement `src/docdoc/evaluation/outcomes.py` with `FieldOutcomeKind` — the six-member closed set — and `FieldOutcome`. Include `redacted`, `expected_hash`, and `predicted_hash` **now**, even though US3 owns the behaviour: adding them later would move every committed report's identity (EVA-11, EVA-15, divergence note 2). Every non-`CORRECT` outcome records both the expected and the predicted value, so a near-miss is diagnosable without re-running anything (FR-026, EVA-15a). Depends on T009
- [ ] T033 [US1] Ensure `FieldOutcome` copies `grounding_status` and `grounding_score` from Milestone 4 and never recomputes them, and that **nothing** on the model reads `model_confidence` (FR-027, FR-028, EVA-15b, EVA-15c). Depends on T032
- [ ] T034 [US1] Implement the scoring walk in `src/docdoc/evaluation/score.py`: for each golden-set document, resolve every label to exactly one `FieldOutcomeKind`, emit `UNLABELED` for predicted paths the golden set does not label, and emit `UNEVALUATED` for a document with no prediction (FR-025, EVA-11). Depends on T023, T026, T029, T032
- [ ] T035 [US1] Implement the failed-document path in `src/docdoc/evaluation/score.py`: a document whose prediction carries a `failed_stage` has its labelled fields counted as `MISSING` and is counted as a processing failure — **never excluded from any denominator**, so that failing on hard documents can never improve a score (FR-037, EVA-9a). Depends on T034
- [ ] T036 [P] [US1] Write `tests/unit/test_unlabeled_excluded.py` asserting a predicted field the golden set does not label is counted, reported, and enters **zero** accuracy denominators, and is never assumed correct or assumed wrong (FR-036, SC-007, EVA-8b)

### Metrics

- [ ] T037 [US1] Implement `src/docdoc/evaluation/definitions.py` with `metric_definitions@1` as **data**: the numerator and denominator of each metric, expressed as a table rather than as formulas embedded in the scorer. The denominators are the part a future contributor will want to "improve" when a number looks bad, and putting them behind a version turns that edit into a visible, comparison-breaking act (FR-035, EVA-17, research.md R2). The table covers at minimum the five the constitution requires — field accuracy, coverage, missing rate, incorrect rate, grounding rate — each with the numerator and denominator it is computed from (FR-029)
- [ ] T038 [US1] Implement `MetricValue` in `src/docdoc/evaluation/metrics.py` carrying `value`, `numerator`, `denominator`, and `averaging`. A zero denominator yields `None`, **never `0.0`** — a rate of zero reads as total failure and there was no question asked (FR-032, EVA-18). Depends on T037
- [ ] T039 [US1] Implement micro and macro averaging in `src/docdoc/evaluation/metrics.py`, both reported and both labelled wherever they can differ (FR-031). The macro side **must** carry `documents_averaged` and `documents_undefined`: a document with an undefined per-document metric cannot enter a mean, and excluding it silently makes the macro number describe an unstated subset — FR-015's failure at a different scale, invisible unless the count travels with the number (EVA-18a, research.md R3). Depends on T038
- [ ] T040 [US1] Implement `DatasetMetrics` and `DocumentScore` in `src/docdoc/evaluation/metrics.py`, reporting every metric at dataset, document, and **per-field-path** level. A single dataset number is not sufficient evidence under Principle IX (FR-030, EVA-19, EVA-20). Depends on T039
- [ ] T041 [US1] Aggregate the grounding rate in `src/docdoc/evaluation/metrics.py` from Milestone 4's recorded `GroundingCounts` — `exact`, `fuzzy`, `ungrounded`, with `not_applicable` outside the denominator — and **never** recompute it from outcomes. This feature must not define a second grounding rate (FR-033, EVA-17d). Depends on T039
- [ ] T042 [US1] Carry Milestone 5's `ValidationCounts` and `Verdict` distribution into the report in `src/docdoc/evaluation/metrics.py`, reused not recomputed, so that "the extraction was wrong" and "validation caught it" stay two independently visible facts. A validator rejecting a wrong value is a success of validation, not an improvement in extraction accuracy (FR-034). Depends on T040
- [ ] T043 [P] [US1] Write `tests/unit/test_metric_definitions.py` asserting all five metrics against **hand-computed literals** at dataset, document, and field-path level. The expected values are written as literals, not derived by the code under test — a test that recomputed them with the function it was testing would pass on any consistent mistake (SC-003)
- [ ] T044 [P] [US1] Write `tests/unit/test_undefined_is_not_zero.py` asserting every metric with an empty denominator reports `None` and none reports `0.0`, including the empty golden set and the golden set where every field is unlabeled (FR-032, SC-005)
- [ ] T045 [P] [US1] Write `tests/unit/test_failures_count.py` asserting that a fixture whose hardest document fails to process scores **lower** than the same dataset with that document removed. This is the single most important test in the feature: the failure it guards against — laundering a crash into an accuracy improvement by shrinking the denominator — is invisible in any individual number (FR-037, SC-006)
- [ ] T046 [P] [US1] Write `tests/unit/test_grounding_rate_reused.py` asserting the reported grounding rate equals the one computed from the aggregated `GroundingCounts`, that `not_applicable` is outside the denominator, and that no second definition exists anywhere in the package (FR-033)

### The report

- [ ] T047 [US1] Implement `src/docdoc/evaluation/report.py` with `EvaluationProvenance`, `EvaluationOptions`, `PartialDeclaration`, and `EvaluationReport`. Include the `partial` field **now**, even though US3 owns the behaviour: FR-001 forbids a partial report that is not marked partial, and a field added later would mean every US1-era report silently claimed to be complete (divergence note 3). Depends on T040
- [ ] T048 [US1] Implement the provenance refusal in `src/docdoc/evaluation/report.py`: a run that cannot record **every** item FR-040 lists is refused rather than reported. A metric whose origin is unknown is the vague AI quality claim Principle IX rejects as evidence, and recording a null would be that claim wearing a field name (FR-041, EVA-21a). Depends on T047
- [ ] T049 [US1] Implement `src/docdoc/evaluation/redact.py` with tier-driven disclosure: an outcome on a `RESTRICTED` document carries `expected_hash` / `predicted_hash` with the values `None` and `redacted=True`, and the report names the redacted tiers. The choice is a property of the **dataset, not of the caller's diligence** (FR-056, EVA-25). Behaviour test is T070 in US3. Depends on T032, T047
- [ ] T050 [US1] Wire `evaluate(golden_set, prediction_set, *, options=None) -> EvaluationReport` in `src/docdoc/evaluation/__init__.py`, returning **exactly one** report or raising `EvaluationError`, never a partial report that is not marked partial (FR-001, contracts §2). Depends on T034, T040, T047, T048, T019
- [ ] T051 [US1] Assert in `src/docdoc/evaluation/score.py` that every aggregate is reachable from the outcomes that produced it without re-running, and that the outcomes are emitted in the total order of T017 (FR-043, FR-055, EVA-26). Depends on T050

### Adversarial tests for User Story 1

- [ ] T052 [P] [US1] Write `tests/unit/test_evaluation_refusals.py` asserting five refusals, each naming **both sides**: a prediction for a document the golden set does not contain; a differing `schema_identity`; a differing `schema_hash`; a run that cannot record a provenance field; and a restricted bundle whose label count disagrees with the manifest (FR-004, FR-005, FR-041, SC-010)
- [ ] T053 [P] [US1] Write `tests/unit/test_inputs_unchanged.py` asserting the golden set, the labels, and the predictions are **byte-identical** before and after evaluation, by hashing the files either side of a run (FR-006, SC-008)
- [ ] T054 [P] [US1] Write `tests/unit/test_no_model_is_asked.py` asserting no adapter is constructed and no model is called anywhere in `docdoc.evaluation` — including for the question of whether a predicted value *means* the same as the expected one. A model judging its own output is the failure Principle II forbids for grounding, and it is no more acceptable when the subject is accuracy (FR-008)
- [ ] T055 [P] [US1] Write `tests/unit/test_model_confidence_unread.py` asserting that varying `ExtractedValue.model_confidence` across its whole range moves no outcome, no metric, and no `report_id`. Untrusted upstream (ADR-0004), untrusted here (FR-028)
- [ ] T056 [P] [US1] Write `tests/unit/test_evaluation_logging.py` asserting exactly one structured event per run carrying identities, versions, counts, duration, and the partial flag, and that **no** document text, field value, or label value appears in log output over the whole golden set — while outcomes carry them by design (FR-057, FR-061, SC-017)
- [ ] T057 [P] [US1] Write `tests/property/test_metrics_reconcile.py` with Hypothesis generating datasets and prediction sets, asserting that summing the per-field outcomes reproduces **every** reported aggregate and that `coverage + missing_rate + unevaluated_rate == 1` exactly over the value labels. This is the test that catches a denominator edit that looked right (SC-004, EVA-17c)
- [ ] T058 [P] [US1] Write `tests/property/test_report_determinism.py` asserting byte-identical reports and `report_id`s for fixed inputs, run under two `PYTHONHASHSEED` values as `.github/workflows/ci.yml` already does for Milestone 4. `PYTHONHASHSEED` randomises string hashing, which is what makes a dict-order dependency show up on someone else's machine and not on yours (FR-043, SC-009)
- [ ] T059 [P] [US1] Write `tests/property/test_report_identity_sensitivity.py` asserting `report_id` moves when the scorer version, a comparator, the metric definition version, the dataset, or the prediction set changes, **and does not move** when something that cannot affect a number changes — a label's `labeler` or `labeled_at`. Both directions, because an identity that moves on everything is as useless as one that moves on nothing (FR-042, SC-012, EVA-6)
- [ ] T060 [P] [US1] Write `tests/unit/test_no_rederivation.py` asserting that scoring calls **none** of `docdoc.extraction.extract`, `docdoc.grounding.ground`, or `docdoc.validation.validate` — patched to raise — and that re-evaluating the same inputs leaves the prior report object untouched and produces a new one with its own provenance. Evaluation reads recorded facts; a stage that quietly recomputed one would be reporting on a pipeline nobody ran (FR-002, FR-044, EVA-9b, EVA-23b)
- [ ] T061 [P] [US1] Write `tests/unit/test_scores_never_averaged.py` asserting that no exact and fuzzy `grounding_score` is ever compared, summed, or averaged anywhere in the report, and that where scores surface they surface per outcome and per tier. An exact score is `1.0` by definition while a fuzzy score is a measured similarity, so a mean over both is a number with no meaning (FR-039, ADR-0004, ADR-0005)

**Checkpoint**: User Story 1 is fully functional and independently testable. This is the MVP

---

## Phase 4: User Story 2 — See the regression before it merges (Priority: P2)

**Goal**: Two reports are compared, and the result says which metrics moved and by how much, lists the
fields that broke and the fields that were fixed, and states which versions differed — so a drop is
attributable to a change rather than merely coincident with it.

**Independent Test**: Score one prediction set twice with one deliberate degradation between them;
confirm the comparison names the moved metrics, lists exactly the changed field outcomes in both
directions, and refuses to compare across a dataset or metric-definition change.

- [ ] T062 [US2] Implement `src/docdoc/evaluation/compare.py` with `Comparison`, carrying per metric `before`, `after`, `delta`, and `judgement`, plus every changed field outcome in both directions (FR-045, EVA-28). Depends on T050
- [ ] T063 [US2] Implement the comparison refusals in `src/docdoc/evaluation/compare.py`: refuse naming **both sides** when `golden_set_id`, a schema identity or hash, or `metric_definition_version` differs, and when one report is partial and the other is not. It must not silently diff numbers that do not mean the same thing (FR-046, EVA-28a, SC-013). Depends on T062
- [ ] T064 [US2] Implement the named grounding regression in `src/docdoc/evaluation/compare.py` as its own field rather than one row among many. The constitution's fourth quality gate treats a fall in grounding rate as blocking, and **a gate cannot read a table** (FR-047, EVA-28b). Depends on T062
- [ ] T065 [US2] Implement provenance diffing in `src/docdoc/evaluation/compare.py`, recording which of `model_id`, `model_version`, `prompt_hash`, `parser_version`, `grounding_version`, `validator_version`, `scorer_version` differ. Without it a reader has a number that moved and a change that happened and no evidence connecting them, which is how a coincidence becomes a conclusion (FR-048, EVA-28). Depends on T062
- [ ] T066 [US2] Implement the undefined-metric judgements in `src/docdoc/evaluation/compare.py`: where either side is `None`, the judgement is `became_defined` or `became_undefined`, never a subtraction. Treating `None` as `0.0` would manufacture a regression out of a dataset that grew a label (EVA-28c). Depends on T062
- [ ] T067 [US2] Ensure `compare()` states what moved and **decides nothing** about it in `src/docdoc/evaluation/compare.py` — the per-metric judgement is `improved` / `unchanged` / `regressed` and no more. Whether a build fails is policy configured on top of this output; a comparison that also decided would bury the decision inside the thing being measured (FR-049, EVA-28d). Depends on T062
- [ ] T068 [P] [US2] Write `tests/unit/test_comparison_judgements.py` asserting, on a fixture with one deliberate degradation, that the comparison names 100% of the changed field outcomes in both directions and **zero** unchanged ones, that a fall in grounding rate appears as a named regression, that provenance differences are recorded, that incomparable reports are refused naming both sides, and that an undefined-to-defined transition is not reported as a delta (FR-045 … FR-049, SC-013, SC-014, SC-015)

**Checkpoint**: User Stories 1 and 2 both work independently

---

## Phase 5: User Story 3 — Run the whole evaluation with nothing but the repository (Priority: P3)

**Goal**: A first-time contributor clones docdoc, installs it, and runs the evaluation with no
credentials, no network, and no customer documents — getting real metrics over a real golden set, and
being told exactly what was skipped rather than handed a smaller denominator that looks like a full
result.

**Independent Test**: With credentials removed and the network disabled, run the documented evaluation
example end to end; confirm it produces a full report over the public tier, and that a run missing the
restricted tier names the absent documents and marks the report partial.

- [ ] T069 [US3] Implement restricted-tier resolution in `src/docdoc/evaluation/golden.py`: a caller-supplied bundle is resolved against the manifest by `blob_sha256`, and a bundle whose label count disagrees with `declared_label_count` is refused. A bundle silently short of its declaration is a smaller denominator wearing a full report's clothes (EVA-5a, EVA-5b). Depends on T012
- [ ] T070 [US3] Implement `PartialDeclaration` population in `src/docdoc/evaluation/score.py`: name the skipped documents and tiers and state the covered fraction as **exact integers** — `covered_labels` of `declared_labels`, read from the manifest's committed counts. A metric computed over an unannounced subset is the failure this requirement exists to prevent (FR-015, EVA-27, EVA-27a). Depends on T047, T069
- [ ] T071 [P] [US3] Write `tests/unit/test_partial_reports.py` asserting that a run without the restricted bundle is marked partial, names what it skipped, states the covered fraction exactly, and that a partial report compared against a full one is refused (FR-015, SC-016, EVA-28a)
- [ ] T072 [P] [US3] Write `tests/unit/test_redaction.py` asserting that under a restricted tier **zero** field values appear in the report, that 100% of the affected outcomes carry a hash instead, that the substitution is stated in the report, and that the choice follows the **tier** rather than any caller argument (FR-056, SC-018, EVA-25)
- [ ] T073 [US3] Implement `record_predictions()` in `src/docdoc/recording/record.py`, running parse → extract → ground → validate per document and recording the result, with a document that fails at any stage recorded with its `failed_stage` and the typed error's class name — **never dropped**, because a dropped failure becomes an accuracy improvement (FR-003, EVA-9a, contracts §7). Depends on T015
- [ ] T074 [US3] Build the committed public tier under `datasets/mvp/` — `manifest.json`, `documents/`, `labels/`, `predictions/` — spanning at least two schemas, produced with the `echo` adapter so it is reproducible offline, with every document's `origin` recording its basis and every synthetic document its generator and version. **State the dataset's size in the manifest and in `docs/concepts/evaluation.md`**, so the distance to gate 5's target of 50 documents / 500 labelled fields is a number a reader can see rather than a gap nobody mentions (ADR-0009, FR-010, FR-011). Depends on T073
- [ ] T075 [US3] Write `tests/unit/test_scoring_is_offline.py` scoring the committed public tier with `socket.socket` patched to raise. This is what actually asserts FR-007: the forbidden-imports contract of T004 **cannot** prove it, because `adapters/gemini.py` imports `google.genai` inside a function and a package reaching a provider through `docdoc.extraction` is invisible to static analysis. Behaviour, not an import graph (FR-007, research.md R1). Depends on T074
- [ ] T076 [P] [US3] Write `tests/integration/test_evaluate_public_tier.py` running the whole path end to end from a checkout with no credentials and no network, asserting it completes and produces metrics over the committed tier (US3, SC-001, SC-022). Depends on T074
- [ ] T077 [P] [US3] Write `tests/integration/test_record_and_score.py` exercising `record_predictions()` against the `echo` adapter and scoring the result, asserting a failed document is recorded rather than dropped and that its labelled fields count as `MISSING` (FR-003, FR-037). Depends on T073
- [ ] T078 [P] [US3] Write `examples/evaluate_golden_set.py` — the quickstart's 30-second version — printing every metric with its numerator and denominator plus the `report_id`, and running with `uv sync --extra dev` alone (no `--extra google`, no `--extra azure`, no `--extra pdf`). Depends on T074

**Checkpoint**: A contributor with nothing but a checkout can produce a complete evaluation

---

## Phase 6: User Story 4 — Correct a wrong answer once and have it count (Priority: P4)

**Goal**: A reviewer records a correction naming the field, both values, where in the document the right
value is, why, who they are, and when; later it is promoted into the golden set by an explicit act, and
the next evaluation measures against it.

**Independent Test**: Record a correction against a committed result, confirm it carries every
constitutionally required field and alters nothing it annotates, then promote it and confirm the dataset
identity changes and the next run scores against the corrected label.

- [ ] T079 [US4] Implement `Correction` in `src/docdoc/evaluation/corrections.py` carrying the seven constitutionally required fields — `field_path`, `predicted_value`, `corrected_value`, `location`, `reason`, `annotator`, `timestamp` — plus `report_id` and `document_id` naming the exact run and result it corrects, so it cannot be read as a correction of a different version's output (FR-050, FR-051, EVA-29). Depends on T010
- [ ] T080 [US4] Implement `promote(golden_set, corrections) -> GoldenSet` in `src/docdoc/evaluation/corrections.py`, returning a **new** golden set with a new `golden_set_id`. It must not mutate: reports either side of a promotion are then not comparable without the difference being visible, which is FR-046 doing the same job from the other end (FR-053, EVA-29b). Depends on T079, T019
- [ ] T081 [US4] Export `Correction` and `promote` from `src/docdoc/evaluation/__init__.py` and add a module comment stating what this feature deliberately does **not** provide: a review interface, assignment, workflow, queue, or storage service. Principle IX permits corrections as a model and forbids the MVP becoming a review platform (FR-054). Depends on T080
- [ ] T082 [P] [US4] Write `tests/unit/test_corrections.py` asserting a correction carries all seven required fields, alters **zero** of the results it annotates, moves **zero** metrics until promoted, that promotion changes the `golden_set_id`, and that the next run scores against the corrected label (FR-050 … FR-053, SC-019, SC-020, EVA-29a). The "alters zero" assertion covers the extraction, grounding, and validation results it annotates, byte for byte (FR-052)

**Checkpoint**: All four user stories are independently functional

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T083 [P] Write `docs/concepts/evaluation.md` explaining the six outcomes and why none may be collapsed, the two tiers and what a partial report means, the metric definitions and their denominators, why location agreement has three values, and why measuring and improving are separate acts. Follows the shape of `docs/concepts/validation.md`
- [ ] T084 [P] Write `examples/compare_reports.py` scoring one prediction set twice with one deliberate degradation, showing the named grounding regression and the provenance differences (US2, SC-014, SC-015)
- [ ] T085 Add `specs/006-golden-set-evaluation/contracts/evaluation-api.md` and `docs/concepts/evaluation.md` to `DOCUMENTS` in `tests/unit/test_documented_api_references_resolve.py`, so a drift between the contract and the code fails the build rather than waiting for a reader. Must run after the package exports are final (T081)
- [ ] T086 [P] Write `tests/unit/test_evaluation_has_no_document_type_code.py` extending the pattern of `tests/unit/test_extraction_has_no_document_type_code.py` to both new packages: no document-type-specific branch, no `invoice`/`receipt` identifier in a code path. A dataset that could only describe invoices would reintroduce in data the coupling Principle VI forbids in code (FR-013, Principle VI)
- [ ] T087 [P] Write `tests/unit/test_scorer_version_snapshot.py` pinning `SCORER_VERSION` and the `report_id` of the committed fixture. A **change detector, not a breakage detector**: it is cleared by bumping the version or refreshing the snapshot with the classification stated in the commit message, which is the same review obligation ADR-0008 places on schema hashes and ADR-0003 on processor versions (EVA-24, research.md R6, R14)
- [ ] T088 [P] Write `tests/perf/test_evaluation_perf.py` (marked `perf`) asserting scoring the target-size dataset completes in < 50 ms excluding load and < 500 ms end to end including load. If load dominates beyond the bound, the fix is a leaner on-disk prediction form, **not** a relaxed bound (research.md R13)
- [ ] T089 Update `README.md`: Milestone 6 from `Next` to `Done` in the roadmap, the status line, a "What it does today" block showing a scored report, and the documentation list gaining `docs/concepts/evaluation.md`
- [ ] T090 Update `CHANGELOG.md` under `[Unreleased]` with the evaluation layer, the two-tier dataset, the five metrics and their denominators, and a **"Known gaps, recorded rather than hidden"** entry stating the committed dataset's size against gate 5's target of 50 documents / 500 labelled fields, and that the gate stays advisory. Follows the shape Milestone 4's entry set
- [ ] T091 [P] Write `tests/unit/test_base_install_excludes_evaluation_data.py` asserting the base install requires neither the golden set nor any new dependency: `datasets/` is outside `[tool.hatch.build.targets.wheel]`'s `packages`, `[project].dependencies` is unchanged at `pydantic` + `rapidfuzz`, and importing `docdoc.evaluation` pulls in no provider SDK. FR-059 currently holds by build configuration rather than by rule, which is exactly the kind of guarantee that erodes silently when someone adds a data file (FR-059)
- [ ] T092 Extend `docs/concepts/evaluation.md` with the **authoring path**: how a maintainer adds a document and its labels to the golden set as data, with one complete worked example of a manifest entry and a label file. SC-023 requires this to be doable without reading the implementation and without writing code; if a reader must open Python to know what to write, FR-022 is not met and the format is the defect, not the maintainer (FR-022, SC-023). Depends on T081, T074
- [ ] T093 Run the whole `quickstart.md` — all nine scenarios — and confirm none skips, none needs a credential, and `lint-imports` rejects `docdoc.evaluation → docdoc.recording`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup — **BLOCKS all user stories**
- **US1 (Phase 3)**: Depends on Foundational. The MVP
- **US2 (Phase 4)**: Depends on US1's `evaluate()` (T050) — it compares two reports, so it cannot precede the thing that produces one
- **US3 (Phase 5)**: Depends on Foundational; T070 depends on US1's report model (T047)
- **US4 (Phase 6)**: Depends on Foundational; independent of US2 and US3
- **Polish (Phase 7)**: Depends on all desired stories

### User Story Dependencies

- **US1 (P1)** — no dependency on another story. Ships alone
- **US2 (P2)** — genuinely depends on US1, unlike most stories in this template. A comparison of two
  reports cannot exist before a report does
- **US3 (P3)** — its *data* work (T073, T074) is independent of US1 and can proceed in parallel; its
  *partial-report* work (T070) needs US1's report model
- **US4 (P4)** — independent of US2 and US3

### Parallel Opportunities

- T005, T006, T007 (fixtures) — three files, no shared state
- T009, T010 (vocabulary modules) — `tiers.py` and `labels.py` are independent
- T013, T014, T018, T022 — four test files against Phase 2 code
- T025, T028, T031, T036 — US1 comparison tests, four files
- T043, T044, T045, T046 — US1 metric tests, four files
- T052 … T061 — ten adversarial and property tests, all separate files
- T071, T072, T076, T077, T078 — US3 tests and example
- T083, T084, T086, T087, T088, T091 — polish, six files

### Parallel Example: User Story 1 adversarial tests

```bash
Task: "tests/unit/test_evaluation_refusals.py"
Task: "tests/unit/test_inputs_unchanged.py"
Task: "tests/unit/test_no_model_is_asked.py"
Task: "tests/unit/test_model_confidence_unread.py"
Task: "tests/unit/test_evaluation_logging.py"
Task: "tests/property/test_metrics_reconcile.py"
Task: "tests/property/test_report_determinism.py"
Task: "tests/property/test_report_identity_sensitivity.py"
Task: "tests/unit/test_no_rederivation.py"
Task: "tests/unit/test_scores_never_averaged.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1: Setup — T001 … T007
2. Phase 2: Foundational — T008 … T022 (**blocks everything**)
3. Phase 3: User Story 1 — T023 … T061
4. **STOP and VALIDATE**: quickstart Scenarios 1–5. Every metric matches a hand-computed value, every
   aggregate reconciles, a failing document lowers the score, two runs are byte-identical

At that point the milestone's reason to exist is satisfied: every quality claim in this repository
stops being an assertion. US2, US3, and US4 each add a distinct capability on top.

### Incremental Delivery

1. Setup + Foundational → the vocabularies and loaders exist
2. **US1** → metrics over a golden set (MVP)
3. **US2** → regressions become visible and attributable; the constitution's fourth gate becomes
   enforceable
4. **US3** → a contributor with nothing but a checkout can produce them, and a partial run says so
5. **US4** → a human correction becomes dataset signal

### Parallel Team Strategy

After Phase 2, three tracks run concurrently:

- Developer A: US1 (the critical path — US2 waits on it)
- Developer B: US3's data work, T073 and T074, which needs no US1 code
- Developer C: US4, which is independent of both

---

## Notes

- `[P]` = different files, no dependency on an incomplete task
- Every task names its file path; every user-story task carries its story label
- Verify each test fails before implementing the code it covers
- Commit after each task or logical group
- **No existing layer is edited by any task in this list.** If a task appears to require one, that is a
  finding worth raising rather than a change worth making: the spec's Out of Scope forbids changing
  extraction, grounding, or validation behaviour to improve a metric, because a milestone that both
  measures and improves can report honestly on neither
