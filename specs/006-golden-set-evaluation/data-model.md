# Phase 1 Data Model: Golden-Set Evaluation

**Feature**: `006-golden-set-evaluation` | **Date**: 2026-08-20 | **Plan**: [plan.md](plan.md)

Every model is a frozen pydantic model with `extra="forbid"`, matching Milestones 3–5. `EVA-n` markers
are referenced from `tasks.md` and from the contract.

Two vocabularies are closed and neither may grow without a version bump: the **field outcome**
(EVA-11) and the **location agreement** (EVA-14). They are deliberately separate axes — a value can be
correct and mislocated, or incorrect and perfectly located, and no reported shape may collapse them.

---

## 1. The golden set

### EVA-1 · `Tier`

```text
PUBLIC     = "public"      # vendored in the repository; labels and predictions committed
RESTRICTED = "restricted"  # identity committed; labels and predictions supplied by the caller
```

Closed. ADR-0009. The tier drives disclosure (EVA-25), partiality (EVA-27), and what a checkout
contains.

### EVA-2 · `DocumentOrigin`

| Field | Type | Note |
|---|---|---|
| `kind` | `OriginKind` | `SYNTHETIC` \| `PUBLIC_DOMAIN` \| `LICENSED` \| `RESTRICTED` |
| `basis` | `str` | the basis on which docdoc may use it. Non-empty, always (FR-011) |
| `source` | `str \| None` | where it came from |
| `generator_id` | `str \| None` | required when `kind is SYNTHETIC` (FR-010) |
| `generator_version` | `str \| None` | required when `kind is SYNTHETIC` |

**EVA-2a** — a document whose `basis` is empty is refused at load, naming it. "Found on the internet"
is not a basis, and the check is that a human wrote *something* they are willing to stand behind.

**EVA-2b** — `kind is SYNTHETIC` with either generator field absent is refused. A synthetic document
whose generator is unknown cannot be regenerated, so its labels are unverifiable.

### EVA-3 · `GoldenDocument`

| Field | Type | Note |
|---|---|---|
| `document_id` | `str` | ADR-0002's document identity |
| `blob_sha256` | `str` | ADR-0002's blob identity |
| `tier` | `Tier` | |
| `origin` | `DocumentOrigin` | |
| `schema_identity` | `str` | `name@version` |
| `schema_hash` | `str` | ADR-0008 |
| `path` | `str \| None` | repo-relative; **required for `PUBLIC`, forbidden for `RESTRICTED`** |
| `declared_label_count` | `int` | see EVA-5a |

### EVA-4 · `EntryKeySpec`

| Field | Type | Note |
|---|---|---|
| `group_path` | `str` | the repeating group, e.g. `line_items` |
| `key_field` | `str` | a scalar field **of that group**, e.g. `sku` |

Declared by the golden set, never by the schema (FR-020, research R9). **EVA-4a** — a `key_field` that
is not a scalar field of `group_path` under that document's schema is refused at load, naming both.

### EVA-5 · `GoldenSet`

| Field | Type | Note |
|---|---|---|
| `documents` | `tuple[GoldenDocument, ...]` | sorted by `document_id` |
| `labels` | `dict[str, tuple[Label, ...]]` | keyed by `document_id` |
| `entry_keys` | `tuple[EntryKeySpec, ...]` | |
| `golden_set_id` | `str` | EVA-6 |

**EVA-5a** — every document carries `declared_label_count`, **including restricted ones whose labels
are not present**. This is what lets a partial report state its covered fraction exactly (FR-015,
research R7). A supplied restricted bundle whose label count disagrees with the declaration is refused.

**EVA-5b** — refused at load, each naming the offender (FR-014): a duplicated `document_id`; a label
whose `field_path` does not resolve under that document's schema; a label whose value the declared
`FieldType` cannot carry; a restricted bundle whose `blob_sha256` is absent from the manifest.

**EVA-5c** — the golden set is never mutated by anything in this feature (FR-006). Promotion returns a
new one (EVA-29).

### EVA-6 · `golden_set_id`

