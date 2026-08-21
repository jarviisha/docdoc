# Evaluation

Extraction answers *what did the model find?* Grounding answers *where is it?*
Validation answers *is this acceptable?* Evaluation answers **how good is it,
actually** — and *did the last change make it worse*.

Until this stage shipped, every quality claim in this repository was an
assertion. Milestone 4 made the grounding rate computable and set no target.
Milestone 5 did the same for validation. Both deferred the same question here.

```python
from docdoc.evaluation import evaluate, load_golden_set, load_prediction_set

golden = load_golden_set("datasets/mvp/manifest.json")
predictions = load_prediction_set("datasets/mvp/predictions")

report = evaluate(golden, predictions)

report.metrics.micro["field_accuracy"].value        # 0.9286, or None if nothing was asked
report.metrics.micro["field_accuracy"].numerator    # 26
report.metrics.micro["field_accuracy"].denominator  # 28
report.report_id                                    # "sha256:…"
```

There is no `document` parameter and no adapter. Evaluation compares recorded
labels against recorded predictions, so it needs no parser, no credentials, and
no network — which is what makes the whole path runnable from a checkout.

## Measuring and improving are separate acts

This milestone changes nothing in extraction, grounding, or validation. A
milestone that both measures and improves can report honestly on neither: every
number it produces was computed by code with an interest in the result, and there
is no earlier run to compare against because the earlier code is gone.

That is also why `compare()` **states what moved and decides nothing about it**.
Whether a build fails is policy configured on top of this output. A comparison
that also decided would bury the decision inside the thing being measured.

## Six outcomes, and why none may be collapsed

Every labelled field resolves to exactly one:

| Outcome | Means |
|---|---|
| `correct` | label and prediction agree — a matching value, or a correctly reported absence |
| `incorrect` | the label states a value, the prediction states a different one |
| `missing` | the label states a value, the prediction reports absent |
| `spurious` | the label asserts absence, the prediction states a value |
| `unlabeled` | the prediction exists, the golden set says nothing |
| `unevaluated` | there is no prediction for this document at all |

Each pair a looser design would have merged is a pair with different fixes:

- **`missing` and `incorrect`** — a blank and a wrong answer are different
  failures. One says the model found nothing; the other says it found the wrong
  thing. Merging them makes "why is accuracy down?" unanswerable.
- **`spurious` and `incorrect`** — inventing a value for a field that should be
  empty is not the same as misreading one that should not.
- **`unlabeled` and `correct`** — a prediction the golden set says nothing about
  is neither right nor wrong. Treating it as either makes accuracy a function of
  how completely the dataset happens to be labelled rather than of how well the
  pipeline performed.
- **`unevaluated` and everything else** — a document with no prediction is not a
  document that failed. Both stay in every denominator; only one is a defect.

**A crash cannot raise a score.** A document that fails mid-pipeline has its
labelled fields counted as `missing`, and a document with no prediction is
`unevaluated`. Neither leaves a denominator. That rule is the single most
important thing in this stage, because the failure it prevents — dropping the
documents you crashed on, so the score goes up — is invisible in every individual
number.

## The metrics, and their denominators

Labels partition into **V** (stating a value) and **A** (asserting a correct
absence). `unlabeled` is in neither.

| Metric | Numerator | Denominator |
|---|---|---|
| `field_accuracy` | correct (value + absence) | \|V\| + \|A\| |
| `coverage` | correct_value + incorrect | \|V\| |
| `missing_rate` | missing | \|V\| |
| `incorrect_rate` | incorrect | \|V\| |
| `grounding_rate` | exact + fuzzy | exact + fuzzy + ungrounded |
| `spurious_rate` | spurious | \|A\| |
| `unevaluated_rate` | unevaluated | \|V\| + \|A\| |
| `mislocation_rate` | disagrees | agrees + disagrees |

The definitions live in `definitions.py` as **data behind a version**, not as
formulas inside the scorer. The denominators are the part a future contributor
will want to "improve" when a number looks unflattering, and putting them behind
`metric_definitions@1` turns that edit into a visible, comparison-breaking act
instead of a step in a chart nobody can explain.

**The grounding rate is Milestone 4's, reused.** Its recorded counts are summed,
never recomputed from outcomes. Two grounding rates in one system is worse than
none: they diverge, and every conversation then starts by establishing which
number somebody is quoting.

**`not_applicable` stays outside the denominator.** A value the model correctly
reported absent is not a grounding failure.

### An empty denominator is `None`, never `0.0`

A rate of zero and an unasked question are different facts, and only one is bad
news. "Mislocation rate: 0.00" reads as *nothing was mislocated*; if the dataset
states no expected locations at all, the true answer is *we did not ask*. Two of
the three substitutions are silently reassuring, and a dashboard cannot tell them
from the real thing.

