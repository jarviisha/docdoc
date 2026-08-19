# Validation

Extraction answers *what did the model find?* Grounding answers *where is it?*
Validation answers *is this acceptable?* — and it is a separate stage with its own
artifact because Principle VII says a validator built into a prompt is
unverifiable, non-reproducible, and impossible to regression-test.

```python
from docdoc.validation import validate

result = validate(extraction, grounding, schema)
result.verdict      # Verdict.VALID | Verdict.INVALID | Verdict.INCOMPLETE
result.findings     # what is wrong, addressed to fields, with locations
result.checks       # every obligation, including the ones that passed
```

There is no `document` parameter. Every location a finding carries was computed
by the grounding stage and is copied through, so the two stages cannot disagree
about where a value sits.

## Three tiers of check

| Tier | What it reads | Examples |
|---|---|---|
| **Structural** | the schema's `required` flags and shape | a required field the model reported absent; a required group that is entirely missing |
| **Field constraints** | `FieldSpec.constraints` | `enum`, `const`, `pattern`, `minimum`, `maximum`, `multiple_of`, `min_length`, `max_length` |
| **Cross-field rules** | `Schema.rules` | `sum(line_items[].amount) == total`; `quantity * unit_price == amount`; `due_date >= issue_date` |
| **Grounding** | the status Milestone 4 recorded | a value that is present but that nothing in the document supports |

Milestone 3 declared every one of those constraints, hashed them into schema
identity, and applied none of them — deliberately, because Principle VII puts
enforcement here. `tests/unit/test_constraint_key_coverage.py` is what stops a
*newly* recognised key from repeating that history one key at a time.

## Three verdicts, not two

```text
invalid     at least one check failed at error severity
incomplete  nothing failed, but at least one check could not be evaluated
valid       every declared check ran, and none failed
```

`incomplete` is the state that earns its place. Without it, a run whose rules
could not be evaluated would report the same word as a run where every rule ran
and passed, and a consumer would have no way to tell an audited document from an
unaudited one. There is no boolean anywhere in the result for a caller to reach
for instead.

Warnings and infos never reject a document. Deciding what to *do* with a verdict
— route it, escalate it, send it to a human — is policy built on this stage's
output, not part of producing it.

## Rules are data

A rule is declared in the schema and evaluated by one generic engine:

```json
{
  "id": "total_matches_lines",
  "kind": "sum_equals",
  "operands": ["line_items.amount", "total"],
  "tolerance": "0.01"
}
```

Two constitutional constraints shape that. Principle VI forbids document-type
knowledge in code paths, so there is no `InvoiceValidator`. Principle VII forbids
delegating a rule to a prompt. What remains is declared data, and a closed
vocabulary of four kinds — `sum_equals`, `product_equals`, `comparison`,
`conditional_presence` — pinned under `RULE_VOCABULARY_VERSION`. Widening the
vocabulary is a versioned act rather than a schema author's improvisation.

A rule that could not work is refused when the schema **loads**: an unresolvable
operand, a comparison across declared types, a per-entry rule whose operands span
two groups. The alternative is a rule that reaches a validation run and quietly
becomes a check nobody notices did not run.

## What a schema author gets wrong, and when they are told

Everything in this section fails **before any check runs** — at schema load where that is possible,
and at the entry to `validate()` for the one case that is not. None of it can reach a verdict, because
a declaration that cannot be evaluated would otherwise become a check that silently never runs, and a
check that always passes is a rule that lies.

| Declaration | What was meant | What it would have done |
|---|---|---|
| `"enum": "EUR"` | `["EUR"]` — a missing pair of brackets | read as `['E','U','R']`, so the schema rejects the value it names |
| `"max_length": "abc"` | a number | failed with a raw error while a document was being checked |
| `"min_length": 3.7` | `3` or `4` | truncated to 3, enforcing a bound nobody wrote |
| `"minimum": null` | a bound | **passed every value** — the comparison could not be made |
| `"multiple_of": 0` | a step | every number is a multiple of zero only by convention |
| `"pattern": "(?=x)y"` | a lookahead | not in the dialect; refused with the construct named |
| a rule operand that does not resolve | a field | a rule that never runs |

The last two are worth separating. A rule or a constraint *value* is refused when the schema **loads**;
a `pattern` is refused at the **entry to `validate()`**, because the dialect belongs to the validation
layer and the schema layer may not import it. Both are before the first check, which is what matters.

### An absent operand is never zero

A sum rule whose line is missing an amount is reported as `not_evaluated`, naming
that line. Summing a missing amount as zero would turn *we could not check this
invoice* into *this invoice adds up* — a wrong answer wearing the costume of a
right one.

An **empty** repeating group is different and is evaluated: the sum of no entries
is a defined quantity, and a document stating a total over no lines is exactly
the case a reader wants flagged.

### What a length bound counts

