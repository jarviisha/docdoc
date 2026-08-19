# Feature Specification: Deterministic Validation

**Feature Branch**: `005-deterministic-validation`

**Created**: 2026-08-18

**Status**: Draft

**Input**: User description: "Milestone 5 — validation: nhận kết quả đã trích xuất và đã grounded, rồi
trả lời câu hỏi *kết quả này có chấp nhận được không?* bằng code deterministic. Bao gồm structural
(required, hình dạng), field constraint mà Milestone 3 đã khai báo và cố tình chưa áp dụng, luật
cross-field kiểu `total == sum(line_items)`, và grounding validation. Mọi phán quyết là finding có cấu
trúc, địa chỉ theo field — không sửa ngầm, không boolean trần, không hỏi lại model. Evaluation là
milestone sau."

Milestone 5 of the docdoc MVP — the last stage of the MVP pipeline, and the one that turns a located
result into an *accepted or rejected* one. Milestone 3 extracted values and deliberately declined to
enforce the constraints it declared; Milestone 4 resolved each value to a place in the document and
deliberately declined to judge whether the value at that place is acceptable. Both deferrals point
here. This feature is governed by the constitution (v1.2.0) — Principle VII above all — and by
ADR-0002, ADR-0003, ADR-0004, and ADR-0008.

## User Scenarios & Testing *(mandatory)*

The consumers of this feature are **developers building on docdoc**: the higher docdoc layers
(pipeline, evaluation, API) and third-party users of the published library. There is no end-user
interface at this milestone. "The system" below means the validation stage. A **check** is one
declared obligation — a field's required flag, one constraint on one field, or one cross-field rule at
one place in the result. A **finding** is a check that did not pass, addressed to the field it is
about.

### User Story 1 - Be told exactly which field is wrong and why (Priority: P1)

A developer has an extraction result for an invoice. The schema says `invoice_number` is required and
must match a pattern, `currency` must be one of three values, and `quantity` must not be negative. They
ask docdoc whether the result is acceptable and get back a verdict plus one finding per violated
obligation, each naming the field path, the rule, what was expected, and what was there.

**Why this priority**: This is the stage's reason to exist and the smallest slice that stands alone.
Milestone 3 declared every one of these constraints, hashed them into schema identity, and applied
none of them; until this story ships, a schema author can write a rule that silently does nothing.
Structural and field-level checking alone is already a useful, auditable product.

**Independent Test**: Take a committed extraction result and a committed grounding result, validate
them against a committed schema with no network and no credentials, and confirm every hand-listed
violation appears exactly once with the right field path and the right reason, and that nothing else
appears.

**Acceptance Scenarios**:

1. **Given** an extraction result, its grounding result, and the schema it was extracted under, **When**
   the caller asks the system to validate, **Then** exactly one validation result is returned, or an
   explicit error is raised, and never a partial verdict.
2. **Given** a schema field marked required whose value the model reported absent, **When** validation
   runs, **Then** a finding names that field path and states that a required value is missing.
3. **Given** a value violating a declared constraint — a pattern, an enum, a numeric bound, a length
   bound, a multiple — **When** validation runs, **Then** a finding names the field path, the
   constraint that failed, the declared expectation, and the value that was there.
4. **Given** a value satisfying every declared constraint, **When** validation runs, **Then** no
   finding is produced for it, and the check that passed is still recorded as having run.
5. **Given** any validation run, **When** it finishes, **Then** every value it read is unchanged: no
   value is trimmed, re-cased, rounded, defaulted, or replaced, and no absence is filled in.
6. **Given** a violated constraint, **When** the caller inspects the finding, **Then** the finding is
   machine-addressable by field path — including an index for a value inside a repeating group — and
   readable without parsing prose.
7. **Given** a validation run, **When** it executes, **Then** it needs no network, no credentials, no
   provider, and no document, and it never asks a model anything.

---

### User Story 2 - Catch the total that does not add up (Priority: P2)

The same developer's invoice lists five line items and states a total. The total is wrong by one line.
Every individual field is well-formed, every constraint passes, and the model was confident. docdoc
reports that the total does not equal the sum of the line items, and points at the total and at every
line item that fed the sum.

**Why this priority**: A result where every field is individually plausible and the arithmetic is wrong
is the failure mode structural checking cannot see, and it is the one that costs money. Principle VII
names this rule specifically and requires it to be deterministic code rather than a prompt
instruction. It is P2 because Story 1 delivers a working stage on its own; this is what makes the
stage worth trusting for a financial document.

**Independent Test**: Validate committed results where the arithmetic holds, where it is off by one
line, and where it is off by a rounding cent, against schemas declaring the corresponding rule with
and without a tolerance, and confirm each verdict.

**Acceptance Scenarios**:

1. **Given** a schema declaring that a scalar field equals the sum of a field across a repeating
   group's entries, **When** the stated total disagrees beyond the declared tolerance, **Then** a
   finding names the rule, the expected sum, the stated total, and the difference.
2. **Given** the same rule and a result where the arithmetic holds, **When** validation runs, **Then**
   no finding is produced and the rule is recorded as having been evaluated.
3. **Given** any arithmetic comparison, **When** it is computed, **Then** it is computed in exact
   decimal arithmetic; two values that differ only in trailing zeros compare equal, and no comparison
   is affected by binary floating-point representation.
4. **Given** a rule whose declared tolerance is zero, **When** two amounts differ by the smallest
   representable amount, **Then** the rule fails; tolerance is declared, never assumed.
5. **Given** a rule one of whose operands is absent from the result, **When** validation runs, **Then**
   the rule is reported as not evaluated with the missing operand named, and the missing operand is
   never treated as zero.
6. **Given** a rule comparing two fields — a due date not before an issue date, a discount not above a
   subtotal — **When** validation runs, **Then** it is evaluated by docdoc's own code and the verdict
   does not depend on what any model was told or answered.
7. **Given** a cross-field finding, **When** the caller inspects it, **Then** every field that
   participated in the rule is named, not only the one the rule is anchored to.

---

### User Story 3 - Never be told "fine" when nothing was checked (Priority: P3)

A developer builds an approval step on top of docdoc. They need certainty that "valid" means every
declared obligation was evaluated and passed — not that the rules quietly failed to run, not that a
value was located but never checked, and not that a value nobody could locate slipped through as
acceptable.

**Why this priority**: A validation stage that silently skips is worse than none: it converts an
unchecked result into a signed one. This story constrains the behaviour Stories 1 and 2 produce
rather than adding new reach, and it is what makes the verdict admissible as evidence.

**Independent Test**: Validate a committed set containing results that pass, results that fail, and
results where a rule cannot be evaluated at all, and confirm the three are mechanically
distinguishable in the verdict, in the per-check record, and in the emitted log event.

**Acceptance Scenarios**:

1. **Given** any declared check, **When** validation finishes, **Then** exactly one outcome is recorded
   for it — passed, failed, or not evaluated — and a not-evaluated check states why.
2. **Given** a run in which some check could not be evaluated, **When** the caller reads the overall
   verdict, **Then** the verdict is distinguishable from one in which everything was evaluated and
   passed; no run in which obligations went unchecked reports the same verdict as a fully checked one.
3. **Given** the recorded checks, **When** their counts are compared against the obligations the schema
   declares for this result, **Then** the counts reconcile exactly, so a rule that never ran is visible
   rather than absent.
4. **Given** a value that is present but ungrounded, **When** validation runs, **Then** a finding
   records that a value was accepted with no located evidence, at the severity the run's grounding
   policy declares.
5. **Given** a value the model reported absent for a field that is not required, **When** validation
   runs, **Then** there is nothing to check, no grounding finding is produced, and the absence is not
   reported as a failure.
6. **Given** a value whose grounding was approximate, **When** validation runs, **Then** that fact is
   reported, and validation neither re-derives the grounding nor changes the recorded grounding status.
7. **Given** a value carrying the model's own self-reported confidence, **When** validation runs,
   **Then** that number influences no check, no severity, and no verdict, and remains labelled
   untrusted.
8. **Given** a failing check, **When** validation finishes, **Then** the result is reported as
   unacceptable and is never repaired: no value is corrected, clamped, coerced, or dropped to make the
   verdict pass.

---

### User Story 4 - Explain and reproduce a verdict six months later (Priority: P4)

A stored result says an invoice was rejected because its total did not add up. A developer needs to
know which rules were in force that day, which version of them ran, and whether re-running today would
reach the same verdict.

**Why this priority**: A verdict whose derivation cannot be reconstructed has the evidentiary value of
an opinion. It is last because it records what Stories 1 to 3 do and can be tested against any of
them.

**Independent Test**: Validate the same inputs twice and confirm the recorded identity is unchanged;
then change a rule's declaration, the grounding policy, and the validator version in turn, and confirm
each moves the identity, while changing something that cannot affect a verdict leaves it alone.

**Acceptance Scenarios**:

1. **Given** any validation result, **When** its provenance is read, **Then** it records the document
   identity, the extraction artifact, the grounding artifact, the schema identity and schema hash, the
   rule vocabulary version, the pattern dialect version, the identity of every enabled rule, the
   grounding policy, and the validator's own identity and version.
2. **Given** two validation runs differing in any enabled rule, in the grounding policy, or in the
   validator version, **When** their artifact identities are compared, **Then** the identities differ.
