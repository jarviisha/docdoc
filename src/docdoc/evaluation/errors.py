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

from contextlib import contextmanager
from typing import TYPE_CHECKING

from docdoc.kernel import DocdocError

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["EvaluationError", "naming"]


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


@contextmanager
def naming(dataset: str | None) -> Iterator[None]:
    """Label every :class:`EvaluationError` raised inside with the dataset at fault.

    FR-060 requires an error to name the dataset, the document, and the field. The
    document and the field are known at the raise site; **the dataset usually is
    not**. A comparator refusing a duplicate alignment key knows the group and the
    entry and has never been told which golden set it is scoring, and threading an
    identity through every helper to satisfy one attribute would put a parameter
    nobody reads into a dozen signatures.

    So the label is attached where it becomes known -- at the entry points, which
    all have it -- rather than where the error is raised. Three consequences worth
    stating:

    - **The innermost label wins.** A nested call that already named a dataset
      keeps it; the outer entry point does not overwrite it. That matters for
      :func:`~docdoc.evaluation.compare`, where two datasets are in scope and the
      refusal has already named the right one.
    - **A future raise site gets this for free**, which is the point. The
      alternative decays: someone adds a check, forgets the argument, and the
      attribute is silently ``None`` again -- which is the state this replaced.
    - **``None`` stays ``None`` when nothing is known.** Where a manifest is
      malformed there is no identity yet, so the caller passes the *path* it read
      rather than inventing an id. An error that has to lie about which field it
      cannot fill is worse than one that carries the path it does know.
    """
    try:
        yield
    except EvaluationError as error:
        if error.dataset is None:
            error.dataset = dataset
        raise
