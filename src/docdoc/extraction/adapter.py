"""The model adapter contract, and the options that reach identity.

A caller never names a provider, a model family, or a model version. Which model
answers is configuration, and the only observable difference between two of them
is in provenance (FR-021). The contract exists so that adding a second provider
is a new module rather than a change here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from docdoc.extraction.budget import DEFAULT_INPUT_BUDGET_TOKENS

if TYPE_CHECKING:
    from docdoc.extraction.prompt import ModelRequest

__all__ = [
    "Availability",
    "ExtractionOptions",
    "ModelAdapter",
    "ModelResponse",
    "ModelUsage",
]


class ExtractionOptions(BaseModel):
    """The settings and budgets a call runs with -- the ones that change a result.

    These are the sampling parameters ADR-0003's ``Extract`` row names, and on the
    chosen provider they all exist -- ``temperature``, ``top_p``, ``top_k``, and a
    ``seed`` (research.md R4). ``max_output_tokens`` caps the model's whole output
    including its reasoning, which is why ``thinking_budget`` is here too: a budget
    sized for the expected JSON alone truncates mid-answer, and the truncation
    arrives as a stop reason rather than as an error (R14).

    ``temperature`` defaults to ``0.0`` rather than to the provider's own default,
    because extraction wants the least variance available. That is not a
    determinism guarantee -- ``seed`` is best-effort and Google promises no
    bit-exactness -- and FR-037 still declines to claim byte-identical repeats.

    Retry, timeout, and deadline are **not** here. They live in
    ``TransportSettings``, which is what makes "transport cannot change identity"
    true by construction rather than by discipline (FR-027).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_output_tokens: int = Field(default=8192, ge=1)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    top_k: int | None = Field(default=None, ge=1)

    #: Best-effort reproducibility. Recorded either way, because a result produced
    #: under an unrecorded seed is not explainable.
    seed: int | None = None

    #: ``None`` leaves the provider's automatic budget in place. Folded into
    #: identity because it changes the answer.
    thinking_budget: int | None = Field(default=None, ge=0)

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

    #: Reasoning tokens, where the provider reports them. Worth its own field
    #: rather than folded into ``output_tokens``: reasoning is billed from the same
    #: output allowance as the answer (R14), so "the response was truncated" and
    #: "the reasoning ate the budget" are different diagnoses and the numbers are
    #: what tell them apart.
    reasoning_tokens: int | None = None


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