`sha256` over `canonical_json` of the manifest and every label, including the declared restricted
counts. Moves whenever any document, any label, any entry key, or any metric-relevant metadata moves
(FR-012). Does **not** move when a label's `labeler` or `labeled_at` changes — those cannot change a
metric, and an identity that moves on them would break comparison for a typo fix.

---

## 2. Labels

### EVA-7 · `ExpectedLocation`

| Field | Type | Note |
|---|---|---|
| `page` | `int` | 0-based, matching `GroundingOutcome.pages` |
| `bbox` | `BBox \| None` | the kernel's `BBox`, reused |

**EVA-7a** — a location is never a text offset. Offsets into `Document.text` move when a parser changes
how it extracts text, so a label pinned to one turns a parser upgrade into mass mislocation that is not
mislocation (FR-038, research R9).

### EVA-8 · `Label`

| Field | Type | Note |
|---|---|---|
| `field_path` | `str` | Milestone 3's form, entry indices included: `line_items[2].amount` |
| `expectation` | `Expectation` | `VALUE` \| `ABSENT` |
| `value` | `Any` | present iff `expectation is VALUE` |
| `location` | `ExpectedLocation \| None` | optional (FR-018) |
| `labeler` | `str \| None` | |
| `labeled_at` | `datetime \| None` | |

**EVA-8a** — `expectation is ABSENT` with a `value` set, or `VALUE` with none, is refused at load.

**EVA-8b** — a field path carrying no label is `UNLABELED` (EVA-11) and enters no accuracy denominator
(FR-036). It is never assumed correct and never assumed wrong.

---

## 3. Predictions

### EVA-9 · `DocumentPrediction`

| Field | Type | Note |
|---|---|---|
| `document_id` | `str` | |
| `extraction` | `ExtractionResult \| None` | Milestone 3, as recorded |
| `grounding` | `GroundingResult \| None` | Milestone 4, as recorded |
| `validation` | `ValidationResult \| None` | Milestone 5, as recorded |
| `failed_stage` | `Stage \| None` | `PARSE` \| `EXTRACT` \| `GROUND` \| `VALIDATE`; `None` when complete |
| `failure_reason` | `str \| None` | the typed error's class name, never a value |

**EVA-9a** — `failed_stage is not None` means the document processed partially. Its labelled fields
count as `MISSING`, not as excluded (FR-037). Failing on hard documents can never raise a score.

**EVA-9b** — nothing in this feature re-derives, re-scores, re-grounds, or re-validates any of these
three (FR-002). They are read.

### EVA-10 · `PredictionSet`

| Field | Type | Note |
|---|---|---|
| `predictions` | `dict[str, DocumentPrediction]` | keyed by `document_id` |
| `recorder_id` | `str` | |
| `recorder_version` | `str` | |
| `prediction_set_id` | `str` | research R6 |

**EVA-10a** — a prediction for a document the golden set does not contain is **refused**, naming it
(FR-005). The two sides do not describe the same thing.

**EVA-10b** — a golden-set document with no prediction is `UNEVALUATED`, named, and removed from no
denominator (FR-005).

**EVA-10c** — a prediction whose `schema_identity` or `schema_hash` differs from the label's is
refused, naming both sides (FR-004, ADR-0008).

---

## 4. Comparison

### EVA-11 · `FieldOutcomeKind` — closed (FR-025)

| Value | Means |
|---|---|
| `CORRECT` | label and prediction agree — a matching value, or a correctly reported absence |
| `INCORRECT` | label states a value, prediction states a different one |
| `MISSING` | label states a value, prediction reports absent |
| `SPURIOUS` | label states absence, prediction states a value |
| `UNLABELED` | prediction exists, golden set says nothing |
| `UNEVALUATED` | no prediction for this document at all |

Six, and no reported shape may collapse two of them. `CORRECT` covers both value-correct and
absence-correct; the split is tracked internally because the two have different denominators (EVA-17),
and is recoverable from `Label.expectation`.

### EVA-12 · `Comparator`

| Field | Type |
|---|---|
| `field_type` | `FieldType` |
| `version` | `str` — `"exact@1"` for every type in the MVP |

