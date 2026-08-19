# Phase 0 Research: Deterministic Validation

Twelve questions this milestone had to answer before design. Each records the decision, why, what was
rejected, and — where a number decided it — the measurement. Measurements were taken on a contributor
laptop (CPython 3.14, Linux) with the snippets named in each item; they are exploratory, and the
`perf`-marked tier is what enforces the bounds afterwards.

## R1 — Where the layer lives

**Decision.** A new package `src/docdoc/validation/`, added to the `import-linter` layers contract
**above** `docdoc.grounding`:

```toml
layers = ["docdoc.validation", "docdoc.grounding", "docdoc.extraction", "docdoc.ingest", "docdoc.kernel"]
```

plus a forbidden-imports contract mirroring grounding's (no `socket`, `urllib`, `http`, `httpx`,
`requests`, provider SDKs, `fastapi`, `sqlalchemy`), because FR-007's "no network, no credentials, no
provider" is this stage's headline property exactly as it was grounding's.

**Rationale.** Milestone 4 already made this call and recorded it: Principle X's chain does not name
grounding or validation, ADR-0003's stage chain names both, and the finer-grained chain is consistent
with the coarser one. Validation consumes a `GroundingResult`, so the dependency runs one way and the
contract makes it mechanical rather than conventional.

**Alternatives rejected.** `extraction/validation.py` — same objection Milestone 4 raised for grounding:
one layer cannot enforce a direction inside itself, and this is eight modules rather than one function.
A `pipeline` package that owns validation — the pipeline is Milestone 7; putting a stage inside a layer
that does not exist yet is the abstraction Principle XI rejects.

**FR-005 ("validation MUST NOT read the document") is enforced by test, not by linter.** The package
legitimately imports `Span` and `Geometry` from the kernel to carry locations through, so a forbidden
contract on `docdoc.kernel` would be wrong. Instead `tests/unit/test_validation_reads_no_document.py`
asserts that `validate()`'s signature accepts no `Document` and that no module under
`docdoc.validation` references the name.

## R2 — How a `pattern` constraint can be evaluated under a hard time bound (FR-024)

The spec's checklist carried this forward as the one genuinely open research question. It is not a
style choice: the stdlib engine cannot satisfy FR-024 at all.

**Measured — CPython's `re`, pattern `^(a+)+$` against `"a"*n + "!"`:**

| n | time |
|---|---|
| 18 | 18.2 ms |
| 20 | 73.2 ms |
| 22 | 296.1 ms |
| 24 | **1,183.5 ms** |

Doubling per character. A 40-character field value — an ordinary length for an address line — is on the
order of days. A length precondition does not rescue it, because the values this stage validates are
routinely 30–60 characters. Any bound implemented as a *timeout* is worse than none: it makes the
verdict depend on machine speed, so two runs of the same artifact could disagree, which Principle III
forbids and which no artifact identity could describe.

Two sound options were measured.

**Measured — `google-re2` 1.1.20251105 (RE2, linear time) vs. a ~180-line pure-Python Thompson NFA
prototype** (`/tmp/nfa_proto.py`, reproduced in the tasks for this milestone):

| case | `google-re2` | pure-Python NFA |
|---|---|---|
| `(a+)+` vs 24 chars | 17.5 µs | 52.0 µs |
| `(a+)+` vs 10,000 chars | 499 µs | 17.2 ms |
| `INV-\d{4}-[A-Z]{2}` vs `INV-2026-VN` (typical) | 6.08 µs | **8.84 µs** |

**Decision: docdoc's own linear-time matcher over a documented, versioned subset — `pattern_dialect@1`
— and no new dependency.** The subset is: literals, `.`, character classes with ranges and negation,
the escapes `\d \D \w \W \s \S` and escaped metacharacters, groups, alternation, `* + ?`, and counted
repetition `{m}`, `{m,}`, `{m,n}` with a repetition limit and a total node budget checked when the
pattern is compiled. A leading `^` and a trailing `$` are accepted and redundant, since FR-024 already
matches the whole value; anywhere else they are rejected. Backreferences, lookaround, named groups, and
inline flags are rejected **at schema load** with an explicit error (FR-056).

**Rationale.** Two arguments, one of which is about correctness rather than weight:

