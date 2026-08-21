# Contract: The Evaluation API

**Feature**: `006-golden-set-evaluation` | **Date**: 2026-08-20

The normative description of what `docdoc.evaluation` and `docdoc.recording` expose. Where this
document and the code disagree, one of them is a defect — `tests/unit/test_documented_api_references_resolve.py`
is extended to this file so the disagreement fails the build rather than waiting for a reader.

---

## 1. Two packages, and the line between them

```text
docdoc.recording   →  produces a PredictionSet by running the pipeline. Needs a provider.
docdoc.evaluation  →  scores a PredictionSet against a GoldenSet. Needs nothing.
```

`docdoc.recording` imports `docdoc.evaluation`. **The reverse fails the build** — the `import-linter`
layers contract places `recording` above `evaluation`, which is how FR-003's "recording MUST NOT be
part of evaluation" becomes a check rather than a convention.

Everything in §2–§6 runs with no credentials, no network, no provider, no database, and no object
store (FR-007). §7 is the only part that does not, and nothing in §2–§6 imports it.

---

## 2. Scoring

```python
from docdoc.evaluation import evaluate, load_golden_set, load_prediction_set

golden = load_golden_set("datasets/mvp/manifest.json")
predictions = load_prediction_set("datasets/mvp/predictions/")

report = evaluate(golden, predictions)

report.metrics.micro["field_accuracy"].value        # 0.94, or None if the denominator is empty
report.metrics.micro["field_accuracy"].numerator    # 470
report.metrics.micro["field_accuracy"].denominator  # 500
report.report_id                                    # "sha256:…"
```

`evaluate(golden_set, prediction_set, *, options=None, facts=None, repo_revision=...) -> EvaluationReport`

`facts` is what the schemas declare, from `schema_facts(...)`. It affects **no number** — it decides
only which comparator version each outcome records. Typing the *values* is the loader's job, done once
when the dataset and the predictions are read, so a caller who omits it still gets a correct report
rather than a quietly wrong one.

Returns **exactly one** report or raises `EvaluationError`. It never returns a partial report without
`report.partial` being set and naming what was omitted (FR-001).

It **refuses**, naming both sides:

| Condition | Requirement |
|---|---|
| A prediction for a document the golden set does not contain | FR-005 |
| A schema identity or hash differing from the labels' | FR-004, ADR-0008 |
| A provenance field it cannot record | FR-041 |
| A restricted bundle whose label count disagrees with the manifest | EVA-5a |

It **does not refuse**, and instead records: a golden-set document with no prediction (`UNEVALUATED`,
named, in every denominator), and a document that failed to process (labelled fields `MISSING`, in
every denominator). Both are FR-005 and FR-037, and both exist so that crashing on hard documents
cannot raise a score.

---

## 3. What a report guarantees

```python
report.outcomes                    # tuple[FieldOutcome, ...] in a total order
report.metrics                     # DatasetMetrics: micro, macro, per_field_path, group_outcomes
report.document_scores             # per document
report.metrics.validation_verdicts # Milestone 5's verdict distribution, reused
report.metrics.validation_counts   # and its counts — None when nothing was validated
report.dataset_size                # per tier, never merged, on every report (FR-009)
report.group_outcomes              # reads through to metrics; EVA-23's surface
report.outcomes_for(document_id)   # one document's outcomes, in the report's order
report.groups_for(document_id)     # and its repeating groups (EVA-19)
report.partial                     # PartialDeclaration | None
report.redacted_tiers              # which tiers carried hashes instead of values
report.provenance                  # everything FR-040 lists
```

- **Every metric states its numerator and denominator** (FR-029). A metric with an empty denominator is
  `None`, never `0.0` (FR-032).
- **Every aggregate is reachable from the outcomes that produced it** without re-running (FR-055).
  Summing the per-field outcomes reproduces every dataset total exactly (SC-004).
- **Micro and macro are both reported and both labelled** wherever they can differ, and the macro side
  states how many documents it averaged over and how many were undefined (FR-031, EVA-18a).
- **Byte-identical on every run and every platform** for fixed inputs and versions (FR-043). The order
  is total and independent of hash seed.
- **Inputs are byte-identical before and after** (FR-006). Evaluation writes nothing.

---

## 4. Comparison

```python
from docdoc.evaluation import compare

delta = compare(before, after)

delta.metrics["field_accuracy"].judgement   # "improved" | "unchanged" | "regressed"
delta.grounding_regression                  # named, not one row among many (FR-047)
delta.provenance_differences                # ("model_version", "prompt_hash")
delta.changed_outcomes                      # both directions
```

