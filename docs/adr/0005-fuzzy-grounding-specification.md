# ADR-0005: Fuzzy Grounding Lives Outside the Kernel and Is Fully Pinned

- **Status**: Accepted
- **Date**: 2026-08-14
- **Resolves**: `TODO(FUZZY_GROUNDING_SPEC)` (BLOCKING, Milestone 4)
- **Principles engaged**: I (Kernel Purity), II (Grounding), III (Determinism), VIII (Versioning)

## Context

The reference design places `find(text, fuzzy=False)` in the kernel. But the kernel may depend on
`pydantic` only, and a credible fuzzy matcher needs either a compiled library or a slow stdlib
fallback. Kernel purity and fuzzy-match quality were in direct conflict.

Separately, "grounding must be deterministic" is unimplementable until the algorithm, threshold,
tie-break, and version identifier are all pinned — four gaps, any one of which makes results
irreproducible.

## Decision

**Placement.** `Document.find(text)` in the kernel is **exact-only**, implemented with the
standard library, returning all non-overlapping matches in ascending `start` order. The kernel
keeps its `pydantic`-only dependency. Fuzzy matching lives in `extraction/grounding.py` and
depends on `rapidfuzz`, a base dependency of the extraction layer (small, pure-wheel, no provider
coupling). The `fuzzy=` parameter is removed from the kernel signature rather than left as a
kernel concern the kernel cannot honor.

**Algorithm, pinned as `grounding_version = "v1"`:**

1. Resolve the model-supplied quote against the **match view** (ADR-0006), not raw source text.
2. Attempt exact match first. Any hit → `grounding = "exact"`, `grounding_score = 1.0`.
3. Otherwise, generate candidate windows over the match view sized to the quote length (± a
   bounded slack), scored with normalized Levenshtein similarity (`rapidfuzz`), scaled to `0.0..1.0`.
4. **Threshold: `>= 0.90`.** Below threshold → `grounding = "ungrounded"`, `grounding_score = None`.
5. **Tie-break, applied in order and total:** highest score → earliest `start` → shortest span.
   Because the ordering is total, the winner is deterministic for any candidate set.
6. Up to **5** runners-up above threshold are recorded in `Value.alternatives`.
7. Winning view-offsets are mapped back through the match view's offset map, so returned spans are
   always **source-text spans**.

**Versioning.** `grounding_version` and `match_view_version` are recorded in every extraction
result and folded into the grounding stage's `options_hash` (ADR-0003). Changing the threshold,
the scorer, the candidate generator, or the tie-break rule REQUIRES a `grounding_version` bump.

## Consequences

- The kernel stays dependency-light and its property tests cover exact matching only, which is the
  invariant that must never break.
- `rapidfuzz` enters the base install for anyone using extraction. It is not a provider SDK and
  does not violate Principle IV.
- The 0.90 threshold is an initial estimate, not a measured optimum. It MUST be tuned against the
  golden set at Milestone 6, and any change bumps `grounding_version`.
- Determinism is testable: the same quote against the same document MUST yield an identical span,
  score, and alternatives list on every run and every platform.
- Ambiguous matches do not get a distinct status. A tie resolves to a single winner with the
  runners-up in `alternatives`, keeping the constitutional three-state model (exact / fuzzy /
  ungrounded) intact. If evaluation shows this hides real ambiguity, adding a fourth state is an
  amendment, not an implementation detail.
