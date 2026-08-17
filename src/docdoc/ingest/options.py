"""Parse options and transport settings — deliberately two types.

Exactly one of them may influence document identity. Options can change what a
parse *produces*, so they are hashed into ``document_id``. Retries and timeouts
cannot change the content of a successful result, so they must not be able to
change its identity (FR-039, ING-5).

Keeping them apart makes that true by construction rather than by discipline:
there is no code path by which a ``TransportSettings`` reaches
``options_hash_for``.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from docdoc.kernel import options_hash_for

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["Deadline", "TransportSettings", "options_fingerprint"]


def options_fingerprint(options: Mapping[str, Any] | None) -> tuple[dict[str, Any], str]:
    """Canonicalize parse options and reduce them to identity.

    Returns the plain mapping to store in provenance and its hash. Key ordering
    is irrelevant to the hash, which ``kernel.options_hash_for`` guarantees
    (FR-022, ING-6).
    """
    materialized = dict(options or {})
    return materialized, options_hash_for(materialized)


class TransportSettings(BaseModel):
    """How a service-backed parse talks to its service.

    Ignored entirely by parsers that run offline.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_attempts: int = Field(default=3, ge=1)
    initial_backoff_s: float = Field(default=0.5, gt=0.0)
    max_backoff_s: float = Field(default=8.0, gt=0.0)
    jitter: bool = True
    attempt_timeout_s: float = Field(default=30.0, gt=0.0)
    deadline_s: float = Field(default=120.0, gt=0.0)

    def backoff_for(self, attempt: int) -> float:
        """Exponential backoff for a 1-based attempt number, before jitter."""
        return float(min(self.initial_backoff_s * (2 ** (attempt - 1)), self.max_backoff_s))

    def start(self) -> Deadline:
        """Open the overall budget for one parse."""
        return Deadline(started_at=time.monotonic(), budget_s=self.deadline_s)


class Deadline:
    """The overall time budget for a single parse.

    Monotonic, so a system clock adjustment mid-parse cannot extend or collapse
    it. Lives outside the kernel precisely because it reads a clock.
    """

    __slots__ = ("_budget_s", "_started_at")

    def __init__(self, *, started_at: float, budget_s: float) -> None:
        self._started_at = started_at
        self._budget_s = budget_s

    @property
    def remaining_s(self) -> float:
        return self._budget_s - (time.monotonic() - self._started_at)

    @property
    def expired(self) -> bool:
        return self.remaining_s <= 0.0

    def allows(self, wait_s: float) -> bool:
        """Whether waiting this long would still leave time to act afterwards.

        A service that asks for a longer wait than the budget allows does not
        get it: the parse fails on the deadline rather than sleeping past it.
        """
        return wait_s < self.remaining_s
