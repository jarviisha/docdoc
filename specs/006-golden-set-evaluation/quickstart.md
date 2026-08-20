# Quickstart: Golden-Set Evaluation

**Feature**: `006-golden-set-evaluation` | **Date**: 2026-08-20

Nine scenarios that prove this feature works. **None of them skip, and none of them need a credential,
a network connection, a database, or an object store.** That is the property under test as much as any
number they print — SC-022 says a contributor runs 100% of this feature's tests and 100% of its
documented examples, and a scenario that skipped would be the first thing to make that false.

## Prerequisites

```bash
uv sync --extra dev
```

No `--extra google`, no `--extra azure`, no `--extra pdf`. If any scenario below starts needing one,
that is the bug, not the setup instruction.

---

## Scenario 1 — Score the committed public tier (US1, SC-001)

The 30-second version, and the one a first-time contributor runs.

```bash
uv run python examples/evaluate_golden_set.py
```

Expected: a report over the committed public tier, printing field accuracy, coverage, missing rate,
incorrect rate, and grounding rate — each with its numerator and denominator — plus the report id.

**What it proves.** The whole path runs from a checkout alone. Watch for the denominators: a metric
printed without one, or a metric printed as `0.00` where it should read `undefined`, is FR-029 or
FR-032 broken.

---

## Scenario 2 — Every metric matches a hand-computed value (SC-003)

```bash
uv run pytest tests/unit/test_metric_definitions.py -v
```

A fixture dataset small enough to compute by hand — the expected values are written as literals in the
test, not derived by the code under test. Checks all five metrics at dataset, document, and field-path
level.

**What it proves.** The arithmetic is right. A test that recomputed the expected value with the same
function it was testing would pass on any consistent mistake; this one cannot.

---

## Scenario 3 — The aggregates reconcile, for any dataset (SC-004)

```bash
uv run pytest tests/property/test_metrics_reconcile.py -v
```

Hypothesis generates datasets and prediction sets; the test sums the per-field outcomes and asserts
they reproduce every reported aggregate, and that `coverage + missing_rate + unevaluated_rate == 1`
exactly over the value labels (EVA-17c).

**What it proves.** No aggregate is reachable only by trusting the reporter. This is the test that
catches a denominator edit that looked right.

---

## Scenario 4 — Failing a document lowers the score (SC-006)

```bash
uv run pytest tests/unit/test_failures_count.py -v
```

A fixture whose hardest document fails to process. The test asserts the score is **lower** than the
same dataset with that document removed.

**What it proves.** FR-037 and FR-005 hold: a crash cannot be laundered into an accuracy improvement by
shrinking the denominator. This is the single most important test in the feature, because the failure
it guards against is invisible in any individual number.

---

## Scenario 5 — Two runs are byte-identical (SC-009)

```bash
PYTHONHASHSEED=0     uv run pytest tests/property/test_report_determinism.py -q
PYTHONHASHSEED=12345 uv run pytest tests/property/test_report_determinism.py -q
```

Both must produce the same report bytes and the same `report_id`.

**What it proves.** Scoring is deterministic even though what it scores was not. Two hash seeds because
`PYTHONHASHSEED` randomises string hashing, which is what would make a dict-order dependency show up
on someone else's machine and not on yours — the same reason `.github/workflows/ci.yml` already pins
two seeds for Milestone 4.

---

## Scenario 6 — Mismatches are refused, not approximated (SC-010, SC-013)

```bash
uv run pytest tests/unit/test_evaluation_refusals.py -v
```

Five refusals, each asserting both sides are named in the error: a prediction for an unknown document;
a differing schema identity; a differing schema hash; a comparison across dataset identities; a
comparison of a partial report against a full one.

**What it proves.** A label written under `invoice@1` says nothing about a result produced under
`invoice@2` (ADR-0008), and the system says so rather than producing a number.

---

## Scenario 7 — A partial run knows exactly what it missed (SC-016)

```bash
uv run pytest tests/unit/test_partial_reports.py -v
```

Runs against a manifest declaring a restricted tier, with no restricted bundle supplied.

Expected: `report.partial` is set, names the skipped documents and tiers, and states the covered
fraction as exact integers — `covered_labels` of `declared_labels`.

**What it proves.** The declared label counts committed in the manifest (EVA-5a) are what make the
covered fraction exact rather than estimated. Without them a checkout could count restricted
*documents* but not their labels, and a partial report would have to guess at its own denominator.

---

## Scenario 8 — The scorer touches no network (FR-007)

```bash
uv run pytest tests/unit/test_scoring_is_offline.py -v
```

Scores the committed public tier with `socket.socket` patched to raise.

**What it proves, and what the import contract proves instead.** These are two different checks and
neither replaces the other. `lint-imports` catches the graph-visible mistake — importing
`docdoc.extraction` as a package pulls in its adapter registry and a provider SDK, and the contract
fires on it. This test catches what a graph cannot see: a module that opens a socket by hand, or
reaches a network through something the contract does not name. Run both. See research.md R1, which
records that an earlier draft had the relationship between them backwards.

---

## Scenario 9 — A regression is visible and attributable (US2, SC-014, SC-015)

```bash
uv run python examples/compare_reports.py
```

Scores one prediction set twice with one deliberate degradation between them.

Expected: the comparison names every changed field outcome in both directions and no unchanged ones;
the fall in grounding rate appears as a **named regression**, not as one delta among many; and
`provenance_differences` names which versions differed, so the change is attributable rather than
merely coincident.

**What it proves.** The constitution's fourth quality gate is enforceable. A gate cannot read a table
of deltas looking for the row that matters.

---

## The whole suite

```bash
uv run pytest                        # unit + property + contract + integration
uv run pytest -m perf                # the performance tier
uv run lint-imports                  # layer direction, including evaluation ⊁ recording
uv run mypy src/docdoc
uv run ruff check .
```

`lint-imports` is listed here rather than left to CI because it is the check that enforces FR-003
structurally: `docdoc.evaluation` importing `docdoc.recording` is what "recording is not part of
evaluation" means in practice, and it fails here before it fails in review.

---

## Adding a document to the golden set (SC-023)

The test of this feature that no test can run: a maintainer adds a document and its labels **without
reading the implementation and without writing code**.

1. Put the document under `datasets/mvp/documents/`.
2. Add an entry to `manifest.json`: its `document_id`, `blob_sha256`, `tier`, `schema_identity`,
   `schema_hash`, `path`, `origin` — including the **basis on which docdoc may use it**, which is not
   optional and not a formality (FR-011) — and its `declared_label_count`.
3. Add its labels to `labels/<document_id>.json`. Field paths are Milestone 3's form, entry indices
   included: `line_items[2].amount`.
4. Record a prediction set: `uv run python -m docdoc.recording …` (the only step needing a provider).
5. `uv run python examples/evaluate_golden_set.py`.

If step 3 requires reading Python to know what to write, FR-022 is not met and the format is the
defect — not the maintainer.
