# Feature Specification: Golden-Set Evaluation

**Feature Branch**: `006-golden-set-evaluation`

**Created**: 2026-08-19

**Status**: Implemented

**Input**: User description: "dựng cho 006 nhé" — Milestone 6 of the roadmap in `README.md`:
*Evaluation: golden dataset, field accuracy, grounding rate*.

Milestone 6 of the docdoc MVP — the milestone that turns four milestones of computable numbers into
*measured* ones. Milestone 3 recorded what the model answered, Milestone 4 made the grounding rate
computable and set no target for it, Milestone 5 made the validation rate computable and set no target
for it either. Each deferred the same question here: **is any of this any good, and did the last change
make it worse?** This feature is governed by the constitution (v1.4.0) — Principle IX above all — and by
ADR-0002, ADR-0003, ADR-0004, ADR-0005, ADR-0008, and ADR-0009.

It was also the milestone gated by a constitutional decision. `TODO(GOLDEN_DATASET_LICENSING)` named this
milestone explicitly, and the constitution's precedence rule forbids resolving it in code. It was decided
explicitly in the clarification session below rather than implicitly here, and is now recorded where the
constitution requires: **ADR-0009**, with the amendment moving the item to Resolved in constitution
v1.3.0. FR-010 and FR-003 carry its consequences.

## Clarifications

### Session 2026-08-20

- Q: Where do golden-set documents come from, and on what basis may docdoc use them
  (`TODO(GOLDEN_DATASET_LICENSING)`, FR-010)? → A: **Two tiers.** A *public tier* vendored into the
  repository — synthetic documents generated from committed generators, plus permissively licensed or
  public-domain documents — which every contributor can evaluate against with no credentials and no
  network. Plus an *optional restricted tier* that is never committed and is referenced only by content
  hash, for documents docdoc may measure against but may not redistribute. A run without the restricted
  tier is a partial report under FR-015, not a smaller full one. This is the only option answering both
  halves of the constitutional TODO — the sourcing strategy and how contributors run evaluation without
  it — and it is the shape FR-015, FR-056, and User Story 3 were already written against.
- Q: How is a prediction set obtained for the scorer to score (FR-003)? → A: **Both paths, replay by
  default.** Prediction artifacts for the public tier are recorded and committed alongside the golden
  set and replayed, so scoring a checkout needs no credentials. A separate, opt-in recording step runs
  the pipeline against a provider to produce a prediction set — the only path available for the
  restricted tier, whose predictions carry restricted document content and therefore cannot be
  committed. Recording is not part of evaluation: the scorer only ever reads recorded artifacts, which
  is what keeps FR-002, FR-007, FR-008, and FR-043 intact under both paths.
- Q: Under what rule does a recorded location count as agreeing with a label's expected one (FR-038)? →
  A: **A page, optionally narrowed by a bounding box, assessed at the finest granularity both sides
  carry.** Agreeing on the page is necessary in every case; where the label states a box and the outcome
  carries geometry, the box must also overlap by a versioned threshold. An expected location is never a
  text offset, because offsets move whenever a parser changes how it extracts text and a parser upgrade
  would then read as mass mislocation. Where the label states a box but the parser supplied no geometry,
  the result is *not assessable* rather than *disagrees* — Milestone 4 already refuses to collapse "no
  geometry available" into "nothing is there", and this keeps that refusal.
- Q: Where is a repeating group's entry-alignment key declared (FR-020)? → A: **In the golden set,
  alongside that group's labels — never in the extraction schema.** A key in the schema would move the
  `schema_hash` under ADR-0008, and FR-004 would then refuse every label already written against that
  schema: adding a key to fix alignment would invalidate the dataset it was meant to measure. Keeping it
  in the dataset also keeps an evaluation-only concept out of a lower layer, which is what Principle X
  and FR-058 ask for. The declared key is covered by the dataset identity of FR-012.
- Q: What is the golden dataset's target size, the one the constitution's fifth quality gate turns
  blocking at, and what is it measured on? → A: **50 documents and 500 labeled fields in the public
  tier**, across at least two schemas with at least twenty documents each. Measured on the public tier
  alone, because gate 5 is a CI gate and CI cannot see the restricted tier. Five hundred labeled fields
  puts one field outcome at roughly 0.2% of the headline accuracy — a judgement about resolution, not a
  power calculation: below that, ordinary movement is indistinguishable from the regressions the gate
  exists to block. FR-009 requires the manifest and every report to carry these counts per tier, so the
  gate is evaluable from a report rather than by inspecting the dataset.

## User Scenarios & Testing *(mandatory)*

The consumers of this feature are **docdoc's own maintainers and contributors**, and secondarily
developers running docdoc against their own documents who want to know what it costs them. There is no
end-user interface at this milestone. "The system" below means the evaluation stage.

Vocabulary, fixed here and used throughout:

- A **golden set** is a collection of documents together with the answers a human states are correct.
- A **label** (or **expectation**) is one such stated answer for one field path in one document: either
  a value, or the statement that the field is correctly absent.
- A **prediction** is what the pipeline produced for one document — its extraction result, its grounding
  result, and its validation result.
- A **prediction set** is the predictions for the documents of one golden set, produced under one set of
  versions.
- A **field outcome** is the comparison of one prediction against one label.
- A **report** is one evaluation run's output: the field outcomes, the metrics computed from them, and
  the provenance that says what was measured.

### User Story 1 - Know how good it actually is (Priority: P1)

