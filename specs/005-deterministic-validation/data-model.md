# Phase 1 Data Model: Deterministic Validation

Entities, the invariants each one enforces (`VAL-1`…`VAL-30`), and the error model. Every type traces
to a spec requirement; anything that traces to none does not belong here (Principle XI).

Types from Milestone 1 (`Span`, `Geometry`), Milestone 3 (`Schema`, `FieldSpec`, `ExtractedValue`,
`ExtractionResult`, `SchemaError`), and Milestone 4 (`GroundingResult`, `GroundingOutcome`,
`GroundingStatus`) are referenced, not redefined.

## 1. RuleKind

The closed vocabulary (FR-027). A `StrEnum`, and the set of its members **is** the vocabulary:

| Kind | Shape | Operands |
|---|---|---|
| `sum_equals` | `sum(group[].field) == total` within `tolerance` | a repeating-group member path, a scalar path |
| `product_equals` | `a * b == c` within `tolerance`, inside one entry | three paths in one repeating group |
| `comparison` | `a <op> b`, `op ∈ {==, !=, <, <=, >, >=}` | two paths of the same declared type |
| `conditional_presence` | if `a` is present then `b` must be present | two paths |

- **VAL-1** — the vocabulary is closed. An unrecognised kind is rejected at schema load, never at
  validation time (FR-027, FR-056).
- **VAL-2** — `RULE_VOCABULARY_VERSION` designates the whole vocabulary: the member set, each kind's
  semantics, and the tolerance convention. Adding, removing, or altering any of them REQUIRES a bump,
  enforced by a snapshot test rather than by review discipline (FR-027, FR-050).

## 2. RuleSpec

One declared cross-field obligation. Schema data — declared in the extraction layer, applied in the
validation layer, exactly as `constraints` already are (EXT-4).

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | Unique within its schema. Named by every finding the rule produces |
| `kind` | `RuleKind` | From the closed vocabulary |
| `operands` | `tuple[str, ...]` | Field paths, arity fixed per kind |
| `operator` | `Operator \| None` | Required for `comparison`, forbidden otherwise |
| `tolerance` | `Decimal` | Numeric kinds only. Default `Decimal(0)` — exact equality (FR-030) |
| `severity` | `Severity \| None` | Author override; `None` means the documented default (FR-040) |

- **VAL-3** — `id` is unique within the schema. A duplicate is rejected at load (FR-028).
- **VAL-4** — every operand path resolves to a field in the schema, with the arity and the declared types
  the kind requires. An unresolvable path, a wrong arity, a `comparison` between a date and a number are
  all rejected at load (FR-029).
- **VAL-5** — **scoping.** `product_equals` operands MUST all lie inside one repeating group;
  `sum_equals` takes exactly one path inside a repeating group and one outside it; `comparison` and
  `conditional_presence` operands MUST either both lie outside every repeating group or both lie inside
  the same one. Anything else is rejected at load (FR-029).
- **VAL-6** — `tolerance` is a non-negative `Decimal` and is declared, never inferred (FR-030).
- **VAL-7** — a rule is data. It carries no expression, no callable, and no reference to code
  (FR-026, FR-033).

## 3. Schema *(extended, Milestone 3)*

`Schema` gains one field:

| Field | Type | Notes |
|---|---|---|
| `rules` | `tuple[RuleSpec, ...]` | Default `()`. Declared here, applied by the validation layer |

- **VAL-8** — `schema_hash` includes `rules` **only when at least one is declared**. A schema that
  declares none hashes exactly as it does today, so no stored extraction artifact is invalidated by this
  milestone (FR-053, SC-019; mechanism in research.md R4).
- **VAL-9** — a declared rule is inside `schema_hash` and therefore inside the extract stage's
  `options_hash`. Editing a rule invalidates the extraction artifact even though no prompt changed;
  that is ADR-0008's rule, and it is correct because the rule changes what the result means.
- **VAL-10** — the schema layer recognises rules and validates their structure. It never evaluates one.

## 4. Severity and Verdict

```text
Severity = error | warning | info
Verdict  = valid | invalid | incomplete
```

- **VAL-11** — documented defaults: requiredness and constraint failures are `error`; a present but
  ungrounded value is `warning`; an approximate grounding is `info`; a not-evaluated check is `warning`.
  A schema may override the severity of a **rule** it declares; the others are fixed. Changing a default
  REQUIRES a validator version bump (FR-040).
