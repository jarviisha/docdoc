# ADR-0003: Per-Stage Content-Addressed Artifact Chain

- **Status**: Accepted
- **Date**: 2026-08-14
- **Resolves**: `TODO(PROCESSING_CACHE_KEY)` (BLOCKING, Milestone 3)
- **Supersedes**: the `processing_id` formula in the reference design
- **Principles engaged**: III (Determinism), VIII (Reproducibility), XI (Scale Through Boundaries)

## Context

The reference design defines:

```text
processing_id = SHA256(document_id + pipeline_version + schema_version)
```

This omits model, prompt hash, parser version, extractor version, and processing options. As
written, changing the LLM returns a **stale cached artifact** — a direct violation of Principle
VIII, and a correctness bug rather than a tuning concern.

## Decision

Every pipeline stage produces a content-addressed artifact:

```text
artifact_id = sha256(input_artifact_id + processor_id + processor_version + options_hash)
```

The chain composes, so each stage transitively inherits every input that can affect it:

```text
blob_id
  → parse artifact       (processor: parser)      == document_id per ADR-0002
  → extraction artifact  (processor: extractor)
  → grounding artifact   (processor: grounder)
  → validation artifact  (processor: validator)
```

Per-stage `options_hash` inputs, at minimum:

| Stage | Inputs folded into `options_hash` |
|-------|-----------------------------------|
| Parse | `ParseOptions` (canonical JSON per ADR-0002) |
| Extract | `schema_name@version`, `prompt_hash`, `model_id`, `model_version`, decoding params (temperature, top_p, seed, max_tokens), `response_format` |
| Ground | `grounding_version`, `match_view_version`, fuzzy threshold |
| Validate | `validator_version`, enabled rule set and rule versions |

`pipeline_version` is folded into the terminal artifact. The **job's `processing_id` is the
terminal artifact id**, so it transitively covers every result-affecting input.

Canonical serialization rules from ADR-0002 apply to every `options_hash`.

## Consequences

- **Partial reuse works.** Changing only the schema reuses the cached parse — the expensive OCR or
  cloud-provider call is not repeated. Changing only the model reuses the parse and invalidates
  extraction onward.
- A stale read now requires a hash collision rather than a missing input, closing the class of bug
  the flat key had.
- Every processor MUST expose a stable `id` and `version`, and MUST bump `version` whenever its
  output changes for fixed inputs. This is a review obligation, not something the system can
  detect on its own.
- Cache keys cannot be computed by hand or eyeballed in logs; log artifact ids and their input
  chain, and provide a CLI to explain how an id was derived.
- Artifacts are immutable, so the store is append-only; garbage collection of unreachable
  artifacts is a later concern and deliberately out of MVP scope.
