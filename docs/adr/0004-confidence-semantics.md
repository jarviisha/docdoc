# ADR-0004: Confidence Is Never a Single Blended Number

- **Status**: Accepted
- **Date**: 2026-08-14
- **Resolves**: `TODO(CONFIDENCE_SEMANTICS)` (BLOCKING, Milestone 4)
- **Supersedes**: the single `"confidence": 0.97` field in the reference result example
- **Principles engaged**: II (Grounding First-Class), III (Deterministic Core), IX (Evaluation)

## Context

Principles II and III forbid trusting a model's self-certification, yet the reference result model
carries a model-supplied `confidence` next to grounding-derived confidence. Two quantities with
very different trust levels were sharing one field name — the kind of ambiguity that produces
confidently wrong automation decisions downstream.

## Decision

An extracted `Value` carries **separate, explicitly labeled** fields. There is no blended
`confidence` field in the MVP.

| Field | Type | Source | Trust |
|-------|------|--------|-------|
| `grounding` | `exact` \| `fuzzy` \| `ungrounded` | docdoc, deterministic | Trusted |
| `grounding_score` | `float \| None` | docdoc, deterministic (1.0 for exact; fuzzy similarity for fuzzy; `None` for ungrounded) | Trusted |
| `model_confidence` | `float \| None` | model self-report, passed through verbatim | **Untrusted** |
| `calibrated_confidence` | `float \| None` | calibrator | Reserved; always `None` in MVP |
| `calibrator_version` | `str \| None` | calibrator | Reserved; always `None` in MVP |

Rules:

- **Routing decisions in the MVP** (automatic vs. human review) use `grounding` status and
  validation outcome only. `model_confidence` MUST NOT influence routing.
- `model_confidence` MUST be documented as untrusted wherever it is exposed — API schema
  description, CLI output, and docs.
- Any future blended score MUST be produced by a versioned calibrator writing to
  `calibrated_confidence`, never by overwriting the inputs.
- Evaluation reports metrics against `grounding` and validation outcomes. `model_confidence` may
  be **recorded** as a candidate calibration feature but is not itself a quality metric.

## Consequences

- Consumers cannot grab one number and treat it as truth; they must decide what they trust. This
  friction is intentional and is the point of the decision.
- Both signals are preserved, so a later calibrator can be fitted using grounding status,
  validation outcome, and model self-report as features, evaluated against the golden set.
- The reference `ExtractionResult` JSON example must be updated before it is used as a contract.
- Grounding score semantics differ by branch (exact is definitionally 1.0, fuzzy is a similarity
  from ADR-0005). Comparing scores across branches is meaningless and MUST NOT be done.
