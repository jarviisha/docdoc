"""The one error this layer raises, and the line it draws.

A **field outcome** is a statement about the document: this value is wrong, this
one is missing, this one nobody labelled. An **:class:`EvaluationError`** is a
statement about the request: this prediction is for a document the golden set does
not contain, these labels were written under a different schema than the result
you handed me.

Neither substitutes for the other, and collapsing them is how a mismatched pair
produces a confident report -- a number computed over two things that do not
describe the same subject, with nothing in the output to say so.

Never retried, per the constitution's error model: there is no transient failure
mode in a deterministic, offline computation, and retrying one would only burn
time before failing identically (FR-060).
"""

from __future__ import annotations

from docdoc.kernel import DocdocError

__all__ = ["EvaluationError"]


class EvaluationError(DocdocError):
    """A prediction set could not be scored at all. Never a failing comparison.

    Both sides of whatever mismatch caused it are carried as attributes rather
    than only interpolated into the message, for the reason ``ValidationError``
    already records: a caller that has to parse prose to learn which side was
    wrong will not do it.
    """

    def __init__(
        self,
        message: str,
        *,
        dataset: str | None = None,
        document_id: str | None = None,
        field_path: str | None = None,
        expected: str | None = None,
        actual: str | None = None,
    ) -> None:
        super().__init__(message)
        #: Identity of the golden set involved, where one was resolved.
        self.dataset = dataset
        #: The offending document.
        self.document_id = document_id
        #: The offending field or label.
        self.field_path = field_path
        #: What the two sides should have agreed on.
        self.expected = expected
        #: What they actually said.
        self.actual = actual