A maintainer has a golden set and a prediction set. They ask docdoc to score it and get back field
accuracy, coverage, missing rate, incorrect rate, and grounding rate — for the dataset as a whole, for
each document, and for each field path — with every number stating the numerator and denominator it came
from, and every number traceable down to the individual comparisons that produced it.

**Why this priority**: This is the milestone's reason to exist and the smallest slice that stands alone.
Principle IX makes measurement a product feature and names these five metrics as the minimum; until this
story ships, every quality claim in this repository is an assertion. It also removes the standing excuse
the four previous milestones each recorded: they made rates computable and declined to compute them.

**Independent Test**: Score a committed prediction set against a committed golden set with no
credentials and no network, and confirm every metric matches a hand-computed value, that each one
reports its denominator, and that summing the per-field outcomes reproduces the dataset totals exactly.

**Acceptance Scenarios**:

1. **Given** a golden set and a prediction set for it, **When** the maintainer asks the system to
   evaluate, **Then** exactly one report is returned, or an explicit error is raised, and never a
   partial report.
2. **Given** a field the golden set says holds a value and the run predicted the same value, **When**
   scoring runs, **Then** the outcome is *correct* and it contributes to accuracy's numerator and
   denominator.
3. **Given** a field the golden set says holds a value and the run predicted a different one, **When**
   scoring runs, **Then** the outcome is *incorrect*, and both the expected and the predicted value are
   recorded so a near-miss is visible as a near-miss.
4. **Given** a field the golden set says holds a value and the run reported absent, **When** scoring
   runs, **Then** the outcome is *missing* — a distinct state from *incorrect*, because a blank and a
   wrong answer are different failures with different fixes.
5. **Given** a field the golden set says is correctly absent and the run predicted a value, **Then** the
   outcome is *spurious*, counted separately and never folded into *incorrect*.
6. **Given** a field the run predicted that the golden set says nothing about, **Then** the outcome is
   *unlabeled*: counted, reported, and excluded from every accuracy denominator.
7. **Given** any metric whose denominator is zero, **Then** it is reported as undefined, never as zero.
8. **Given** a report, **When** a maintainer reads any aggregate, **Then** they can reach the individual
   field outcomes it was computed from without re-running anything.

---

### User Story 2 - See the regression before it merges (Priority: P2)

The same maintainer changes the grounding threshold. They evaluate before and after and get a comparison
that says which metrics moved and by how much, lists the fields that broke and the fields that were
fixed, and states which versions differed between the two runs — so a drop is attributable to the change
rather than merely coincident with it.

**Why this priority**: The constitution's fourth quality gate makes a fall in grounding rate on the
golden set a blocking event that must be justified, and the fifth gate requires golden-set metrics to be
reported for any change to parsers, prompts, models, schemas, or grounding. Neither gate is enforceable
against a single number with nothing to compare it to. Story 1 without Story 2 tells you where you are;
Story 2 tells you which way you are moving, which is the one that stops a merge.

**Independent Test**: Score one prediction set twice with one deliberate degradation between them, and
confirm the comparison names the moved metrics, lists exactly the changed field outcomes in both
directions, and refuses to compare across a dataset or metric-definition change.

**Acceptance Scenarios**:

1. **Given** two reports over the same golden set, **When** they are compared, **Then** the result states
   each metric's before, after, and delta, and lists every field outcome that changed in either
   direction.
2. **Given** a comparison where the grounding rate fell, **Then** the fall is reported as a named
   regression rather than as one delta among many.
3. **Given** two reports over different golden sets, different schema identities, or different metric
   definition versions, **When** they are compared, **Then** the comparison is refused with an error
   naming both sides — it does not silently diff numbers that do not mean the same thing.
4. **Given** two runs that differ in model, prompt hash, parser version, or grounding version, **When**
   they are compared, **Then** the comparison records which of those differed, so a metric change can be
   attributed.
5. **Given** any comparison, **When** it finishes, **Then** it states what moved and does not decide what
   should happen about it.

---

### User Story 3 - Run the whole evaluation with nothing but the repository (Priority: P3)

A first-time contributor clones docdoc, installs it, and runs the evaluation. They have no API
credentials, no network access, and no customer documents. They get real metrics over a real golden set,
and where part of the dataset is unavailable to them, they are told exactly what was skipped rather than
handed a smaller denominator that looks like a full result.

**Why this priority**: An evaluation only maintainers can run is not a product feature, it is an internal
report; Principle IX makes it a product feature, and Principle XII requires the suite to run for every
contributor. This is also the half of `TODO(GOLDEN_DATASET_LICENSING)` the constitution states
explicitly — "how contributors run evaluation without it" — and the reason a silent partial evaluation is
the most dangerous failure in this milestone: it reports success over the easy documents.

**Independent Test**: With credentials removed and the network disabled, run the documented evaluation
example end to end and confirm it produces a full report over the openly available tier, and that a run
missing a restricted tier names the absent documents and marks the report as partial.

**Acceptance Scenarios**:

1. **Given** a checkout with no credentials and no network, **When** the contributor runs the documented
   evaluation, **Then** it completes and produces metrics.
2. **Given** a golden set whose restricted tier is unavailable, **When** evaluation runs, **Then** the
   report is explicitly marked partial, names what was not evaluated, and reports the covered fraction
   of the dataset.
3. **Given** a partial report, **When** it is compared against a full one, **Then** the comparison is
   refused or explicitly flagged, never silently performed.
