"""Half-open text ranges — the unit that ties extracted values to their source.

Positions are Python string indices, i.e. **Unicode code points**, never bytes
and never grapheme clusters (FR-004, research.md R5). ``text[span.start:span.end]``
is therefore correct by construction.
"""

from __future__ import annotations

from typing import NamedTuple

from docdoc.kernel.errors import SpanError

__all__ = ["Span"]


class Span(NamedTuple):
    """A half-open range ``[start, end)`` over a document's canonical text.

    ``__len__`` is deliberately overridden to report the *character* length
    rather than the field count. Two consequences are intentional and tested:

    * ``len(Span(10, 25)) == 15``, while ``start, end = span`` still unpacks.
    * An empty span is falsy, so ``if span:`` means "is non-empty". Kernel code
      avoids that idiom and tests for ``is_empty`` explicitly.

    The bare tuple constructor cannot validate, so :meth:`create` is the
    checked entry point; every kernel operation validates spans it receives.
    """

    start: int
    end: int

    @classmethod
    def create(cls, start: int, end: int) -> Span:
        """Construct a span, enforcing SP-1 (``0 <= start <= end``)."""
        for name, value in (("start", start), ("end", end)):
            # bool is an int subclass; accepting it would let True silently mean 1.
            if not isinstance(value, int) or isinstance(value, bool):
                raise SpanError(
                    f"span {name} must be an int, got {type(value).__name__}",
                    span=(start, end),
                )
        if start < 0:
            raise SpanError(f"span start must not be negative, got {start}", span=cls(start, end))
        if end < start:
            raise SpanError(
                f"span end must not precede start, got start={start} end={end}",
                span=cls(start, end),
            )
        return cls(start, end)

    def __len__(self) -> int:
        return self.end - self.start

    @property
    def is_empty(self) -> bool:
        return self.start == self.end

    def contains(self, position: int) -> bool:
        """Whether ``position`` falls inside the range, half-open."""
        return self.start <= position < self.end

    def contains_span(self, other: Span) -> bool:
        """Whether ``other`` lies entirely within this range."""
        return self.start <= other.start and other.end <= self.end

    def intersects(self, other: Span) -> bool:
        """Whether the two ranges share at least one position.

        An empty span covers no positions, so it intersects nothing — including
        a range that contains its offset. This is what lets ``locate()`` return
        an empty result for a zero-length query (FR-009).
        """
        if self.is_empty or other.is_empty:
            return False
        return self.start < other.end and self.end > other.start

    def shift(self, delta: int) -> Span:
        """Rebase both bounds by ``delta``.

        The primitive ``slice`` and ``merge`` are built from. Shifting below zero
        is an error rather than a clamp, so a rebasing bug surfaces immediately
        instead of silently producing a valid-looking span.
        """
        return Span.create(self.start + delta, self.end + delta)

    def validate_within(self, text_length: int) -> None:
        """Enforce SP-2 — the span must fit the text it is used against."""
        if self.start < 0 or self.end < self.start or self.end > text_length:
            raise SpanError(
                f"span {self.start}:{self.end} does not fit text of length {text_length}",
                span=self,
                text_length=text_length,
            )