3. **Given** two validation runs over the same grounding result with all of those unchanged, **When**
   their identities are compared, **Then** the identities are equal and the verdicts and findings are
   identical.
4. **Given** a result that is re-validated, **When** both results are read, **Then** both exist with
   their own provenance and the earlier one is unmodified.
5. **Given** a change to a check's semantics, to a default severity, or to the rule vocabulary, **When**
   it is proposed, **Then** it requires a version bump, and an automated check fails the build if the
   version did not move.
6. **Given** any validation run, successful or refused, **When** the log output is inspected, **Then**
   one structured event carries the identities, the versions, the per-severity and per-outcome counts,
   the duration, and the verdict — and zero field values, document text, and claim text appear anywhere
   in the logs.

### Edge Cases

- A schema that declares no fields, and one that declares fields but no constraints and no rules: a
  valid verdict with zero findings and zero checks, not an error and not a refusal.
- A result in which every value is absent and no field is required.
- A required field inside a repeating group that is present in entries 1 and 3 and absent in entry 2:
  one finding, addressed to entry 2.
- A repeating group with zero entries where the group itself is required, and one with zero entries
  where it is not.
- A sum rule over a repeating group with zero entries: the sum of nothing is zero and the rule is
  evaluated against the stated total, rather than skipped.
- A sum rule where one entry's operand is absent: not evaluated, with the entry named — never summed as
  though the missing amount were zero.
- Two amounts that are numerically equal but written with different scales (`1240.0` and `1240.00`), and
  two that differ by the smallest unit the declared type can express.
- A tolerance declared as a negative number, and a tolerance larger than the value being compared.
- An enum comparison where the value differs only in case, and one where it differs only by surrounding
  whitespace: both fail, because trimming a value to make it pass is a silent correction.
- A pattern constraint against a value containing a newline, where "matches" must mean the whole value
  and not a substring of it.
- A pattern that is syntactically invalid, and one that is valid but pathological on adversarial input.
- A length bound on a string containing combining marks or characters outside the Basic Multilingual
  Plane, where a byte count, a code point count, and a grapheme count disagree.
- A numeric bound declared on a field whose declared type is not numeric, and a comparison rule between
  a date field and a number field.
- A rule whose operand path exists in the schema but not in this result, and one whose operand path
  does not exist in the schema at all.
- A rule anchored outside a repeating group that references a field inside one, and a per-entry rule
  that references a field outside its own entry.
- Two rules declared with the same identity in one schema.
- A result whose recorded schema identity or schema hash differs from the schema handed to the
  validator.
- A grounding result that was produced from a different extraction result than the one supplied.
- A value that is present, satisfies every constraint, and is ungrounded — acceptable content with no
  located evidence.
- A value that grounded approximately at a score just above the threshold, where the extracted value
  and the text at the resolved range disagree: the disagreement Milestone 4 refused to judge, and the
  one this stage exists to be able to state.
- A result with hundreds of repeating-group entries, each carrying several per-entry rules, where the
  number of checks is much larger than the number of declared rules.
- Every check in a run reported as not evaluated.

## Requirements *(mandatory)*

### Functional Requirements

**The validation contract**

- **FR-001**: The system MUST accept a grounding result, the extraction result it grounded, and the
  schema that extraction was produced under, and MUST produce exactly one validation result or raise an
  explicit error. It MUST NOT return a partial verdict.
- **FR-002**: The system MUST refuse to validate a grounding result that was not produced from the
  supplied extraction result, and MUST refuse a result whose recorded schema identity or schema hash
  differs from the supplied schema. Both refusals MUST name both sides. A verdict computed against a
  different schema than the one that produced the values is structurally valid and meaningless.
- **FR-003**: Validation MUST be computed by docdoc from the declared schema, the extracted values, and
  the grounding outcomes alone. No model may contribute to, confirm, or veto any check, and no check
  input may originate from a model's self-assessment.
- **FR-004**: Validation MUST NOT modify the extraction result, the grounding result, the schema, or any
  value within them. It produces a new result. No value is corrected, clamped, coerced, rounded,
  trimmed, defaulted, or dropped, on any path, including the failure path.
- **FR-005**: Validation MUST NOT read the document. Every location a finding needs is already recorded
  in the grounding result, and re-deriving one here would let two stages disagree about where a value
  is.
- **FR-006**: Validation MUST NOT re-derive, re-score, upgrade, or downgrade a grounding outcome. It
  reads the status Milestone 4 recorded and reports on it.
- **FR-007**: Validation MUST NOT require a network, credentials, a provider, a database, or an object
  store, and MUST NOT contact a model for any reason.

**What a check is**

- **FR-008**: Every obligation the schema declares MUST become a check at exactly one place in the
  result: one per required flag, one per declared constraint on a field, and one per rule at each place
  the rule applies — which for a per-entry rule means one check per repeating-group entry.
