# Public API Contract: Validation Layer

The surface `docdoc.validation` exposes, and the promises each part makes. Written against
[spec.md](../spec.md) and [data-model.md](../data-model.md); where the two disagree, the spec wins.

Everything here runs offline: no network, no credentials, no provider, no database, and — unlike every
earlier stage — no `Document` (FR-005, FR-007).

## 1. Entry point

```python
from docdoc.validation import validate

result = validate(extraction, grounding, schema, options=None)
```

| Parameter | Type | Notes |
|---|---|---|
| `extraction` | `ExtractionResult` | Milestone 3's artifact. Read, never modified |
| `grounding` | `GroundingResult` | Milestone 4's artifact, and this stage's input in the ADR-0003 chain |
| `schema` | `Schema` | The schema the extraction was produced under. Checked, not assumed (§6) |
| `options` | `ValidationOptions \| None` | Defaults to the documented policy of data-model §7 |

Returns exactly one `ValidationResult`, or raises. There is no partial verdict (FR-001).

The call is synchronous, in-process, deterministic, and free of any clock on its result path — a
monotonic clock is read once, for the log event's duration, exactly as grounding does.

## 2. The verdict

```python
result.verdict            # Verdict.VALID | Verdict.INVALID | Verdict.INCOMPLETE
result.counts.declared    # every obligation this schema placed on this result
result.counts.failed
result.counts.not_evaluated
```

| Verdict | Means |
|---|---|
| `valid` | every declared check ran, and none failed at error severity |
| `invalid` | at least one check failed at error severity |
| `incomplete` | nothing failed at error severity, but at least one check could not be evaluated |

`incomplete` is not a soft `valid`. It is the state that stops a run where the rules never fired from
being indistinguishable from one where they fired and passed (FR-041). A consumer that treats
`verdict != INVALID` as acceptance has opted into unchecked results, and the type does not hide that
choice — there is no boolean anywhere in the result to make it by accident (FR-042).

## 3. Checks and findings

```python
result.checks     # tuple[CheckOutcome, ...] — every declared check, passed ones included
result.findings   # tuple[Finding, ...]      — the non-passing ones, in a total order
```

`checks` answers "did this rule run?" months later; `findings` answers "what is wrong?" now. The second
is derived from the first, so they cannot disagree.

A finding carries the field path, the check id, a closed reason code, the severity, the declared
expectation, the actual value, every participating field, and — where the value grounded — the span,
pages, and boxes **copied from the grounding outcome** (FR-038). A validation failure is therefore
something a reviewer can be shown on the page, not merely told about.

```python
for f in result.findings:
    print(f.field_path, f.reason, f.expected, f.actual, f.span)
```

Ordering is total: anchor position in schema declaration order, then entry index, then check id
(FR-043). It does not vary with platform, hash seed, or dict order.

## 4. What is checked

| Check kind | Source | Default severity |
|---|---|---|
| `required` | `FieldSpec.required` | error |
| `constraint` | `FieldSpec.constraints` — `enum`, `const`, `pattern`, `minimum`, `maximum`, `multiple_of`, `min_length`, `max_length` | error |
| `rule` | `Schema.rules` — the closed vocabulary of data-model §1 | error, overridable per rule |
| `grounding` | the recorded `GroundingStatus` against the run's policy | warning (ungrounded), info (fuzzy) |
| *(any kind)* | a check that could not be evaluated | warning — the verdict takes `incomplete` from the outcome, not from this severity |

Comparisons are exact: no case folding, no trimming, no coercion across declared types (FR-021).
Numeric work is `Decimal` throughout; a `number` field's `float` enters as `Decimal(str(value))`, and
`number` is documented as lossy by declaration — `decimal` is the type for money (research.md R3).

`min_length` and `max_length` count **Unicode code points** on a string and **entries** on a repeating
group — not bytes, which would make one bound mean different things in different scripts, and not
grapheme clusters, which would need a dependency and a version of their own (FR-023).

`pattern` is evaluated against the **whole value** under `pattern_dialect@1`, docdoc's own documented,
versioned, linear-time subset. Backreferences, lookaround, named groups, and inline flags are not in
the dialect. Every declared pattern is compiled at the **entry** to `validate()`, before the first
check is enumerated, and one outside the dialect raises `SchemaError` naming the field and the
construct — never silently at verdict time (FR-024, FR-056).

## 5. Nothing is repaired

No value is corrected, clamped, coerced, rounded, trimmed, defaulted, or dropped — on the success path
or any failure path (FR-004). A value that would pass only after adjustment fails. Validation reports;
it does not fix, and it does not ask a model to try again (FR-003).

Absent operands are not zero. A sum rule whose entry is missing an amount is reported `not_evaluated`
with the entry named, because summing a missing amount as zero is how a wrong total passes (FR-031).

## 6. Errors

| Raised | When |
|---|---|
| `ValidationError` | the grounding result was not produced from the supplied extraction result; the recorded schema identity or hash differs from the supplied schema; the value tree does not fit the schema |
| `SchemaError` | *at schema load*: an unrecognised rule kind, a duplicate rule id, an unresolvable or wrongly scoped operand, a constraint on an incompatible type. *At the entry to `validate()`, before any check is enumerated*: a pattern outside `pattern_dialect@1` — the dialect belongs to this layer, and the layer below may not import it |

Both name both sides of the mismatch. Neither is retried (FR-055). A failing check never raises, and an
error is never returned as a finding (FR-044) — a finding is a statement about the document, an error is
a statement about the request.

## 7. Options and identity

```python
ValidationOptions(grounding_policy=GroundingPolicy(ungrounded=Severity.ERROR), enabled_rules=None)
```

Both fields change verdicts, so both fold into `options_hash`, and

```text
artifact_id = sha256(grounding_artifact_id + validator_id + validator_version + options_hash)
```

chains this stage onto the grounding artifact (ADR-0003). Changing the policy, disabling a rule, editing
a rule, changing the vocabulary or the pattern dialect, or bumping the validator all move the id;
changing something that cannot affect a verdict does not (FR-048, SC-017).

`result.provenance` records every one of those inputs, so a verdict can be explained without re-running
it (FR-049).

## 8. Observability

One structured event per run, successful or refused, on the `docdoc.validation` logger: identities,
versions, per-outcome and per-severity counts, duration, verdict. **No values.** Findings carry values
because that is what a finding is for; logs carry counts (FR-057).

## 9. What this layer will not do

- Repair, normalize, or re-ask (§5).
- Decide what happens to an invalid result — routing, escalation, and review are policy built on the
  verdict, not part of producing it (FR-046).
- Read `model_confidence`. It routes nothing here, exactly as in Milestone 4 (FR-045, ADR-0004).
- Read the document, or compute a location of its own (FR-005).
- Evaluate anything outside the declared vocabulary. Rules are data; extending what they can express is a
  vocabulary version bump, not a schema author's freedom (FR-033).
- Persist anything. The artifact id is computed; storing it is the pipeline milestone's job.
