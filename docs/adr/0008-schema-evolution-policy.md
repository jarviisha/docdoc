# ADR-0008: Schema Evolution Is a Two-Level Contract

- **Status**: Accepted
- **Date**: 2026-08-17
- **Resolves**: `TODO(SCHEMA_EVOLUTION_POLICY)` (Milestone 3)
- **Refines**: ADR-0003's `Extract` row — adds `schema_hash` to that stage's `options_hash`
- **Principles engaged**: VI (Schema-Driven Extraction), VII (Validation Separate), VIII
  (Reproducibility and Versioning), XI (MVP Discipline)

## Context

Principle VI requires versioned schema identities — `invoice@1`, `purchase_order@1` — and requires
every extraction result to reference the exact schema name and version used. It does not say what
forces the `1` to become a `2`. Leaving that open produces one of two failures, depending on which
way an implementer guesses:

- If the version integer is the only schema input folded into the extraction cache key of ADR-0003,
  then an edit that changes model output *without* a bump — a reworded field description, a widened
  enum — returns a **stale cached artifact**. ADR-0003 already names that class of thing a
  correctness bug rather than a tuning concern.
- If every edit forces a bump, the version stops meaning "the consumer contract changed" and starts
  meaning "somebody touched the file". Consumers pinning `invoice@1` are broken by typo fixes, and
  no one can distinguish a field rename from a description edit by reading the number.

The two pull in opposite directions because one integer is being asked to answer two different
questions: *did the result change?* and *did the contract change?*

## Decision

Two identifiers, deliberately distinct — the same split ADR-0002 makes between `blob_id` and
`document_id`.

| Identifier | Answers | Origin |
|---|---|---|
| `schema_version` | *Did the consumer contract change?* | Human-assigned. A major integer in `name@version`, monotonic per name, never reused |
| `schema_hash` | *Did anything that can change a result change?* | Derived: `sha256` over the schema's canonical JSON, using the serialization rules of ADR-0002 |

Both are folded into the extract stage's `options_hash` per ADR-0003, alongside `prompt_hash`,
`model_id`, `model_version`, and the decoding parameters. Both are recorded in the extraction
result.

Keying on the hash as well as the integer is what makes a no-bump edit behave correctly: a
description rewrite invalidates the extraction artifact and **reuses the parse**, so the expensive
cloud-provider call is not repeated. Keying on the integer alone cannot express that.

### What forces a major bump

The test is not "did the file change" but "can a consumer holding a stored `invoice@1` result still
read it, and does a document that was valid under `invoice@1` stay valid?"

**MUST bump the major version:**

| Change | Why it breaks the contract |
|---|---|
| Removing a field | Consumers reading it break outright |
| Renaming a field | A remove plus an add; downstream cannot tell it from a removal |
| Changing a field's type | `str` → `Decimal` changes every consumer's parsing and every comparison |
| Changing cardinality (scalar ↔ list) | Same shape break as a type change, and silently truncating in the wrong direction |
| Optional → required | Documents that legitimately lack the value now fail |
| Tightening a field constraint — narrowing an enum, a stricter pattern, a tighter range | Values valid under v1 are now invalid: the same document yields a validation failure that is not a defect in the document |
| Changing a field's **meaning** while keeping its name and type | No structural signal exists at all. The most dangerous row in this table, and the one no tooling can catch |

**MUST NOT bump the major version** (the hash changes, the extraction cache invalidates, the
contract holds):

| Change | Why the contract holds |
|---|---|
| Adding an optional field | v1 consumers ignore what they do not read |
| Loosening a constraint — widening an enum, relaxing a range | Everything valid under v1 stays valid |
| Editing a description, prompt hint, or example | Changes what the model produces, not the shape the consumer is promised |

Reordering fields changes nothing at all: canonical JSON sorts keys, so the hash does not move and
neither does the cache.

### Field constraints versus cross-field rules

A constraint expressed *on a field* — type, enum, pattern, range, required — is part of the schema
and therefore part of `schema_hash`. A cross-field rule such as `sum(line_items) == total` is
validator code under Principle VII, versioned as ADR-0003's `validator_version`.

Changing one MUST NOT bump the other. That separation is what lets a buggy cross-field rule be
fixed without invalidating the schema reference carried by every stored extraction.

### Serving multiple versions

Concurrent majors are allowed and expected. The registry is keyed by `name@version`, and `invoice@1`
and `invoice@2` may resolve at the same time. Anything less would mean that a bump breaks every
pipeline in flight, which in practice pressures contributors into *not* bumping — precisely the
failure the table above exists to prevent.

- An extraction request MUST name a concrete `name@version`. There is no `latest` and no implicit
  resolution in the library core. A request whose meaning changes when the registry changes is not
  reproducible, and Principle VIII does not permit it. This mirrors the ingest layer's
  `CapabilityRequest`: you ask for what you need, you are not handed whatever is newest.
- An edge — CLI or HTTP API — MAY offer a `latest` convenience, but MUST resolve it to a concrete
  version *before* extraction, MUST record the resolved version in the result, and MUST log the
  resolution.
- A stored result's schema reference is never rewritten. Reprocessing under a new version produces a
  new result with new provenance (Principle VIII). Past results are not migrated in the MVP, and
  that is the point: a result's evidentiary value is that it states what was extracted under the
  schema in force at the time.
- An unknown `name@version` raises `SchemaError`, naming the requested identity and the available
  versions. There is no fallback to a neighbouring version — silent fallback is forbidden by
  Principle VIII.
- No deprecation lifecycle, sunset dates, or compatibility shims in the MVP. Deferred, not rejected
  (Principle XI).

### Enforcement

Classification is human judgment. The tooling's job is to guarantee the judgment is *made*.

- A checked-in snapshot fixture maps every registered `name@version` to its `schema_hash`. CI fails
  when a registered version's hash moves.
- That check is a **change detector, not a breakage detector**. Bump-worthy and non-bump-worthy
  edits both trip it. The contributor clears it either by bumping the major — new entry added, old
  entry retained — or by refreshing the snapshot with the classification stated in the commit
  message. This is the same review obligation ADR-0003 places on processor `version` bumps, for the
  same reason: no system detects a semantic change on its own.
- Every registered schema needs a test that it loads and validates. A schema that cannot be loaded
  is not a version; it is a broken file.

## Consequences

- Two identifiers where the reference design had one. Results and logs must carry both:
  `schema_hash` is the debugging answer to "why did this re-run?", `schema_version` is the contract.
- A missed bump is caught only when a consumer breaks. No worse than the status quo, but the
  snapshot's commit-message requirement leaves a written record of who classified what, so the
  mistake is reviewable after the fact rather than invisible.
- Tightening an over-permissive constraint now costs a major version. That is the intended price;
  the alternative is stored results whose validity silently changes underneath them.
- Concurrent majors mean the registry, the prompt data, and any evaluation set must carry every live
  version. Evaluation (Milestone 6) therefore reports metrics per `name@version`, never aggregated
  per name — recorded here so it is not discovered late.
- No `latest` in the core will feel bureaucratic in interactive use. It is the same friction ADR-0004
  accepts around confidence, accepted for the same reason: the convenient default is the one that
  produces confidently unreproducible results.
