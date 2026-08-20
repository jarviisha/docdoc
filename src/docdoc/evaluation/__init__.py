"""Golden-set evaluation — how good is it, and did the last change make it worse.

Milestone 3 recorded what the model answered. Milestone 4 made the grounding rate
computable and set no target for it. Milestone 5 did the same for validation. Each
deferred the same question to this layer, and until it shipped every quality claim
in this repository was an assertion.

This layer takes a **golden set** -- documents together with the answers a human
states are correct -- and a **prediction set** -- what the pipeline recorded for
those documents -- and produces one report: every labelled field resolved to
exactly one of six closed outcomes, the five constitutionally required metrics
computed from those outcomes, and the provenance that says what was measured.

What it refuses to do, and why each refusal is load-bearing:

**It does not re-derive anything.** Not the extraction, not the grounding, not the
validation. It reads recorded facts. A stage that quietly recomputed one would be
reporting on a pipeline nobody ran (FR-002).

**It does not ask a model anything** -- including whether a predicted value
*means* the same as the expected one. A model judging its own output is the
failure Principle II forbids for grounding, and it is no more acceptable when the
subject is accuracy (FR-008).

**It does not read ``model_confidence``.** Untrusted upstream by ADR-0004, and
untrusted here (FR-028).

**It does not open a document.** It compares labels against recorded predictions,
so it has no need of one -- which is also why scoring needs no parser, no
credentials, and no network (FR-007).

**It does not normalize, case-fold, trim, round, or coerce** to make a value
match. Any leniency is a declared, versioned comparator recorded next to every
metric it affected (FR-024).

**It does not define a second grounding rate.** Milestone 4's definition and its
recorded counts are reused (FR-033).

**It does not persist anything**, and it does not decide what should happen about
a regression. Whether a build fails is policy configured on top of this output; a
comparison that also decided would bury the decision inside the thing being
measured (FR-049).

**It does not import :mod:`docdoc.recording`.** Producing a prediction set needs a
provider; scoring one must not. That separation is the ``import-linter`` layers
contract, not a convention -- ``docdoc.recording`` sits *above* this package, so
the import fails the build (FR-003).
"""

from __future__ import annotations

__all__: list[str] = []