### Micro and macro are both reported

Micro pools every outcome and divides — one field, one vote. Macro averages the
per-document metrics — one document, one vote. They differ whenever documents
carry different numbers of labels, so both are reported and both are labelled.

A macro average also states **how many documents it averaged over and how many
were undefined**. A document whose own metric is undefined cannot enter a mean,
and excluding it silently makes the macro number describe an unstated subset.

Every `MetricValue` carries an `averaging` field naming which of the two it is,
so a number cannot be quoted out of a report without saying how it was computed.

## What an outcome carries

Each `FieldOutcome` records the comparison that produced it, so a near-miss is
diagnosable without re-running anything:

```python
outcome.kind                 # one of the six
outcome.expected             # what the label said — None when correct, or when redacted
outcome.predicted            # what the run produced
outcome.comparator_version   # "exact@1" — which rule decided this
outcome.grounding_status     # copied from Milestone 4, never recomputed
outcome.location_agreement   # a separate axis, or None when the label stated no location
```

`comparator_version` travels with the outcome because a future leniency must be
visible in the report rather than only in the code. `grounding_status` and
`grounding_score` are **copied**: whether a value is correct and whether it is
grounded are independent facts, so a correct but ungrounded value is `correct`
here and `ungrounded` in the grounding rate.

## Comparison is exact, and refuses when it cannot be

`comparators@1` is exact equality on the **typed** value, with a type-identity
gate in front of it:

```python
equal(a, b) := type(a) is type(b) and a == b
```

The gate is the requirement, not defensive coding. In Python `True == 1`,
`Decimal(1) == True`, and `1 == 1.0` are all true, so without it a boolean label
silently matches an integer prediction and the report calls it correct.
`isinstance` is actively wrong here, because `bool` subclasses `int`.

What the gate does *not* do is reject representational difference within a type:
`Decimal("1240.00") == Decimal("1240.0")` is true, and should be. Trailing zeros
are representation, not value.

**No normalization, case-folding, trimming, rounding, or cross-type coercion.**
Any future leniency is a new comparator with a new version, recorded next to
every metric it affected.

## Location agreement has three values

A separate axis from the outcome above, and it must stay separate: a value can be
correct and mislocated, or wrong and perfectly located.

| Value | Means |
|---|---|
| `agrees` | the expected page is present, and the box test passed or no box was stated |
| `disagrees` | grounded, but not where the label says |
| `not_assessable` | the label states a box and the parser supplied no geometry |

`page_box@1` requires the expected page to appear in the outcome's pages; where
the label states a box and the outcome carries geometry, at least **half** the
recorded area on that page must lie inside the expected box.

**Containment, not IoU.** A human labelling a value draws a loose box; docdoc's
geometry is tight on the tokens. IoU punishes exactly that pairing — a perfectly
located tight box inside a generous hand-drawn one scores far below 0.5 IoU while
being completely right.

**`not_assessable` is never reported as `disagrees`.** `geometry is None` means
the parser supplied none; `()` means geometry exists and covers no tokens.
Collapsing the first into a disagreement would report a parser's silence as a
grounding error, making the mislocation rate a function of which parser ran.
Not-assessable outcomes leave the mislocation denominator entirely.

## Two tiers, and what a partial report means

A public repository cannot ship real customer invoices, and an evaluation only
maintainers can run is not a product feature. [ADR-0009](../adr/0009-golden-dataset-licensing.md)
resolved that with two tiers:

- **public** — vendored into the repository, evaluable with no credentials and no
  network, and **sufficient on its own for a complete report**.
- **restricted** — never committed, referenced by content hash. Its labels and
  predictions are supplied by whoever holds the corpus.

A run without the restricted bundle is **partial**, and says so:

```python
report.partial.skipped_documents  # ("restricted-invoice-001", "restricted-invoice-002")
report.partial.skipped_tiers      # (Tier.RESTRICTED,)
report.partial.covered_labels     # 28
report.partial.declared_labels    # 48
```

The declaration names the skipped documents and tiers and states the covered
fraction as **exact integers** — `covered_labels` of `declared_labels` — because every document
commits a `declared_label_count` even when its labels are not committed. Without
that number a checkout could count restricted *documents* and would have to guess
at their labels, so a partial report would be estimating its own denominator.

A partial report cannot be compared against a full one. The smaller number is not
worse; it is *less*.

**Disclosure follows the tier, not the caller.** An outcome on a restricted
document carries `expected_hash` and `predicted_hash` instead of values, and the
report names the tiers it redacted. Were it a caller argument, the day someone ran
without the flag a restricted corpus's contents would be in a report, and the
dataset's terms would be enforced by memory.

