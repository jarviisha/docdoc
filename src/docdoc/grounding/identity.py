"""This stage's place in the ADR-0003 artifact chain.

    blob_id -> parse artifact (== document_id) -> extraction artifact
            -> **grounding artifact** -> validation artifact

The options hash folds exactly three things -- ``grounding_version``,
``match_view_version``, and ``GroundingOptions`` -- and nothing else. Both halves
of that matter. Omitting an input that can change the output is the stale-cache
bug ADR-0003 exists to close; including one that cannot invalidates a cache for
no reason, which is how a chain stops being trusted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docdoc.grounding.view import MATCH_VIEW_VERSION
from docdoc.kernel import canonical_json, content_id_for, options_hash_for

__all__ = [
    "GROUNDER_ID",
    "GROUNDER_VERSION",
    "GROUNDING_VERSION",
    "grounding_artifact_id_for",
    "options_hash_for_grounding",
]

if TYPE_CHECKING:
    from docdoc.grounding.options import GroundingOptions

#: The processor id of this stage. Stable; the *version* is what moves.
GROUNDER_ID = "deterministic-grounder"

#: ADR-0003 requires this to move whenever output changes for fixed inputs.
GROUNDER_VERSION = "1.0.0"

#: ADR-0005's pinned algorithm identifier. It designates the **complete**
#: algorithm -- both tiers, the total tie-break, the alternatives limit, the
#: derived candidate slack, and the default threshold. Changing any of them
#: REQUIRES a bump here, and the snapshot test is what makes that a build failure
#: rather than a review obligation.
GROUNDING_VERSION = "v1"


def options_hash_for_grounding(options: GroundingOptions) -> str:
    """Identity of one grounding configuration (FR-037).

    ``candidate_budget`` is folded, and the reason it belongs here is worth
    stating because Milestone 3 made the opposite call for its transport
    settings: a timeout cannot change the content of a successful result, so it
    stayed out of identity. A budget can -- reaching it changes which candidates
    were examined, and therefore which one won.
    """
    return options_hash_for(
        {
            "grounding_version": GROUNDING_VERSION,
            "match_view_version": MATCH_VIEW_VERSION,
            "threshold": options.threshold,
            "candidate_budget": options.candidate_budget,
        }
    )


def grounding_artifact_id_for(
    *,
    extraction_artifact_id: str,
    options: GroundingOptions,
) -> str:
    """``sha256(input_artifact_id + processor_id + processor_version + options_hash)``.

    Chained from the *extraction* artifact, so this id transitively inherits the
    document, the parser, the schema, the prompt, and the model without naming
    any of them (ADR-0003).
    """
    payload = canonical_json(
        {
            "input_artifact_id": extraction_artifact_id,
            "processor_id": GROUNDER_ID,
            "processor_version": GROUNDER_VERSION,
            "options_hash": options_hash_for_grounding(options),
        }
    )
    return content_id_for(payload)