- **VAL-12** — the verdict is derived, never authored: `invalid` if any check failed at `error`;
  otherwise `incomplete` if any check is `not_evaluated`; otherwise `valid` (FR-041).
- **VAL-13** — the verdict is not representable as a boolean anywhere in the result (FR-042).

## 5. Check and CheckOutcome

| Field | Type | Notes |
|---|---|---|
| `check_id` | `str` | Stable and derivable: `<field_path>#required`, `<field_path>#<constraint_key>`, `rule:<rule_id>@<anchor_path>` |
| `field_path` | `str` | The anchor, in Milestone 4's path form including entry indices |
| `kind` | `CheckKind` | `required \| constraint \| rule \| grounding` |
| `outcome` | `Outcome` | `passed \| failed \| not_evaluated` |
| `reason` | `ReasonCode \| None` | Set when `failed` or `not_evaluated`; `None` when `passed` |

- **VAL-14** — one check per declared obligation per place in the result, including one per entry for a
  per-entry rule or a field inside a repeating group (FR-008, FR-016).
- **VAL-15** — exactly one outcome per check. There is no fourth outcome and no unrecorded check
  (FR-009, FR-011).
- **VAL-16** — `check_id` is unique within a result and is derived from the anchor, so two runs over the
  same inputs produce the same ids (FR-051).
- **VAL-17** — `not_evaluated` carries a closed reason code — `operand_absent`, `operand_group_absent`,
  `type_mismatch` — and never the string "unknown" (FR-010, research.md R10).

  **Corrected during implementation.** This invariant originally listed a fourth code,
  `value_absent`, for a constraint on a field with no value. Building it showed the code cannot exist:
  a constraint constrains a value, so where the model reported none there is no obligation, and
  declaring one anyway would make an optional absent field push the verdict to `incomplete`. Nearly
  every real document has one, so the state would stop meaning "an obligation went unchecked". Absence
  is the requiredness check's subject and it is the one that reports it (FR-014).
- **VAL-18** — a `not_evaluated` check is never reported as `passed`, and its missing operand is never
  replaced by a zero, an empty value, or a default (FR-010).

## 6. Finding

The non-passing view of a check. Derived from `CheckOutcome`, never authored independently.

| Field | Type | Notes |
|---|---|---|
| `field_path` | `str` | The anchor |
| `check_id` | `str` | The check this came from |
| `kind` | `CheckKind` | As above |
| `reason` | `ReasonCode` | Machine-readable, closed set |
| `severity` | `Severity` | Resolved: author override or documented default |
| `expected` | `str \| None` | The declared expectation, rendered canonically |
| `actual` | `str \| None` | What was there, rendered canonically |
| `participants` | `tuple[str, ...]` | Every field path the check read — for a rule, all of them (FR-032) |
| `span` | `Span \| None` | Copied from the grounding outcome. Never recomputed (FR-038) |
| `pages` | `tuple[int, ...]` | Copied |
| `geometry` | `tuple[Geometry, ...] \| None` | Copied. `None` means the parser supplied no geometry |
| `message` | `str` | Human prose. Carries nothing the structured fields do not (FR-039) |

- **VAL-19** — every field a check read appears in `participants`, not only the anchor (FR-032).
- **VAL-20** — locations are copied from `GroundingOutcome`, byte-identical. Validation holds no document
  and can compute no location of its own (FR-005, FR-038, SC-011).
- **VAL-21** — `message` is redundant by construction: removing it loses no machine-readable information
  (FR-039).

## 7. GroundingPolicy

| Field | Type | Notes |
|---|---|---|
| `ungrounded` | `Severity \| None` | Default `warning`. `None` emits no check |
| `fuzzy` | `Severity \| None` | Default `info` |
| `exact` | `Severity \| None` | Default `None` — a located value needs no finding |

- **VAL-22** — a value the model reported absent produces no grounding check at all, mirroring Milestone
  4's exclusion of a correctly reported absence (FR-036).
- **VAL-23** — the policy reads the recorded grounding status and never recomputes, upgrades, or
  downgrades it (FR-006).
- **VAL-24** — a fuzzy finding carries the recorded score and never compares it with an exact tier's
  score, per ADR-0004 and Milestone 4's FR-031 (FR-037).

## 8. ValidationOptions