## What a report guarantees

- **The dataset's size travels with every report**, per tier and never merged:
  `report.dataset_size` gives documents and labelled fields for each tier, on
  complete and partial runs alike. The constitution's fifth quality gate turns
  blocking at a target size, and a target nobody can read off a report is a
  target nobody can apply. It describes the *dataset*; `report.partial` describes
  what the *run* covered, and conflating the two would leave a reader unable to
  tell a small dataset from a large one measured narrowly.
- **Milestone 5's verdicts and its counts are both carried**, reused rather than
  recomputed. `validation_verdicts` says how many documents came out `invalid`;
  `validation_counts` says how many checks ran, passed, failed, or could not be
  evaluated. Only the second distinguishes a document that failed one check from
  one where nothing could be checked — which is the distinction Milestone 5 added
  a third verdict to preserve. It is `None`, not zeroed, when nothing was
  validated: zeroed counts reconcile perfectly and read as "everything passed".
- **A report navigates to its own parts.** `report.group_outcomes` and
  `report.validation_verdicts` read through to the metrics that hold them, and
  `report.outcomes_for(document_id)` / `report.groups_for(document_id)` answer
  "which fields were wrong on *this* document?" — the question a per-document
  score prompts next. The storage stays normalized: outcomes live in one flat,
  totally ordered tuple, because keeping a second copy per document would double
  the bytes FR-043 requires to be identical, to save a filter the report performs
  itself.
- Every metric states its numerator and denominator.
- Every aggregate is reachable from the outcomes that produced it, without
  re-running anything. Summing the per-field outcomes reproduces every dataset
  total exactly — **checked at runtime, on every report**, across the three
  independently computed views (the flat outcome list, the per-document slices,
  and the per-field-path grouping). A disagreement between them is not a rounding
  difference; it is a lost or double-counted outcome, and it is refused rather
  than reported.
- Byte-identical on every run and every platform for fixed inputs and versions.
  The outcome order is total — `line_items[2]` precedes `line_items[10]`, and
  nothing depends on hash seed.
- Inputs are byte-identical before and after. Evaluation writes nothing.
- Seventeen provenance fields, or the run is **refused** rather than reported. A
  metric whose origin is unknown is the vague quality claim Principle IX rejects
  as evidence, and recording a null would be that claim wearing a field name.

## Corrections

A reviewer who sees a wrong value and knows the right one can record it:

```python
from docdoc.evaluation import Correction, promote

corrected = promote(golden, [correction])   # a NEW GoldenSet, a NEW golden_set_id
```

A correction carries the seven fields the constitution requires and names the
exact run and result it corrects. It **alters nothing** it annotates and **moves
no metric until promoted** — the scorer never reads corrections, and `evaluate()`
has no parameter that would let one in.

Promotion returns a new golden set with a new identity, which makes reports
either side of it visibly incomparable rather than silently different.

**Not provided, deliberately**: a review interface, assignment, workflow, queue,
or storage service. Principle IX permits corrections as a model and forbids the
MVP becoming a review platform.

## What this stage will not do

- **Re-derive anything.** Not the extraction, not the grounding, not the
  validation. It reads recorded facts; a stage that quietly recomputed one would
  be reporting on a pipeline nobody ran.
- **Ask a model anything** — including whether a predicted value *means* the same
  as the expected one. A model judging its own output is the failure Principle II
  forbids for grounding, and it is no more acceptable when the subject is
  accuracy.
- **Read `model_confidence`.** Untrusted upstream by ADR-0004, untrusted here.
- **Open a document.**
- **Average an exact score against a fuzzy one.** An exact score is `1.0` by
  definition and a fuzzy score is measured, so a mean over both means nothing.
- **Persist anything.** No database, no object store, no cache.
- **Decide** what should happen about a regression.

## Recording — the only part that touches a provider

```python
from docdoc.recording import record_predictions

predictions = record_predictions(golden, adapter=adapter, registry=registry)
```

`docdoc.recording` runs parse → extract → ground → validate and records what came
back. It is **opt-in**, it is the **only path for the restricted tier**, and it is
**not part of evaluation** — enforced by the `import-linter` layers contract,
which places it *above* `docdoc.evaluation` so that `evaluation → recording` fails
the build.

A document that fails at any stage is recorded with its `failed_stage` and the
typed error's class name — never dropped, because a dropped failure becomes an
accuracy improvement.

**Known limitation.** There is no `ArtifactStore`, so every recording run
re-parses every document. On the public tier, which uses the `echo` adapter, that
costs nothing. On a restricted tier reached through a real provider it is a
repeated, billable cost. Recorded here so it is known rather than discovered on
an invoice.

## The committed dataset, and its distance from the gate