1. **Dialect ownership.** FR-024 and FR-056 require the pattern language to be documented and versioned,
   because a pattern that changes meaning changes verdicts. With RE2 the dialect is "whatever the
   installed binary implements", so the engine's own version would have to be folded into `options_hash`
   — and two machines with different wheels would produce different verdicts under otherwise identical
   inputs. Owning the dialect makes `pattern_dialect_version` a docdoc constant that a bump can protect.
2. **Base-install weight.** `rapidfuzz` was accepted into the base install on the stated grounds that it
   is "a small pure-wheel package with no transitive dependencies". `google-re2` is a native C++
   extension bundling abseil; its wheels cover cp310–cp314 on manylinux_2_28 (x86_64, aarch64), macOS,
   and Windows, but not musl — so `pip install docdoc` on Alpine, or on glibc older than 2.28, becomes a
   C++17 source build. That is a large bill for one constraint key.

The 1.45× cost against RE2 on typical values (8.84 µs vs 6.08 µs) is irrelevant at this scale — see R8.
The 34× gap on a 10,000-character value is real and accepted: a field value that long is itself
pathological, and the matcher stays linear there, which is the property FR-024 asks for.

**Risk, stated plainly.** Writing a regex engine is the kind of thing Principle XI exists to stop.
Three things contain it: the subset is small and closed; the prototype already exists and is measured;
and correctness is checked **differentially** — a Hypothesis property test generates patterns inside the
subset and random inputs, and asserts docdoc's matcher and `re.fullmatch` return the same answer. The
stdlib is the oracle for *what* matches; docdoc's engine exists for *how long it may take*.

**Alternatives rejected.** Static rejection of "dangerous" patterns with stdlib `re` — sound rejection
of catastrophic backtracking requires building the automaton anyway, and star-height heuristics miss
`(a|aa)*`. A timeout — nondeterministic, as above. Dropping `pattern` support — it is a recognised
constraint key since Milestone 3, and a declared constraint that is never enforced is the exact failure
this milestone exists to end (FR-019).

## R3 — Exact decimal arithmetic when the extraction layer already produced a float

FR-022 and FR-030 require exact decimal arithmetic. Milestone 3's `conform` produces `Decimal` for a
`decimal` field, `int` for `integer` — and **`float` for `number`** (`extraction/conform.py`). The
precision is therefore already gone before this stage sees it.

**Decision.** Comparisons and aggregations are performed in `Decimal`. A `float` from a `number` field
enters via `Decimal(str(value))`, never `Decimal(value)`. `date`/`datetime` compare natively. The
documentation states that `number` is lossy **by declaration** and that `decimal` is the type for money.

**Measured:**

| expression | result |
|---|---|
| `Decimal(str(1240.10))` | `1240.10` |
| `Decimal(1240.10)` | `1240.09999999999990905052982270717620849609375` |
| `Decimal("1240.0") == Decimal("1240.00")` | `True` |

`repr` of a float is the shortest string that round-trips, so `Decimal(str(x))` is stable across
platforms — the property FR-051 needs.

**Rationale.** This is the honest reading of FR-022: validation does not reintroduce binary floating
point, and it cannot recover precision an upstream declaration threw away. Pretending otherwise would
put a guarantee in the docs that the type system contradicts.

**Alternatives rejected.** Rejecting numeric constraints and rules on `number` fields as authoring
errors — defensible, and too aggressive for a type the schema layer offers; a bound on a `number` field
is still meaningful, it is merely as precise as the declaration. Changing `conform` to parse `number`
as `Decimal` — a Milestone 3 contract change that would alter every stored extraction result's values,
for a type whose whole point is that it is a JSON number.

## R4 — Where rule declarations live, and why `schema_hash` survives them

**Decision.** Rules are declared **in the schema**, as data: `Schema.rules: tuple[RuleSpec, ...] = ()`,
each carrying an id, a kind drawn from a closed frozenset, operand field paths, an optional tolerance,
and an optional severity override. The extraction layer **recognises** rule kinds and checks operand
paths structurally at load; the validation layer **applies** them. That split is not new — Milestone 3
already does exactly this for `constraints` (EXT-4: "recognised, never applied"), and reusing the
pattern keeps one place where a schema is judged well-formed.

