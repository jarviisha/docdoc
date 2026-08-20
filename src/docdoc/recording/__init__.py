"""Recording — the only part of evaluation that reaches a provider.

:func:`record_predictions` runs parse -> extract -> ground -> validate over the
documents of a golden set and records what came back, so that
:mod:`docdoc.evaluation` has something to score.

Three properties, and the third is why this is a package of its own:

**It is opt-in.** Nothing in :mod:`docdoc.evaluation` calls it, and scoring never
requires it. Public-tier predictions are recorded once, committed, and replayed,
so a contributor scores a checkout with no credentials at all.

**It is the only path for the restricted tier.** A recorded prediction carries the
values it extracted, so a prediction over a document docdoc may not redistribute
is itself not redistributable. Those predictions are produced by whoever holds the
corpus and are never committed.

**It is not part of evaluation, and that is enforced rather than asserted.** This
package sits *above* ``docdoc.evaluation`` in the ``import-linter`` layers
contract. It imports the scorer's types to build a ``PredictionSet``; the reverse
fails the build. Inside one package FR-003's "recording MUST NOT be part of
evaluation" would be a naming convention. As two layers it is a check.

**Known limitation: there is no ``ArtifactStore``, so every run re-parses every
document.** ADR-0003's whole point is that changing a prompt invalidates the
extraction artifact and *reuses* the parse; with no store it cannot, and
:mod:`docdoc.grounding` rebuilds its match view per call although ADR-0006 says
the view is cached by ``document_id`` + ``match_view_version``. On the public
tier, which uses the ``echo`` adapter, that costs nothing. On a restricted tier
reached through a real provider it is a repeated, billable cost every time
predictions are refreshed. Recorded here so it is known rather than discovered on
an invoice; persistence is out of scope for Milestone 6 by that milestone's own
specification.
"""

from __future__ import annotations

__all__: list[str] = []