- **FR-009**: Every check MUST record exactly one outcome: **passed**, **failed**, or **not evaluated**.
  No fourth outcome may be introduced, and no check may be silently omitted.
- **FR-010**: A check that could not be evaluated MUST record why, naming the missing or unusable
  input. "Not evaluated" MUST NOT be reported as "passed" under any circumstance, and an unevaluable
  operand MUST NOT be replaced with a default, a zero, or an empty value.
- **FR-011**: The validation result MUST record the outcome of every check, including the ones that
  passed. A stage that records only its failures cannot answer "did this rule run?" after the fact,
  which is the question a disputed verdict turns on.
- **FR-012**: The counts of checks declared, evaluated, passed, failed, and not evaluated MUST be
  present in the result and MUST reconcile exactly, so that a rule which never ran is visible as a
  number rather than as an absence.
- **FR-013**: Every check MUST be addressable by field path, using the same path form the grounding
  stage uses, including the entry index for a value inside a repeating group.

**Structural checks**

- **FR-014**: A field declared required whose value is absent MUST produce a finding. A field not
  declared required whose value is absent MUST NOT.
- **FR-015**: Requiredness MUST be evaluated against the reported presence of the value, not against its
  content. A present value that is an empty string satisfies requiredness, because Milestone 3 draws
  that distinction deliberately and erasing it here would discard information the extraction layer
  preserved.
- **FR-016**: A required field declared inside a repeating group MUST be checked once per entry, and a
  finding MUST name the entry it is about.
- **FR-017**: A required group whose entire group is absent MUST produce one finding for the group,
  and MUST NOT additionally produce one finding per field inside it.
- **FR-018**: The system MUST verify that the shape of the result matches the schema it is validated
  against — every declared field present in the tree, no undeclared field, and cardinality as
  declared. A mismatch is a refusal, not a finding: it means the two artifacts do not belong together.

**Field constraints**

- **FR-019**: The system MUST enforce every constraint key the schema layer recognises: `enum`, `const`,
  `pattern`, `minimum`, `maximum`, `multiple_of`, `min_length`, and `max_length`. A constraint that is
  declared, hashed into schema identity, and never enforced is a rule that lies.
- **FR-020**: `enum` and `const` MUST be enforced here even though the extraction layer projects them
  onto the wire for the provider to honour. "The provider promised" and "the bytes that arrived" are
  different claims, and only one of them is checkable.
- **FR-021**: Constraint comparison MUST be exact: no case folding, no whitespace trimming, no numeric
  coercion between declared types, and no locale-dependent behaviour. A value that would pass only
  after adjustment fails.
- **FR-022**: `minimum`, `maximum`, and `multiple_of` MUST be evaluated in exact decimal arithmetic, and
  MUST NOT be evaluated in binary floating point at any point in their computation.
- **FR-023**: `min_length` and `max_length` MUST apply to the character length of a string value and to
  the entry count of a repeating group, MUST state which unit of length they count, and MUST count it
  identically on every platform.
- **FR-024**: `pattern` MUST be evaluated as a match against the whole value, MUST be documented as
  such, and MUST be bounded so that no declared pattern and no extracted value can make validation run
  unboundedly.
- **FR-025**: A constraint declared on a field whose declared type cannot carry it — a numeric bound on
  a boolean, a length bound on a number — MUST be rejected before any result is validated, as an
  authoring error naming the field and the constraint. It MUST NOT become a per-result finding and MUST
  NOT be silently ignored.

**Cross-field rules**

- **FR-026**: Cross-field rules MUST be declared as schema data and evaluated by deterministic docdoc
  code. A rule expressed as a prompt instruction, or as a document-type-specific code path, is a
  violation of Principles VII and VI respectively.
- **FR-027**: The rule vocabulary MUST be a closed, documented set of rule kinds carrying its own
  version. The MVP set is: the sum of a field across a repeating group's entries equals a scalar field;
  the product of two fields within an entry equals a third field in that entry; a comparison between two
  fields using one of `==`, `!=`, `<`, `<=`, `>`, `>=`; and a conditional presence rule making one
  field's presence obligatory when another is present. Adding, removing, or altering a kind REQUIRES a
  vocabulary version bump.
- **FR-028**: Every declared rule MUST carry an identity that is unique within its schema, and every
  finding MUST name it. A duplicate identity MUST be rejected at schema load.
- **FR-029**: Every rule MUST declare its operands as field paths. A path that does not exist in the
  schema, a per-entry rule whose operands are not all within one entry, and a scalar rule referencing a
  field inside a repeating group MUST all be rejected before any result is validated.
