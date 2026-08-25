"""The four stages as one identified, versioned processor.

parse -> extract -> ground -> validate. Until this layer existed that sequence
was written out inside one private function of :mod:`docdoc.recording`, a
package that exists to serve evaluation -- so the order in which docdoc
processes a document lived in the module least likely to be read by someone
asking what the order is.

Principle XI wants stages explicit, identified, and versioned, so that a
queue-based or DAG executor can arrive later without touching a domain type.
That is what this layer is: a function from inputs to one ``PipelineResult``,
with each stage's processor identity and version recorded, and the reuse
decision made against :mod:`docdoc.artifacts`.

**It sequences stages; it does not reimplement them.** Every rule about what a
stage *means* stays in that stage's layer. A behaviour reachable only through the
pipeline is a bug.

**What it must never import:** ``docdoc.evaluation``, ``docdoc.recording``,
``docdoc.cli``, ``docdoc.api``, any HTTP framework, or any provider SDK. It
drives the stages below it and is driven by the interfaces above it.

**It is not a DAG engine** and must not become one: no stage graph, no
conditional stage, no user-supplied stage. The MVP has four stages and they are
these four.
"""

from __future__ import annotations

from docdoc.pipeline import observe
from docdoc.pipeline.errors import PipelineError
from docdoc.pipeline.result import (
    PipelineResult,
    RunProvenance,
    StageOutcome,
    StageStatus,
)
from docdoc.pipeline.runner import run
from docdoc.pipeline.stages import (
    PIPELINE_ID,
    PIPELINE_VERSION,
    STAGE_SPECS,
    Stage,
    StageSpec,
)

__all__ = [
    "PIPELINE_ID",
    "PIPELINE_VERSION",
    "STAGE_SPECS",
    "PipelineError",
    "PipelineResult",
    "RunProvenance",
    "Stage",
    "StageOutcome",
    "StageSpec",
    "StageStatus",
    "observe",
    "run",
]
