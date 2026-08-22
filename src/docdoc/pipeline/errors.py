"""Errors raised by the pipeline layer.

``PipelineError`` is the second of the two names the constitution's error model
lists that no code defined until Milestone 7.

It is deliberately narrow. A stage failure is **not** a ``PipelineError``: it is
the stage's own typed error, recorded on the result and re-raised or reported as
that error, because wrapping a ``GroundingError`` in a pipeline-shaped name would
send whoever reads it to the wrong code. ``PipelineError`` is for failures of the
*sequencing itself* — a stage asked to run without the result its predecessor
was supposed to produce, or a run configured in a way no stage could satisfy.
"""

from __future__ import annotations

from docdoc.kernel.errors import DocdocError

__all__ = ["PipelineError"]


class PipelineError(DocdocError):
    """The run could not be sequenced, independently of any stage's own failure."""

    def __init__(
        self,
        message: str,
        *,
        stage: str | None = None,
        reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.reason = reason