4. **Given** any document in the golden set, **When** a reader asks where it came from, **Then** the
   dataset records its origin and the basis on which docdoc may use it.
5. **Given** an evaluation over a restricted tier, **When** the report is written or logged, **Then** no
   document text and no field value from that tier appears in it.

---

### User Story 4 - Correct a wrong answer once and have it count (Priority: P4)

A reviewer sees that docdoc read `1,240.00` where the invoice says `1,249.00`. They record a correction
naming the field, both values, where in the document the right value is, why, who they are, and when.
Later, that correction is promoted into the golden set by an explicit act, and the next evaluation run
measures against it.

**Why this priority**: Principle IX makes human correction a product feature alongside evaluation and
fixes the minimum fields a correction carries; Milestone 5 explicitly assigned annotations to this
milestone. It is P4 because Stories 1–3 are what make a correction worth recording: a correction with no
measurement to feed is a note in a file.

**Independent Test**: Record a correction against a committed result, confirm it carries every
constitutionally required field and alters nothing it annotates, then promote it into the golden set and
confirm the dataset identity changes and the next run scores against the corrected label.

**Acceptance Scenarios**:

1. **Given** a predicted value a human judges wrong, **When** they record a correction, **Then** it
   carries the field path, the predicted value, the corrected value, the source location, the reason,
   the annotator, and the timestamp.
2. **Given** a correction, **When** it is stored, **Then** the extraction, grounding, and validation
   results it annotates are unchanged.
3. **Given** a correction, **When** a reader inspects it, **Then** it names the exact run and result it
   corrects, so it cannot be mistaken for a correction of a different version's output.
4. **Given** a correction promoted into the golden set, **Then** the promotion is a recorded act, the
   dataset identity changes, and reports produced before it are not comparable to reports produced
   after it without that difference being visible.
5. **Given** a correction not yet promoted, **Then** it changes no metric.

---

### Edge Cases

- **A document in the golden set has no prediction.** Reported as unevaluated and named. It is never
  dropped from the denominator, because dropping it is how a crash becomes an accuracy improvement.
- **A document failed to parse, extract, ground, or validate.** Counted as a processing failure at
  document level, with its labeled fields counted as missing rather than excluded — the same reason.
- **A prediction exists for a document the golden set does not contain.** Refused, naming the document:
  the two sides do not describe the same thing.
- **The run's schema identity or hash differs from the one the labels were written against.** Refused.
  A label written against `invoice@1` says nothing about a result produced under `invoice@2` (ADR-0008).
- **A repeating group where the run found four line items and the truth has five.** The entry-count
  mismatch is its own reported fact, not only the field-level wreckage that positional alignment
  produces downstream of the missing entry.
- **A value that is correct but ungrounded.** Correct for accuracy, ungrounded for the grounding rate.
  Two facts, two numbers, never merged.
- **A value that is grounded, but to the wrong place in the document.** Counted as grounded by the rate
  Milestone 4 defined, and — where the label states an expected location — separately counted as
  mislocated. A rate that cannot see this is the rate that lets a plausible wrong span pass.
- **A value that is wrong and the validation stage already rejected it.** Both facts are reported;
  neither suppresses the other. Validation catching an error is a success of validation, not an
  improvement in extraction accuracy.
- **An empty golden set, or a golden set where every field is unlabeled.** Every metric is undefined,
  and the report says so rather than reporting a perfect score over nothing.
- **Two runs of the same prediction set.** Byte-identical reports. Scoring is deterministic even though
  what it scores was not.
- **The same document appears twice in the golden set.** Refused at load: a duplicated document silently
  doubles its weight in every metric.
- **A label states a value the schema's declared type cannot carry.** An authoring error raised when the
  golden set loads, never a document that permanently scores zero for reasons nobody can see.
- **A metric definition changes.** Every historical number computed under the old definition becomes
  incomparable, and the version is what makes that visible instead of a step in a chart nobody can
  explain.

## Requirements *(mandatory)*

### Functional Requirements

**Inputs, and what a run refuses**

- **FR-001**: The system MUST accept a golden set and a prediction set and produce exactly one
  evaluation report, or raise an explicit error. It MUST NOT produce a partial report without marking it
  partial and naming what it omitted.
- **FR-002**: A prediction MUST be the recorded output of the pipeline for one document — its extraction
  result, its grounding result, and its validation result — and the system MUST NOT re-derive, re-score,
  re-ground, or re-validate any of it. Evaluation reads recorded facts; it does not recompute them.
- **FR-003**: A prediction set MUST be an input to scoring, never something scoring produces. It MUST be
  obtainable by two paths, and scoring MUST behave identically under both because it reads the same
  recorded artifacts either way.
  - **Replay (the default).** Prediction artifacts for the public tier MUST be recorded and committed
    alongside the golden set, so that scoring the public tier requires nothing but a checkout.
  - **Recording (opt-in).** A separate step MAY run the pipeline against a provider and record a
    prediction set for later scoring. This step MUST NOT be part of evaluation, MUST NOT be required to
    score, and MUST be the only place in this feature where a provider, credentials, or a network are
    used. It is the only path available for the restricted tier.
  - Prediction artifacts for restricted-tier documents MUST NOT be committed to the repository, because
    a recorded prediction carries the values it extracted from the document.
  - A prediction set MUST carry its own identity and MUST record the versions it was produced under, so
    that FR-040 can be satisfied by a report scoring a set the scoring run did not produce.