| Field | Type | Notes |
|---|---|---|
| `grounding_policy` | `GroundingPolicy` | Above |
| `enabled_rules` | `frozenset[str] \| None` | `None` means every rule the schema declares |

- **VAL-25** — both fields fold into `options_hash`. Nothing that cannot change a verdict does
  (FR-048, research.md R7).
- **VAL-26** — disabling a rule is a recorded, identity-bearing choice, not a silent omission: the
  disabled rule's absence from `checks` is explained by the provenance record.

## 9. ValidationResult

| Field | Type | Notes |
|---|---|---|
| `verdict` | `Verdict` | Derived per VAL-12 |
| `checks` | `tuple[CheckOutcome, ...]` | Every check, passed ones included (FR-011) |
| `findings` | `tuple[Finding, ...]` | The non-passing view, in the total order of VAL-28 |
| `counts` | `ValidationCounts` | `declared`, `evaluated`, `passed`, `failed`, `not_evaluated`, and per severity |
| `provenance` | `ValidationProvenance` | §10 |
| `artifact_id` | `str` | §11 |

- **VAL-27** — `counts.declared == passed + failed + not_evaluated`, and `evaluated == passed + failed`.
  The reconciliation is asserted in the type, not left to the caller (FR-012, SC-002).
- **VAL-28** — `findings` is ordered by the anchor's position in the schema declaration walk, then entry
  index ascending, then `check_id` lexicographically. Total by construction (FR-043, research.md R5).
- **VAL-29** — the result is frozen, and validating twice over the same inputs produces an equal one
  (FR-051).

## 10. ValidationProvenance

| Field | Type |
|---|---|
| `document_id` | `str` |
| `extraction_artifact_id` | `str` |
| `grounding_artifact_id` | `str` |
| `schema_identity` | `str` |
| `schema_hash` | `str` |
| `rule_vocabulary_version` | `str` |
| `pattern_dialect_version` | `str` |
| `enabled_rules` | `tuple[str, ...]` |
| `grounding_policy` | `GroundingPolicy` |
| `validator_id` | `str` |
| `validator_version` | `str` |
| `options` | `ValidationOptions` |

- **VAL-30** — `pattern_dialect_version` is recorded and folded because the dialect decides what a
  `pattern` constraint means, and a dialect change changes verdicts (research.md R2).

## 11. Identity

```text
options_hash    = sha256(canonical_json({
                     rule_vocabulary_version, pattern_dialect_version,
                     enabled_rules (sorted), grounding_policy
                  }))
artifact_id     = sha256(canonical_json({
                     input_artifact_id: grounding_artifact_id,
                     processor_id:      VALIDATOR_ID,
                     processor_version: VALIDATOR_VERSION,
                     options_hash
                  }))
```

Chained from the **grounding** artifact, so the id transitively inherits the document, the parser, the
schema, the prompt, the model, and the grounding threshold without naming any of them (ADR-0003,
FR-047). `VALIDATOR_ID = "deterministic-validator"`, stable; `VALIDATOR_VERSION` moves whenever output
moves for fixed inputs (FR-050).

**Why a rule's content is not folded here, and why no severity overrides are.** A rule's operands,
tolerance, and severity override are declared on its `RuleSpec` (§2), so they are inside `schema_hash`,
which is inside the extract stage's `options_hash`, which the grounding artifact chains from and this
stage chains from in turn. Folding them again would restate what the chain already carries. What the
chain does *not* say is **which** of the declared rules a given run enabled, which is why
`enabled_rules` is folded and the rule bodies are not. There is no run-level severity override in
`ValidationOptions` (§8) to fold; if one is ever added, this payload is where it belongs.

## 12. Error model

| Error | Raised when | Layer |
|---|---|---|
| `ValidationError` | the grounding result did not come from the supplied extraction result; the recorded schema identity or hash differs from the supplied schema; the value tree does not fit the schema (FR-002, FR-018) | `docdoc.validation` |
| `SchemaError` *(existing)* | an unrecognised rule kind, a duplicate rule id, an unresolvable or wrongly scoped operand, a constraint on an incompatible type, a pattern outside `pattern_dialect@1` (FR-025, FR-028, FR-029, FR-056) | `docdoc.extraction` |

Neither is retried (FR-055). A failing check never raises, and an error is never reported as a finding
(FR-044).