**EVA-12a** — `equal(a, b) := type(a) is type(b) and a == b`. The type gate is the requirement, not
defensive coding: `True == 1` and `Decimal(1) == True` are both true in Python, and FR-024 forbids
coercing across types to make a value match. `isinstance` is wrong here — `bool` subclasses `int`.

**EVA-12b** — no normalization, case-folding, trimming, rounding, or cross-type coercion. Any leniency
is a new comparator with a new version, recorded next to every metric it affected.

### EVA-13 · `EntryAlignment`

| Field | Type |
|---|---|
| `group_path` | `str` |
| `policy` | `"positional@1"` \| `"keyed@1"` |
| `key_field` | `str \| None` |

**EVA-13a** — duplicate key values within one side are refused at load. Ambiguous alignment silently
picks one, which is the class of thing this feature exists to end.

### EVA-14 · `LocationAgreement` — closed, and a separate axis from EVA-11

| Value | Means |
|---|---|
| `AGREES` | expected page present, and the box test passed or no box was stated |
| `DISAGREES` | grounded, but not where the label says |
| `NOT_ASSESSABLE` | label states a box, the parser supplied no geometry |

**EVA-14a** — `page_box@1`: the expected page must appear in `GroundingOutcome.pages`; where the label
states a box and the outcome carries geometry, the recorded area on that page lying inside the expected
box must be ≥ **0.5** of the recorded area. Containment, not IoU — a hand-drawn box is loose and a tight
correct box inside it would fail IoU (research R9).

**EVA-14b** — `NOT_ASSESSABLE` is never reported as `DISAGREES`. `GroundingOutcome.geometry is None`
means the parser supplied none; `()` means geometry exists and covers no tokens. Milestone 4's GRD-17
refuses to collapse those and so does this.

