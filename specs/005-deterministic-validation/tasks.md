---

description: "Task list for 005-deterministic-validation"
---

# Tasks: Deterministic Validation

**Input**: Design documents from `/specs/005-deterministic-validation/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/validation-api.md](contracts/validation-api.md), [quickstart.md](quickstart.md)

**Tests**: **NOT optional here.** The template's constitution override names *Validation* explicitly —
"tests for schema, grounding, field-level, and cross-field rules" — and *Layer boundaries*, and this
feature is both. Principle XII additionally requires property tests where invariants are load-bearing,
and two here are: the pattern dialect's agreement with the stdlib oracle (research.md R2) and the
reconciliation of check counts (FR-012), which is what makes a rule that never ran visible. Test tasks
below are requirements, not suggestions.

**On the golden-set metrics task the template mandates.** The override requires a golden-set task for
evaluation-affecting changes. **There is none here, for the reason Milestone 4 recorded and which has not
changed**: `TODO(GOLDEN_DATASET_LICENSING)` is still open and gates Milestone 6, and the constitution's
quality gate 5 keeps the evaluation gate advisory until the dataset reaches its target size. What this
milestone owes instead is that the validation rate become *computable*, which T033 delivers through
`ValidationCounts` and T083 pins in the log event. No target for that rate is claimed here.

**Organization**: Grouped by user story so each is independently implementable and testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US4, mapping to spec.md's prioritised stories
- Exact file paths in every description

## Path Conventions

Single Python project, `src/` layout. New code lands in `src/docdoc/validation/`; three additive edits
land in `src/docdoc/extraction/`; tests in `tests/`.

## Where implementation and tests diverge

Three properties the spec assigns to later stories must be *implemented* in US1, because `validate()`
cannot ship without them: the mismatched-artifact and mismatched-schema refusals (FR-002), the artifact
identity and provenance (FR-047 … FR-049), and the verdict derivation including `incomplete` (FR-041).
Shipping US1 without the first would produce confident verdicts over artifacts that do not belong
together, which is the failure this stage exists to prevent. Their **dedicated adversarial tests** stay in
the story that owns them, and each such task says so.

**A second divergence, specific to this milestone.** The pattern dialect (research.md R2) is a US1
deliverable because `pattern` is one of the eight constraint keys US1 enforces, but it is the largest
single piece of work here and carries the milestone's only Complexity Tracking entry. It is broken into
six tasks (T038–T043) so that a reviewer can judge the engine separately from the constraint evaluator
that calls it.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Package skeleton, the layer contracts that make this stage's headline property a build
failure rather than a claim, and the fixtures every later phase reads

- [X] T001 Create the package skeleton `src/docdoc/validation/__init__.py` with the module docstring stating what this layer is and what it refuses to do — no repair, no re-asking a model, no routing decision, no document read, no network — mirroring the shape of `src/docdoc/grounding/__init__.py`
- [X] T002 Add `docdoc.validation` to the layers contract in `pyproject.toml` **above** `docdoc.grounding` (`layers = ["docdoc.validation", "docdoc.grounding", "docdoc.extraction", "docdoc.ingest", "docdoc.kernel"]`) and extend the existing comment, which already explains that higher layers are added as their milestones land. Must run after T001 — `import-linter` errors on a layer naming a non-existent module
- [X] T003 Add a forbidden-imports contract for `docdoc.validation` in `pyproject.toml` listing `socket`, `urllib`, `http`, `httpx`, `requests`, `openai`, `anthropic`, `google`, `boto3`, `azure`, `fastapi`, and `sqlalchemy`, mirroring grounding's. This is what turns FR-007 and SC-022 — "no network, no credentials, ever" — into a build failure. Depends on T002
- [X] T004 [P] Build the constraint fixtures in `tests/fixtures/validation/schemas.py`: test-only schemas exercising every recognised constraint key (`enum`, `const`, `pattern`, `minimum`, `maximum`, `multiple_of`, `min_length`, `max_length`) on each declared type that can carry it, plus the incompatible-pairing cases FR-025 must reject at load. **These live under `tests/`, not in `schemas/`** — adding a file to the registry would move the committed snapshot that SC-019 exists to protect
- [X] T005 [P] Build the rule fixtures in `tests/fixtures/validation/rules.py`: one schema per rule kind (`sum_equals`, `product_equals`, `comparison`, `conditional_presence`), plus the authoring-error cases of VAL-3 … VAL-5 — duplicate id, unresolvable operand, a `comparison` between a date and a number, a `product_equals` whose operands span two repeating groups
- [X] T006 [P] Build the artifact-pair fixtures in `tests/fixtures/validation/artifacts.py`: committed `ExtractionResult` + `GroundingResult` pairs produced offline with Milestone 3's `echo` adapter over Milestone 2's committed documents — one whose arithmetic holds, one a line short, one carrying an ungrounded present value, and one whose extraction came from a *different* document, for the refusal tests

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The schema-layer data, the result types, and the enumeration walk that every story reads.
No user story can start until this phase completes

- [X] T007 Add `RuleKind`, `Operator`, and `RuleSpec` to `src/docdoc/extraction/schema.py` as **data only**, with a module comment stating that this layer recognises rules and never evaluates one — the same split EXT-4 already applies to `constraints` (data-model §1, §2, VAL-10)
- [X] T008 Add `Schema.rules: tuple[RuleSpec, ...] = ()` in `src/docdoc/extraction/schema.py`. Default empty, frozen like the rest of the model (VAL-8)
- [X] T009 Implement the load-time authoring checks in `src/docdoc/extraction/schema.py` and `src/docdoc/extraction/loader.py`. For rules: unrecognised kind, duplicate id, unresolvable operand path, wrong operand arity, wrong operand types, and the scoping rules of VAL-5. For constraints: a key declared on a field whose declared type cannot carry it — a numeric bound on a boolean, a length bound on a number, a `multiple_of` on a date (FR-025, VAL-4). All raise the **existing** `SchemaError` naming the rule id or field path and the offending declaration. A rule or a constraint that cannot work must fail when the schema loads, never at verdict time, because the alternative is a declaration that quietly becomes a skipped check — the defect this milestone exists to end (FR-025, FR-029, FR-056, research.md R9)
- [X] T010 [P] Write `tests/unit/test_schema_authoring_errors.py` asserting that each rule fixture from T005 **and each incompatible constraint/type pairing from T004** raises `SchemaError` at load, with the rule id or field path and the offending declaration in the message, and that a well-formed schema loads unchanged. The T004 half is what closes FR-025: the fixture is already built, and without this assertion nothing reads it. The point of the test is that *no* authoring fault survives into a validation run to become a skipped check (FR-025, SC-014)
- [X] T011 Fold `rules` into `schema_hash_for` in `src/docdoc/extraction/identity.py` **only when the schema declares at least one rule**, with a comment explaining why the conditional is not a canonicalisation wart: a schema that declares no rules is the same schema it was before this milestone (FR-053, VAL-8, research.md R4)
- [X] T012 [P] Write `tests/unit/test_rules_in_schema_hash.py` asserting that adding, editing, or reordering a rule moves `schema_hash`, and — the load-bearing half — run the existing `tests/unit/test_schema_snapshot.py` **unedited** to prove the three committed schemas still hash to their pinned values (SC-019). Refreshing that snapshot to accommodate this milestone is clearing the alarm the milestone must not trip
- [X] T013 [P] Implement `src/docdoc/validation/errors.py` with `ValidationError` extending `DocdocError`, carrying the two identities of whichever mismatch raised it, and documenting that it is never retried (FR-054, FR-055, data-model §12)
- [X] T014 Implement the result models in `src/docdoc/validation/result.py`: `Severity`, `Verdict`, `Outcome`, `CheckKind`, `ReasonCode`, `CheckOutcome`, `Finding`, `ValidationCounts`, `ValidationProvenance`, `ValidationResult` — all frozen, `extra="forbid"` (data-model §4 … §10)
- [X] T015 Enforce VAL-27 inside `ValidationCounts` in `src/docdoc/validation/result.py` as a model validator: `declared == passed + failed + not_evaluated` and `evaluated == passed + failed`. The reconciliation belongs in the type, not in a caller's discipline — it is what makes a rule that never ran a number rather than an absence (FR-012)
- [X] T016 [P] Implement `src/docdoc/validation/options.py` with `GroundingPolicy` and `ValidationOptions`, frozen, carrying the documented defaults of VAL-11 and VAL-22 (`ungrounded → warning`, `fuzzy → info`, `exact → no check`)
- [X] T017 Implement `src/docdoc/validation/identity.py`: `VALIDATOR_ID = "deterministic-validator"`, `VALIDATOR_VERSION`, `RULE_VOCABULARY_VERSION`, `options_hash_for_validation`, and `validation_artifact_id_for`, chaining from the **grounding** artifact id. The options hash folds the vocabulary version, the pattern dialect version, the sorted enabled-rule ids, the grounding policy, and severity overrides — and nothing that cannot change a verdict (FR-047, FR-048, data-model §11)
- [X] T018 Implement the enumeration walk in `src/docdoc/validation/enumerate.py`: traverse `Schema.fields` in declaration order alongside the value tree, entering repeating groups in entry order, emitting one check per required flag, per declared constraint in fixed key order, and per rule at each anchor. Produces the ordered check list every other module consumes (FR-008, FR-013, VAL-14, research.md R5)
- [X] T019 Implement the shape refusal inside the same walk in `src/docdoc/validation/enumerate.py`: a node missing from the tree, present but of the wrong kind, or undeclared raises `ValidationError`. It costs an `if` in a walk that had to happen, and the alternative is a confident verdict over a mismatched tree (FR-018, research.md R11)
- [X] T020 [P] Write `tests/unit/test_check_enumeration.py` asserting the check list is complete and correctly ordered for a nested schema with a repeating group — one check per obligation per entry — and that the same schema and tree always produce the same ordered list
- [X] T021 [P] Implement `src/docdoc/validation/numeric.py`: the single entry point that turns an extracted value into a `Decimal` (`Decimal(str(v))` for a `float` from a `number` field, never `Decimal(v)`), plus tolerance comparison. Docstring records the measurement that decided it — `Decimal(1240.10)` is `1240.0999…` while `Decimal(str(1240.10))` is `1240.10` — and that `number` is lossy by declaration (FR-022, research.md R3)
- [X] T022 [P] Write `tests/property/test_decimal_semantics.py` using Hypothesis with `fractions.Fraction` as an independent oracle: tolerance comparison agrees with exact rational arithmetic, `1240.0 == 1240.00`, and no comparison changes answer under a value written with different scale
- [X] T023 Implement `src/docdoc/validation/verdict.py`: severity resolution (author override, else the documented default), the verdict derivation of FR-041, the counts, and the total finding order of VAL-28. Verdict is derived, never authored (FR-040 … FR-043)
- [X] T024 [P] Implement `src/docdoc/validation/observe.py` on the `docdoc.validation` logger, modelled on `src/docdoc/grounding/observe.py`: one structured event per run, successful or refused, carrying identities, versions, per-outcome and per-severity counts, duration, and verdict. **No values.** The docstring must state the boundary explicitly — findings carry values, logs carry counts — because the logging rule reads as covering both (FR-057, FR-058, research.md R9)

**Checkpoint**: the schema layer declares rules, the result types exist and cannot hold inconsistent
counts, and the walk that turns a schema plus a tree into checks works. User stories can start.

---

## Phase 3: User Story 1 — Be told exactly which field is wrong and why (Priority: P1) 🎯 MVP

**Goal**: A developer validates an extraction result and gets one finding per violated obligation, each
naming the field path, the rule, the expectation, and what was there.

**Independent test**: Validate the committed artifact pair from T006 against the constraint fixtures from
T004 with no network and no credentials, and confirm every hand-listed violation appears exactly once
with the right field path and reason code, and nothing else appears.

- [X] T025 [US1] Implement the requiredness check in `src/docdoc/validation/constraints.py`: a required field whose value is absent fails; a non-required absent value produces no finding. Evaluated against reported *presence*, never against content — a present empty string satisfies requiredness, because Milestone 3 draws that distinction deliberately (FR-014, FR-015)
- [X] T026 [US1] Implement group and entry requiredness in `src/docdoc/validation/constraints.py`: a required field inside a repeating group is checked once per entry with the entry named, and a required group that is entirely absent produces **one** finding for the group rather than one per field inside it (FR-016, FR-017)
- [X] T027 [P] [US1] Implement `enum` and `const` in `src/docdoc/validation/constraints.py`, with a comment recording why they are re-checked despite the provider enforcing them on the wire: "the provider promised" and "the bytes that arrived" are different claims, which is the reasoning Milestone 3's `conform` already applies to shape (FR-020)
- [X] T028 [P] [US1] Implement `minimum`, `maximum`, and `multiple_of` in `src/docdoc/validation/constraints.py`, routed through `numeric.py` so no comparison touches binary floating point (FR-022)
- [X] T029 [P] [US1] Implement `min_length` and `max_length` in `src/docdoc/validation/constraints.py`, applying to the Unicode code-point length of a string and to the entry count of a repeating group. The unit is documented wherever the bound is exposed, because a reader assuming grapheme clusters would be misled (FR-023)
- [X] T030 [US1] Implement the exactness rule across `src/docdoc/validation/constraints.py`: no case folding, no whitespace trimming, no coercion across declared types, no locale-dependent behaviour. A value that would pass only after adjustment fails (FR-021)
- [X] T031 [US1] Implement `validate()` in `src/docdoc/validation/__init__.py` — the entry point of [contracts/validation-api.md](contracts/validation-api.md) §1 — wiring enumeration, constraints, verdict, identity, provenance, and the log event into exactly one `ValidationResult` or one raised error. Never a partial verdict (FR-001)
- [X] T032 [US1] Implement the refusals in `src/docdoc/validation/__init__.py`: a grounding result not produced from the supplied extraction result, and a recorded schema identity or hash differing from the supplied schema, each raise `ValidationError` naming both sides. **Implemented here because `validate()` cannot ship without it**; its adversarial tests are T075 in US4's phase (FR-002)
- [X] T033 [US1] Populate `ValidationCounts` and every `CheckOutcome` — passed ones included — in `src/docdoc/validation/__init__.py`. Recording a passed check is what lets "did this rule run?" be answered months later, and R8 shows it costs one small record per check (FR-011)
- [X] T034 [P] [US1] Write `tests/unit/test_required_checks.py` covering: absent required scalar, absent non-required scalar, present empty string, required field absent in entry 2 of 3, required group absent entirely, and a repeating group with zero entries both with and without `min_length`
- [X] T035 [P] [US1] Write `tests/unit/test_constraints.py` covering every recognised key against passing and failing values on each type that can carry it, plus the exactness cases of T030 — a value differing from an enum member only in case, and one differing only by surrounding whitespace, both fail
- [X] T036 [P] [US1] Write `tests/unit/test_constraint_key_coverage.py`: iterate `CONSTRAINT_KEYS` from `src/docdoc/extraction/schema.py` and fail if any key has no enforcement path. This is SC-005, and its purpose is that a *newly recognised* key cannot ship silently unenforced — the exact defect this milestone exists to end
- [X] T037 [US1] Write `tests/integration/test_validate_invoice.py::Structural` over the committed artifact pair from T006: the hand-listed violations appear exactly once each with the right path and reason, conforming values produce no finding, and their checks are present with `outcome == passed`

### The pattern dialect (research.md R2, plan.md Complexity Tracking)

- [X] T038 [US1] Implement the dialect parser in `src/docdoc/validation/pattern.py`: literals, `.`, character classes with ranges and negation, the escapes `\d \D \w \W \s \S` and escaped metacharacters, groups, alternation, `* + ?`, and counted repetition `{m}` `{m,}` `{m,n}`. A leading `^` and a trailing `$` are accepted and redundant; anywhere else they are rejected. The module docstring carries the measurement that forced this decision — CPython's `re` at **1,183 ms** on `^(a+)+$` against 24 characters, doubling per character — so a reader does not mistake it for reinvention
- [X] T039 [US1] Implement Thompson NFA construction and the linear-time simulation in `src/docdoc/validation/pattern.py`, with the repetition limit and total node budget checked at compile time so a `{1000}{1000}` expansion is rejected rather than built (FR-024)
- [X] T040 [US1] Define `PATTERN_DIALECT_VERSION` in `src/docdoc/validation/pattern.py` and fold it into `options_hash` in `src/docdoc/validation/identity.py`. The dialect decides what a `pattern` constraint *means*, so a dialect change changes verdicts (VAL-30). Depends on T017
- [X] T041 [US1] Wire `pattern` into `src/docdoc/validation/constraints.py` as a **whole-value** match, and document that reading — a substring match would make `pattern: "[0-9]{4}"` accept any string containing four digits, failing silently in the permissive direction (FR-024)
- [X] T042 [US1] Reject out-of-dialect patterns at schema load in `src/docdoc/extraction/loader.py`, raising `SchemaError` naming the unsupported construct — backreference, lookaround, named group, inline flag. A pattern that cannot be evaluated must never reach a validation run to become a skipped check (FR-056). Depends on T009, T038
- [X] T043 [P] [US1] Write `tests/property/test_pattern_dialect.py`: Hypothesis generates patterns inside the dialect and random inputs, and asserts docdoc's matcher and `re.fullmatch` return the same answer. **The stdlib is the oracle for *what* matches; docdoc's engine exists only for *how long it may take*** — this test is what contains the Complexity Tracking entry
- [X] T044 [P] [US1] Write `tests/unit/test_pattern_dialect_rejections.py`: every out-of-dialect construct is rejected at load with the construct named; a pattern exceeding the node budget is rejected; and `(a+)+` against 10,000 characters — the input on which CPython's `re` is effectively non-terminating — completes in milliseconds

**Checkpoint**: US1 is a shippable stage on its own. Every constraint Milestone 3 declared is now
enforced, and a schema author's rule can no longer be silently inert.

---

## Phase 4: User Story 2 — Catch the total that does not add up (Priority: P2)

**Goal**: A schema declares `total == sum(line_items[].amount)` as data, and docdoc reports the invoice
whose arithmetic is a line short, pointing at the total and at every line that fed the sum.

**Independent test**: Validate the committed pairs from T006 whose arithmetic holds, is off by one line,
and is off by a rounding cent, against rule fixtures declared with and without a tolerance.

- [X] T045 [US2] Implement the rule dispatcher in `src/docdoc/validation/rules.py`: resolve each `RuleSpec` to its anchors via the enumeration walk, evaluate by kind, and emit one check per anchor. Rules are data evaluated by one generic engine — nothing in this module may branch on a schema name or a document type (FR-026, Principle VI)
- [X] T046 [P] [US2] Implement `sum_equals` in `src/docdoc/validation/rules.py`, summing in `Decimal` through `numeric.py`, within the rule's declared tolerance. An **empty** repeating group sums to zero and the rule is *evaluated* against the stated total, because the sum of no entries is a defined quantity (FR-031)
- [X] T047 [P] [US2] Implement `product_equals` in `src/docdoc/validation/rules.py`, scoped to one repeating-group entry — quantity × unit price against the line amount — with the same tolerance convention (VAL-5)
- [X] T048 [P] [US2] Implement `comparison` in `src/docdoc/validation/rules.py` for `==`, `!=`, `<`, `<=`, `>`, `>=`, over operands of the same declared type, with dates and datetimes compared natively rather than through `Decimal`
- [X] T049 [P] [US2] Implement `conditional_presence` in `src/docdoc/validation/rules.py`: if operand A is present, operand B must be present. The one rule kind that reads presence rather than value
- [X] T050 [US2] Implement the absent-operand path in `src/docdoc/validation/rules.py`: any rule whose operand is absent is `not_evaluated` with `operand_absent` or `operand_group_absent` and the operand named. **Never summed as zero, never defaulted** — treating a missing amount as zero is precisely how a wrong total passes (FR-031, VAL-17, VAL-18)
- [X] T051 [US2] Populate `Finding.participants` for every rule in `src/docdoc/validation/rules.py` with every field the rule read, not only the anchor, plus the expected and actual sides of the comparison (FR-032)
- [X] T052 [US2] Define `RULE_VOCABULARY_VERSION` semantics in `src/docdoc/validation/rules.py` and add `tests/unit/test_rule_vocabulary_snapshot.py` pinning the member set and each kind's semantics, so adding or altering a kind without a bump fails the build (VAL-2, FR-027)
- [X] T053 [P] [US2] Write `tests/unit/test_rules.py` covering each kind passing and failing, tolerance zero versus a declared allowance, a tolerance larger than the compared value, an empty repeating group, and a rule over a 200-entry group
- [X] T054 [P] [US2] Write `tests/unit/test_rule_not_evaluated.py`: an absent operand, an absent containing group, and a type the load-time checks could not see, each producing `not_evaluated` with the right reason code and the operand named — and **never** `passed`
- [X] T055 [US2] Write `tests/integration/test_validate_invoice.py::Arithmetic` over the committed pairs: the sum rule fails on the line-short document with the difference reported, passes on the sound one, and the finding names every participating line item

**Checkpoint**: the rule Principle VII names by example now runs as deterministic code over declared
data, with no prompt instruction and no per-document-type code path anywhere in the call.

---

## Phase 5: User Story 3 — Never be told "fine" when nothing was checked (Priority: P3)

**Goal**: `valid` means every declared obligation was evaluated and passed. A run where rules could not
run, or where a value was accepted with no located evidence, is mechanically distinguishable.

**Independent test**: Validate a committed set containing passing results, failing results, and results
where a rule cannot be evaluated, and confirm the three are distinguishable in the verdict, in
`checks`, and in the emitted log event.

- [X] T056 [US3] Implement `src/docdoc/validation/grounding_policy.py`: for every present value, compare the recorded `GroundingStatus` against the run's policy and emit a check at the mapped severity. The recorded status is read and never recomputed, upgraded, or downgraded (FR-034, FR-006, VAL-23)
- [X] T057 [US3] Implement the absence rule in `src/docdoc/validation/grounding_policy.py`: a value the model reported absent produces no grounding check at all, mirroring Milestone 4's exclusion of a correctly reported absence from the grounding rate (FR-036, VAL-22)
- [X] T058 [US3] Copy the location — span, pages, geometry — from the `GroundingOutcome` into every finding about a grounded value in `src/docdoc/validation/result.py` and its callers. **Copied, never recomputed**: this stage holds no document and can compute no location of its own (FR-038, FR-005, VAL-20)
- [X] T059 [P] [US3] Implement the fuzzy reporting path in `src/docdoc/validation/grounding_policy.py`: an approximately grounded value carries the recorded score, and nothing in this module compares that score with an exact tier's (FR-037, ADR-0004)
- [X] T060 [P] [US3] Write `tests/unit/test_grounding_policy.py` covering: present-but-ungrounded produces a warning under the default policy; absent produces nothing; fuzzy produces an info finding carrying the score; the recorded grounding status is identical before and after; a policy of `ungrounded → error` moves the verdict to `invalid`; and — closing SC-011 — every finding about a grounded value carries `span`, `pages`, and `geometry` **equal field by field** to the corresponding `GroundingOutcome`'s, not merely non-empty, so a location that was recomputed instead of copied fails the test (FR-038, SC-011)
- [X] T061 [P] [US3] Write `tests/unit/test_verdict_derivation.py`: `invalid` when any error-severity check fails regardless of warning count; `incomplete` when nothing failed but something was `not_evaluated`; `valid` only when every check ran and none failed; the boundary case where a run's checks are *all* `not_evaluated`; and — FR-042 — that no field or property anywhere on `ValidationResult` reduces the verdict to a boolean, checked by introspection rather than by reading the class, so that a later `is_valid` convenience property fails this test instead of quietly collapsing three states into two
- [X] T062 [P] [US3] Write `tests/property/test_counts_reconcile.py` with Hypothesis over randomly generated schemas and value trees: `declared == passed + failed + not_evaluated` and `evaluated == passed + failed` in every case, and every declared obligation appears exactly once in `checks` (FR-012, SC-002)
- [X] T063 [P] [US3] Write `tests/unit/test_no_repair.py`: extraction result, grounding result, and schema compare equal before and after validation on every path including runs that produce findings and runs that raise — no value corrected, clamped, coerced, rounded, trimmed, defaulted, or dropped. Also assert the other half of the line FR-044 draws: a result whose every check failed at error severity still **returns** — it raises nothing — while a refusal raises and returns nothing. A finding is a statement about the document; an error is a statement about the request (FR-004, FR-044, SC-006)
- [X] T064 [P] [US3] Write `tests/unit/test_model_confidence_routes_nothing.py`: validate one committed set twice with `model_confidence` altered and assert the two results are identical, including the verdict, every check outcome, and the artifact id (FR-045, SC-012)
- [X] T065 [US3] Write `tests/unit/test_validation_answers_whether.py` — the mirror of Milestone 4's `test_no_validation_judgment.py`. A value whose claim resolves to text reading `1,420.00` while the extracted value is `1240.00` produced **no** finding at Milestone 4 by design; here, with a sum rule or a comparison declared, it produces one. The two tests together are what make the stage boundary mechanical rather than a matter of discipline (FR-010 of Milestone 4, Principle VII)
- [X] T066 [US3] Write `tests/unit/test_findings_are_addressable.py`: every finding's `field_path` resolves in the value tree including entry indices, every rule finding lists all participants, and removing `message` from a finding loses no machine-readable information (FR-039, VAL-21)

**Checkpoint**: the verdict can now be trusted to mean what it says, and a vacuously passing run is
unrepresentable.

---

## Phase 6: User Story 4 — Explain and reproduce a verdict six months later (Priority: P4)

**Goal**: A stored verdict records which rules were in force, at which versions, under which policy — and
re-running today reaches the same answer or explains why not.

**Independent test**: Validate the same inputs twice and confirm the artifact id is unchanged; then change
a rule, the policy, and the validator version in turn and confirm each moves it, while changing something
that cannot affect a verdict leaves it alone.

- [X] T067 [US4] Populate `ValidationProvenance` completely in `src/docdoc/validation/__init__.py`: document identity, both upstream artifacts, schema identity and hash, vocabulary version, pattern dialect version, enabled rules, grounding policy, and the validator's id and version (FR-049, data-model §10)
- [X] T068 [P] [US4] Write `tests/contract/test_validation_identity.py`: identical inputs yield equal artifact ids and equal results; changing the grounding policy, disabling a rule, editing a rule, or bumping the validator each move the id; and a change that cannot affect a verdict — logging configuration, the order the caller walks the result — does not (FR-048, SC-017)
- [X] T069 [P] [US4] Write `tests/unit/test_validator_version_snapshot.py` pinning check semantics, the documented default severities, the verdict derivation, and the finding order, so that changing any of them without a `VALIDATOR_VERSION` bump fails the build. Its failure message must state the remedy, following the pattern of `tests/unit/test_schema_snapshot.py` — a check whose remedy is unclear gets bypassed (FR-050, SC-018)
- [X] T070 [P] [US4] Write `tests/unit/test_revalidation_is_immutable.py`: validating twice produces two results, each with its own provenance, and the earlier one is unmodified (FR-052)
- [X] T071 [P] [US4] Write `tests/unit/test_validation_logging.py`: one structured event per run, successful or refused, carrying identities, versions, per-outcome and per-severity counts, duration, and verdict — and **zero** field values, document text, or claim text anywhere in the captured log output, checked over the whole fixture set (FR-057, FR-058, SC-021)
- [X] T072 [P] [US4] Write `tests/unit/test_finding_order_is_total.py`: shuffling the input dict order and running under a second `PYTHONHASHSEED` produces byte-identical finding order, and a candidate set containing ties in every ordering key still yields one deterministic sequence (FR-043, SC-013)
- [X] T073 [P] [US4] Write `tests/unit/test_validation_reads_no_document.py`: `validate()`'s signature accepts no `Document`, and no module under `src/docdoc/validation/` references the name. FR-005 cannot be expressed as an import contract, because the package legitimately imports `Span` and `Geometry` — so it is asserted here (research.md R1)
- [X] T074 [P] [US4] Write `tests/unit/test_validation_has_no_document_type_code.py` extending the existing Milestone 3 check across `src/docdoc/validation/`: no module branches on a schema name or a document type. Principle VI is the reason rules are data, and this is what keeps them so
- [X] T075 [US4] Write `tests/unit/test_validation_refusals.py` — the adversarial tests for T032. A grounding result validated against an extraction it did not come from, and a result whose recorded schema identity or hash differs from the supplied schema, are each refused with both sides named, produce no verdict, and emit the refusal log event (FR-002, SC-015)

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T076 [P] Write `examples/validate_invoice.py` — the quickstart's 30-second version: extract with the `echo` adapter, ground, validate a sound document and a line-short one, and print the verdict with each finding's page and span. Must run with no credentials and no network
- [X] T077 [P] Write `docs/concepts/validation.md`: the three tiers, the three verdicts and why there are three, the closed rule vocabulary, `pattern_dialect@1` and what it deliberately does not support, and the boundary with grounding — where answering *whether* begins
- [X] T078 [P] Write `tests/perf/test_validation_perf.py` (marked `perf`) enforcing SC-020's 50 ms bound for 200 values with 20 rules, and the adversarial-pattern case of T044. Depends on T031, T045
- [X] T079 [P] Write `tests/perf/bench_pattern_dialect.py` reproducing research.md R2's table for docdoc's dialect and CPython's `re`. The `google-re2` column is deliberately not reproducible from this repository — the package is not a dependency, and the measurement exists to record why
- [X] T080 [P] Update `README.md`: the roadmap row for Milestone 5 to **Done**, the status line, and the documentation index with `docs/concepts/validation.md` and the validation API contract
- [X] T081 [P] Update `CHANGELOG.md` with the Milestone 5 entry, naming the two decisions a reader will want explained: the pattern dialect and why no dependency was taken, and the grounding policy entering `options_hash` beyond ADR-0003's literal row
- [X] T082 Draft the clarifying amendment to `docs/adr/0003-content-addressed-artifact-chain.md` recording two refinements of the Validate row: it also folds the grounding policy and the pattern dialect version, and its "rule versions" is satisfied by the rule *identities* plus `schema_hash` rather than by a per-rule version integer — rules are schema content, so ADR-0008 already versions them and a second counter would be a third answer to a question two identifiers already answer. Per the constitution's precedence rule, a decision that extends an ADR must be raised and recorded, not resolved silently in code (plan.md design decision 2, FR-048)
- [X] T083 Add the validation-rate note to `docs/concepts/validation.md` and the log event: the counts make the rate computable, and no target is claimed here — that is Milestone 6's, and `TODO(GOLDEN_DATASET_LICENSING)` still gates it
- [X] T084 [P] Extend `tests/unit/test_documented_api_references_resolve.py` to cover the new documents, so every symbol named in `docs/concepts/validation.md`, `contracts/validation-api.md`, and `quickstart.md` resolves against the shipped package
- [X] T085 Run the gates over `tests/` and `src/docdoc/` — the full suite under two `PYTHONHASHSEED` values, `uv run pytest -m perf`, `mypy --strict`, `ruff`, and `import-linter` against `pyproject.toml`'s contracts — and record the results in the PR description. The dual-seed run is Milestone 4's convention and exists because dict and set iteration is the classic way a "total order" turns out not to be one

---

## Dependencies

```text
Phase 1 (T001–T006)  Setup
        ↓
