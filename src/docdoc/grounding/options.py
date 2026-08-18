"""The settings a grounding run used that can change its outcome.

Both fields participate in artifact identity, because both can change an answer.
That is the rule Milestone 3 applied in the other direction: its transport
settings stayed *out* of identity because a timeout cannot change the content of
a successful result. A budget is not a timeout -- reaching it changes the answer.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["DEFAULT_CANDIDATE_BUDGET", "DEFAULT_THRESHOLD", "GroundingOptions"]

#: ADR-0005's pinned threshold. An initial estimate, not a measured optimum: it
#: MUST be tuned against the golden set at Milestone 6, and that tuning bumps
#: ``GROUNDING_VERSION``.
DEFAULT_THRESHOLD = 0.90

#: **Derived from SC-020, not chosen.** ``500 ms / 20 values / 72 candidate
#: starts per ms ~= 1,800``, rounded down for headroom on a CI runner slower than
#: the machine that produced the measurement. This is what makes SC-020 hold by
#: construction rather than by hope.
#:
#: Do not "round it up to a nicer number". An earlier draft used 20,000, which at
#: the measured rate is ~278 ms for a *single* value -- one value could consume
#: 56% of the budget meant for twenty, and the adversarial document that
#: motivated the cap (9,998 candidate starts) would never have reached it. A
#: backstop sized so it can never trip is decoration.
#:
#: On ordinary text the filter produces ~17 candidate starts, so this sits about
#: 100x above the normal case and fires only on pathological input.
DEFAULT_CANDIDATE_BUDGET = 1_500


class GroundingOptions(BaseModel):
    """Threshold and budget. Frozen, and folded into the stage's identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Minimum similarity for the approximate tier.
    #:
    #: This is **not** a post-filter. The candidate generator derives its edit
    #: budget from it -- ``k = floor((1 - threshold) * m / threshold)`` -- so the
    #: threshold changes which candidates are *generated*, not merely which are
    #: accepted. A stored result therefore cannot be re-thresholded after the
    #: fact; it has to be re-grounded.
    threshold: float = Field(default=DEFAULT_THRESHOLD, ge=0.0, le=1.0)

    #: Maximum candidate positions scored per value. See the module constant for
    #: why this number is what it is.
    candidate_budget: int = Field(default=DEFAULT_CANDIDATE_BUDGET, gt=0)
