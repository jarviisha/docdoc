# ADR-0006: Normalization Happens in a Comparison-Time Match View

- **Status**: Accepted
- **Date**: 2026-08-14
- **Resolves**: `TODO(NORMALIZATION_VS_GROUNDING)` (BLOCKING, Milestone 4)
- **Principles engaged**: I (Kernel-First IR), II (Grounding), VIII (Versioning), XI (MVP Discipline)

## Context

The MVP forbids normalization: `Document.text` stays source text, with no Unicode normalization,
line joining, hyphen removal, or whitespace collapsing. Principle II simultaneously wants exact
matching to dominate. On real PDFs these conflict — ligatures (`ﬁ`), soft hyphens, hyphenated
line breaks, non-breaking spaces, and irregular whitespace push a large share of otherwise-correct
values to `fuzzy` or `ungrounded` for purely cosmetic reasons, depressing the grounding rate that
Principle IX measures.

## Decision

`Document.text` remains **byte-faithful source text**. The normalization ban holds for the
canonical IR.

Grounding builds a derived, versioned **match view**:

- **Transformations** (pinned as `match_view_version = "v1"`): NFKC normalization, ligature
  expansion, soft-hyphen (`U+00AD`) removal, de-hyphenation across line breaks, non-breaking-space
  folding, and whitespace collapsing.
- **Offset map**: every match-view offset maps back to a source-text offset. Transformations are
  length-changing, so the map is explicit, not arithmetic.
- **Returned spans are always source-text spans.** The match view is never exposed as a document,
  never persisted as canonical text, and never handed to consumers as `Document.text`.
- The view is derived and cached as a content-addressed artifact (ADR-0003), keyed by
  `document_id` + `match_view_version`.
- `match_view_version` is recorded in every extraction result. Changing any transformation
  REQUIRES a version bump.

This is a **scoped EditMap, restricted to matching**. The full deferred EditMap — which would
normalize `Document.text` itself for all consumers — remains deferred.

## Consequences

- Exact-match rate rises substantially on real documents without compromising the canonical IR, so
  `grounding = "exact"` keeps meaning "found verbatim in the source" modulo documented,
  versioned cosmetic folding.
- The offset map is the same machinery a full EditMap needs. Its invariants MUST be property-tested
  now: every view offset maps to exactly one source offset; mapping is monotonic; round-tripping a
  source span through the view and back is the identity.
- An incorrect offset map produces confidently wrong bounding boxes — grounded-looking values
  pointing at the wrong place. This is the highest-risk component in the grounding path and
  warrants the strongest tests outside the kernel itself.
- Scope grew relative to a strict no-normalization MVP. Accepted, because the alternative reports
  a misleadingly low grounding rate and would drive tuning against an artifact of the measurement.
- The match view lives in the transform/grounding layer, not the kernel, so kernel purity holds.
