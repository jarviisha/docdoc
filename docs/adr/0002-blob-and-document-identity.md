# ADR-0002: Blob Identity and Document Identity Are Separate

- **Status**: Accepted
- **Date**: 2026-08-14
- **Resolves**: `TODO(DOCUMENT_IDENTITY)` (BLOCKING, Milestone 1)
- **Principles engaged**: I (Kernel-First IR), III (Determinism), VIII (Provenance)

## Context

The reference design defines `document_id = SHA256(original_bytes)`. But spans and geometry are
only meaningful relative to a **specific parse**: the same bytes parsed by the native PDF parser
and by a cloud provider produce different canonical text, different offsets, and different token
geometry. Under a bytes-only identity, both parses share one id while carrying mutually
incompatible spans — a silent correctness hazard for every grounded value.

## Decision

Two distinct, content-addressed identities:

```text
blob_id     = sha256(original_bytes)
document_id = sha256(blob_id + parser_id + parser_version + options_hash)
```

- `blob_id` identifies the **source file**. Format: `sha256:<lowercase-hex>`.
- `document_id` identifies **one specific parse of that file**. Same format.
- `options_hash` = `sha256` over a canonical serialization of `ParseOptions`: JSON with sorted
  keys, no insignificant whitespace, UTF-8, stable number formatting.
- **All spans, tokens, blocks, tables, and geometry anchor to `document_id`.**
- A `Document` carries both: `source: BlobRef` (holding `blob_id`) and its own `document_id`.

Two parsers over identical bytes therefore yield one `blob_id` and two `document_id`s. Spans from
one can never be silently applied to the other.

## Consequences

- Re-parsing with an improved parser produces a **new** `Document` with new provenance rather than
  invalidating or mutating stored spans, satisfying Principle VIII's no-overwrite rule.
- The API separates the two: upload returns a `blob_id`; extraction operates on and returns a
  `document_id`. Job and result records reference `document_id`.
- `document_id` is not recognizable as "the file" to a human. The `blob_id` is the user-facing
  handle for deduplicating uploads; `document_id` is the processing handle.
- Document identity stability (a Principle XII invariant) is now testable as two separate
  properties: identical bytes always yield identical `blob_id`; identical bytes plus identical
  parser, version, and options always yield identical `document_id`.
- Canonical `ParseOptions` serialization is load-bearing and MUST have its own unit tests; an
  unstable hash silently fragments the cache.