- **FR-004**: The system MUST refuse to score a prediction against labels written under a different
  schema identity or schema hash, with an error naming both sides.
- **FR-005**: The system MUST refuse a prediction set containing a document the golden set does not
  contain, naming it. A golden-set document with no prediction MUST be reported as unevaluated and
  MUST NOT be removed from any denominator.
- **FR-006**: Evaluation MUST NOT modify the golden set, the labels, the predictions, or the schemas.
  Every input is byte-identical before and after.
- **FR-007**: Scoring MUST NOT require a network, credentials, a provider, a database, or an object
  store.
- **FR-008**: Evaluation MUST NOT ask a model anything, including whether a predicted value means the
  same thing as the expected one. A model judging its own output is the failure Principle II forbids for
  grounding, and it is no more acceptable when the subject is accuracy.

**The golden set**

- **FR-009**: A golden set MUST be an explicit, versioned collection carrying a manifest: its documents
  (or references to them), its labels, its schema identities, and its own identity. The manifest MUST
  state its size as the number of documents and the number of labeled fields, counted per tier and never
  merged across tiers, and every report MUST carry those counts. The constitution's fifth quality gate
  turns blocking at a target size, and a target nobody can read off a report is a target nobody can
  apply.
- **FR-010**: A golden set MUST be organised into exactly two tiers, and every document MUST declare
  which tier it belongs to.
  - The **public tier** MUST be vendored into the repository and MUST consist only of documents docdoc
    may redistribute: synthetic documents generated from generators committed to the repository, and
    permissively licensed or public-domain documents. It MUST be evaluable with no credentials and no
    network, and it MUST be sufficient on its own to produce a complete, non-partial report.
  - The **restricted tier** MUST be optional, MUST NOT be committed to the repository, and MUST be
    referenced only by content hash together with the provenance FR-011 requires. Its documents MUST be
    resolved from a location the caller supplies; a run that cannot resolve them MUST behave under
    FR-015 rather than failing, and its report MUST be marked partial.
  - A synthetic document MUST record the generator and the generator version that produced it, so that
    a label is traceable to the thing that made the document it describes.
  - The tier MUST determine the disclosure rules of FR-056 rather than the caller's diligence
    determining them.
- **FR-011**: Every document in the golden set MUST record its origin and the basis on which docdoc may
  use it. A document whose provenance cannot be stated MUST NOT be admitted.
- **FR-012**: The golden set MUST carry an identity that changes whenever any document, any label, or
  any metric-relevant metadata changes, and every report MUST record it. Two reports over datasets with
  different identities are not comparable, and FR-045 is what enforces that.
- **FR-013**: The golden set MUST support more than one schema and more than one document type. A
  dataset that can only describe invoices would reintroduce, in data, the document-type coupling
  Principle VI forbids in code.
- **FR-014**: The golden set MUST refuse to load with a duplicated document, an unresolvable field path,
  or a label whose value the declared type cannot carry, naming the offender. These are authoring errors
  and MUST be raised at load, before any metric is computed.
- **FR-015**: Where part of the dataset is unavailable to the caller, the run MUST name what was skipped
  and mark the report partial. A metric computed over an unannounced subset is the failure mode this
  requirement exists to prevent.

**Labels**

- **FR-016**: A label MUST state, for one field path, either the expected value or that the field is
  expected to be absent. A field path carrying no label MUST be treated as *unlabeled*, and MUST be
  counted and reported rather than assumed correct or assumed wrong.
- **FR-017**: Labels MUST address fields by the same field-path form extraction, grounding, and
  validation use, including the index of a value inside a repeating group.
- **FR-018**: A label MAY additionally state the expected source location of the value, as a page and
  optionally a bounding box (FR-038). Where it does, the report MUST measure whether the recorded
  location agrees with it; where it does not, only the grounding rate is measurable and the report MUST
  say which of the two it is reporting.
- **FR-019**: A label MUST be able to record who stated it and when.
- **FR-020**: Expectations for a repeating group MUST align entries under an explicit, documented,
  versioned policy. The default MUST be positional in declared entry order; a group MAY declare a key
  field, and where it does, entries MUST align by that key with unmatched entries on either side counted
  as missing or spurious entries. The key MUST be declared by the golden set, alongside the labels for
  that group, and MUST NOT be declared in the extraction schema. Declaring it in the schema would move
  the `schema_hash` under ADR-0008, and FR-004 would then refuse every label already written against
  that schema — so the act of improving alignment would invalidate the dataset it was meant to measure.
  The declared key is part of what FR-012 makes the dataset identity cover: changing it changes the
  identity, because it can change a metric.
- **FR-021**: An entry-count mismatch in a repeating group MUST be reported as its own fact, not only
  through the field outcomes it causes.
- **FR-022**: Labels MUST be readable and writable as data, without code, so that adding a document to
  the golden set is an act of authoring rather than of programming.

**Comparison**

- **FR-023**: Correctness MUST be decided by comparing the typed value against the labeled value under a
  documented comparator for the declared type. The MVP comparator set is exact equality on the typed
  value: decimals compared as decimals, dates as dates, strings byte-for-byte.
- **FR-024**: Comparison MUST NOT normalize, case-fold, trim, round, or coerce across types to make a
  value match. Any leniency MUST be an explicitly declared, versioned comparator, recorded in the report
  wherever the metric it affected is reported.