- **FR-030**: Every numeric rule MUST declare its tolerance, with a documented default of exact
  equality, and MUST be evaluated in exact decimal arithmetic. A tolerance is a declared property of the
  rule, never an implicit allowance chosen by the implementation.
- **FR-031**: A rule whose operands include an absent value MUST be reported as not evaluated, naming
  the absent operand. A rule over an empty repeating group whose only aggregate is a sum MUST be
  evaluated with that sum equal to zero, because the sum of no entries is a defined quantity while a
  missing amount is not.
- **FR-032**: A cross-field finding MUST name every field that participated in the rule, the values
  that were used, and the expected and actual sides of the comparison — not only the field the rule is
  anchored to.
- **FR-033**: Rules MUST NOT be able to express arbitrary computation. The vocabulary is data, not a
  language; anything outside it is a vocabulary change with a version bump, not a schema author's
  freedom.

**Grounding validation**

- **FR-034**: For every value that is present, the system MUST check the grounding status Milestone 4
  recorded against the run's grounding policy, and MUST report a value that is present but ungrounded.
- **FR-035**: The grounding policy MUST be an explicit, documented run option mapping each grounding
  status to a severity, with documented defaults. It MUST participate in the stage's identity, because
  it changes verdicts.
- **FR-036**: A value the model reported absent MUST NOT produce a grounding finding, mirroring
  Milestone 4's exclusion of a correctly reported absence from the grounding rate.
- **FR-037**: A value that grounded approximately MUST be reported as such. Where the run's policy makes
  it a finding, the finding MUST carry the recorded score, and MUST NOT compare that score with an exact
  tier's score.
- **FR-038**: Where a finding concerns a value that grounded, the finding MUST carry that value's
  location — its character range, pages, and boxes — copied from the grounding outcome and never
  recomputed. This is what makes a validation failure something a human can be shown rather than told.

**Findings, severity, and verdict**

- **FR-039**: A finding MUST carry: the field path, the check's identity, a stable machine-readable
  reason code, the declared expectation, the actual value or state, the severity, and the location
  where one exists. Prose in a finding is for humans and MUST NOT be the only place any of this appears.
- **FR-040**: Severity MUST be one of **error**, **warning**, or **info**. Each check kind MUST have a
  documented default severity, and a schema MAY override the severity of a rule it declares. Changing a
  documented default REQUIRES a validator version bump, because it silently changes every verdict
  produced without an explicit setting.
- **FR-041**: The overall verdict MUST be exactly one of **valid**, **invalid**, or **incomplete**, and
  MUST be derived mechanically: `invalid` if any check failed at error severity; otherwise `incomplete`
  if any check could not be evaluated; otherwise `valid`. A run in which obligations went unchecked MUST
  NOT be reportable as `valid`.
- **FR-042**: The verdict MUST NOT be representable as a bare boolean anywhere in the result, and the
  per-severity and per-outcome counts MUST be readable from the result without re-running anything.
- **FR-043**: Findings MUST be ordered by a **total** ordering — the anchor field's position in the
  schema's declaration order, then entry index ascending, then check identity — so that no output
  depends on iteration order, hash order, or platform.
- **FR-044**: A failing check MUST NOT raise. An error MUST NOT be reported as a finding. A finding is a
  statement about the document; an error is a statement about the request.
- **FR-045**: The model's self-reported confidence MUST NOT influence any check, any severity, or the
  verdict, MUST be passed through untouched, and MUST remain labelled untrusted, per ADR-0004.
- **FR-046**: This feature MUST NOT decide what happens to an invalid result. Routing, escalation, human
  review, and acceptance thresholds are policy built on this verdict, not part of producing it.

**Identity, provenance, and reproducibility**

- **FR-047**: Validation MUST be its own stage in the ADR-0003 chain, with its artifact identity derived
  from the grounding artifact's identity together with the validator's identity, version, and options
  hash.
- **FR-048**: The options hash MUST fold the rule vocabulary version, the pattern dialect version, the
  identities of the enabled rules, and the grounding policy, and MUST NOT fold anything that cannot
  change a verdict. A rule's *content* — its operands, its tolerance, its severity override — MUST NOT
  be folded here, because it already lives inside the schema hash and therefore inside every artifact
  this stage chains from. Folding a rule's identity records which rules ran, which the chain does not
  otherwise state.
- **FR-049**: Every validation result MUST record the document identity, the extraction artifact, the
  grounding artifact, the schema identity and schema hash, the rule vocabulary version, the pattern
  dialect version, the enabled rules, the grounding policy, and the validator's identity and version.
- **FR-050**: The validator MUST expose a stable identity and version and MUST change its version
  whenever its output changes for unchanged inputs. An automated check MUST fail the build when check
  semantics, a default severity, the verdict derivation, or the finding order changes without a bump.