**EVA-14c** — `NOT_ASSESSABLE` outcomes leave the mislocation denominator and are counted separately
(FR-032's convention).

### EVA-15 · `FieldOutcome`

| Field | Type | Note |
|---|---|---|
| `document_id` | `str` | |
| `field_path` | `str` | |
| `kind` | `FieldOutcomeKind` | |
| `expected` | `str \| None` | canonical rendering; `None` when redacted or when correct |
| `predicted` | `str \| None` | as above |
| `expected_hash` | `str \| None` | set instead, when redacted (EVA-25) |
| `predicted_hash` | `str \| None` | as above |
| `redacted` | `bool` | |
| `comparator_version` | `str \| None` | which rule decided it |
| `grounding_status` | `GroundingStatus \| None` | **copied** from Milestone 4 (FR-027) |
| `grounding_score` | `float \| None` | copied; never averaged across tiers (FR-039) |
| `location_agreement` | `LocationAgreement \| None` | `None` when the label states no location |

**EVA-15a** — every non-`CORRECT` outcome records both values, subject to redaction (FR-026). A
near-miss is diagnosable without re-running anything.

**EVA-15b** — whether a value is correct and whether it is grounded are independent and separately
reported (FR-027). A correct but ungrounded value is `CORRECT` here and `ungrounded` in the grounding
rate. Two facts, two numbers.

**EVA-15c** — nothing on this model reads `model_confidence`. It is untrusted upstream (ADR-0004) and
stays untrusted here (FR-028).

### EVA-16 · `GroupOutcome`

| Field | Type |
|---|---|
| `document_id`, `group_path` | `str` |
| `expected_entries`, `predicted_entries` | `int` |
| `missing_entries`, `spurious_entries` | `int` |
| `alignment` | `EntryAlignment` |

**EVA-16a** — an entry-count mismatch is its own reported fact (FR-021), not only the field-level
wreckage positional alignment produces downstream of a missing entry.

---

## 5. Metrics

### EVA-17 · `MetricDefinition` — `metric_definitions@1`

Labels partition into **V** (`expectation is VALUE`) and **A** (`expectation is ABSENT`).

| Metric | Numerator | Denominator |
|---|---|---|
| `field_accuracy` | `CORRECT` | \|V\| + \|A\| |
| `coverage` | value-`CORRECT` + `INCORRECT` | \|V\| |
| `missing_rate` | `MISSING` | \|V\| |
| `incorrect_rate` | `INCORRECT` | \|V\| |
| `grounding_rate` | `exact` + `fuzzy` | `exact` + `fuzzy` + `ungrounded` |

Also reported: `spurious_rate` (`SPURIOUS` / \|A\|), `unevaluated_rate`, `mislocation_rate`
(`DISAGREES` / (`AGREES` + `DISAGREES`)), and the `UNLABELED` count.

**EVA-17a** — `UNEVALUATED` is in every denominator (FR-005, FR-037). Crashing on the hard documents
lowers the score.

**EVA-17b** — `UNLABELED` is in none (FR-036).

**EVA-17c** — the reconciliation identity, asserted as a property (SC-004):

```text
coverage + missing_rate + unevaluated_rate_V == 1        exactly, over V
```

**EVA-17d** — the grounding rate reuses Milestone 4's `GroundingCounts`, `not_applicable` outside the
denominator, and is never recomputed from outcomes (FR-033).

### EVA-18 · `MetricValue`

| Field | Type | Note |
|---|---|---|
| `value` | `float \| None` | `None` when the denominator is zero — **never `0.0`** (FR-032) |
| `numerator`, `denominator` | `int` | every metric states both (FR-029) |
| `averaging` | `Averaging` | `MICRO` \| `MACRO` |
| `documents_averaged` | `int \| None` | macro only |
| `documents_undefined` | `int \| None` | macro only |

**EVA-18a** — a macro-average that does not report how many documents it averaged over describes an
unstated subset. Documents with an undefined per-document metric cannot enter a mean, and excluding
them silently is FR-015's failure at a different scale (research R3).

**EVA-18b** — micro and macro are both reported and both labelled wherever they can differ (FR-031).

### EVA-19 · `DocumentScore`

Per document: its outcomes, its group outcomes, its metrics, its `failed_stage`, and whether it was
evaluated at all.

### EVA-20 · `DatasetMetrics`

Dataset-level metrics with the per-field-path breakdown (FR-030). Every aggregate is reachable from the
outcomes that produced it without re-running (FR-055).

---

## 6. The report

### EVA-21 · `EvaluationProvenance`

`repo_revision`, `golden_set_id`, `prediction_set_id`, `schema_identities`, `schema_hashes`,
`prompt_hash`, `model_id`, `model_version`, `parser_id`, `parser_version`, `grounding_version`,
`validator_version`, `scorer_id`, `scorer_version`, `metric_definition_version`,
`comparator_versions`, `entry_alignment_version`, `location_rule_version`, `options`.

**EVA-21a** — a run that cannot record every one of these is **refused**, not reported (FR-041). A
metric whose origin is unknown is the vague quality claim Principle IX rejects as evidence.

### EVA-22 · `EvaluationOptions`

`metric_definition_version`, `comparator_versions`, `entry_alignment_version`, `location_rule_version`,
`location_threshold`, `include_restricted`. Identity-bearing: folded into `options_hash` (EVA-24).

### EVA-23 · `EvaluationReport`

| Field | Type |
|---|---|
| `outcomes` | `tuple[FieldOutcome, ...]` — total order, EVA-26 |
| `group_outcomes` | `tuple[GroupOutcome, ...]` |
| `document_scores` | `tuple[DocumentScore, ...]` |
| `metrics` | `DatasetMetrics` |
| `validation_verdicts` | `dict[Verdict, int]` — reused from Milestone 5 (FR-034) |
| `partial` | `PartialDeclaration \| None` |
| `redacted_tiers` | `tuple[Tier, ...]` |
| `provenance` | `EvaluationProvenance` |
| `report_id` | `str` |

**EVA-23a** — exactly one report, or an explicit error. Never a partial report that is not marked
partial (FR-001).

**EVA-23b** — re-evaluating produces a new report with its own provenance and mutates, overwrites, and
reinterprets nothing (FR-044).

### EVA-24 · `report_id`

`sha256(golden_set_id ‖ prediction_set_id ‖ SCORER_ID ‖ SCORER_VERSION ‖ options_hash)`.

Named `report_id` and **not** `artifact_id`: evaluation is not a stage in ADR-0003's chain — it consumes
the validation artifact and produces nothing a document-processing stage consumes. The formula's shape
is deliberately ADR-0003's, because the argument for content-addressing is the same; only chain
membership is not claimed (research R6).

**EVA-24a** — identical inputs produce an identical id; anything that can move a number moves it
(FR-042, SC-012).

### EVA-25 · Redaction

Driven by `Tier`, not by the caller (FR-056). An outcome on a `RESTRICTED` document carries
`expected_hash` / `predicted_hash` and `redacted=True`, with the values `None`; the report names the
redacted tiers. FR-026 is satisfied by the hash under this rule — a restricted near-miss is diagnosable
as *different* but not *how*, which is the trade the dataset's terms impose.

### EVA-26 · Ordering

`(tier, document_id, path_key(field_path), field_path)`, where `path_key` types entry indices as
integers so that entry 2 precedes entry 10. Total, and independent of dict order, hash seed, and
platform (FR-043). Milestone 5's declaration-order `sort_key` is deliberately not reused: a dataset
spans schemas, so there is no single declaration order, and no walk has been run (research R5).

### EVA-27 · `PartialDeclaration`

| Field | Type |
|---|---|
| `skipped_documents` | `tuple[str, ...]` |
| `skipped_tiers` | `tuple[Tier, ...]` |
| `covered_documents`, `declared_documents` | `int` |
| `covered_labels`, `declared_labels` | `int` |

**EVA-27a** — the declared counts come from the manifest, which commits them for the restricted tier
even though it cannot commit its labels (EVA-5a). That is what makes the covered fraction exact rather
than estimated (SC-016).

---

## 7. Comparison and corrections

### EVA-28 · `Comparison`

Per metric: `before`, `after`, `delta`, `judgement` ∈ {`improved`, `unchanged`, `regressed`,
`became_defined`, `became_undefined`}. Plus every changed field outcome in both directions, and
`provenance_differences` naming which of model, prompt hash, parser, grounding, validator, or scorer
version differ (FR-048).

**EVA-28a** — refused, naming both sides, when `golden_set_id`, a schema identity or hash, or
`metric_definition_version` differs, and when one side is partial and the other is not (FR-046).

**EVA-28b** — a fall in the grounding rate is surfaced as its own named regression, not as one row
among many (FR-047). The constitution's fourth gate treats it as blocking and a gate cannot read a
table.

**EVA-28c** — `None` is not zero. A delta against an undefined metric is `became_defined` /
`became_undefined`, never a subtraction that manufactures a regression from a dataset that grew a label.

**EVA-28d** — the comparison states what moved and decides nothing about it (FR-049).

### EVA-29 · `Correction`

`field_path`, `predicted_value`, `corrected_value`, `location`, `reason`, `annotator`, `timestamp` —
the seven the constitution requires — plus `report_id` and `document_id` naming the exact run and
result it corrects (FR-051).

**EVA-29a** — a correction alters nothing it annotates (FR-052) and moves no metric until promoted.
There is no path by which it could: the scorer reads labels and never reads corrections.

**EVA-29b** — `promote(golden_set, corrections) -> GoldenSet` returns a **new** golden set with a new
`golden_set_id` (FR-053). Reports either side of a promotion are then not comparable without the
difference being visible, which is EVA-28a doing the same job from the other end.

---

## 8. Errors

### EVA-30 · `EvaluationError(DocdocError)`

One error type, carrying both sides of whatever mismatch caused it as attributes rather than only
interpolated into prose — the shape `ValidationError` already establishes, for the reason it already
states: a caller that must parse a message to learn which side was wrong will not do it.

`dataset`, `document_id`, `field_path`, `expected`, `actual`.

**EVA-30a** — never retried. There is no transient failure mode in a deterministic, offline
computation (FR-060), and the constitution's error model permits retries for LLM and network calls
only.

**EVA-30b** — an error is a statement about the *request* (these artifacts did not come from each
other; this label was written under a different schema). A **field outcome** is a statement about the
document. Collapsing them is how a mismatched pair produces a confident report.
