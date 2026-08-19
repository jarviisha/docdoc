"""The one error this layer raises, and the line it draws.

A **finding** is a statement about the document: this value is too long, these
lines do not add up. A **`ValidationError`** is a statement about the request:
these two artifacts did not come from each other, this result was produced under
a different schema than the one you handed me. Neither can substitute for the
other, and collapsing them is how a mismatched pair produces a confident verdict
(FR-044).

Never retried, per the constitution's error model: there is no transient failure
mode in a deterministic, offline computation.
"""

from __future__ import annotations

from docdoc.kernel import DocdocError

__all__ = ["ValidationError"]


class ValidationError(DocdocError):
    """A result could not be validated at all. Never a failing check.

    Both sides of whatever mismatch caused it are carried as attributes rather
    than only interpolated into the message, because a caller that has to parse
    prose to learn which artifact was wrong will not do it.
    """

    def __init__(
        self,
        message: str,
        *,
        expected: str | None = None,
        actual: str | None = None,
        field_path: str | None = None,
    ) -> None:
        super().__init__(message)
        #: What the supplied artifacts should have named.
        self.expected = expected
        #: What they did name.
        self.actual = actual
        #: The offending field, for a shape disagreement.
        self.field_path = field_path