`compare(before, after) -> Comparison`

**Refuses**, naming both sides, when `golden_set_id`, a schema identity or hash, or
`metric_definition_version` differs, or when one report is partial and the other is not (FR-046). It
does not silently diff numbers that do not mean the same thing.

**States what moved and decides nothing about it** (FR-049). Whether a build fails is policy configured
on top of this output. That is not a limitation to be fixed later — a comparison that also decided
would make the constitution's quality gates unreadable, because the decision would be buried in the
thing being measured.

**`None` is not zero.** Where a metric is undefined on one side, the judgement is `became_defined` or
`became_undefined`, never a subtraction (EVA-28c).

---

## 5. Corrections

```python
from docdoc.evaluation import Correction, promote

correction = Correction(
    document_id=..., report_id=..., field_path="total",
    predicted_value="1240.00", corrected_value="1249.00",
    location=..., reason="misread the thousands separator",
    annotator="jh", timestamp=...,
)

corrected = promote(golden, [correction])   # a NEW GoldenSet, a NEW golden_set_id
```

- A correction carries the seven fields the constitution requires and names the exact run and result it
  corrects (FR-050, FR-051).
- It **alters nothing** it annotates and **moves no metric** until promoted (FR-052). The scorer never
  reads corrections; there is no path by which one could leak into a number.
- `promote` returns a new golden set. It does not mutate (FR-053). Reports either side of a promotion
  are not comparable without the difference being visible, which §4's refusal enforces.

**Not provided, deliberately** (FR-054): a review interface, assignment, workflow, queue, or storage
service. Principle IX permits corrections as a model and forbids the MVP becoming a review platform.

---

## 6. What this layer will not do

Stated because each is a thing a reader might reasonably expect:

- **It does not ask a model anything** (FR-008) — including whether a predicted value *means* the same
  as the expected one. A model judging its own output is the failure Principle II forbids for grounding
  and it is no more acceptable when the subject is accuracy.
- **It does not read `model_confidence`** (FR-028, ADR-0004). Untrusted upstream, untrusted here.
- **It does not re-derive, re-score, re-ground, or re-validate** (FR-002). It reads recorded facts.
- **It does not open a document.** It has no need to: it compares labels against recorded predictions.
- **It does not normalize, case-fold, trim, round, or coerce** to make a value match (FR-024).
- **It does not define a second grounding rate** (FR-033). Milestone 4's definition and its recorded
  counts are reused.
- **It does not change extraction, grounding, or validation** to improve a metric. Measuring and
  improving are separate acts, and a milestone that does both can report honestly on neither.
- **It does not persist anything.** No database, no object store, no cache (Out of Scope).
- **It does not decide** what should happen about a regression.

---

## 7. Recording — the only part that touches a provider

```python
from docdoc.recording import record_predictions

predictions = record_predictions(golden, adapter=adapter, restricted_root=path)
```

`record_predictions(golden_set, *, adapter, registry, documents=None, root=None, include_restricted=False) -> PredictionSet`

`documents` supplies already-parsed documents and **skips the parse**, which is how the offline suite
records without a PDF reader — and how a caller who already holds parsed documents avoids re-parsing
them, since there is no `ArtifactStore` to do it for them.

Runs parse → extract → ground → validate for each document and records the result. It is:

- **opt-in** — nothing in §2–§6 calls it, and scoring never requires it;
- **the only path for the restricted tier**, whose predictions carry document content and therefore
  cannot be committed;
- **not part of evaluation**, enforced by the layers contract rather than by this sentence.

A document that fails at any stage is recorded with its `failed_stage` and the typed error's class
name — never dropped, because a dropped failure becomes an accuracy improvement (EVA-9a).

**Why this exists at all.** Without it, "how was the committed prediction set produced?" has no
answer in the repository, and refreshing it after a prompt or model change becomes an ad-hoc act
nobody can reproduce. That is the same argument ADR-0003 makes for recording options in an artifact
id, applied one level up.

---

## 8. Errors

`EvaluationError(DocdocError)` — one type, carrying `dataset`, `document_id`, `field_path`, `expected`,
and `actual` as attributes rather than only in the message.

**Never retried** (FR-060). There is no transient failure mode in a deterministic, offline computation,
and the constitution's error model permits retries for LLM and network calls only.

An **error** is a statement about the request; a **field outcome** is a statement about the document.
Neither substitutes for the other, and collapsing them is how a mismatched pair produces a confident
report (EVA-30b).