- **FR-051**: For fixed inputs, versions, and options, the verdict, the findings, their order, and every
  recorded check outcome MUST be identical on every run and on every platform.
- **FR-052**: Re-validating MUST produce a new result with its own provenance and MUST NOT mutate,
  overwrite, or reinterpret a prior result.
- **FR-053**: Introducing rule declarations into the schema MUST NOT change the `schema_hash` of a schema
  that declares none. A representation change that alters the identity of every existing schema would
  invalidate every stored extraction artifact in exchange for a feature those schemas do not use.

**Errors, safety, and observability**

- **FR-054**: All failures MUST surface as docdoc's own typed, provider-neutral validation and schema
  errors carrying enough detail to identify the artifacts, the schema, and the field or rule at fault.
- **FR-055**: Validation errors MUST NOT be retried, per the constitution's error model. There is no
  transient failure mode in a deterministic, offline computation.
- **FR-056**: An authoring error in a schema — an unusable constraint, an unresolvable rule operand, a
  duplicate rule identity, an uncompilable pattern — MUST be raised when the schema is loaded, before
  any result is validated, so that a rule which cannot work is never mistaken for a rule that passed.
- **FR-057**: Field values, document text, and claim text MUST NOT be written to logs. Logs may carry
  identifiers, hashes, versions, counts, reason codes, severities, and timings only. Findings carry
  values; logs do not.
- **FR-058**: Every validation run, successful or refused, MUST emit one structured event carrying the
  document identity, the extraction and grounding artifact identities, the validation artifact identity
  where one was produced, the validator and vocabulary versions, the per-outcome and per-severity
  counts, the duration, and the verdict.

### Key Entities

- **Check**: One declared obligation at one place in the result — a required flag on a field, one
  constraint on one field, or one rule at one anchor. The unit everything else counts.
- **CheckOutcome**: What became of one check: passed, failed, or not evaluated, with a reason where it
  was not evaluated. Recorded for every check, not only the failing ones.
- **Finding**: The non-passing view of a check, addressed to a field path and carrying the check
  identity, reason code, expectation, actual value, severity, participating fields, and location where
  one exists.
- **Severity**: Error, warning, or info. Declared per check kind with documented defaults, overridable
  per rule by the schema author, and version-bound.
- **Rule**: One declared cross-field obligation — its identity, its kind from the closed vocabulary, its
  operand field paths, its tolerance where numeric, and its severity override where declared. Schema
  data, never code, never a prompt instruction.
- **RuleVocabulary**: The closed set of rule kinds the system can evaluate, carrying a version that
  moves whenever a kind is added, removed, or altered.
- **GroundingPolicy**: The mapping from grounding status to severity that a run applies. Part of the
  stage's identity, because it changes verdicts.
- **ValidationOptions**: The settings a run used that can change its verdict — principally the grounding
  policy and the enabled rule set.
- **ValidationResult**: The verdict, every check outcome, the findings in their total order, the
  per-severity and per-outcome counts, the provenance, and the content-addressed artifact identity.
- **ValidationProvenance**: Document identity, extraction artifact, grounding artifact, schema identity
  and hash, vocabulary version, pattern dialect version, enabled rules, grounding policy, and the
  validator's identity and version.
- **Validator**: The processor for this stage in the ADR-0003 chain, with a stable identity and a
  version that moves whenever output moves for fixed inputs.
- **Schema / FieldSpec** *(existing, Milestone 3)*: Supplies requiredness, declared types, cardinality,
  and the constraints declared there and deliberately unenforced. This feature is what enforces them,
  and extends the schema with rule declarations.
- **ExtractionResult / ExtractedValue** *(existing, Milestone 3)*: Supplies the values, their reported
  presence, and the untrusted model confidence this feature must not read.
- **GroundingResult / GroundingOutcome** *(existing, Milestone 4)*: The input artifact of this stage.
  Supplies the grounding status this feature checks and the locations its findings carry.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A committed extraction result and its grounding result are validated with no credentials,
  no network access, no document, no database, and no object storage.
- **SC-002**: 100% of obligations a schema declares for a given result become exactly one check;
  declared, evaluated, passed, failed, and not-evaluated counts reconcile in 100% of runs.
- **SC-003**: 100% of checks record exactly one of the three outcomes; zero are omitted from the result,
  and zero record a fourth outcome.
- **SC-004**: On a committed set with a hand-listed expected verdict, 100% of expected findings appear
  exactly once with the correct field path, check identity, and reason code, and zero unexpected
  findings appear.
- **SC-005**: 100% of every constraint key the schema layer recognises is enforced, verified by a test
  that fails when a recognised key has no enforcement path — so a newly recognised key cannot ship
  silently unenforced.
