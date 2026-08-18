"""Extraction error hierarchy.

Two new types, and one reused from the ingest layer rather than redefined
(research.md R9): the constitution's error model is one flat list, which reads as
one taxonomy, and a second class also called ``ProviderError`` in a second module
is how a taxonomy becomes two.

Reusing it has one friction the plan did not anticipate. ``ProviderError``
requires ``parser_id`` -- parser vocabulary -- and the extraction layer has an
*adapter*, not a parser. ``ModelProviderError`` is a thin subclass that takes
``adapter_id`` and exposes it under that name, so ``except ProviderError`` still
catches every provider failure in the system while the attribute a caller reads
says what it means.

**Both new types root at ``DocdocError``, and that was wrong here for nine review
passes.** They derived from bare ``Exception`` while ``ModelProviderError``
inherited the root through ingest's chain, so ``except DocdocError`` caught every
provider failure and silently missed every schema and conformance failure -- the
two most common outcomes of a wrong call. A partial catch is worse than no catch,
because it looks like it works. The kernel and ingest layers both root correctly
(``KernelError(DocdocError)``, ``IngestError(DocdocError)``); this layer was the
only one outside the taxonomy the constitution's error model describes as one
list, while three design artifacts said otherwise.
"""

from __future__ import annotations

# Imported from the submodule, not from `docdoc.ingest`. Importing the package
# facade pulls in `docdoc.ingest.parse` -> `registry` -> `parsers.pdf_text` ->
# `pymupdf`, which would put a provider SDK in the extraction layer's transitive
# import graph. `lint-imports` catches that; the AST boundary scan does not,
# because the direct import looks innocent. It also matters at runtime and not
# only to the linter: PyMuPDF is AGPL-3.0, and `import docdoc.extraction` has no
# business loading it.
from docdoc.ingest.errors import ProviderError
from docdoc.kernel import DocdocError

__all__ = [
    "ExtractionError",
    "ModelProviderError",
    "ProviderError",
    "SchemaError",
]


class SchemaError(DocdocError):
    """A schema could not be loaded, registered, or resolved. Never retried.

    Covers an unknown identity, a malformed file, an unrecognised type or
    constraint, a duplicate field name, a repeating group nested inside another,
    and a schema registered without a prompt.

    ``available`` carries what *does* exist when the failure is a resolution
    failure, because FR-016 requires the error to name it: an error that says
    only "not found" sends the caller to read the registry by hand.
    """

    def __init__(
        self,
        message: str,
        *,
        identity: str | None = None,
        path: str | None = None,
        field_path: str | None = None,
        available: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.identity = identity
        #: The schema *file* at fault, for a load-time failure. Never its content.
        self.path = path
        #: Dotted path to the offending field, for a structural failure.
        self.field_path = field_path
        self.available = available


class ExtractionError(DocdocError):
    """A model's answer could not become a result. Never retried.

    Covers a response that is not the requested shape, a value that does not
    parse to its declared type, a declared field the response omitted, an
    over-budget input or output, and a truncated response.

    Retrying an ``ExtractionError`` would re-send the same request and get the
    same class of answer, which is why the constitution's error model puts
    extraction failures outside the retryable set.
    """

    REASONS = frozenset(
        {
            "shape",
            "type",
            "missing_field",
            "input_budget",
            "output_budget",
            "truncated",
        }
    )

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        document_id: str | None = None,
        schema_identity: str | None = None,
        adapter_id: str | None = None,
        field_path: str | None = None,
    ) -> None:
        if reason not in self.REASONS:
            raise ValueError(f"unknown extraction failure reason {reason!r}")
        super().__init__(message)
        self.reason = reason
        #: SC-012 requires every failure to name the document, the schema, and
        #: the responsible adapter. They are attributes rather than message text
        #: so a caller routes on them without parsing prose.
        self.document_id = document_id
        self.schema_identity = schema_identity
        self.adapter_id = adapter_id
        self.field_path = field_path


class ModelProviderError(ProviderError):
    """A model-backed extraction failed at the transport or service level.

    ``transient`` is inherited and still derived from ``reason``, so the retry
    loop and the caller cannot disagree about whether another attempt is worth
    making.

    ``refusal`` is the reason that does not look like a failure on the wire: the
    provider answers with a *successful* response whose stop reason says it
    declined. It is permanent -- re-sending the same content gets the same
    decision -- and it is the one an adapter that reads content without checking
    the stop reason would report as an answer.
    """

    REASONS = frozenset(
        {
            "timeout",
            "deadline",
            "rate_limit",
            "auth",
            "transport",
            "service",
            "refusal",
            "unavailable",
            "request",
        }
    )
    TRANSIENT_REASONS = frozenset({"timeout", "rate_limit", "transport", "service"})

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        adapter_id: str,
        document_id: str | None = None,
        schema_identity: str | None = None,
        attempts: int = 0,
        retry_after_s: float | None = None,
        refusal_category: str | None = None,
    ) -> None:
        if reason not in self.REASONS:
            raise ValueError(f"unknown provider failure reason {reason!r}")
        super().__init__(
            message,
            reason=reason,
            parser_id=adapter_id,
            attempts=attempts,
            retry_after_s=retry_after_s,
        )
        self.document_id = document_id
        self.schema_identity = schema_identity
        #: Whatever the provider said about *why* it declined, passed through
        #: verbatim and never interpreted.
        self.refusal_category = refusal_category

    @property
    def adapter_id(self) -> str:
        """The adapter that failed.

        The base class stores this as ``parser_id`` because it was written for
        the ingest layer. This is the name the extraction layer uses; both read
        the same value.
        """
        return str(self.parser_id)
