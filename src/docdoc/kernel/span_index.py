"""Fast lookup from a text range to the tokens covering it.

Document construction guarantees tokens are ascending and non-overlapping
(DOC-2, DOC-3), which reduces the general interval-stabbing problem to a binary
search. An interval tree would only earn its complexity if intervals could
overlap, which construction forbids (research.md R2).
"""

from __future__ import annotations

from bisect import bisect_right
from typing import TYPE_CHECKING, Any

from pydantic_core import core_schema

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from pydantic import GetCoreSchemaHandler

    from docdoc.kernel.span import Span
    from docdoc.kernel.token import Token

__all__ = ["SpanIndex"]


class SpanIndex:
    """An immutable, ordered view of a document's tokens.

    Complexity: ``tokens_in`` is O(log n + k), ``token_at`` is O(log n).
    """

    __slots__ = ("_starts", "_tokens")

    def __init__(self, tokens: Sequence[Token] = ()) -> None:
        # Copy defensively: a caller mutating the source list afterwards must
        # not be able to change an index that a frozen Document already holds.
        self._tokens: tuple[Token, ...] = tuple(tokens)
        self._starts: tuple[int, ...] = tuple(t.span.start for t in self._tokens)

    @property
    def tokens(self) -> tuple[Token, ...]:
        return self._tokens

    def __len__(self) -> int:
        return len(self._tokens)

    def __iter__(self) -> Iterator[Token]:
        return iter(self._tokens)

    def __getitem__(self, index: int) -> Token:
        return self._tokens[index]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SpanIndex):
            return NotImplemented
        return self._tokens == other._tokens

    def __hash__(self) -> int:
        return hash(self._tokens)

    def __repr__(self) -> str:
        return f"SpanIndex({len(self._tokens)} tokens)"

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        """Serialize as the token sequence, and rebuild the index on the way back.

        Without this a ``Document`` cannot be written to JSON at all, which means
        the parse stage has no artifact and "changing a prompt reuses the parse"
        stays the text it was for six milestones. This class is the only thing in
        a ``Document`` pydantic could not already handle.

        The stored form is the tokens and nothing else: ``_starts`` is derived
        from them in ``__init__``, and persisting a derived index would be a
        second copy of the same fact that could arrive disagreeing with the
        first. Rebuilding it costs one pass and cannot be wrong.

        ``pydantic_core`` is pydantic's own runtime rather than a second
        dependency, so Principle I's "the kernel's only permitted runtime
        dependency is pydantic" holds. The forbidden-imports contract lists what
        the kernel may not reach, and this is not on it.
        """
        from docdoc.kernel.token import Token

        tokens = handler.generate_schema(tuple[Token, ...])
        from_tokens = core_schema.no_info_after_validator_function(cls, tokens)

        return core_schema.json_or_python_schema(
            json_schema=from_tokens,
            # An existing index passes through untouched; a sequence of tokens is
            # accepted too, so `Document(tokens=[...])` keeps working exactly as
            # every caller since Milestone 1 has written it.
            python_schema=core_schema.union_schema(
                [core_schema.is_instance_schema(cls), from_tokens]
            ),
            serialization=core_schema.plain_serializer_function_ser_schema(
                lambda index: index.tokens,
                return_schema=tokens,
                when_used="always",
            ),
        )

    def tokens_in(self, span: Span) -> tuple[Token, ...]:
        """Every token intersecting ``span``, in document order.

        An empty span intersects nothing, so this returns ``()`` for one.
        """
        if span.is_empty or not self._tokens:
            return ()

        # Tokens are non-overlapping and ordered, so the first candidate is the
        # last token starting at or before the query start; anything earlier
        # ends before it. Walk forward while tokens still start before the
        # query ends.
        first = bisect_right(self._starts, span.start) - 1
        if first < 0:
            first = 0

        # Walk by index rather than iterating a slice: `self._tokens[first:]`
        # would copy the whole tail on every call, making this O(n) instead of
        # O(log n + k) -- invisible on small documents and crippling on large
        # ones, where grounding calls this once per candidate.
        result: list[Token] = []
        total = len(self._tokens)
        position = first
        while position < total:
            token = self._tokens[position]
            if token.span.start >= span.end:
                break
            if token.span.intersects(span):
                result.append(token)
            position += 1
        return tuple(result)

    def token_at(self, position: int) -> Token | None:
        """The token covering ``position``, or ``None`` if it falls in a gap."""
        if not self._tokens:
            return None
        index = bisect_right(self._starts, position) - 1
        if index < 0:
            return None
        token = self._tokens[index]
        return token if token.span.contains(position) else None
