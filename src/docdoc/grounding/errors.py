"""The grounding layer's one error type.

``GroundingError`` is named in the constitution's error model and has had no
implementation until this milestone.

**It carries no ``transient`` flag, and the absence is the point.** ``ProviderError``
has one because a network has transient failures; retrying a timeout can succeed.
Grounding is deterministic arithmetic over data already in memory, so a failure
here means an input was wrong or an invariant broke -- retrying reproduces it
exactly. The constitution's error model says as much ("retries are permitted for
LLM/network calls only"), and a flag defaulting to ``False`` on every instance
would invite a caller to write a retry loop that can only ever spin.
"""

from __future__ import annotations

from docdoc.kernel import DocdocError

__all__ = ["GroundingError"]


class GroundingError(DocdocError):
    """Grounding could not be performed. Never retried.

    Raised in two situations, and deliberately not in a third:

    * The extraction result did not come from this document. Spans anchor to a
      specific parse (ADR-0002), so resolving one parse's claims against another
      would produce ranges that are valid and wrong -- the failure that most
      looks like success.
    * An offset-map invariant failed at runtime. This is a defensive check: a
      broken map must fail loudly rather than return a plausible wrong range.

    It is **not** raised when a value cannot be located. That is an
    ``ungrounded`` outcome, and being able to say so is what this stage is for.
    """

    def __init__(
        self,
        message: str,
        *,
        document_id: str | None = None,
        extraction_document_id: str | None = None,
        field_path: str | None = None,
    ) -> None:
        super().__init__(message)
        self.document_id = document_id
        self.extraction_document_id = extraction_document_id
        self.field_path = field_path