- **SC-006**: Values, presence flags, grounding statuses, and schemas are byte-identical before and
  after validation in 100% of runs, including runs that produce findings; zero values are corrected,
  coerced, or dropped.
- **SC-007**: 100% of numeric comparisons produce the same verdict as exact decimal arithmetic, verified
  against cases where binary floating point would disagree; zero verdicts depend on representation.
- **SC-008**: 100% of rules whose operands include an absent value are reported as not evaluated with
  the operand named; zero treat the absent operand as zero, empty, or default.
- **SC-009**: 0% of runs in which any check went unevaluated report the `valid` verdict.
- **SC-010**: 100% of present-but-ungrounded values produce a grounding finding under the default
  policy, and values reported absent produce grounding findings in 0% of runs.
- **SC-011**: 100% of findings about a grounded value carry the character range, pages, and boxes
  recorded by the grounding stage, byte-identical to that record; zero locations are recomputed.
- **SC-012**: The model's self-reported confidence influences 0% of verdicts, verified by validating one
  committed set twice with that value altered and confirming the results are identical.
- **SC-013**: Repeating validation of identical inputs produces identical verdicts, findings, finding
  order, and check outcomes in 100% of runs and across every supported platform; zero outputs vary with
  iteration order or hash order.
- **SC-014**: 100% of schema authoring errors — an unusable constraint, an unresolvable operand, a
  duplicate rule identity, an uncompilable pattern — are raised at schema load; 0% surface as a
  per-result finding or as a silently skipped check.
- **SC-015**: A grounding result validated against an extraction it did not come from, and a result
  validated against a schema whose identity or hash differs from the one recorded, are refused in 100%
  of cases with an error naming both sides; zero produce a verdict.
- **SC-016**: 100% of validation results record the document identity, the extraction and grounding
  artifacts, the schema identity and hash, the vocabulary version, the pattern dialect version, the
  enabled rules, the grounding policy, and the validator identity and version, readable without
  re-running validation.
- **SC-017**: Changing an enabled rule, the grounding policy, a severity override, or the validator
  version changes the artifact identity in 100% of cases; changing anything that cannot affect a verdict
  changes it in 0% of cases.
- **SC-018**: A change to check semantics, a default severity, the verdict derivation, or the finding
  order without a validator version bump fails the build in 100% of cases.
- **SC-019**: Adding rule declarations to the schema layer changes the `schema_hash` of 0% of schemas
  that declare no rules, verified against the hashes committed at Milestone 3.
- **SC-020**: Validating a result of 200 values against a schema declaring 20 rules completes in under
  50 ms, and the bound holds for adversarial values and patterns, not only for ordinary ones.
- **SC-021**: Zero field values, document text, or claim text appear in log output over the whole
  committed set, and 100% of runs emit one structured event carrying identities, versions, counts,
  duration, and verdict.
- **SC-022**: A contributor with no credentials and no network access runs 100% of this feature's tests
  and 100% of its documented examples; zero are skipped for want of a provider.
- **SC-023**: A schema author declares a cross-field rule and sees it enforced by following a single
  documented example, without reading the implementation and without writing code.

## Assumptions

These are reasonable defaults chosen where the source material did not specify. Each is a candidate for
`/speckit-clarify` if it proves wrong in review.

- **Rules are schema data, not user code.** Principle VI forbids document-type knowledge in code paths,
  which rules out a per-document-type validator; Principle VII forbids expressing a rule as a prompt
  instruction. What remains is declared data evaluated by one generic engine. A plug-in interface for
  user-supplied rule code is a plausible later feature and is out of scope here, because there is no
  present-tense need for it and every abstraction needs one (Principle XI).
- **The rule vocabulary starts at four kinds.** Sum, product, comparison, and conditional presence cover
  the invoice arithmetic the reference design names and nothing more. The vocabulary is closed and
  versioned precisely so that widening it is a visible, deliberate act rather than a schema author's
  improvisation.
- **Validation consumes the grounding artifact and therefore requires one.** ADR-0003 places validation
  after grounding in the chain, and grounding validation is a constitutional requirement, so a grounding
  result is a mandatory input rather than an optional enrichment. Validating an ungrounded extraction
  directly is not offered; ground it first, which costs nothing and requires no provider.
- **The document is not an input.** Everything a finding needs about location was recorded by Milestone
  4. Taking the document as well would create a second path to the same fact and a way for the two to
  disagree.
- **Type parseability was already settled upstream.** Milestone 3's conformance step raises when a value
  does not parse to its declared type, so the values reaching this stage are already type-conformant.
  "Schema and type validation" here therefore means requiredness, shape agreement, cardinality, and the
  declared constraints — plus the refusal in FR-002, which is the check that the two artifacts belong to
  the same schema at all.
