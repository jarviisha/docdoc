"""The model adapter contract, and the options that reach identity.

A caller never names a provider, a model family, or a model version. Which model
answers is configuration, and the only observable difference between two of them
is in provenance (FR-021). The contract exists so that adding a second provider
is a new module rather than a change here.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from docdoc.extraction.budget import DEFAULT_INPUT_BUDGET_TOKENS

if TYPE_CHECKING:
    from docdoc.extraction.prompt import ModelRequest

__all__ = [
    "Availability",
    "Effort",
    "ExtractionOptions",
    "ModelAdapter",
    "ModelResponse",
    "ModelUsage",
    "Thinking",
]


class Effort(StrEnum):
    """How much reasoning the model spends.

    A result-affecting input the reference design never contemplated, and
    therefore folded into identity (research.md R4).
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class Thinking(StrEnum):
    ADAPTIVE = "adaptive"
    DISABLED = "disabled"


class ExtractionOptions(BaseModel):
    """The settings and budgets a call runs with -- the ones that change a result.

    There is deliberately no ``temperature``, no ``top_p``, and no ``seed``: the
    chosen provider's current models reject the first two outright and have never
    had the third. Offering a knob that cannot be honoured would be worse than
    not offering one (research.md R4).

    Retry, timeout, and deadline are **not** here. They live in
    ``TransportSettings``, which is what makes "transport cannot change identity"
    true by construction rather than by discipline (FR-027).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_tokens: int = Field(default=8192, ge=1)
    effort: Effort = Effort.HIGH
    thinking: Thinking = Thinking.ADAPTIVE
    input_budget_tokens: int = Field(default=DEFAULT_INPUT_BUDGET_TOKENS, ge=1)


class ModelUsage(BaseModel):
    """What a call consumed.

    Every field is optional because an adapter with no notion of tokens -- the
    in-repo one, for instance -- reports none, and that is a normal condition
    rather than an error.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None


class ModelResponse(BaseModel):
    """One structured answer, plus what it cost and who produced it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    payload: dict[str, Any]
    model_id: str
    model_version: str
    usage: ModelUsage = ModelUsage()


class Availability(BaseModel):
    """Whether an adapter can be used, and if not, why.

    A missing credential or a missing optional dependency is reported with its
    reason rather than by omitting the adapter from consideration: silence would
    make "not installed" indistinguishable from "no such thing" (FR-028).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    usable: bool
    reason: str | None = None


@runtime_checkable
class ModelAdapter(Protocol):
    """Anything that answers a structured request about a document.

    The real adapter and the in-repo ``EchoAdapter`` are two instances of this
    one contract, and the contract suite runs against both -- which is what keeps
    it from being a description of whichever one exists.
    """

    @property
    def id(self) -> str:
        """Stable identity of the adapter itself."""

    @property
    def version(self) -> str:
        """Bumped whenever output changes for unchanged inputs (FR-036)."""

    def available(self) -> Availability: ...

    def complete(self, request: ModelRequest, options: ExtractionOptions) -> ModelResponse:
        """Exactly one response, or a typed error. Never a partial one."""
