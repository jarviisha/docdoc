"""Assembling the request, in the order the provider's cache requires.

The provider's prompt cache is a **prefix match**: any byte change invalidates
everything after it. Everything derived from ``schema@version`` -- the response
shape, the instructions, the field descriptions -- is identical for every
document extracted against that schema, and the document is the only volatile
part. So the request is assembled stable-to-volatile, with the cache breakpoint
at the end of the per-schema prefix.

Putting the document first, which is the natural reading order, would make every
extraction a full-price cold write. The failure is silent: results stay correct
and the bill multiplies. That is why the ordering has a test rather than a
comment (EXT-19, research.md R15).

**Nothing per-request may appear before the breakpoint** -- no timestamp, no
document id, no request id, no counter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docdoc.extraction.registry import RegisteredSchema

__all__ = ["ModelRequest", "build_request"]


class ModelRequest:
    """One request, split at the cache breakpoint.

    ``prefix`` is byte-identical for every document extracted against one
    schema; ``document_text`` is the only part that varies. An adapter sends
    them in that order and marks the boundary.
    """

    __slots__ = ("document_id", "document_text", "prefix", "response_shape", "schema_identity")

    def __init__(
        self,
        *,
        schema_identity: str,
        document_id: str | None = None,
        prefix: str,
        document_text: str,
        response_shape: dict[str, object],
    ) -> None:
        #: Which schema this request was built for. Carried explicitly because an
        #: adapter needs it for its own logging and error messages, and reading
        #: it back out of the rendered prompt would be parsing our own output.
        self.schema_identity = schema_identity
        #: Carried so that an error raised *inside* an adapter can name the
        #: document. Without it every adapter-raised failure reported
        #: ``document_id=None``, and SC-012 requires all three of document,
        #: schema, and adapter on 100% of failures.
        self.document_id = document_id
        self.prefix = prefix
        self.document_text = document_text
        self.response_shape = response_shape

    def rendered(self) -> str:
        """The whole request as one string, for adapters that take one."""
        return f"{self.prefix}\n\n{self.document_text}"


_DOCUMENT_HEADER = "Here is the document.\n\n"


def build_request(
    entry: RegisteredSchema,
    document_text: str,
    *,
    response_shape: dict[str, object],
    document_id: str | None = None,
) -> ModelRequest:
    """Assemble stable-to-volatile.

    The prefix is derived only from ``entry``. If a future change needs
    per-request context, it belongs *after* the breakpoint -- appended to
    ``document_text`` -- not woven into the prefix, however natural that reads.
    """
    prefix = entry.prompt.text.rstrip("\n")
    return ModelRequest(
        schema_identity=entry.identity,
        document_id=document_id,
        prefix=prefix,
        document_text=f"{_DOCUMENT_HEADER}{document_text}",
        response_shape=response_shape,
    )
