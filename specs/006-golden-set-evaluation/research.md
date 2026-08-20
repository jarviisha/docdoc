# Phase 0 Research: Golden-Set Evaluation

**Feature**: `006-golden-set-evaluation` | **Date**: 2026-08-20 | **Plan**: [plan.md](plan.md)

Fourteen questions the spec left to the plan, plus the two the clarification session answered and this
document turns into mechanism. Measurements are labelled as measurements; estimates are labelled as
estimates. Where a number is derived rather than observed, the derivation is shown.

---

## R1 — Where evaluation sits, and how FR-003's separation is made structural

**Decision.** Two new top-level packages, not one:

- `docdoc.evaluation` — the scorer, the dataset, the labels, the metrics, the comparison, the
  corrections. Added to the `import-linter` layers contract above `docdoc.validation`.
- `docdoc.recording` — the opt-in step that runs the pipeline and produces a prediction set. Added
  **above** `docdoc.evaluation`.

Layers become:

```text
docdoc.recording > docdoc.evaluation > docdoc.validation > docdoc.grounding
                 > docdoc.extraction > docdoc.ingest > docdoc.kernel
```

**Rationale.** FR-003 says recording "MUST NOT be part of evaluation, MUST NOT be required to score".
Inside one package that is a naming convention. As two layers it is the layers contract: `recording`
may import `evaluation` (it constructs a `PredictionSet`), and `evaluation` importing `recording`
fails the build. FR-058 asks for exactly this to be machine-checked, and the ordering falls out of the
data flow rather than being imposed on it — the recorder produces the type the scorer consumes.

**The uncomfortable finding that shaped this.** The obvious alternative was one package plus a
forbidden-imports contract on `docdoc.evaluation` naming `socket`, `httpx`, `google`, and the rest,
mirroring what `docdoc.grounding` and `docdoc.validation` already carry. That contract would **pass
whether or not the recorder were inside it.** `src/docdoc/extraction/adapters/gemini.py` imports
`google.genai` inside a function (line 182), and `adapter_registry` imports `GeminiAdapter` inside a
function (line 173) — deliberately, so that the SDK stays out of the import graph. The provider is
therefore invisible to static import analysis, and a forbidden contract cannot see a package that
reaches a provider through `docdoc.extraction`.

This is not a defect in the existing contracts: as *preventive* checks they still fire the moment
someone writes `import httpx` in `docdoc.grounding`, which is what they were for. But it means FR-007
cannot be proved for evaluation the way it was proved for grounding. Three things are needed instead,
and all three are carried:

1. The **layers contract**, forbidding `evaluation → recording`. This is the edge that is real and
   statically visible, and it is docdoc-internal.
2. The same **forbidden-imports list** on `docdoc.evaluation`, kept for its preventive value and not
   mistaken for proof.
3. A **runtime test** that scores the committed public tier with `socket.socket` patched to raise, so
   that "the scorer touches no network" is asserted against behaviour rather than against a graph
   (R14).

**Alternatives rejected.** *One package with the contract scoped to submodules by name*
(`source_modules = ["docdoc.evaluation.score", …]`) — a new module added later escapes the contract
silently, which is the failure mode of every allowlist nobody updates. *Recording as a script under
`tools/`* — it needs the same models and the same typed errors as the rest of the feature, and a
script is not importable, testable, or version-bearing.

---

## R2 — The five metrics: numerators, denominators, and why they are versioned data

**Decision.** `metric_definitions@1`, pinned as follows. Labels partition into **V** (labels stating a
value) and **A** (labels stating an expected absence). `unlabeled` is in neither.

| Metric | Numerator | Denominator |
|---|---|---|
| **field accuracy** | `correct` (value-correct + absence-correct) | \|V\| + \|A\| |
| **coverage** | `correct_value` + `incorrect` | \|V\| |
| **missing rate** | `missing` | \|V\| |
| **incorrect rate** | `incorrect` | \|V\| |
| **grounding rate** | `exact` + `fuzzy` | `exact` + `fuzzy` + `ungrounded` |

Reported alongside, and not constitutionally required: **spurious rate** (`spurious` / \|A\|),
**unevaluated rate**, **mislocation rate** (R9), and the **unlabeled count** (FR-036).

**The reconciliation identity**, which a property test asserts for every generated dataset (R14):

```text
coverage + missing_rate + unevaluated_rate_V == 1     exactly, over V
```