- **FR-025**: Every comparison MUST resolve to exactly one outcome from a closed, documented set:
  **correct**, **incorrect**, **missing**, **spurious**, **unlabeled**, **unevaluated**. Collapsing any
  two of these MUST NOT be possible in the reported shape.
- **FR-026**: Every non-correct outcome MUST record the expected and the predicted value, subject to
  FR-056, so that a near-miss is diagnosable without re-running anything.
- **FR-027**: Whether a value is correct and whether it is grounded MUST be measured independently and
  reported as separate numbers. A correct but ungrounded value MUST count as correct for accuracy and as
  ungrounded for the grounding rate.
- **FR-028**: No comparison, metric, or outcome MUST read the model's self-reported confidence
  (ADR-0004). It is passed through by earlier stages and MUST remain untrusted here.

**Metrics**

- **FR-029**: The system MUST compute at least: **field accuracy**, **coverage**, **missing rate**,
  **incorrect rate**, and **grounding rate**. Each MUST be reported together with the numerator and the
  denominator it was computed from.
- **FR-030**: Every metric MUST be reported at document level and at field level — per field path across
  the dataset — as well as for the dataset as a whole. A single dataset number is not sufficient
  evidence under Principle IX.
- **FR-031**: Dataset-level aggregation MUST state which average it is. Where a micro-average and a
  macro-average can differ, both MUST be reported and each MUST be labeled, so a reader cannot mistake
  one for the other.
- **FR-032**: A metric whose denominator is zero MUST be reported as undefined, never as zero, following
  the convention Milestone 4 set for the per-document grounding rate.
- **FR-033**: The grounding rate MUST reuse Milestone 4's definition and its recorded per-result counts,
  including its exclusion of correctly reported absences from the denominator. This feature MUST NOT
  define a second grounding rate.
- **FR-034**: The report MUST include the distribution of validation verdicts and the validation counts
  Milestone 5 records, reusing them rather than recomputing them, so that "the extraction was wrong" and
  "validation caught it" are separately visible.
- **FR-035**: The metric definitions MUST carry a version. Changing a definition, a denominator, or an
  aggregation rule REQUIRES a bump, and comparing reports computed under different metric definition
  versions MUST be refused.
- **FR-036**: Unlabeled predictions MUST be counted, reported, and excluded from every accuracy
  denominator.
- **FR-037**: A document that failed to process MUST be counted as a processing failure and its labeled
  fields counted as missing. It MUST NOT be excluded from any denominator, so that failing on hard
  documents can never improve a score.

**Grounding correctness**

- **FR-038**: Where a label states an expected location, the report MUST state whether the recorded
  location agrees with it under a documented, versioned agreement rule, and MUST count a value grounded
  to a disagreeing location separately from a value that did not ground at all. The rule is:
  - An expected location MUST state a page, and MAY additionally state a bounding box. It MUST NOT be
    expressed as an offset into the document text: text offsets move when a parser changes how it
    extracts text, which would turn a parser upgrade into a mass mislocation that is not one.
  - Agreement MUST be assessed at the finest granularity both the label and the recorded outcome carry.
    Agreeing on the page is necessary in every case; where the label states a box and the outcome
    carries geometry, the recorded geometry MUST also overlap the expected box by at least a versioned
    threshold.
  - Location agreement MUST resolve to exactly one of **agrees**, **disagrees**, or **not assessable**,
    and this MUST be a separate axis from the field outcome of FR-025 — it MUST NOT add a seventh
    outcome to that closed set.
  - **Not assessable** MUST be used, and MUST NOT be reported as disagreement, when the label states a
    box but the recorded outcome carries no geometry because the parser supplied none. Milestone 4
    already distinguishes "the parser supplied no geometry" from "geometry exists and covers nothing";
    collapsing the first into a disagreement would report a parser's silence as a grounding error.
  - Values whose location is not assessable MUST be excluded from the mislocation denominator and
    counted and reported separately, under FR-032's convention.
- **FR-039**: A grounding score MUST NOT be compared or averaged across the exact and fuzzy tiers
  (ADR-0004, ADR-0005). Where scores are reported, they MUST be reported per tier.

**Run identity and provenance**

- **FR-040**: Every report MUST record: the repository revision, the golden-set identity, the schema
  identity and hash, the prompt hash, the model identity and version, the parser identity and version,
  the grounding version, the validator version, the scorer identity and version, the metric definition
  version, the comparator versions, and the run's options.
- **FR-041**: A run that cannot record every item in FR-040 MUST be refused rather than reported. A
  metric whose origin is unknown is the "vague AI quality claim" Principle IX rejects as evidence.
- **FR-042**: The report MUST carry a content-addressed identity derived from the golden-set identity,
  the prediction set's identity, the scorer's identity and version, the metric definition version, and
  the options hash. Identical inputs MUST produce an identical identity; any change that can move a
  number MUST move it.
- **FR-043**: For fixed inputs and versions, the report — every outcome, every metric, and the order of
  everything in it — MUST be byte-identical on every run and on every platform. Ordering MUST be total.
- **FR-044**: Re-evaluating MUST produce a new report with its own provenance and MUST NOT mutate,
  overwrite, or reinterpret a prior one.

**Comparison between runs**

- **FR-045**: The system MUST compare two reports and produce: each metric's before, after, and delta,
  and every field outcome that changed, in both directions.
