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

`resolve` returns a **RegisteredSchema** — the schema, its prompt, and both hashes, computed once at
registration rather than per extraction. `describe` returns a **SchemaDescription**: identity, content
hash, and one row per declared field path. Neither carries a document or a result, so inspecting a
schema costs nothing and reveals nothing about what has been extracted with it.

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

- **EXT-14** — the projection carries types, cardinality, `enum`, and string formats, and sets
  `additionalProperties: false` at every object. It **drops** numeric bounds, array-length bounds,
  `minLength`/`maxLength`, and `pattern`. Only the last two are unenforceable by the provider; the rest are
  dropped **by choice**, because Principle VII puts constraint enforcement in Milestone 5 and a bound on
  the wire would make violating it the provider's extraction failure rather than a located validation
  failure (research.md R3). Dropping is not a silent lossy step: what is dropped stays in the `Schema`,
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

## 6b. AdapterRegistry

The adapters a running system knows, and the rule that picks one. Exists because FR-021 forbids
application code from naming a provider, and until it did there was no way to obey that.

| Member | Notes |
|---|---|
| `register(adapter)` | Asks the adapter whether it is usable and records the answer |
| `register_unavailable(id, reason)` | For an adapter whose extra is not installed, so the failure can name what to install |
| `candidates()` | Every known adapter in selection order: configured priority, then the adapter id as a total tie-break |
| `select()` | The first usable adapter, or `ModelProviderError` carrying **every** candidate's reason |

- **EXT-25** — selection never depends on registration order or dictionary iteration. Priority decides;
  the adapter id breaks ties.
- **EXT-26** — an unusable adapter is recorded with its reason, never omitted. Silence would make "not
  installed" indistinguishable from "no such thing" (FR-028), and the resulting error would name
  nothing.
- **EXT-27** — **the echo adapter is never selected automatically**, even when registered first and
  even when nothing else is usable. It answers from fixtures, so auto-selecting it would turn a missing
  credential into a stream of confident, fabricated extractions carrying full provenance — silently
  wrong data rather than an error. It is excluded structurally, not by ordering, and it remains usable
  when passed explicitly.

`default_adapter()` is the call FR-021 asks application code to make. `AdapterCandidate` is the record
`candidates()` returns: id, availability, reason, and the adapter itself when there is one.

## 6c. Configuration

What "configuration decides" resolves to. Three environment variables, read where an object is
constructed and never inside `extract()`, which still takes every result-affecting input explicitly
(research.md R17).

| Name | Read by | Decides | Default |
|---|---|---|---|
| `DOCDOC_SCHEMA_PATHS` | `default_registry()` | Schema data locations, `os.pathsep`-separated | none — an empty registry |
| `DOCDOC_MODEL_ADAPTERS` | `AdapterRegistry.__init__` | Adapter preference order, comma-separated | `("gemini",)` |
| `DOCDOC_GEMINI_MODEL` | `GeminiAdapter.__init__` | The model that answers | `DEFAULT_MODEL` |

- **EXT-28** — an explicit argument always beats configuration, for all three. A caller who passed one
  meant it.
- **EXT-29** — blank and whitespace-only entries are dropped rather than treated as an id or a path, so
  a trailing separator is a typo that costs nothing instead of a candidate matching nothing.
- **EXT-30** — `default_registry()` with neither an argument nor the variable returns an **empty**
  registry rather than falling back to a bundled schema, because docdoc ships none: a schema is a
  deployment's data (FR-049).

Credentials are separate and unchanged: `GEMINI_API_KEY` or `GOOGLE_API_KEY`, reported through
`available()` as an unavailable adapter with a stated reason (FR-028).

## 7. ExtractionOptions and the budget

| Field | Type | In identity? |
|---|---|---|
| `max_output_tokens` | `int` | **Yes** |
| `temperature` | `float` | **Yes** — defaults to `0.0`, not the provider's default, so it must be recorded |
| `top_p`, `top_k` | `float \| None`, `int \| None` | **Yes** |
| `seed` | `int \| None` | **Yes** — best-effort on the provider, folded anyway (research.md R4) |
| `thinking_budget` | `int \| None` | **Yes** — reasoning shares the output budget (R14) |
| `input_budget_tokens` | `int` | **Yes** — it decides whether a result exists at all |
| *(transport)* | `TransportSettings` | **No** — separate type, cannot change a successful result (FR-027) |