`min_length` and `max_length` count **Unicode code points** on a string, and
**entries** on a repeating group. Not bytes, which would make the same bound mean
something different in Vietnamese than in English; and not grapheme clusters,
which would need a dependency and a version of their own. `"Cảm ơn"` is six code
points and nine UTF-8 bytes, and a `max_length: 6` accepts it.

## Arithmetic

Everything numeric is `Decimal`. A `number` field arrives as a Python `float`
because that is how Milestone 3 parses it, and it enters through
`Decimal(str(value))` — never `Decimal(value)`, which would read `1240.10` as
`1240.0999999999999090505…`.

**`number` is lossy by declaration; `decimal` is the type for money.** Validation
does not reintroduce binary floating point, and it cannot recover precision an
upstream declaration already spent.

Tolerances are declared per rule, defaulting to exact equality. A default
allowance would be an invisible loosening of every author's rule.

## `pattern_dialect@1`

`pattern` is matched against the **whole value**, under docdoc's own documented
regular-expression subset. A substring match would make `pattern: "[0-9]{4}"`
accept any string containing four digits — failing in the permissive direction,
which is the direction that produces a clean verdict for a document nobody
checked.

The dialect is: literals, `.`, character classes with ranges and negation, the
escapes `\d \D \w \W \s \S`, groups, alternation, `*`, `+`, `?`, and counted
repetition `{m}`, `{m,}`, `{m,n}`. A leading `^` and a trailing `$` are accepted
and redundant.

Not in the dialect, and rejected **before any check runs** — with the construct
named and the field path attached, as a `SchemaError`: backreferences,
lookaround, named groups, inline flags, lazy quantifiers. Every declared pattern
is compiled at the entry to `validate()`, so one that cannot be evaluated fails
there rather than becoming a check nobody notices did not run.

**Why docdoc has its own engine.** CPython's `re` backtracks: `^(a+)+$` against 24
characters takes about 1.2 seconds and doubles per character, so an ordinary
40-character field value runs for days. A timeout would make a verdict depend on
machine speed, so one artifact id could describe two different answers. `RE2` is
linear and was measured — and declined, because the dialect would then be
whatever binary happened to be installed, and its version would have to enter
`options_hash`. Correctness of the engine is not re-derived: a Hypothesis test
asserts it agrees with `re.fullmatch` on every pattern in the subset. The stdlib
is the oracle for *what* matches; this engine exists for *how long it may take*.

## Findings

```python
for finding in result.findings:
    finding.field_path     # "line_items[1].amount"
    finding.reason         # ReasonCode.SUM_MISMATCH — closed, countable
    finding.severity       # error | warning | info
    finding.expected       # "1420.00"
    finding.actual         # "1240.00"
    finding.participants   # every field the check read
    finding.rule_id        # the rule it came from, or None
    finding.span           # copied from the grounding outcome
    finding.pages
    finding.geometry
```

`message` is redundant by construction: every fact it states appears in a
structured field beside it. Findings carry values because that is what a finding
is for; **logs carry counts**. One structured event per run, with identities,
versions, per-outcome counts, duration, and verdict — and no value, claim, or
document text.

## Reproducibility

```text
artifact_id = sha256(grounding_artifact_id + validator_id + validator_version + options_hash)
```

The options hash folds the rule vocabulary version, the pattern dialect version,
the enabled rule ids, and the grounding policy — and nothing that cannot change a
verdict. A rule's *content* is not folded here: it lives on the rule, so it is
already inside `schema_hash` and therefore inside every artifact this stage
chains from.

`tests/unit/test_validator_version_snapshot.py` pins the observable behaviour —
default severities, the verdict truth table, check-id formats, the reason
vocabulary — so a change to any of them fails the build unless
`VALIDATOR_VERSION` moves with it.

## What this stage will not do

- **Repair anything.** No value is corrected, clamped, coerced, rounded, trimmed,
  defaulted, or dropped. A value that would pass only after adjustment fails.
- **Ask a model.** A failing check is reported as failing.
- **Read `model_confidence`.** It is untrusted, it routes nothing, and no module
  here mentions it (ADR-0004).
- **Read the document.**
- **Decide what happens next.** Routing and review are Milestone 6's and
  Milestone 7's.

## Measuring, not asserting

The counts make a validation rate computable — `passed`, `failed`,
`not_evaluated`, and the per-severity totals are on every result, so no second
pass is needed. **No target is claimed here.** What a healthy rate looks like is
Milestone 6's question, and `TODO(GOLDEN_DATASET_LICENSING)` still gates it.

## See also

- [Validation API contract](../../specs/005-deterministic-validation/contracts/validation-api.md)
- [Grounding](grounding.md) — where a value is, and why this stage does not re-decide it
- [Extraction](extraction.md) — where constraints are declared
- [ADR-0003](../adr/0003-content-addressed-artifact-chain.md) — the artifact chain
- [ADR-0008](../adr/0008-schema-evolution-policy.md) — what a schema edit costs
