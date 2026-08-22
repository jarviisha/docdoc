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

---

## Amendment (**accepted 2026-08-22**, proposed 2026-08-18) — the Validate row, refined by Milestone 5

> **Accepted by Milestone 7.** This amendment was written by Milestone 5 and left marked *proposed*
> for four months, during which the validation stage already folded what it describes. Milestone 7's
> FR-058 requires each stage to fold **exactly** the inputs this ADR names, which made building on an
> unaccepted amendment an implicit resolution of an open question — the thing the constitution's
> precedence rule forbids. So it is decided rather than inherited: the table below is the operative
> one, and the Validate row in the Decision section above is superseded by it.


The Validate row above was written before a validation stage existed, and two of its terms turned out
to under-describe what that stage folds. Recorded here rather than resolved silently in code, per the
constitution's precedence rule.

**1. The row's `options_hash` inputs are extended to:**

| Stage | Inputs folded into `options_hash` |
|-------|-----------------------------------|
| Validate | `validator_version`, the enabled rule **ids**, `rule_vocabulary_version`, `pattern_dialect_version`, and the grounding policy |

The two additions both change verdicts, which is this ADR's own test for inclusion. The **pattern
dialect** decides what a `pattern` constraint means, so a dialect change silently re-decides every
result that carries one. The **grounding policy** decides whether a value nobody could locate is
acceptable; a deployment that raises it from a warning to an error has changed what its verdicts mean
and must see its artifact ids move.

**2. "rule versions" is satisfied by rule identities plus `schema_hash`, not by a per-rule counter.**

Rules are declared in the schema (`Schema.rules`), so their content is already inside `schema_hash`,
inside the extract stage's `options_hash`, and inside every artifact the chain composes from. A
per-rule version integer would be a *third* identifier answering a question `schema_version` and
`schema_hash` already answer between them — and ADR-0008 exists precisely because one integer being
asked two questions is how a version stops meaning anything.

What the chain does **not** carry is which of the declared rules a given run evaluated. That is why
the enabled rule **ids** are folded, and why the rule bodies are not.

**Consequence.** Editing a rule invalidates the extraction artifact and everything downstream of it,
reusing the parse. That is the same behaviour ADR-0008 already specifies for editing a constraint, and
it is correct for the same reason: the result did not change, but what the result *means* did.