because `correct_value + incorrect + missing + unevaluated_V == |V|` by construction — every value
label resolves to exactly one of four states, which is FR-025's closed set restricted to V.

**Rationale for the two decisions inside this table.**

*`unevaluated` is in every denominator.* FR-005 says a golden-set document with no prediction "MUST
NOT be removed from any denominator", and FR-037 says the same for a document that failed to process.
Both exist to stop the same arithmetic: dropping the documents you crashed on raises your score. So a
crash lowers accuracy, lowers coverage, and shows up in its own rate.

*`unlabeled` is in none of them.* FR-036, and the reason is that including it would make accuracy a
function of how completely the dataset happens to be labelled rather than of how well the pipeline
performed.

**Coverage was the one genuinely open choice.** Principle IX names "coverage" without defining it, and
two readings were live:

- **Answer rate over value labels** (adopted): of the fields the truth says hold a value, for how many
  did the run produce one, right or wrong. Standard in IDP, reconciles exactly with missing rate and
  the unevaluated rate over the same denominator, and answers the question a maintainer actually asks
  first — "how much does it even attempt?"
- **Evaluation completeness** (rejected): the fraction of labelled fields that received any outcome.
  Rejected because it is already carried, more precisely, by the partial-report declaration of FR-015
  and by the unevaluated rate, and because a metric that is 1.0 on every healthy run tells a reader
  nothing on the runs that matter.

**Why these are data with a version rather than formulas in code.** FR-035 requires a bump when a
definition, a denominator, or an aggregation rule changes, and FR-046 requires a comparison across
differing versions to be refused. The denominators above are the part most likely to be "improved" by
a future contributor who finds a number unflattering; putting them behind a version is what turns that
edit into a visible, comparison-breaking act instead of a step in a chart nobody can explain.

---

## R3 — Micro and macro, and the denominator a macro-average hides

**Decision.** Every dataset-level metric is reported twice, each explicitly labelled (FR-031):

- **micro** — pool the outcomes across all documents, then divide. One field, one vote.
- **macro** — the mean of the per-document metrics. One document, one vote.

**And the macro-average reports how many documents it averaged over.** A document whose denominator is
zero has an undefined metric (FR-032) and cannot enter a mean; excluding it silently means the macro
number describes an unstated subset. That is FR-015's failure at a different scale, and it is invisible
unless the count travels with the number. So `MetricValue` carries `documents_averaged` and
`documents_undefined` on the macro side, and numerator and denominator on the micro side.

**Rationale.** The two diverge exactly when document sizes differ, which on a golden set they always
do: a one-field receipt and a forty-field invoice are one vote each under macro and 1:40 under micro.
Reporting one alone lets a reader draw the opposite conclusion from the same run.

---

## R4 — Dataset and label serialization: JSON

**Decision.** Manifests, labels, corrections, prediction sets, and reports are **JSON**. No new
dependency; `json` from the standard library, with pydantic doing the validation.

**Rationale.** Three reasons, in order of weight:

1. **The dataset identity is a hash over canonical JSON** (FR-012, R6). Authoring in the same form the
   identity is computed over removes a conversion step where two representations could disagree about
   what was hashed. `canonical_json` already exists in the kernel and already settles key ordering and
   float formatting (ADR-0002).
2. **FR-059** — the base install must not require evaluation-only dependencies. JSON needs none.
3. **FR-014** requires authoring errors to be raised at load. pydantic validates JSON natively and
   names the offending path, which is most of that requirement for free.

**Alternative rejected.** *YAML*, which is materially nicer to author — comments, multi-line strings,
no trailing-comma hazard — and which SC-023 (a maintainer adds a document by following one example)
would benefit from. Rejected because it adds a parser to the trust path of numbers that are meant to
gate merges, and because the round-trip to canonical JSON for hashing introduces a second
representation of the same dataset. Authoring comfort did not outweigh either. Recorded rather than
dismissed: if extending the golden set proves to be the bottleneck it is the first thing to revisit,
behind a `docdoc[eval]` extra.

---

## R5 — A total order across a dataset that spans schemas

**Decision.** Field outcomes are ordered by `(tier, document_id, path_key(field_path), field_path)`,
where `path_key` decomposes a path into segments and types entry indices as integers:

```text
"line_items[10].amount"  ->  (("k","line_items"), ("i",10), ("k","amount"))
```

`field_path` is the final tie-break so the order is total even if two distinct paths ever decompose
identically.

