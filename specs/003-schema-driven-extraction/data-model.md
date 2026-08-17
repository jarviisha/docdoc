# Phase 1 Data Model: Schema-Driven Extraction

Entities, the invariants each one enforces (`EXT-1`…`EXT-24`), and the error model. Every type traces to a
spec requirement; anything that traces to none does not belong here (Principle XI).

Types from Milestone 1 (`Document`, `Span`, `BlobRef`) and the two imported from Milestone 2
(`ProviderError`, `TransportSettings`, research.md R9) are referenced, not redefined.

## 1. FieldSpec

One declared field. The unit of a schema.

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | Unique among its siblings |
| `type` | `FieldType` | `string \| integer \| number \| boolean \| date \| datetime \| decimal` |
| `cardinality` | `Cardinality` | `scalar \| group \| repeating_group` |
| `required` | `bool` | Whether absence is a *validation* failure later — never an extraction failure (FR-005) |
| `description` | `str` | What the model is told to look for. Steers output, so it is hashed |
| `constraints` | `Mapping[str, Any]` | Declared here, enforced by Milestone 5 (FR-006) |
| `fields` | `tuple[FieldSpec, ...]` | Non-empty exactly when `cardinality` is `group` or `repeating_group` |

- **EXT-1** — `name` is unique among siblings. A duplicate is rejected at load time (FR-050).
- **EXT-2** — `fields` is non-empty if and only if `cardinality` is `group` or `repeating_group`. A
  scalar with children, or a group without, is rejected at load time.
- **EXT-3** — **the one-level repetition bound**: no `repeating_group` may contain, at any depth, another
  `repeating_group`. Violation is rejected at load time with an error naming the limit and the offending
  field path (FR-048). Groups may nest freely inside a repeating group.
- **EXT-4** — `constraints` keys are validated as *recognised*, never *applied*. An unrecognised key is
  rejected at load time; a recognised one is stored and hashed and otherwise untouched by this layer
  (FR-006).

## 2. Schema

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | e.g. `invoice`. Matches `^[a-z][a-z0-9_]*$` |
| `version` | `int` | The major. `>= 1`, monotonic per name, never reused (ADR-0008) |
| `fields` | `tuple[FieldSpec, ...]` | May be empty — a zero-field schema is legal and extracts nothing |
| `schema_hash` | `str` | Derived, `sha256:…`. Never authored |

- **EXT-5** — `identity` is the string `f"{name}@{version}"` and is the only form a request may name
  (FR-014). Case is significant; two identities differing only in case are two identities, and the name
  pattern makes an upper-case name unloadable rather than a near-duplicate.
- **EXT-6** — `schema_hash` is `sha256` over `canonical_json` of the whole schema — every field, type,
  cardinality, `required` flag, constraint, and description (research.md R6). It is **not** taken over the
  wire projection of §5.
