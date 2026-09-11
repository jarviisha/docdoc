# ADR-0017: The Confidence Routing Policy, and the Signal It May Not Read

- **Status**: Accepted
- **Date**: 2026-09-04
- **Implements**: Milestone 10 (`specs/010-operations-and-corrections/spec.md`), FR-067 – FR-076, FR-105
- **Relates to**: [ADR-0004](0004-confidence-semantics.md) (trusted and untrusted confidence, and the blended field it forbids), [ADR-0005](0005-fuzzy-grounding-specification.md) (`grounding_version`, the precedent for pinning a policy), [ADR-0008](0008-schema-evolution-policy.md) (versioning a thing that changes results)
- **Principles engaged**: II (Source grounding is a first-class feature), III (Deterministic core), IX (Evaluation and human correction are product features)

## Context

Principle IX says confidence "MUST eventually support routing decisions (high → automatic, low →
human review), and confidence semantics MUST be documented and versioned rather than passed through
from a model's self-report unexamined." Milestone 10 is *eventually*.

The constraint that shapes the whole decision is one sentence in Principle II: **"MVP routing
decisions MUST NOT read `model_confidence`."** ADR-0004 keeps the docdoc-computed signals —
`grounding`, `grounding_score` — separate from the model's passthrough self-report and forbids a
blended number. A router that read the self-report would breach both, and it would do it invisibly,
because its decisions would look entirely reasonable.

There is a second reason to be careful, specific to this milestone. Milestone 9 deferred routing and
corrections together, and gave a reason: mixing product features into an infrastructure milestone
"would make SC-010 unmeasurable" — SC-010 being the requirement that golden-set metrics not move.
Milestone 10 carries both anyway, and the only thing that makes that defensible is that **neither
computes a value**. Routing has to be a read, or the objection stands and the milestone should have
been split.

## Decision

### 1. Two outcomes, and no third

`automatic` and `review`. The pair Principle IX names, and nothing else.

A third outcome would have to say what the deployment *does* with a result, and docdoc does not know
that. `reject` is a **disposition** and belongs to the caller — the boundary FR-083 already holds when
it forbids this milestone from becoming a review platform. `retry` is worse than out of scope: it
turns routing into a control-flow decision that re-enters the pipeline, and the only reason routing
could be admitted to this milestone at all is that it reads a finished result and writes nothing.

The set is closed and **not configurable**. An outcome a policy could name freely is one no client can
switch on exhaustively and no test can cover.

### 2. The inputs are exactly the signals docdoc computed itself

- grounding status and `grounding_score`, per field;
- validation severities and verdicts;
- schema requiredness.

Each is produced by deterministic code this project controls, which is what Principle II means by
trusted. Nothing else is an input.

**`model_confidence` is not read, now or later, without an ADR that reopens ADR-0004.** This is
asserted mechanically rather than reviewed: `tests/unit/test_routing_ignores_model_confidence.py`
varies that field alone across a fixture set and requires the decision not to move (SC-017).

### 3. The policy is data, and it is versioned

A `RoutingPolicy` is loaded from configuration and carries a version string. Every decision records
the version it was made under. The same result under the same policy version routes the same way,
forever.

This follows `grounding_version` (ADR-0005) and `schema_hash` (ADR-0008) rather than inventing a
mechanism: anything that can change a result is versionable, and a routing outcome delivered to a
client is a result.

**Editing a policy alters no existing decision.** Re-routing is an explicit act that produces a new
decision recording the new version. A policy edit that silently rewrote history would make every past
outcome unexplainable, which is the failure the version exists to prevent.

### 4. A decision names the signals that produced it

`reasons` carries, per entry, the field path, the signal, and the threshold it crossed. A reader can
see *why* without re-deriving it, and an auditor can see it a year later against a policy version
that is no longer current.

`reasons` carries no value and no claimed text. The no-content rule that governs every observer in
this project reaches here unchanged.

### 5. A routing decision is not an artifact and never enters the ADR-0003 chain

It is stored on the run's projection. An artifact carrying a routing outcome would make the terminal
`artifact_id` a function of a policy, so `processing_id` would move when somebody edited a threshold —
and two deployments with different policies would disagree about the identity of the same result.

This is the concrete form of §6 below: routing is a **read over** the chain, never a link in it.

### 6. It computes nothing, and that is what keeps the milestone measurable

`decide(result, policy)` is a pure function. No clock, no store, no network, no artifact written, no
value produced, no verdict changed, no severity altered. It cannot turn a validation failure into a
pass.

That is the answer to Milestone 9's objection, and it is checkable rather than asserted: SC-001
requires golden-set metrics bit-identical with every capability enabled and with all of them
disabled. If routing ever writes, that criterion fails and this ADR was wrong.

## Consequences

**This is not a blended confidence wearing a different name.** ADR-0004 forbids a single number that
merges trusted and untrusted signals; what this produces is a **predicate over trusted signals**, and
the constitution's requirement that "confidence semantics MUST be documented and versioned" is
satisfied by the policy record — which can be read, diffed, and disagreed with — rather than by a
number nobody can decompose.

**A deployment that configures no policy gets no routing decision**, and the `routing` field is absent
rather than null. Behaviour is identical to Milestone 9, which FR-101 requires of every capability
here.

**The router cannot see the one signal a model vendor most wants it to see.** A deployment convinced
that `model_confidence` carries information it needs is describing a change to ADR-0004, and should
write that ADR rather than widening the input list here.

**What a deployment does with `review` is the deployment's.** docdoc marks the result and records the
correction that comes back; it does not assign it, queue it, track a reviewer, or provide an interface
for one. Principle IX permits the model and forbids the platform, and the deferred-technology list
names "a full review UI".
