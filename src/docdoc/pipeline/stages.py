"""The four stages, named once, with their processors and format versions.

**This module delegates every identity computation and derives none of its own.**
That is the whole design, and it is worth stating because the opposite is the
obvious implementation. Each layer below already computes its own artifact id
from the inputs ADR-0003 names for it — ``document_id_for``,
``extraction_artifact_id_for``, ``grounding_artifact_id_for``,
``validation_artifact_id_for`` — and each already folds an options hash it
documents and tests. Reimplementing that here would put the folded set in two
places, and the moment they disagreed the store would return an artifact for
inputs that had changed. Under-folding is the stale-cache bug ADR-0003 exists to
close; over-folding destroys reuse for nothing. Neither is visible in any output,
so neither would be found by looking at results.

What this module owns is the part no single layer can: the *list* of four, the
processor identity and version of each, and the artifact-format version of what
each stores.

**``ARTIFACT_FORMAT_VERSION`` is not the docdoc release version** (ADR-0010 §3).
Bump one when its stored model's shape changes incompatibly — a field removed,
renamed, or retyped, or a new field whose absence is not answerable from what was
stored. Adding a field whose default is never load-bearing is compatible and
must not move it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from pydantic import BaseModel

__all__ = [
    "PIPELINE_ID",
    "PIPELINE_VERSION",
    "STAGE_SPECS",
    "Stage",
    "StageSpec",
    "artifact_id_of",
    "spec_for",
]

#: The pipeline is itself a processor under ADR-0003, and ``PIPELINE_VERSION`` is
#: folded into the **terminal** artifact and into nothing else. It moves when the
#: sequencing changes in a way that changes a result — a stage added, removed, or
#: reordered — not when a stage's own behaviour changes, which is that stage's
#: version to move.
PIPELINE_ID = "docdoc-pipeline"
PIPELINE_VERSION = "1.0.0"


class Stage(StrEnum):
    """The four stages, in order, and there are exactly four.

    ``docdoc.evaluation`` defines a same-named enum with the same four members
    for recording which stage a document failed at. They are deliberately not
    merged: that one is folded into ``prediction_set_id``, so unifying them would
    move the identity of the committed public tier and invalidate a dataset for a
    tidiness gain.
    """

    PARSE = "parse"
    EXTRACT = "extract"
    GROUND = "ground"
    VALIDATE = "validate"


class StageSpec(NamedTuple):
    """What the store needs to know about one stage."""

    stage: Stage
    #: Stable across versions. The *version* is what moves (ADR-0003).
    processor_id: str
    #: The shape of what this stage stores, per ADR-0010 §3.
    artifact_format_version: int


#: The parse stage's processor id and version are properties of the parser
#: *chosen for a document*, not constants — a PDF routed to the native path and a
#: scan routed to a cloud provider are different processors. So the id recorded
#: here is a placeholder that the run replaces from the document's own provenance,
#: which is where ingest already records it.
_PARSER_PLACEHOLDER = "ingest-parser"

STAGE_SPECS: dict[Stage, StageSpec] = {
    Stage.PARSE: StageSpec(Stage.PARSE, _PARSER_PLACEHOLDER, 1),
    Stage.EXTRACT: StageSpec(Stage.EXTRACT, "schema-extractor", 1),
    Stage.GROUND: StageSpec(Stage.GROUND, "deterministic-grounder", 1),
    Stage.VALIDATE: StageSpec(Stage.VALIDATE, "deterministic-validator", 1),
}


def spec_for(stage: Stage) -> StageSpec:
    """The spec for one stage."""
    return STAGE_SPECS[stage]


def artifact_id_of(stage: Stage, result: BaseModel) -> str:
    """Read a stage's artifact id off the result it produced.

    Read rather than recomputed, deliberately. Every stage already derives its
    own id and carries it, so recomputing here would be a second derivation of a
    value that already exists — and a second derivation is a second thing that
    can be wrong.

    The parse stage names it ``id`` because ADR-0002 named ``document_id`` first
    and the kernel predates the artifact chain; the other three name it
    ``artifact_id``. That inconsistency is real and is absorbed here rather than
    pushed onto every caller.
    """
    if stage is Stage.PARSE:
        return str(result.id)  # type: ignore[attr-defined]
    return str(result.artifact_id)  # type: ignore[attr-defined]