- **FR-046**: Comparison MUST be refused, naming both sides, when the golden-set identity, a schema
  identity or hash, or the metric definition version differs. A partial report MUST NOT be compared
  against a full one without the difference being stated.
- **FR-047**: A fall in the grounding rate MUST be reported as a named regression rather than as one
  delta among many, because the constitution's fourth quality gate treats it as blocking.
- **FR-048**: The comparison MUST record which provenance fields differ between the two runs — model,
  prompt hash, parser version, grounding version, validator version, scorer version — so that a metric
  change is attributable rather than merely observed.
- **FR-049**: The comparison MUST produce a machine-readable per-metric judgement of improved,
  unchanged, or regressed, and MUST NOT decide what happens as a result. Whether a build fails is policy
  configured on top of this output, not part of producing it.

**Corrections**

- **FR-050**: A correction MUST carry at minimum: the field path, the predicted value, the corrected
  value, the source location, the reason, the annotator, and the timestamp.
- **FR-051**: A correction MUST name the exact result and run it corrects, so it cannot be read as a
  correction of a different version's output.
- **FR-052**: A correction MUST NOT alter the extraction, grounding, or validation result it annotates,
  and MUST NOT alter any metric until it is promoted.
- **FR-053**: Promoting a correction into the golden set MUST be an explicit, recorded act that changes
  the golden-set identity. A correction MUST NOT silently become truth.
- **FR-054**: This feature MUST NOT provide a review interface, assignment, workflow, queue, or storage
  service for corrections. Principle IX permits corrections as a model and forbids the MVP becoming a
  review platform.

**Reporting, safety, and boundaries**

- **FR-055**: The report MUST be machine-readable, and every aggregate MUST be traceable to the field
  outcomes that produced it without re-running evaluation.
- **FR-056**: Where the dataset's terms restrict disclosure, the report MUST carry hashes in place of
  document text and field values, and MUST state that it did so. The choice MUST be a property of the
  dataset, not of the caller's diligence.
- **FR-057**: Document text, field values, and label values MUST NOT appear in log output. Logs carry
  identities, versions, counts, and hashes only.
- **FR-058**: Evaluation MUST sit above validation in the dependency order, MUST NOT be imported by any
  lower layer, and this MUST be enforced by an automated check rather than by convention.
- **FR-059**: The base install MUST NOT require the golden set, evaluation-only dependencies, or any
  provider SDK. The core library remains usable and installable without any of it.
- **FR-060**: Failures MUST surface as docdoc's own typed, provider-neutral errors naming the dataset,
  the document, and the field or label at fault, and MUST NOT be retried — there is no transient failure
  mode in a deterministic, offline computation.
- **FR-061**: Every evaluation run MUST emit one structured log event carrying the identities, the
  versions, the counts, the duration, and whether the report is partial.

### Key Entities

- **GoldenSet**: A versioned collection of documents and labels with a manifest and an identity that
  moves whenever anything metric-relevant in it moves, organised into a public and a restricted tier and
  declaring its per-tier size in documents and labeled fields.
- **GoldenDocument**: One document in the set — its blob identity, its tier, its origin, the basis on
  which it may be used, the generator and generator version where it is synthetic, its schema identity,
  and its labels.
- **Label / Expectation**: One stated truth for one field path: a value or an asserted absence,
  optionally an expected source location as a page and an optional bounding box, optionally the labeler
  and the date.
- **EntryAlignment**: The versioned policy deciding which predicted repeating-group entry is compared
  against which expected one — positional by default, by key where the golden set declares one for that
  group. The key belongs to the dataset, never to the extraction schema.
- **LocationAgreement**: The versioned rule and its three-valued result — agrees, disagrees, not
  assessable — comparing a recorded location against a label's expected one. A separate axis from the
  field outcome, never a seventh member of it.
- **PredictionSet**: The recorded pipeline outputs for the documents of one golden set, produced under
  one set of versions, carrying its own identity. Committed and replayed for the public tier; produced
  by the opt-in recording step for the restricted tier, and never committed for it.
- **Comparator**: The versioned rule deciding whether a predicted value equals a labeled one, per
  declared type. Data with a version, never an ad-hoc comparison inside the scorer.
- **FieldOutcome**: One comparison — the field path, the expected value, the predicted value, the closed
  outcome, the grounding facts carried through from Milestone 4, and its location agreement where the
  label states an expected location.
- **DocumentScore**: One document's outcomes and metrics, including whether it processed at all.
- **DatasetMetrics**: The dataset-level metrics with their numerators, denominators, and stated
  averaging, plus the per-field-path breakdown.
- **MetricDefinition**: The versioned definitions of the five metrics — what each counts and what it
  divides by — so a change of denominator is visible rather than mysterious.
- **EvaluationReport**: The outcomes, the metrics, the partial-run declaration, the provenance, and the
  content-addressed identity.
- **EvaluationProvenance**: Repository revision, dataset identity, schema identity and hash, prompt hash,
  model, parser, grounding version, validator version, scorer identity and version, metric definition
  version, comparator versions, options.
- **Scorer**: The processor for this stage, with a stable identity and a version that moves whenever its
  output moves for fixed inputs.
- **Comparison**: Two reports' deltas, the changed outcomes in both directions, the provenance fields
  that differed, and the per-metric judgement.
- **Correction**: A human annotation carrying the constitutionally required seven fields, naming the
  result it corrects, and promotable into a label by a recorded act.