Phase 2 (T007–T024)  Foundational — BLOCKS every story
        ↓
Phase 3 (T025–T044)  US1 · P1 · MVP ─────────────┐
        ↓                                        │
Phase 4 (T045–T055)  US2 · P2  (needs T018, T021)│
        ↓                                        │
Phase 5 (T056–T066)  US3 · P3  (needs T023, T045)│
        ↓                                        │
Phase 6 (T067–T075)  US4 · P4  (needs T017, T031)│
        ↓                                        │
Phase 7 (T076–T085)  Polish ←────────────────────┘
```

- **US1** depends only on the foundational phase. It is shippable alone.
- **US2** needs the enumeration walk (T018) and the numeric entry point (T021); its rules are otherwise
  independent of US1's constraints.
- **US3** needs the verdict derivation (T023) and the rule engine (T045), because the `not_evaluated`
  path it asserts is produced mostly by rules.
- **US4** needs identity (T017) and the entry point (T031); its tests are adversarial rather than
  additive, which is why it is last.

## Parallel opportunities

- **Phase 1**: T004, T005, T006 are three independent fixture files.
- **Phase 2**: T010, T012, T013, T016, T020, T021, T022, T024 touch distinct files.
- **US1**: the four constraint implementations T027–T029 are separate functions in one module, so treat
  `[P]` as "reviewable independently, merge in order"; the test tasks T034–T036 and T043–T044 are truly
  parallel.
- **US2**: T046–T049, the four rule kinds, are independent of one another.
- **US3** and **US4**: almost every task is a distinct test file and runs in parallel.
- **Phase 7**: T076, T077, T078, T079, T080, T081, T084 are seven distinct files.

## Implementation strategy

**MVP = Phase 1 + Phase 2 + Phase 3 (US1).** That delivers a validation stage that enforces every
constraint Milestone 3 declared and never applied, produces field-addressable findings, refuses
mismatched artifacts, and computes its own artifact identity. It is honest on its own: it makes no claim
about cross-field arithmetic, and a schema that declares no rules gets a complete verdict from it.

Then US2 makes the stage worth trusting for a financial document; US3 makes the verdict trustworthy as
evidence; US4 makes it explainable after everything has moved. Each is a checkpoint that can be reviewed
and merged on its own.