- **Enum and const are re-checked despite wire enforcement.** The same reasoning Milestone 3 applied to
  shape conformance: a provider's promise and the bytes that arrived are different claims. The cost is
  two comparisons per value; the alternative is a constraint whose enforcement depends on which provider
  answered.
- **Length is counted in Unicode code points.** Code points are stable across platforms and cheap;
  grapheme clusters would need a dependency and a version, and bytes would make a bound mean different
  things in different scripts. The unit is documented wherever a length bound is exposed, because a
  reader assuming graphemes would be misled.
- **A pattern must match the whole value.** A substring match would make `pattern: "[0-9]{4}"` accept
  any string containing four digits, which is almost never what an author means and fails silently in
  the permissive direction.
- **Severity defaults: structural and constraint failures are errors; a present-but-ungrounded value is
  a warning; an approximate grounding is info; a check that could not be evaluated is a warning whose
  effect on the verdict comes from the `incomplete` state rather than from its severity.** These are
  version-bound, so a deployment that disagrees changes them explicitly and sees its artifact identities
  move.
- **`valid`, `invalid`, and `incomplete` are the whole vocabulary.** Three states, mirroring the three
  grounding outcomes, for the same reason: a two-state verdict would have to fold "nothing failed" and
  "nothing ran" into one word.
- **Tolerances default to exact equality.** Values are exact decimals by the time they arrive; a default
  allowance would be an unrequested, invisible loosening of every author's rule.
- **Validation is synchronous, in-process, and offline.** No queue, no worker, no batching across
  documents. It is deterministic code over data already in memory.
- **The stage computes its artifact identity but stores nothing.** Consistent with Milestones 3 and 4;
  persistence remains the pipeline milestone's work.
- **Validation never re-asks the model.** A failing check is reported as failing. Asking a model to
  reconsider would make the verdict depend on a probabilistic edge, which Principle III forbids for this
  stage, and would make the same result verdict differently on two runs.

## Dependencies

- **Milestone 1 (`001-kernel-document-ir`)** — supplies the span and geometry types that findings carry
  through from grounding, and the identity model that FR-002's refusals rest on.
- **Milestone 3 (`003-schema-driven-extraction`)** — supplies the schema, the constraints declared and
  deliberately unenforced, the requiredness flag it deliberately did not check, the presence/absence
  distinction, the typed values, and the extraction result. Its typed errors and provenance conventions
  MUST be reused rather than duplicated under a second incompatible name. This feature extends its
  schema with rule declarations under the constraint of FR-053.
- **Milestone 4 (`004-deterministic-grounding`)** — supplies this stage's input artifact, the grounding
  statuses and scores it checks, the locations its findings carry, the field-path form it addresses, and
  the boundary it drew: a value whose claim resolves but whose number disagrees with the text is this
  stage's finding, not that one's.
- **Constitution v1.2.0** — Principles III, VI, VII, VIII, X, XI, and XII bind this feature directly.
  Principle VII is the one it exists to satisfy.
- **ADR-0003** — fixes validation as its own stage, its position in the chain, and the inputs its
  options hash folds: validator version, enabled rule set, and rule versions.
- **ADR-0004** — fixes that a verdict may read the trusted grounding fields and MUST NOT read the
  untrusted model self-report, and that routing built on this verdict is not part of it.
- **ADR-0008** — fixes what a schema edit costs. Its table already prices this stage's semantics:
  optional → required and tightening a constraint are major bumps precisely because they change what
  validation accepts. Introducing rules makes that table load-bearing rather than anticipatory.

## Out of Scope

Explicitly deferred, and MUST NOT appear in this feature:

- Any repair, correction, normalization, clamping, or defaulting of a value to make a check pass.
- User-supplied rule code, a rule expression language, arithmetic beyond the declared vocabulary, and
  rules spanning more than one document or more than one result.
- Routing policy, acceptance thresholds, escalation, approval workflows, and any automatic-versus-human
  review decision built on the verdict.
- Confidence calibration and any blended or derived score.
- Golden datasets, accuracy and coverage metrics, validation-rate targets, and regression evaluation —
  Milestone 6. This feature makes the verdict computable; it sets no target for how often it should
  pass.
- Human corrections, annotations, correction storage, and any review interface — Milestone 6's model.
- Re-extraction, re-grounding, re-parsing, or any feedback loop that asks an earlier stage to try again
  because a check failed.
- Cross-document rules, duplicate detection, and reconciliation against external systems or master data.
- Persistence, caching, and storage of validation results, and any database.
- Queues, workers, background execution, batching across documents, and any HTTP interface or
  command-line tool — Milestone 7.
- Metrics counters, latency histograms, and distributed tracing — this feature emits structured log
  events only.