- **ExtractionResult / GroundingResult / ValidationResult** *(existing, Milestones 3–5)*: The predictions
  this feature scores. Their recorded counts — grounding statuses, validation outcomes — are reused, not
  recomputed.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A committed golden set and a committed prediction set are scored with no credentials, no
  network access, no database, and no object storage.
- **SC-002**: 100% of labeled fields receive exactly one outcome from the closed set; zero are omitted
  and zero receive a seventh outcome.
- **SC-003**: On a hand-computed fixture, 100% of the five constitutionally required metrics match the
  hand-computed value exactly, at dataset, document, and field-path level.
- **SC-004**: Summing the per-field outcomes reproduces every reported aggregate in 100% of runs; zero
  aggregates are reachable only by trusting the reporter.
- **SC-005**: 100% of reported metrics state their numerator and denominator; 0% of metrics with an empty
  denominator are reported as zero.
- **SC-006**: A document that fails to process is counted in 100% of runs and excluded from denominators
  in 0% of them, verified by a fixture whose failure would otherwise raise the score.
- **SC-007**: 100% of predicted fields the golden set does not label are reported as unlabeled and enter
  0% of accuracy denominators.
- **SC-008**: Golden set, labels, and predictions are byte-identical before and after evaluation in 100%
  of runs.
- **SC-009**: Repeating evaluation of identical inputs produces byte-identical reports in 100% of runs
  and on every supported platform; zero outputs vary with iteration order or hash order.
- **SC-010**: A prediction set scored against labels written under a different schema identity or hash,
  and a prediction set naming a document the golden set does not contain, are refused in 100% of cases
  with both sides named; zero produce metrics.
- **SC-011**: 100% of reports record the repository revision, dataset identity, schema identity and hash,
  prompt hash, model, parser version, grounding version, validator version, scorer version, metric
  definition version, and comparator versions; a run that cannot record one is refused in 100% of cases.
- **SC-012**: Changing the scorer version, a comparator, the metric definition version, the dataset, or
  the prediction set changes the report identity in 100% of cases; changing something that cannot move a
  number changes it in 0% of cases.
- **SC-013**: A comparison across differing dataset identities, schema identities, or metric definition
  versions is refused in 100% of cases; 0% are silently performed.
- **SC-014**: On a fixture with one deliberate degradation, the comparison names 100% of the changed
  field outcomes in both directions and zero unchanged ones.
- **SC-015**: A fall in grounding rate is reported as a named regression in 100% of comparisons where it
  occurs.
- **SC-016**: A run over an unavailable dataset tier is marked partial and names what it skipped in 100%
  of cases; 0% report a metric over an unannounced subset.
- **SC-017**: Zero document text, field values, and label values appear in log output over the whole
  golden set, and 100% of runs emit one structured event carrying identities, versions, counts,
  duration, and the partial flag.
- **SC-018**: Under a disclosure-restricted dataset, 0% of values appear in the report and 100% of the
  affected outcomes carry a hash instead, with the substitution stated in the report.
- **SC-019**: A correction carries 100% of the seven constitutionally required fields, alters 0% of the
  results it annotates, and moves 0% of metrics until it is promoted.
- **SC-020**: Promoting a correction changes the golden-set identity in 100% of cases.
- **SC-021**: 100% of golden-set authoring errors — a duplicated document, an unresolvable field path, a
  label the declared type cannot carry — are raised at load; 0% surface as a silently zero-scoring
  document.
- **SC-022**: A contributor with no credentials and no network access runs 100% of this feature's tests
  and 100% of its documented examples; zero are skipped for want of a provider.
- **SC-023**: A maintainer adds a document and its labels to the golden set by following a single
  documented example, without reading the implementation and without writing code.
- **SC-024**: An import from evaluation into any lower layer fails the build in 100% of cases.

## Assumptions

These are reasonable defaults chosen where the source material did not specify. Each is a candidate for
`/speckit-clarify` if it proves wrong in review. The two decisions that are **not** assumed — the dataset
sourcing strategy and how predictions are obtained — were decided explicitly in the Clarifications
session above and recorded in FR-010 and FR-003, because the constitution forbids settling the first
silently and the second depends on it.

- **Evaluation is a new top layer, not a stage in the artifact chain.** It consumes the validation
  artifact and produces no artifact any document-processing stage consumes, so it extends the
  machine-checked layer contract one level above validation — the same reading Milestones 4 and 5
  applied — but does not extend ADR-0003's per-document chain. Its report still carries a
  content-addressed identity
  (FR-042), because Principle VIII's argument applies to anything whose origin has to be reconstructed.
- **Scoring is deterministic even though what it scores is not.** The model that produced a prediction is
  probabilistic; the comparison of that prediction against a label is arithmetic. This is what makes
  FR-043 achievable and is the boundary that keeps evaluation in the deterministic core.
- **The five constitutional metrics are the whole MVP set.** Precision, recall, F1, per-provider
  leaderboards, latency and cost dashboards, and calibration curves are all defensible and all absent:
  Principle XI requires a present-tense reason for each, and the five named metrics are what the gates
  actually read.
- **Accuracy is measured over labeled fields only.** An unlabeled prediction is neither correct nor
  wrong; treating it as either would make the accuracy number a function of how completely the dataset
  happens to be labeled.
