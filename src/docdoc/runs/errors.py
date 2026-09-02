"""Typed, provider-neutral errors for the run layer (FR-074).

The constitution's error model names eleven errors and none of them describes a
run: they describe documents, parsers, schemas, and stages — the things that go
wrong *inside* a run. This layer fails in ways that have nothing to do with a
document, and reusing `PipelineError` for "your database is unreachable" would
tell an operator to look at the wrong thing.

**No error here names a driver.** `psycopg.OperationalError` is caught at the
boundary and becomes `RunStateUnavailableError`, for the same reason
`extraction/adapters/gemini.py` maps every provider exception: an error whose
type changes when a dependency releases is not an error a caller can handle.
"""

from __future__ import annotations

__all__ = [
    "RunAbandonedError",
    "RunError",
    "RunNotCancellableError",
    "RunNotFoundError",
    "RunStateUnavailableError",
]


class RunError(Exception):
    """Base for everything this layer raises."""


class RunStateUnavailableError(RunError):
    """The run store could not be reached.

    Raised on submission rather than swallowed, because accepting a run that
    cannot be recorded is accepting work that will never be done and never be
    reported — the silent failure FR-057 exists to prevent. The API turns this
    into a retryable 503.

    Deliberately *not* raised for an unreachable **artifact** store: that one
    degrades to running without reuse and never fails a run, which is ADR-0010
    §4's existing rule and a different situation. One is "I cannot remember what
    you asked me"; the other is "I cannot remember what I already knew".
    """


class RunNotFoundError(RunError):
    """No run under this identity is visible to this tenant.

    The message must not distinguish "does not exist" from "belongs to someone
    else" (FR-066). Callers that need an HTTP status map this to 404 in both
    cases, and the body is byte-identical — SC-008 asserts it, because a
    different message would be an existence oracle spelled in prose.
    """


class RunNotCancellableError(RunError):
    """Cancellation was requested for a run already in a terminal state.

    Carries the state so the caller can say which one. Refusing rather than
    silently succeeding is FR-031: a succeeded run has a `processing_id` and a
    stored result, and reporting it as cancelled would make a retrievable result
    unreachable through a lie about its history.
    """

    def __init__(self, state: str) -> None:
        super().__init__(f"run is already {state}")
        self.state = state


class RunAbandonedError(RunError):
    """A run reached the attempt limit without completing.

    Recorded as the `error_class` of the terminal row, and it means something
    specific: the run was claimed, executed, and lost its worker, that many
    times. It is **not** the class for a run that failed on configuration — a
    withdrawn schema fails once and terminally under FR-091, precisely so that
    this word keeps naming only the poison-document case. An operator who reads
    `RunAbandonedError` should go and look at the document.
    """