Every parameter ADR-0003's `Extract` row names exists on the chosen provider, so the row is followed
literally rather than refined (research.md R4).

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

**ValueTree** is the recursive shape a result's `values` takes: a field name maps to an
`ExtractedValue`, a nested group, or a tuple of repeating-group entries. Recursive by declaration and
bounded to one level of *repetition* by EXT-3, which is checked when the schema is constructed rather
than when a result is walked.

### ExtractionProvenance

Records document identity, `schema_identity`, `schema_hash`, `prompt_hash`, `projection_id`, adapter id
and version, `model_id` and `model_version`, the `ExtractionOptions` as they actually ran,
`extractor_version`, and `ModelUsage`.

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
| `ModelProviderError` *(subclasses ingest's `ProviderError`)* | Transport, service, and credential failures, and every refusal | Transient causes only |

`ModelProviderError` exists because `ProviderError` requires `parser_id` — parser vocabulary — and this
layer has an *adapter*. The subclass takes `adapter_id` and exposes it under that name, so
`except ProviderError` still catches every provider failure in the system while the attribute a caller
reads says what it means. It also carries `refusal_category`, passed through verbatim and never
interpreted.

A refusal is **not** one condition. The provider splits it, and the categories do not mean the same
thing:

| Signal | Category | Why it is its own branch |
|---|---|---|
| `SAFETY`, `PROHIBITED_CONTENT`, `BLOCKLIST` | `safety`, `prohibited_content`, `blocklist` | The output was blocked |
| `RECITATION` | `recitation` | Output resembled copyrighted material. **Not misconduct** — an invoice quoting standard payment terms can trip it |
| `SPII` | `sensitive_personal_information` | For an engine whose job is documents full of names and account numbers, a reason to expect rather than an edge case |
| `promptFeedback.blockReason` | `prompt_blocked:<reason>` | The *prompt* was blocked before generation, so no candidate exists at all |

- Transient, retried within the `TransportSettings` limit: connection failure, timeout, rate limit,
  server error, overloaded.
- Permanent, first attempt final: rejected credential, malformed request, unknown model, request too
  large, **and a content refusal** — which arrives as a *successful* HTTP response whose stop reason says
  refusal, so the adapter branches on the stop reason before touching content (research.md R12).
- No provider exception crosses the adapter boundary; each is translated with `__cause__` preserved.

## 10. The retry loop

`call_with_retries` wraps one adapter call. It lives in the layer rather than in each adapter, because
if every adapter implemented its own policy then "at most N attempts bounded by a deadline" would be a
claim about whichever adapter you happened to be using.

Three rules, and the third is the one that gets forgotten:

1. Only transient failures are retried. A rejected credential, a malformed request, and **every
   refusal** fail on the first attempt (FR-025).
2. A service-requested wait is honoured in preference to our own backoff — it knows its own load better
   than an exponential curve does — and is not jittered, because jittering a wait the service asked for
   defeats the point of asking.
3. **The deadline overrides both.** A service asking for a wait longer than the remaining budget fails
   on the deadline rather than sleeping past it. Otherwise one response header could silently extend an
   extraction past the bound the caller set.

`sleep` is injectable, so the tests assert the policy — including how long it *would* have waited —
without spending that time. A retry test that actually sleeps is a slow test, and a slow test gets
marked skip.

## 11. Relationships

```text
Document (M1) ──┐
                ├─→ extract(document, identity, options) ─→ ExtractionResult
SchemaRegistry ─┤                                              ├─ ExtractedValue[]  (grounding unresolved)
  Schema ───────┤                                              └─ ExtractionProvenance
  PromptTemplate┘                                                    └─ extraction_artifact_id
                                                                          ↑
ModelAdapter ────────────────────────────────────────────────── document_id (M1) + options_hash
  ├─ echo (offline, deterministic)
  └─ gemini (extra: google)
```

Milestone 4 consumes `claimed_text` and resolves the grounding fields in its own stage, with its own
artifact. Milestone 5 consumes `constraints` — carried here, acted on there.