**`schema_hash` stability (FR-053, SC-019) is achievable and was verified by reading the code.**
`extraction/identity.py:schema_hash_for` hashes an explicitly constructed payload —
`options_hash_for({"fields": _sorted_payloads(schema.fields)})` — not a `model_dump`. A `"rules"` key is
therefore added to that payload **only when the schema declares at least one rule**, so every schema
committed at Milestone 3 keeps the hash it has today, and no stored extraction artifact is invalidated
by a feature those schemas do not use. Rules that *are* declared are hashed, because they change what a
result means.

**The check already exists.** `tests/unit/test_schema_snapshot.py` pins every committed schema's hash in
`tests/fixtures/snapshots/schema_hashes.json` and fails when one moves. SC-019 is therefore satisfied by
that test continuing to pass **unedited** — refreshing the snapshot to accommodate this milestone would
be clearing the exact alarm the milestone must not trip.

**Alternatives rejected.** A separate rules file loaded by the validation layer — rules would then sit
outside `schema_hash`, so editing one would not invalidate anything, and ADR-0008's rule that tightening
a constraint is a contract break would not reach the rules that are most likely to tighten. Registering
rules as Python callables — Principle VI forbids document-type knowledge in code paths, which is exactly
what a per-schema callable is.

## R5 — Enumerating checks deterministically

**Decision.** Checks are enumerated by walking the schema's `fields` in **declaration order**, entering
repeating groups in entry order, and emitting, per field: requiredness, then each declared constraint in
a fixed key order, then the rules anchored at that field. Findings are ordered by the position of their
anchor in this walk, then by entry index, then by check identity — a total order (FR-043).

**Rationale.** `Schema.fields` is a tuple and preserves declaration order; only the *hashing* payload
sorts by name (`_sorted_payloads`, so that reordering a file does not change identity). Declaration order
is the one a schema author can predict when reading a findings list, and it is stable for a fixed schema
because a reorder that would change it also changes nothing else about the result.

**Note for review.** This means finding order can change when a schema file is reordered, while
`schema_hash` does not. That is intentional: order is presentation, and presentation is allowed to
follow the file.

## R6 — Three verdicts rather than two

**Decision.** `valid | invalid | incomplete`, derived mechanically (FR-041), with every check recording
`passed | failed | not_evaluated` (FR-009) and the counts reconciling (FR-012).

**Rationale.** The failure this stage must not have is a vacuous pass: a run where the rules could not be
evaluated and the caller was told "valid". Two states force that collapse. Three make it impossible to
express — and mirror Milestone 4's three grounding outcomes, which exist for the same reason.

**Alternatives rejected.** Two states plus a `checks_not_evaluated` count — the count is present either
way (FR-012), but a consumer branching on the verdict alone would still be wrong, and consumers branch
on verdicts. A per-severity threshold ("invalid if more than N warnings") — that is routing policy, which
FR-046 puts outside this feature.

## R7 — The grounding policy, and what it folds into identity

**Decision.** One `GroundingPolicy` mapping each grounding status to a severity, defaulting to:
`ungrounded → warning`, `fuzzy → info`, `exact → no check emitted`. It is part of `ValidationOptions`
and folds into `options_hash`, alongside the rule vocabulary version, the enabled rule identities and
versions, and any severity override in force.

**Rationale.** ADR-0003's Validate row names "`validator_version`, enabled rule set and rule versions".
The policy is not in that list because the row was written before the policy existed; it changes
verdicts, so under the same ADR's own rule ("any cache key that omits an input which can change the
output is a correctness bug") it must be folded. The default is `warning` rather than `error` because an
ungrounded value is a statement about evidence, not about content, and a deployment that wants to reject
on it says so explicitly — and sees its artifact ids move when it does.

**Not folded.** Anything that cannot change a verdict: logging configuration, the order results are
walked in, the process's locale.

## R8 — Whether SC-020's 50 ms bound is real

The spec's checklist flagged this bound as an estimate. Derived here from measurements, for the stated
shape of 200 values, ~3 checks per value, and 20 rules:

| component | measured unit cost | count | total |
|---|---|---|---|
| Pattern check (`pattern_dialect@1`, typical value) | 8.84 µs | 600 worst case | 5.3 ms |
| Sum over a 200-entry repeating group + comparison | 26.4 µs | 20 | 0.5 ms |
| Tolerance comparison (`Decimal`) | 688 ns | 600 | 0.4 ms |
| `float → Decimal(str(...))` | 689 ns | 600 | 0.4 ms |
| Result construction (pydantic models, **not yet measured**) | ~1–3 µs | ~800 | ~1.6–2.4 ms |

≈ **8 ms**, about 6× under the bound — and the worst case above assumes every value carries a pattern,
which no real schema does. The unmeasured row is the one the `perf` tier exists to confirm; if model
construction dominates, the fix is to build `CheckOutcome` records as a plain tuple-backed structure
rather than to relax the bound.

**Also derived from this table**: recording *passed* checks (FR-011) costs one small record per check,
which is why FR-011 is affordable rather than a nice idea.

## R9 — Errors and observability, reusing what exists

**Decision.** `ValidationError` (new, in `docdoc/validation/errors.py`, extending `DocdocError`) for
refusals: mismatched artifacts, mismatched schema identity or hash, and shape disagreement.
`SchemaError` — the **existing** type from `docdoc.extraction.errors` — for authoring faults raised at
schema load: an unrecognised rule kind, a duplicate rule id, an unresolvable operand, a constraint on an
incompatible type, an uncompilable pattern. One structured log event per run, modelled directly on
`grounding/observe.py`, carrying identities, versions, per-outcome and per-severity counts, duration, and
verdict — and no values (FR-057).

**Rationale.** The constitution's error model lists both names already. Raising a second, incompatible
"schema is wrong" error from a different layer would give one fault two types depending on which stage
noticed it. The load-time faults are found where schemas are loaded, which is the extraction layer, so
they use that layer's error type.

**Note.** Findings carry values; logs do not. The boundary is worth restating because a finding is
precisely the place a value belongs, and the logging rule is often read as "values never appear
anywhere".

## R10 — Reasons a check cannot be evaluated

**Decision.** A closed, documented set of reason codes, so that "not evaluated" is diagnosable and
countable rather than prose: `operand_absent` (a rule operand the model reported absent),
`operand_group_absent` (the containing group is absent), `type_mismatch` (an operand's declared type
cannot participate — reached only where load-time checks could not see it), and
`value_absent` (a constraint on a field with no value; the requiredness check owns that case, and the
constraint is not silently passed).

**Rationale.** FR-010 says a not-evaluated check must name the missing input. A free-text reason cannot
be counted, and the counts are what make a silently-skipped rule visible (FR-012).

## R11 — What survives of "shape validation" (FR-018)

The checklist raised that FR-018 may be unreachable, since `conform` already drops undeclared fields and
raises on missing declared ones, and FR-002 refuses a mismatched schema.

**Decision.** It stays, as a **refusal**, and costs nothing extra: the enumeration walk of R5 traverses
the schema and the value tree together, so a node that is missing or of the wrong kind is discovered by
the walk that had to happen anyway. It raises rather than producing a finding, because a value tree that
does not fit its schema is a statement about the two artifacts, not about the document.

**Rationale.** The alternative is to assume an upstream invariant holds. The cost of the assumption
being wrong is a confident verdict over a mismatched tree; the cost of the check is an `if` in a walk.

## R12 — Testing strategy

**Decision.** Four tiers, **all offline**, with nothing to skip — the second milestone in a row of which
that is true:

- **Unit** — one module per concern: enumeration, constraints, the pattern dialect, rules, grounding
  policy, verdict derivation, ordering, identity, observability, refusals.
- **Property (Hypothesis)** — (a) the differential regex oracle of R2; (b) decimal comparison against
  `fractions.Fraction` as an independent oracle for tolerance arithmetic; (c) the finding order is a
  total order under shuffling of the inputs; (d) counts reconcile for randomly generated schemas and
  value trees.
- **Contract** — the public surface of `contracts/validation-api.md`, including that a mismatched
  artifact pair is refused and that re-validating is byte-identical.
- **Perf** (`perf`-marked) — SC-020's bound and the adversarial pattern case of R2.

The suite runs under two `PYTHONHASHSEED` values, as Milestone 4 established, because dict and set
iteration are the classic way a "total order" turns out not to be one.