`datasets/mvp/` holds **4 public documents and 28 labelled fields**, across two
schemas, plus 2 restricted documents declaring 20 more.

The constitution's fifth quality gate targets **50 documents and 500 labelled
fields**. This milestone built the machinery and a dataset large enough to
exercise every code path; it did not build a dataset at the target size, because
that is dataset authoring work rather than implementation work. The distance is
stated here, and in `manifest.json`, so it is a number a reader can see rather
than a gap nobody mentions. **The gate stays advisory until the dataset reaches
that size.**

## Adding a document to the golden set

The test of this feature that no test can run: a maintainer adds a document and
its labels **without reading the implementation and without writing code**. If
step 3 below requires opening Python to know what to write, the format is the
defect — not the maintainer.

### 1. Put the document under `datasets/mvp/documents/`

### 2. Add an entry to `manifest.json`

```json
{
  "document_id": "invoice-003",
  "blob_sha256": "sha256:9f2c…",
  "tier": "public",
  "origin": {
    "kind": "synthetic",
    "basis": "Synthetic. Written for this repository's golden set; no real document content.",
    "source": "datasets/mvp/make_dataset.py",
    "generator_id": "datasets.mvp.make_dataset",
    "generator_version": "1.0.0"
  },
  "schema_identity": "invoice@1",
  "schema_hash": "sha256:4b1e…",
  "path": "documents/invoice-003.pdf",
  "declared_label_count": 5
}
```

`basis` is **not optional and not a formality**: it is the answer to "on what
grounds may docdoc use this document", and a document whose provenance cannot be
stated is refused at load. `generator_id` and `generator_version` are required
when `kind` is `synthetic` — a synthetic document that cannot be regenerated has
labels nobody can verify.

`declared_label_count` must match the number of labels in step 3. For a
restricted document it is committed *without* the labels, which is what lets a
partial report state its covered fraction exactly.

### 3. Add its labels to `labels/invoice-003.json`

```json
[
  {
    "field_path": "invoice_number",
    "expectation": "value",
    "value": "INV-003",
    "location": {"page": 0, "bbox": [0.0, 0.0, 1.0, 0.5]},
    "labeler": "your-name",
    "labeled_at": "2026-08-20T00:00:00"
  },
  {
    "field_path": "total",
    "expectation": "value",
    "value": "1240.00"
  },
  {
    "field_path": "line_items[0].amount",
    "expectation": "value",
    "value": "1000.00"
  },
  {
    "field_path": "supplier.tax_id",
    "expectation": "absent"
  }
]
```

Four things worth knowing before you write the first one:

- **Field paths are Milestone 3's form**, entry indices included:
  `line_items[2].amount`. A path the schema does not declare is refused at load.
- **A label says exactly one of two things**: it holds this value
  (`"expectation": "value"`), or it is correctly absent (`"expectation":
  "absent"`). A field you write no label for is a third state — `unlabeled` — and
  enters no accuracy denominator. Leaving a field unlabelled is a legitimate
  choice, not an omission.
- **Decimals, dates, and date-times are written as strings**, and are typed by
  the schema when the dataset loads: `"1240.00"` becomes `Decimal("1240.00")`
  because `total` is declared `decimal`. Writing `1240.00` as a JSON number is
  refused, because the float conversion is lossy and an invoice total must not
  inherit the loss.
- **`location` is optional**, and never a text offset. Offsets move when a parser
  changes how it extracts text, so a label pinned to one would turn a parser
  upgrade into mass mislocation that is not mislocation.

`labeler` and `labeled_at` are optional and cannot change a metric — they do not
enter `golden_set_id`, so fixing a typo in a name does not break comparison.

### 4. Record a prediction set

```bash
uv run --extra pdf python datasets/mvp/make_dataset.py
```

The only step that runs the pipeline. It needs the `pdf` extra because it parses
the documents; it does **not** need a provider, because the public tier is
recorded with the `echo` adapter.

### 5. Score it

```bash
uv run python examples/evaluate_golden_set.py
```

Every metric with its numerator and denominator, plus the `report_id`.

## See also

- [Evaluation API contract](../../specs/006-golden-set-evaluation/contracts/evaluation-api.md)
- [Validation](validation.md) — the verdicts this stage counts and never recomputes
- [Grounding](grounding.md) — the grounding rate this stage reuses
- [ADR-0003](../adr/0003-content-addressed-artifact-chain.md) — the identity shape `report_id` borrows
- [ADR-0004](../adr/0004-two-confidence-signals.md) — why `model_confidence` is unread
- [ADR-0008](../adr/0008-schema-evolution-policy.md) — why a schema hash mismatch is refused
- [ADR-0009](../adr/0009-golden-dataset-licensing.md) — the two tiers, and why
