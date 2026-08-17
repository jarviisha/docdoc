"""Ingest error hierarchy.

The type names come from the constitution's fixed error vocabulary; the
``reason`` attribute carries the distinction a new type would otherwise
fragment the list to express (research.md R10). Every error is structured, so a
caller routes on attributes rather than on message text.

Two errors in docdoc are called "capability" and they answer different
questions:

``kernel.CapabilityError``
    *This document* cannot answer that -- its parser supplied no geometry.

``ingest.ParserCapabilityError``
    *No available parser* can satisfy this request.

They are never interchangeable.
"""

from __future__ import annotations

from docdoc.kernel import DocdocError

__all__ = [
    "IngestError",
    "ParserCapabilityError",
    "ParserError",
    "ProviderError",
    "UnsupportedDocumentError",
]


class IngestError(DocdocError):
    """Root of every error raised by the ingest layer.

    ``blob_id`` identifies the offending input without carrying any of its
    content, which is what keeps error reporting compatible with the rule that
    document contents never reach a log (FR-029).
    """

    def __init__(self, message: str, *, blob_id: str | None = None) -> None:
        super().__init__(message)
        self.blob_id = blob_id


class UnsupportedDocumentError(IngestError):
    """The file cannot be accepted at all. Never retried.

    ``parser_id`` is set when a *parser* refused the file -- an encrypted PDF, or
    one a service rejected as unreadable -- and left ``None`` when the refusal
    happened before any parser was chosen, as it does for an unrecognized
    signature or an over-limit file. SC-007 requires a failure to name the
    responsible parser, and "there wasn't one yet" is itself the answer in the
    second case.
    """

    REASONS = frozenset({"mime_type", "size_limit", "page_limit", "encrypted", "corrupt"})

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        blob_id: str | None = None,
        media_type: str | None = None,
        parser_id: str | None = None,
    ) -> None:
        super().__init__(message, blob_id=blob_id)
        self.reason = reason
        self.media_type = media_type
        self.parser_id = parser_id


class ParserCapabilityError(IngestError):
    """No available parser satisfies the request.

    Carries the full candidate view rather than a bare message: the constitution
    requires a failure to name the parser, the required capability, and its
    availability (Principle VIII), which a caller cannot reconstruct from prose.
    """

    def __init__(
        self,
        message: str,
        *,
        required: tuple[str, ...] = (),
        media_type: str | None = None,
        candidates: tuple[tuple[str, bool, str | None], ...] = (),
        blob_id: str | None = None,
    ) -> None:
        super().__init__(message, blob_id=blob_id)
        self.required = required
        self.media_type = media_type
        #: ``(parser_id, available, unavailable_reason)`` for every candidate.
        self.candidates = candidates


class ParserError(IngestError):
    """A parser produced something that cannot become a valid Document."""

    REASONS = frozenset(
        {
            "invalid_order",
            "capability_mismatch",
            "empty_result",
            # The document does not correspond to the file that was handed over,
            # or claims a parser or verdict other than the one that ran.
            "wrong_document",
            "internal",
        }
    )

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        parser_id: str,
        blob_id: str | None = None,
        detail: str = "",
    ) -> None:
        super().__init__(message, blob_id=blob_id)
        self.reason = reason
        self.parser_id = parser_id
        self.detail = detail


class ProviderError(IngestError):
    """A service-backed parse failed.

    ``transient`` is derived from ``reason`` rather than passed in, so a caller
    and the retry loop cannot disagree about whether a failure is worth another
    attempt (ING-21).
    """

    REASONS = frozenset({"timeout", "deadline", "rate_limit", "auth", "transport", "service"})
    TRANSIENT_REASONS = frozenset({"timeout", "rate_limit", "transport", "service"})

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        parser_id: str,
        blob_id: str | None = None,
        attempts: int = 0,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(message, blob_id=blob_id)
        self.reason = reason
        self.parser_id = parser_id
        self.attempts = attempts
        #: How long the service asked the caller to wait, when it said so. The
        #: retry loop honours it in preference to its own backoff, and the
        #: deadline overrides both (FR-038). Declared here rather than attached
        #: dynamically, because the retry loop depends on it and an attribute
        #: that only sometimes exists is not a contract.
        self.retry_after_s = retry_after_s

    @property
    def transient(self) -> bool:
        """Whether another attempt could plausibly succeed."""
        return self.reason in self.TRANSIENT_REASONS
