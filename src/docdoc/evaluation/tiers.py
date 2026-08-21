"""Tiers and provenance — the shape ADR-0009 decided.

A public repository cannot ship real customer invoices, and an evaluation only
maintainers can run is not a product feature. ADR-0009 resolved that with two
tiers rather than one, and this module holds the vocabulary.

Deliberately importing nothing else from this package: :mod:`.golden` and
:mod:`.redact` both need ``Tier``, and both would otherwise have to import each
other.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

__all__ = ["DocumentOrigin", "OriginKind", "Tier"]


class Tier(StrEnum):
    """Which half of the dataset a document belongs to (EVA-1).

    The tier decides three things that would otherwise be left to a caller's
    diligence: whether the document's labels and predictions live in the
    repository, whether a run missing them is *partial* rather than merely
    smaller, and whether the report carries values or hashes.
    """

    #: Vendored into the repository. Evaluable with no credentials and no
    #: network, and **sufficient on its own for a complete report**. That last
    #: clause is the load-bearing one: a dataset whose public half is a demo,
    #: with the real measurement happening privately, satisfies the letter of
    #: ADR-0009 and none of its purpose.
    PUBLIC = "public"

    #: Never committed; referenced by content hash. Its labels and predictions
    #: are supplied by whoever holds the corpus, and a run without them is a
    #: partial report that names what it skipped.
    RESTRICTED = "restricted"


class OriginKind(StrEnum):
    """Where a golden-set document came from."""

    SYNTHETIC = "synthetic"
    PUBLIC_DOMAIN = "public_domain"
    LICENSED = "licensed"
    RESTRICTED = "restricted"


class DocumentOrigin(BaseModel):
    """Where a document came from and on what basis docdoc may use it (EVA-2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: OriginKind

    #: The basis on which docdoc may use this document. Non-empty, always
    #: (FR-011). This is not a formality and not a licence identifier lookup:
    #: the check is that a human wrote something they are willing to stand
    #: behind. "Found on the internet" is not a basis, and a document whose
    #: provenance cannot be stated is not admitted.
    basis: str

    source: str | None = None

    #: Required when ``kind is SYNTHETIC``. A synthetic document whose generator
    #: is unknown cannot be regenerated, which makes its labels unverifiable --
    #: nobody can check the truth against the thing that produced the document
    #: the truth describes.
    generator_id: str | None = None
    generator_version: str | None = None

    @model_validator(mode="after")
    def _basis_is_stated(self) -> DocumentOrigin:
        if not self.basis.strip():
            raise ValueError(
                "origin.basis must state the basis on which docdoc may use this "
                "document; a document whose provenance cannot be stated MUST NOT "
                "be admitted (FR-011)"
            )
        return self

    @model_validator(mode="after")
    def _synthetic_names_its_generator(self) -> DocumentOrigin:
        if self.kind is OriginKind.SYNTHETIC and not (self.generator_id and self.generator_version):
            raise ValueError(
                "a synthetic document must record generator_id and "
                "generator_version, so a label is traceable to the thing that "
                "made the document it describes (FR-010)"
            )
        return self
