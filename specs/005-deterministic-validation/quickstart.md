# Quickstart & Validation: Deterministic Validation

**Feature**: `005-deterministic-validation` | **Date**: 2026-08-18 | **Plan**: [plan.md](plan.md)

How to run this feature and how to convince yourself it works. Types and invariants are in
[data-model.md](data-model.md); the public surface is in
[contracts/validation-api.md](contracts/validation-api.md).

**No credentials and no network are needed for anything on this page**, and no `Document` either — this
stage reads two artifacts and a schema, and nothing else (FR-005, FR-007).

## Setup

```bash
uv sync --all-extras                # same reason as Milestone 4: the repo suite imports google-genai
uv run pytest                       # expect the Milestone 4 baseline plus this milestone's tests
```

No new dependency arrives with this milestone. The pattern engine is docdoc's own
(`pattern_dialect@1`), which is why there is no `re2` wheel to install and no platform to exclude
(research.md R2).

## The 30-second version

```bash
uv run python examples/validate_invoice.py
```

Builds a one-page invoice, grounds a constructed extraction against it, and validates twice against a
schema declaring `sum(line_items[].amount) == total`. The first run states the total the lines add up
to and prints `valid`; the second states `1240.00` — a transposition a human makes and a model repeats
— and prints:

```text
stated total: 1240.00
  verdict: invalid
  checks: 10 declared, 9 passed, 1 failed, 0 not evaluated
  [error] total: sum_mismatch
      expected 1420.00, got 1240.00
      participants: total, line_items[0].amount, line_items[1].amount
      found on page 0 at Span(start=142, end=149), reading '1420.00'
      box: (0.10, 0.40) - (0.90, 0.43)
```

The last line is the point of the whole product: a failed rule that can be pointed at on the page,
because the location came from the grounding outcome rather than from a second guess (FR-038).

---

## Scenario 1 — Every declared obligation is checked and named (US1, SC-002, SC-004)

**Validates**: FR-008 … FR-025.

```bash
uv run pytest tests/integration/test_validate_invoice.py -k Structural -v
```

**Expected**: a required field the model reported absent produces exactly one finding; a value violating
`enum`, `pattern`, `minimum`, or `min_length` produces exactly one finding each, naming the constraint,
the expectation, and the value; a conforming value produces none — and its check is still present in
`result.checks` with `outcome == passed`.

The last clause is the one to watch. Absence of a finding is not evidence that a rule ran.

## Scenario 2 — The total that does not add up (US2, SC-007, SC-008)

**Validates**: FR-026 … FR-033.

```bash
uv run pytest tests/integration/test_validate_invoice.py -k Arithmetic -v
uv run pytest tests/property/test_decimal_semantics.py -v
```

**Expected**: the sum rule fails when the arithmetic is off and passes when it holds; `1240.0` and
`1240.00` compare equal; a case where binary floating point would disagree with exact decimal produces
the decimal answer; and a rule whose operand is absent is reported `not_evaluated` naming that operand
rather than summing it as zero.

## Scenario 3 — "Valid" cannot mean "nothing ran" (US3, SC-002, SC-009)

**Validates**: FR-009 … FR-012, FR-041.

```bash
uv run pytest tests/unit/test_verdict_derivation.py -v
uv run pytest tests/property/test_counts_reconcile.py -v
```

**Expected**: `declared == passed + failed + not_evaluated` for every randomly generated schema and value
tree; a run containing any `not_evaluated` check reports `incomplete`, never `valid`; and a run with a
failing error-severity check reports `invalid` regardless of how many warnings it also carries.

## Scenario 4 — Grounding is read, never re-decided (US3, SC-010, SC-011)

**Validates**: FR-034 … FR-038.

```bash
uv run pytest tests/unit/test_grounding_policy.py -v
```

**Expected**: a present but ungrounded value produces a warning-severity finding under the default
policy; a value the model reported absent produces none; a finding about a grounded value carries the
span, pages, and boxes byte-identical to the grounding outcome; and the recorded grounding status is
identical before and after validation.

## Scenario 5 — Nothing is repaired, and the model's confidence routes nothing (US3, SC-006, SC-012)

**Validates**: FR-004, FR-045.

```bash
uv run pytest tests/unit/test_no_repair.py -v
```

**Expected**: extraction result, grounding result, and schema are equal before and after validation on
every path, including runs that produce findings; and validating one committed set twice with
`model_confidence` altered produces identical results, byte for byte.

## Scenario 6 — A pattern cannot hang the run (SC-020, research.md R2)

**Validates**: FR-024, FR-056.

```bash
uv run pytest tests/property/test_pattern_dialect.py -v
uv run pytest tests/perf/test_validation_perf.py -m perf -v
```

**Expected**: for patterns inside the dialect, docdoc's matcher and `re.fullmatch` return the same
answer on random inputs (the stdlib is the oracle for *what* matches); a backreference or a lookahead
raises `SchemaError` from `validate()` **before the first check is enumerated**, naming the field and
the construct; and `(a+)+` against 10,000 characters — the input on which CPython's `re` is effectively
non-terminating — completes in milliseconds.

The rejection happens at the entry to validation rather than at schema load, and that is a decision
rather than a shortcut: `docdoc.extraction` may not import `docdoc.validation`, and the dialect belongs
to the layer that evaluates it. What FR-056 requires is that such a pattern never reach verdict time,
which is what the entry check delivers (plan.md design decision 6).

## Scenario 7 — Explain and reproduce a verdict (US4, SC-016, SC-017)

**Validates**: FR-047 … FR-053.

```bash
uv run pytest tests/contract/test_validation_identity.py -v
uv run pytest tests/unit/test_schema_snapshot.py -v      # existing, must pass UNEDITED
```

**Expected**: validating identical inputs twice yields equal artifact ids and equal results; changing the
grounding policy, disabling a rule, editing a rule, or bumping the validator moves the id; and changing
something that cannot affect a verdict does not.

The second command is the one that protects everyone else's stored work, and it is not a new test:
Milestone 3's schema-hash snapshot (`tests/fixtures/snapshots/schema_hashes.json`) already pins the hash
of every committed schema. SC-019 is the requirement that this milestone passes it **without refreshing
the snapshot** — the three committed schemas declare no rules, so their hashes must not move
(research.md R4).

## Scenario 8 — Refusals (SC-015)

```bash
uv run pytest tests/unit/test_validation_refusals.py -v
```

**Expected**: a grounding result validated against an extraction it did not come from is refused with
both artifact ids named; a result whose recorded schema identity or hash differs from the supplied
schema is refused with both named; and neither produces a verdict of any kind.

## Reproducing the research measurements

```bash
uv run pytest tests/perf/test_validation_perf.py -m perf -v      # SC-020's bound
uv run python tests/perf/bench_pattern_dialect.py                 # the R2 table
```

The R2 comparison against `google-re2` is not reproducible from the repository by design: that package
is not a dependency, and the measurement exists to record why it is not.