**Rationale, and why Milestone 5's `sort_key` is not reused.** `docdoc.validation.verdict.sort_key`
orders by the enumeration walk's position — that is, by **schema declaration order** — with the entry
index supplied separately so that entry 10 does not sort before entry 2. That is the right answer for
a result produced under exactly one schema, and Milestone 5's plan records the reasoning. A dataset
spans schemas by requirement (FR-013), so there is no single declaration order to appeal to, and no
walk has been run: evaluation reads recorded facts and never re-derives them (FR-002).

What *is* reused is the lesson rather than the function: the reason `sort_key` exists at all is that
lexicographic ordering puts `[10]` before `[2]`, and that hazard is identical here. The decomposition
above is the minimum that fixes it without inventing a second field-path grammar — the form is
Milestone 3's, produced by `conform.py` and consumed unchanged (FR-017).

**Cost.** Decomposition measured at **1.85 µs** per path. At the target dataset size (500 labelled
fields) that is 0.93 ms if recomputed per comparison, so keys are computed once per outcome and cached.

---

## R6 — Identities: the dataset, the prediction set, and the report

**Decision.** Three content-addressed identities, all built with the kernel's existing
`canonical_json` / `options_hash_for` (ADR-0002's serialization rules):

| Identity | Derived from |
|---|---|
| `golden_set_id` | the manifest: every document's tier, blob hash, schema identity and hash, provenance, declared entry-alignment keys, and every label — including the **declared counts** of restricted-tier labels (R7) |
| `prediction_set_id` | `{document_id: validation_artifact_id}` for every member, sorted, plus the failure stage for members that did not process, plus the recorder's id and version |
| `report_id` | `sha256(golden_set_id ‖ prediction_set_id ‖ SCORER_ID ‖ SCORER_VERSION ‖ options_hash)` |

`options_hash` folds the metric definition version, the comparator versions, the entry-alignment
version, the location-rule version and its threshold, and whether the restricted tier was included.

**`prediction_set_id` leans on one validation artifact id per document deliberately.** ADR-0003 makes
the terminal artifact id transitively cover every result-affecting input of every earlier stage, so
recording the validation artifact id records the parser, the schema, the prompt, the model, the
grounding version, and the validator in one field that cannot drift from them. Re-deriving that set by
hand would be a second, weaker copy of a guarantee the chain already gives.

**The report's identity is named `report_id`, not `artifact_id`.** Evaluation is not a stage in
ADR-0003's chain: it consumes the validation artifact and produces nothing any document-processing
stage consumes, which is the reading the spec's Assumptions already take. Calling the field
`artifact_id` would invite a reader to look for it in that chain and find it absent. The *shape* of
the formula is deliberately ADR-0003's, because the argument for content-addressing is the same one;
only the chain membership is not claimed.

**Consequence, stated so it is not discovered later.** `SCORER_VERSION` must move whenever scoring
output moves for fixed inputs, exactly as `validator_version` does. This carries the same review
obligation ADR-0008 places on schema hashes and ADR-0003 on processor versions: no system detects a
semantic change on its own, so a snapshot test is a change detector and the classification is made by
a human in a commit message (R14).

---

## R7 — Two tiers, and what a partial report has to know about what it is missing

**Decision.** The scorer never opens a document. This falls out of FR-002 — evaluation reads recorded
predictions and does not re-derive anything — and it changes the shape of the tier problem entirely.

What the repository commits:

| | Public tier | Restricted tier |
|---|---|---|
| Manifest entry (tier, blob hash, provenance, schema identity) | committed | **committed** |
| Labels | committed | not committed |
| Prediction artifacts | committed | not committed |
| Declared label counts | committed | **committed** |

A restricted document's *identity and provenance* are public facts; its labels and its predictions
carry values read out of a document nobody may redistribute, so they are not. The caller supplies them
as a bundle; the scorer resolves the bundle against the manifest by blob hash.

**The load-bearing detail is the declared label counts.** FR-015 requires a partial report to state
"the covered fraction of the dataset". Without committed counts for the restricted tier, a checkout
knows how many restricted *documents* exist but not how many labels they carry, so the covered fraction
is unknowable and the report would have to either guess or omit it. Committing the counts — integers,
no values — makes a partial report able to state its own denominator exactly. This is the mechanism
behind SC-016.

**Refusals at load** (FR-014): a duplicated document, an unresolvable field path, a label whose value
the declared type cannot carry, a supplied restricted bundle whose blob hash is not in the manifest, or
a restricted bundle whose label count disagrees with the manifest's declaration. That last one exists
because a bundle silently short of its declared labels is a smaller denominator wearing a full report's
clothes.

**Alternative rejected.** *Committing restricted labels with values hashed.* Superficially attractive —
the dataset would be complete in the repository — but a hashed expected value can only ever produce
`correct`/`incorrect`, never the expected-versus-predicted diagnosis FR-026 requires, and it would put
a per-value oracle for a private corpus in a public repository. Redaction belongs in the report
(R12), not in the truth.

---

## R8 — Comparators, and the type check that has to come first

**Decision.** `comparators@1` — exact equality on the typed value, per declared `FieldType`, with an
explicit type-identity gate before the comparison:

```text
equal(expected, predicted) := type(expected) is type(predicted) and expected == predicted
```

| `FieldType` | Python type | Note |
|---|---|---|
| `DECIMAL` | `Decimal` | `Decimal("1240.00") == Decimal("1240.0")` is `True`; trailing zeros are representation, not value |
| `INTEGER` | `int` | |
| `NUMBER` | `float` | lossy **by declaration**, per Milestone 5 research R3 — restated, not re-litigated |
| `BOOLEAN` | `bool` | |
| `DATE` / `DATETIME` | `date` / `datetime` | |
| `STRING` | `str` | byte-for-byte; no case-folding, trimming, or normalization (FR-024) |

**The type gate is not defensive coding, it is the requirement.** In Python `True == 1`,
`Decimal(1) == True`, and `1 == 1.0` are all true. FR-024 forbids coercing across types to make a
value match; without `type(...) is type(...)` a boolean label would silently match an integer
prediction and the report would call it correct. `isinstance` is wrong here for the same reason —
`bool` is a subclass of `int`.

**Rationale for exact-only.** The spec's Assumptions argue it and this plan does not reopen it:
comparing *typed* values already absorbs the differences that are representational rather than
semantic, and anything beyond that is a judgement about meaning. FR-026 keeps a near-miss visible by
recording both values rather than by scoring it as correct.

Any future leniency is a **new comparator with a new version**, recorded in the report next to every
metric it touched (FR-024). The registry is keyed by `FieldType`, so adding `string:casefold@1` is a
data change and an identity move, not an `if` inside the scorer.

---

## R9 — Entry alignment and location agreement

**Entry alignment — `positional@1` and `keyed@1`.** Positional by declared entry order is the default.
Where the golden set declares a key for a group (FR-020, and it is the *dataset* that declares it —
never the schema, because a key in the schema would move `schema_hash` under ADR-0008 and FR-004 would
then refuse every label already written against it), entries align by that key. Unmatched entries on
either side are **missing entries** and **spurious entries**, counted as such.

Refused at load: a declared key that is not a scalar field of that group, or duplicate key values
within one side. Duplicate keys make alignment ambiguous, and an ambiguous alignment silently picks
one — which is the class of thing this whole feature exists to stop.

FR-021's entry-count mismatch is reported as its own fact on `GroupOutcome`, not only through the
field outcomes it causes, because positional alignment downstream of a missing entry produces
field-level wreckage that reads as many independent errors instead of one.

**Location agreement — `page_box@1`.** Three-valued (`agrees` / `disagrees` / `not_assessable`), a
separate axis from FR-025's closed outcome set:

1. The expected page must appear in the outcome's recorded `pages`. Necessary in every case.
2. If the label states a box **and** the outcome carries geometry, the recorded geometry on that page
   must additionally overlap the expected box by ≥ **0.5**, measured as **the fraction of the recorded
   area that lies inside the expected box**.
3. If the label states a box and `geometry is None` — the parser supplied none — the result is
   `not_assessable`, never `disagrees`.

**Why containment rather than IoU.** A human labelling a value draws a loose box around it; docdoc's
geometry is a tight box on the tokens. IoU punishes exactly that pairing — a perfectly located tight
box inside a generous human box can score below 0.5 IoU while being completely right. Containment asks
the question that is actually being tested: *is what docdoc found inside what the human pointed at?*
IoU is recorded as the rejected alternative; if the labels ever become machine-tight rather than
hand-drawn, it becomes the better measure and gets a `page_box@2`.

**Why `not_assessable` is a third value and not a `disagrees`.** `GroundingOutcome.geometry` already
distinguishes `None` (the parser supplied no geometry) from `()` (geometry exists and this range covers
no tokens) — Milestone 4's GRD-17, which exists precisely so that "unavailable" is not read as "nothing
is there". Collapsing the first into a disagreement would report a parser's silence as a grounding
error, and would make the mislocation rate a function of which parser ran. Values that are
`not_assessable` are excluded from the mislocation denominator and counted separately, under FR-032's
convention.

---

## R10 — Reusing Milestone 4's grounding rate and Milestone 5's counts

**Decision.** The grounding rate is aggregated from the recorded `GroundingCounts` of each prediction —
`exact`, `fuzzy`, `ungrounded`, with `not_applicable` outside the denominator — and is **never**
recomputed from the outcomes. Milestone 4 put `grounding_rate` on `GroundingCounts` as a property that
returns `None` for an empty denominator; that is the same convention FR-032 states, and it is reused
rather than reimplemented.

The micro-average sums the counts and divides once. The macro-average means the per-document rates,
skipping documents whose rate is `None` and reporting how many it skipped (R3).

FR-039's tier separation survives untouched because **no score is averaged at all**. `GroundingOutcome`
carries `score` — `1.0` for exact by definition, a measured similarity for fuzzy — and ADR-0004 makes
those incomparable. The grounding *rate* counts outcomes, not scores, so nothing in this feature needs
to average across tiers; where scores are surfaced they are surfaced per tier and per outcome. The
simplest way to honour FR-039 turned out to be to need nothing from it.

**Validation verdicts (FR-034).** `ValidationCounts` and `Verdict` are carried into the report as a
distribution, reused not recomputed. The report therefore shows "the extraction was wrong" and
"validation caught it" as two independent facts, which is the point: a validator that rejects a wrong
value is a success of validation and not an improvement in extraction accuracy.

---

## R11 — Comparison between reports, and what makes a delta attributable

**Decision.** `compare(before, after) -> Comparison`, refusing (FR-046) when `golden_set_id`, any
schema identity or hash, or `metric_definition_version` differs — naming both sides — and when one
report is partial and the other is not.

The comparison carries, per metric, `before` / `after` / `delta` / `judgement`, where `judgement` is
`improved` | `unchanged` | `regressed` and nothing more (FR-049). Whether a build fails is policy built
on top of this output.

**A fall in the grounding rate is a named regression** (FR-047), surfaced as its own field rather than
as one row among many, because the constitution's fourth quality gate treats it as blocking and a gate
cannot read a table.

**Provenance diffing is what makes a delta attributable.** The comparison records which of
`model_id`, `model_version`, `prompt_hash`, `parser_version`, `grounding_version`, `validator_version`,
`scorer_version` differ between the two runs (FR-048). Without it a reader has a number that moved and
a change that happened, and no evidence connecting them — which is how a coincidence becomes a
conclusion.

**Undefined is not zero, and a delta against undefined is not a delta.** Where either side's metric is
`None`, the judgement is `unchanged` only if both are `None`; otherwise it is reported as
`became_defined` / `became_undefined`. Subtracting from `None` by treating it as `0.0` would manufacture
a regression out of a dataset that grew a label.

---

## R12 — Corrections, promotion, and disclosure

**Corrections.** A `Correction` carries the seven constitutionally required fields — field path,
predicted value, corrected value, source location, reason, annotator, timestamp — plus
`report_id`/`document_id` naming the exact run it corrects (FR-051). It is stored as its own JSON
document and alters nothing (FR-052): the extraction, grounding, and validation results it annotates
are inputs, and this feature never writes to them.

**Promotion is a function, not a mutation.** `promote(golden_set, corrections) -> GoldenSet` returns a
**new** golden set with a new `golden_set_id`. Reports produced before and after are then not comparable
without the difference being visible, which is FR-053 and FR-046 doing the same job from two ends. An
unpromoted correction moves no metric, and there is no code path by which it could — the scorer reads
labels and never reads corrections.

**Disclosure is driven by the tier** (FR-056), not by the caller. An outcome belonging to a
restricted-tier document carries `expected_hash` / `predicted_hash` — `sha256` over the canonical
rendering — with `expected` / `predicted` set to `None` and `redacted=True`, and the report states at
top level which tiers were redacted. FR-026's "record the expected and the predicted value" is
satisfied by the hash under this rule, which is what its "subject to FR-056" clause means: a near-miss
in a restricted tier is diagnosable as *different* but not *how*, and that is the trade the dataset's
terms impose.

**Logs carry neither** (FR-057), in either tier. This continues the boundary Milestone 5's
`observe.py` documents: findings carry values by design, logs carry identities, versions, counts, and
hashes. One structured event per run (FR-061), including the partial flag.

---

## R13 — Performance, derived from measurements

Measured on this machine, CPython 3.11, `timeit`:

| Operation | Measured | Basis |
|---|---|---|
| `Decimal == Decimal` | **34.8 ns** | 2 000 000 iterations |
| `date == date` | **19.8 ns** | 2 000 000 iterations |
| `str == str`, 28 chars | **24.5 ns** | 2 000 000 iterations |
| Field-path decomposition | **1.85 µs** | 300 000 iterations |
| Frozen pydantic model construction, 8 fields | **1.98 µs** | 200 000 iterations |
| Sorting 500 outcomes | **0.040 ms** | 2 000 iterations |
| `options_hash_for(options)` | **6.87 µs** | 100 000 iterations |

Derived for the gate-5 target dataset — 50 documents, 500 labelled fields:

| Component | Derived | From |
|---|---|---|
| 500 comparisons | ≈ 0.02 ms | 500 × ~35 ns |
| 500 outcome models | ≈ 0.99 ms | 500 × 1.98 µs (measured) |
| 500 path keys, computed once | ≈ 0.93 ms | 500 × 1.85 µs (measured) |
| Sort | ≈ 0.04 ms | measured |
| Identity | ≈ 0.01 ms | measured |
| **Scoring total** | **≈ 2 ms** | sum |

**Target: scoring completes in < 50 ms for the target-size dataset, excluding load.** That is ~25×
headroom over the derived 2 ms, which is deliberate: the derivation covers the pieces that were
measured and the real `FieldOutcome` will carry more fields than the eight-field model benchmarked
here.

**One row is deliberately not derived: loading.** Reading and validating the manifest, the labels, and
500 prediction artifacts from disk is I/O plus pydantic validation, and it will dominate the 2 ms of
arithmetic by an order of magnitude or more. It is bounded separately — **< 500 ms end to end
including load** — and it is what the `perf` tier measures. If load dominates beyond that, the fix is a
leaner on-disk prediction form, not a relaxed bound. This is the same discipline Milestone 5 applied
when it declined to assert an unmeasured model-construction cost.

---

## R14 — Testing: offline, no skips, and the three things that need property tests

Four tiers, continuing Milestones 4 and 5. **This feature adds no provider tier and no test that
skips** — the third milestone in a row of which that is true. The repository's existing 11 skips are
Milestone 2's and Milestone 3's live provider tests and stay untouched. The `docdoc.recording` package
is exercised against the `echo` adapter, which is exactly why that adapter exists and why it is never
auto-selected.

**Property tests**, where the invariant is universal rather than exemplary:

1. **Metric reconciliation** — for any generated dataset and prediction set, summing the per-field
   outcomes reproduces every aggregate, and `coverage + missing_rate + unevaluated_rate_V == 1` over V
   (SC-004, R2). This is the test that catches a denominator edit that "looked right".
2. **Order totality** — the outcome order is independent of dict order, hash seed, and platform
   (FR-043, SC-009). Run under two `PYTHONHASHSEED` values, as `.github/workflows/ci.yml` already does
   for Milestone 4's tie-breaks.
3. **Identity sensitivity** — changing the scorer version, a comparator, the metric definition version,
   the dataset, or the prediction set moves `report_id`; changing something that cannot move a number
   does not (SC-012). Both directions, because an identity that moves on everything is as useless as
   one that moves on nothing.

**Behavioural tests that assert what a static check cannot** (R1): scoring the committed public tier
with `socket.socket` patched to raise, proving FR-007 against behaviour rather than an import graph.

**A `SCORER_VERSION` snapshot test**, mirroring `test_validator_version_snapshot.py`: a change detector,
not a breakage detector, cleared by bumping the version or refreshing the snapshot with the
classification stated in the commit message (R6).

**Deliberately not built.** A golden set large enough to hit the gate-5 target is dataset authoring
work, not implementation work; this milestone builds the machinery and a fixture dataset of a size that
exercises every code path. The plan states the target so the gap is visible rather than implied — see
plan.md's Structure Decision.
