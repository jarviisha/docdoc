"""Deciding whether a document's native text layer is usable.

This is the decision Principle V calls out: the preferred path is a PDF with a
usable text layer read by a native parser, and the choice must be *explicit and
inspectable* rather than implicit. So it happens before a parser is chosen, by a
single versioned rule, and its output is recorded on every document it routes.

The rule, ``text-layer@1``:

* a page is **text-bearing** when it yields at least ``min_chars_per_page``
  characters after discarding whitespace, control characters, and U+FFFD;
* the document's text layer is **usable** when at least
  ``min_text_bearing_fraction`` of its pages are text-bearing, and at least one
  is.

The default of 100 characters was chosen from the shape of the problem and then
checked against the committed fixtures: text-bearing pages there measure
242-307 characters, page furniture on a scan measures 8. The threshold sits in
the empty middle of that gap rather than near either population.

Changing either default requires a new rule id, so a retune is visible in
results produced before it (FR-010).
"""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from docdoc.ingest.errors import ParserCapabilityError, UnsupportedDocumentError
from docdoc.kernel import PageTextVerdict, TextLayerRecord

if TYPE_CHECKING:
    from docdoc.ingest.source import SourceFile

__all__ = ["TextLayerAssessment", "TextLayerRule", "assess_text_layer", "meaningful_length"]

#: The assessment *is* the record the document carries. There is no second type
#: mirroring the first: an ingest-side class with identical fields would have no
#: present-tense reason to exist (Principle XI), and two copies of a verdict is
#: exactly how the two drift apart.
TextLayerAssessment = TextLayerRecord


def meaningful_length(text: str) -> int:
    """Characters that constitute evidence of a text layer.

    Whitespace is excluded because a page of spaces is not text. Control
    characters and U+FFFD are excluded because they are what a broken decode
    leaves behind, and counting them would let a failed extraction pass as a
    successful one.
    """
    return sum(
        1
        for character in text
        if not character.isspace()
        and character != "�"
        and unicodedata.category(character)[0] != "C"
    )


class TextLayerRule(BaseModel):
    """The rule, its thresholds, and the identity that versions them together."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default="text-layer@1", min_length=1)
    min_chars_per_page: int = Field(default=100, ge=0)
    min_text_bearing_fraction: float = Field(default=0.5, ge=0.0, le=1.0)

    def verdict_for(self, page_texts: tuple[str, ...]) -> TextLayerRecord:
        """Apply the rule to already-extracted page text."""
        pages = tuple(
            PageTextVerdict(
                page_index=index,
                char_count=(count := meaningful_length(text)),
                text_bearing=count >= self.min_chars_per_page,
            )
            for index, text in enumerate(page_texts)
        )
        bearing = sum(1 for page in pages if page.text_bearing)
        usable = bool(pages) and bearing >= len(pages) * self.min_text_bearing_fraction

        return TextLayerRecord(
            rule_id=self.id,
            min_chars_per_page=self.min_chars_per_page,
            min_text_bearing_fraction=self.min_text_bearing_fraction,
            pages=pages,
            text_layer_usable=usable and bearing > 0,
        )

    def as_override(self, record: TextLayerRecord, *, forced_native: bool) -> TextLayerRecord:
        """Re-cast a verdict as one a caller overrode.

        ``text_layer_usable`` becomes the route actually taken and
        ``overridden_verdict`` keeps what the rule said, so the rule's output is
        preserved rather than replaced (FR-012).
        """
        return TextLayerRecord(
            rule_id=record.rule_id,
            min_chars_per_page=record.min_chars_per_page,
            min_text_bearing_fraction=record.min_text_bearing_fraction,
            pages=record.pages,
            text_layer_usable=forced_native,
            overridden=True,
            overridden_verdict=record.text_layer_usable,
            rule_not_run=record.rule_not_run,
        )

    def skipped(self, reason: str, *, overridden: bool = False) -> TextLayerRecord:
        """A record for a rule that could not run.

        The emptiness is explained rather than left to be inferred: a caller
        must be able to tell "no page had text" from "nobody looked" (ING-10).
        """
        return TextLayerRecord(
            rule_id=self.id,
            min_chars_per_page=self.min_chars_per_page,
            min_text_bearing_fraction=self.min_text_bearing_fraction,
            pages=(),
            text_layer_usable=False,
            overridden=overridden,
            rule_not_run=reason,
        )


def assess_text_layer(source: SourceFile, *, rule: TextLayerRule | None = None) -> TextLayerRecord:
    """Decide whether this file's native text layer is usable.

    Deterministic and cheap: it reads page text, never builds an IR, and never
    touches the network. Identical bytes always yield an identical assessment,
    character counts included (FR-010, ING-12).

    Raises:
        ParserCapabilityError: the source is a PDF and no native reader is
            installed. The question cannot be answered, so it is not guessed at.
            A caller in that position asks for a path explicitly instead, which
            skips the assessment (FR-012).
    """
    rule = rule or TextLayerRule()

    if not source.is_pdf:
        # An image has no text layer to assess. Short-circuiting means no bytes
        # are inspected at all (ING-13).
        return TextLayerRecord(
            rule_id=rule.id,
            min_chars_per_page=rule.min_chars_per_page,
            min_text_bearing_fraction=rule.min_text_bearing_fraction,
            pages=(PageTextVerdict(page_index=0, char_count=0, text_bearing=False),),
            text_layer_usable=False,
        )

    try:
        from docdoc.ingest.parsers.pdf_text import page_text_lengths
    except ImportError as error:
        raise ParserCapabilityError(
            "cannot assess the text layer of a PDF without the native reader; "
            "install docdoc[pdf], or ask for a path explicitly with force=",
            required=("text",),
            media_type=source.media_type,
            candidates=(("pdf-text", False, "extra_not_installed"),),
            blob_id=source.blob_id,
        ) from error

    page_texts = page_text_lengths(source)
    if not page_texts:
        # A PDF with no pages is structurally valid and carries nothing. Left to
        # the rule it would come out "text layer not usable", and the caller would
        # be told to find a recognition parser for a document that has no content
        # to recognize. Refusing it here says what is actually wrong.
        raise UnsupportedDocumentError(
            "the PDF contains no pages",
            reason="corrupt",
            blob_id=source.blob_id,
            media_type=source.media_type,
        )
    return rule.verdict_for(page_texts)