- **EXT-7** — two schemas differing only in field order or file formatting hash identically (SC-005,
  guaranteed by ADR-0002's key sorting).
- **EXT-8** — two schemas differing in any field, type, cardinality, constraint, or description hash
  differently (SC-005).
- **EXT-9** — `schema_hash` does not depend on `name` or `version`, so a pure `@1` → `@2` bump with no
  other edit leaves the hash unchanged. This is intentional: the hash answers "did anything
  result-affecting change?", and a bump alone did not change the result. The *identity* still differs, and
  both are folded into `options_hash` (§8), so the two artifacts remain distinct.

## 3. SchemaRegistry

The set of schemas a running system knows, loaded from paths configuration names.

| Member | Notes |
|---|---|
| `register(schema, prompt)` | Rejects a duplicate identity; rejects a schema that fails EXT-1…EXT-4 |
| `resolve(identity) -> Schema` | Concrete `name@version` only |
| `identities() -> tuple[str, ...]` | For inspection without extracting (FR-018) |
| `describe(identity)` | Fields, types, descriptions — readable without a model call (FR-018) |

- **EXT-10** — `resolve` accepts only a concrete `name@version`. There is no `latest`, no partial match,
  and no implicit newest (FR-014). An edge may resolve a convenience alias, but the concrete version is
  what reaches the registry and what is recorded.
- **EXT-11** — multiple majors of one name resolve independently and neither shadows the other (FR-015).
- **EXT-12** — an unknown identity raises `SchemaError` naming the requested identity **and** the
  registered versions of that name, or all registered names when the name itself is unknown (FR-016). No
  neighbouring version is substituted.
- **EXT-13** — registration is all-or-nothing. A schema that fails any check leaves the registry exactly
  as it was; there is no partially registered schema (FR-050).

## 4. PromptTemplate

Instruction data keyed to one schema identity.

| Field | Type | Notes |
|---|---|---|
| `identity` | `str` | The `name@version` it belongs to |
| `text` | `str` | Instructions. Data, never code (FR-020) |
| `prompt_hash` | `str` | `sha256:…` over the canonical form of `text` |

A schema without a prompt is rejected at registration: an extraction that reached the model with no
instructions would be a silent quality failure rather than a loud one.

## 5. ResponseShape

The projection of a `Schema` onto what the provider can enforce (research.md R3).

| Field | Type | Notes |
|---|---|---|
| `projection_id` | `str` | `response-shape@1` — versioned, because the projection is code that changes results (research.md R7) |
| `shape` | `Mapping[str, Any]` | The JSON Schema sent on the wire |

- **EXT-14** — the projection carries types, cardinality, `enum`, `const`, and string formats, sets
  `additionalProperties: false` at every object, and **drops** numeric bounds, string-length bounds, and
  complex array constraints. Dropping is not a silent lossy step: what is dropped stays in the `Schema`,
  stays in `schema_hash`, and is Milestone 5's input.

Each field is asked for as a pair — the typed `value` and the `claimed_text` — so FR-003's byte-faithful
claim is part of the enforced shape rather than a hope about prompt wording.

## 6. ModelAdapter (protocol)

| Member | Notes |
|---|---|
| `id` / `version` | Stable identity; version bumps when output changes for fixed inputs (FR-036) |
| `model_id` / `model_version` | The model actually reached, recorded per result (FR-033) |
| `available() -> Availability` | Usable, or unusable with a stated reason (FR-028) |
| `complete(request) -> ModelResponse` | Exactly one response or a typed error. Never partial |

The real adapter and the in-repo `echo` adapter are two instances of one contract, and the contract suite
runs against both (research.md R11).

- **EXT-15** — a response whose shape does not match the requested `ResponseShape`, or whose value does
  not parse to its declared type, raises `ExtractionError` naming the field path. Nothing is coerced,
  defaulted, or truncated into place (FR-007).
- **EXT-16** — a declared field the response omits is recorded as an explicit absence, distinguishable
  from a field the model returned as empty (FR-002, FR-005). Absence is never an error.
- **EXT-17** — a value for an undeclared field is discarded and the occurrence recorded; it never reaches
  the result (FR-008).
- **EXT-18** — `claimed_text` is stored byte-for-byte as the model returned it: no trimming, no case
  folding, no Unicode normalisation. Milestone 4 cannot resolve what this layer has already altered
  (FR-003).

## 7. DecodingOptions and the budget

| Field | Type | In identity? |
|---|---|---|
| `max_tokens` | `int` | **Yes** |
| `effort` | `Effort` | **Yes** — a result-affecting input the reference design never contemplated (research.md R4) |
| `thinking` | `ThinkingMode` | **Yes** |
| `input_budget_tokens` | `int` | **Yes** — it decides whether a result exists at all |
| *(transport)* | `TransportSettings` | **No** — separate type, cannot change a successful result (FR-027) |

There is no `temperature`, no `top_p`, and no `seed`: the chosen provider's current models reject the
first two and have never had the third (research.md R4).

- **EXT-19** — the request is assembled stable-to-volatile: response shape, then schema instructions and
  field descriptions, then the document text last, with the cache breakpoint at the end of the per-schema
  prefix. Nothing per-request — no timestamp, no document id, no request id — may appear before the
  breakpoint (research.md R15).
- **EXT-20** — the input-budget guard runs locally and **before** any transmission, over-estimates rather
  than under-estimates, and raises `ExtractionError` naming the document, the bound, and the estimate. The
  provider's own too-long rejection maps to the same error so a caller sees one condition (research.md R5).

## 8. ExtractionResult and ExtractionProvenance

### ExtractedValue

| Field | Type | Trust |
|---|---|---|
| `field_path` | `str` | — |
| `value` | `Any \| None` | The typed value, or `None` for an explicit absence |
| `present` | `bool` | Distinguishes absence from an empty value (EXT-16) |
| `claimed_text` | `str \| None` | Byte-faithful (EXT-18) |
| `model_confidence` | `float \| None` | **Untrusted** — stored verbatim, routes nothing (ADR-0004) |
| `grounding` | `GroundingStatus \| None` | **Unresolved here** |
| `grounding_score` | `float \| None` | **Unresolved here** |
| `calibrated_confidence` | `float \| None` | Reserved; always `None` in the MVP |
| `calibrator_version` | `str \| None` | Reserved; always `None` in the MVP |

- **EXT-24** — every grounding field is left unresolved by this feature. Extraction may not set a
  grounding status it did not compute, and it computes none — not even the exact tier the kernel's search
  could satisfy cheaply (FR-032, FR-047, SC-018).

### ExtractionProvenance

Records document identity, `schema_identity`, `schema_hash`, `prompt_hash`, `projection_id`, adapter id
and version, `model_id` and `model_version`, the `DecodingOptions`, `extractor_version`, and `ModelUsage`.

### Artifact identity

```text
options_hash = canonical hash of {
    schema_identity, schema_hash, prompt_hash, projection_id,
    model_id, model_version, max_tokens, effort, thinking, input_budget_tokens
}
extraction_artifact_id = sha256(document_id + extractor_id + extractor_version + options_hash)
```

- **EXT-21** — any change to a folded input changes `extraction_artifact_id` (SC-009).
- **EXT-22** — a change to retry, timeout, or deadline changes it in **zero** cases, because those live
  in `TransportSettings` and are not folded (FR-027, SC-009).
- **EXT-23** — the chain composes: the id derives from `document_id`, so changing only the schema reuses
  the parse and triggers no re-parse and no ingest-provider call (ADR-0003, SC-010).

## 9. Error model

Rooted at the existing `DocdocError`. Two new types; one reused (research.md R9).

| Error | Raised when | Retryable |
|---|---|---|
| `SchemaError` | Unknown identity; malformed schema file; unrecognised type or constraint; duplicate field; repetition bound exceeded; missing prompt | **Never** |
| `ExtractionError` | Response shape mismatch; unparseable value; missing declared field; over-budget input or output; truncated response | **Never** |
| `ProviderError` *(from ingest)* | Transport, service, and credential failures, and a content refusal | Transient causes only |

- Transient, retried within the `TransportSettings` limit: connection failure, timeout, rate limit,
  server error, overloaded.
- Permanent, first attempt final: rejected credential, malformed request, unknown model, request too
  large, **and a content refusal** — which arrives as a *successful* HTTP response whose stop reason says
  refusal, so the adapter branches on the stop reason before touching content (research.md R12).
- No provider exception crosses the adapter boundary; each is translated with `__cause__` preserved.

## 10. Relationships

```text
Document (M1) ──┐
                ├─→ extract(document, identity, options) ─→ ExtractionResult
SchemaRegistry ─┤                                              ├─ ExtractedValue[]  (grounding unresolved)
  Schema ───────┤                                              └─ ExtractionProvenance
  PromptTemplate┘                                                    └─ extraction_artifact_id
                                                                          ↑
ModelAdapter ────────────────────────────────────────────────── document_id (M1) + options_hash
  ├─ echo (offline, deterministic)
  └─ anthropic_messages (extra: anthropic)
```

Milestone 4 consumes `claimed_text` and resolves the grounding fields in its own stage, with its own
artifact. Milestone 5 consumes `constraints` — carried here, acted on there.