- **Exact typed equality is the only MVP comparator.** Comparing typed values already absorbs the
  differences that are representational rather than semantic — `1,000.00` and `1000` are the same
  decimal number. Anything beyond that is a judgement about meaning, and a judgement made silently inside a
  scorer is a metric nobody can audit. A near-miss stays visible through FR-026 rather than by being
  scored as correct.
- **Entry alignment is positional unless a key is declared.** Optimal matching between predicted and
  expected entries is a real improvement and a real complication; the honest MVP is a documented default
  plus an explicit key, with the entry-count mismatch surfaced separately (FR-021) so a reader can see
  when positional alignment is misleading them.
- **The evaluation gate stays advisory, and its target size is 50 documents and 500 labeled fields in
  the public tier.** The constitution's fifth quality gate makes it advisory until the golden dataset
  reaches its target size; this milestone defines the metrics, the comparison, and that target, and does
  not flip the gate. The target is measured on the public tier alone, spread across at least two schemas
  with at least twenty documents each, because gate 5 is a CI gate and CI cannot see the restricted
  tier — a gate that flips on data the gate cannot read is not a gate. Five hundred labeled fields puts
  one field outcome at roughly 0.2% of the headline accuracy, which is a judgement about resolution
  rather than a power calculation: it is the point at which a regression worth blocking a merge over is
  larger than the movement of a single field. Flipping the gate is a constitution amendment, not a
  configuration change. The fourth gate — grounding regressions must be justified — is already binding
  and is what FR-047 serves.
- **No quality target is claimed by this milestone either.** It makes accuracy measurable and regressions
  visible. What a good field accuracy is for a given schema is a property of a deployment and a dataset,
  and asserting a number here would be the vague quality claim Principle IX rejects.
- **Corrections are a model and a promotion path, not a system.** Principle IX requires the shape and
  requires corrections to be reusable as dataset signal; the same rule forbids the MVP becoming a review
  platform, and "full review UI" is on the deferred-technology list.
- **Labels are data files, not code.** For the same reason rules are data in Milestone 5: a golden set
  that requires programming to extend will not be extended.
- **The golden set carries its own identity rather than relying on git.** The repository revision is
  recorded too (FR-040), but a dataset referenced by hash may live outside the repository, and a metric's
  denominator must not depend on which checkout the maintainer happened to be on.
- **Evaluation is synchronous, in-process, and offline.** No queue, no worker, no service. Batching
  across documents is iteration, not infrastructure.

## Dependencies

- **ADR-0009 — the resolution of `TODO(GOLDEN_DATASET_LICENSING)`, the constitutional decision that
  gated this milestone.** A vendored public tier that is sufficient on its own for a complete report,
  plus an optional restricted tier referenced by content hash whose absence makes a report partial.
  FR-010 and FR-003 carry its consequences, and it is the source of the target size in FR-009. Recorded
  in constitution v1.3.0, which also states that target size in quality gate 5 and adds the two
  golden-set rules to Principle IX. This is the one dependency that is not code.
- **Milestone 3 (`003-schema-driven-extraction`)** — supplies the schema identities and hashes labels are
  written against, the typed values comparison operates on, the presence/absence distinction that
  separates *missing* from *incorrect*, the prompt hash and model identity every report must record, and
  the untrusted confidence this feature must not read.
- **Milestone 4 (`004-deterministic-grounding`)** — supplies the grounding rate definition this feature
  reuses rather than redefines, the per-result counts it aggregates, the locations FR-038 compares
  against expected ones, and the tier incomparability FR-039 preserves.
- **Milestone 5 (`005-deterministic-validation`)** — supplies the verdicts and counts the report carries,
  and assigned corrections and annotations to this milestone in its own Out of Scope section. Its typed
  errors, provenance conventions, and field-path form MUST be reused rather than duplicated under a
  second incompatible name.
- **Constitution v1.4.0** — Principles III, VIII, IX, X, XI, and XII bind this feature directly.
  Principle IX is the one it exists to satisfy; quality gates 4 and 5 are the ones it makes enforceable.
- **ADR-0002, ADR-0003** — supply the identity model the dataset, prediction set, and report identities
  extend.
- **ADR-0004, ADR-0005** — fix that scores are not comparable across tiers and that model confidence
  routes nothing, both of which constrain what this feature may average.
- **ADR-0008** — fixes why a label written under one schema version says nothing about a result produced
  under another, which is what FR-004 refuses.

## Out of Scope

Explicitly deferred, and MUST NOT appear in this feature:

- Any change to extraction, grounding, or validation behaviour to improve a metric. Measuring and
  improving are separate acts, and a milestone that does both cannot report honestly on either.
- Automatic model selection, prompt tuning, threshold search, calibration fitting, or any training loop
  driven by the metrics.
- A model asked to judge correctness, similarity, or equivalence.
- Precision, recall, F1, confusion matrices, per-provider leaderboards, cost and latency dashboards, and
  calibration curves.
- Confidence calibration and any blended or derived confidence score.
- A review interface, annotation workflow, task assignment, reviewer queue, or correction storage
  service.
- Routing policy, acceptance thresholds, and automatic-versus-human review decisions built on the
  metrics.
- Flipping the constitution's evaluation gate from advisory to blocking.
- Persistence and storage of reports, datasets, or corrections in a database, and any object store
  requirement.
- Queues, workers, background execution, and any HTTP interface or command-line tool — Milestone 7.
- Charts, dashboards, hosted result browsers, and any rendering beyond a machine-readable report and its
  documented example.
- Cross-document evaluation, duplicate detection, and reconciliation against external systems.
