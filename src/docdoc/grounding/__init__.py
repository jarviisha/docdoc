"""Where an extracted value came from, decided by docdoc rather than by a model.

Milestone 3 recorded, for every extracted value, the verbatim text a model
*claims* it read that value from -- and resolved none of it. This layer turns
that claim into a character range in the document's canonical text, the pages it
occupies, and the boxes covering it. Or it says, plainly, that it could not.

Four things this layer refuses to do, each for a reason:

**It does not reach a model.** Not to re-ask, not to confirm, not to re-quote.
Principle II forbids a model deciding whether its own output is grounded, so the
answer has to come from deterministic code the project controls. The practical
consequence is that this is the first milestone since the kernel with no
probabilistic edge at all: no network, no credentials, no provider, and no test
a contributor has to skip. A ``forbidden`` contract in ``pyproject.toml`` makes
that a build failure rather than a promise.

Stated precisely, because the loose version is checkable and false: **no module in
this layer imports anything that can open a connection.** Importing this package
does put ``socket`` in ``sys.modules``, exactly as importing ``docdoc.kernel``
alone does -- pydantic reaches ``email.utils`` while building models. That is
pre-existing and permitted; what is new here is that nothing in this layer's own
code can reach a network.

**It does not judge whether the value is right.** It answers *where*, not
*whether*. A value whose claim resolves but whose number disagrees with the text
at that range is a validation finding, and validation is Milestone 5. Reporting
it here would put a semantic rule in the wrong stage (Principle VII).

**It does not modify what it reads.** Not the document, not its canonical text,
not its provenance, not the extraction result. Grounding produces a new result.

**It does not expose the match view.** Matching runs against a derived, versioned
folding of the document's text (ADR-0006) so that ligatures, soft hyphens, and
line-break hyphenation do not defeat a correct value. That folded text is never
returned, never logged, and never presented as ``Document.text``, which stays
byte-faithful. Every range this layer returns is a range into the *source*.
"""

from __future__ import annotations

from docdoc.grounding.errors import GroundingError
from docdoc.grounding.ground import ground
from docdoc.grounding.options import GroundingOptions
from docdoc.grounding.result import (
    Alternative,
    GroundingCounts,
    GroundingOutcome,
    GroundingProvenance,
    GroundingResult,
    GroundingStatus,
)

__all__ = [
    "Alternative",
    "GroundingCounts",
    "GroundingError",
    "GroundingOptions",
    "GroundingOutcome",
    "GroundingProvenance",
    "GroundingResult",
    "GroundingStatus",
    "ground",
]
