"""The transport retry loop, once, for every service-backed parser.

Extracted from the Azure adapter when a second service-backed parser arrived.
The reasoning is the one ``docdoc.extraction.retry`` already states for model
adapters: transport policy belongs to the layer, because two adapters carrying
their own copies drift, and "at most N attempts, bounded by a deadline" stops
being a property of docdoc and becomes a property of whichever parser you
happened to pick.

The extraction layer has its own loop rather than sharing this one. They look
alike but they are not the same policy — that one raises ``ModelProviderError``
and treats a service-requested wait as exact, this one raises ``ProviderError``
and treats it as a floor (see ``_sleep_before_retry``). Merging them would mean
one call site silently getting the other's semantics, and the layering forbids
ingest importing extraction anyway.

Three rules, and the third is the one that gets forgotten:

1. Only transient failures are retried. A rejected credential or an unsupported
   document fails on the first attempt, because trying again cannot change the
   answer and doing so would just spend the deadline (ING-21).
2. A service-requested wait is honoured in preference to our own backoff.
3. **The deadline overrides both.** A service asking for a wait longer than the
   remaining budget must fail on the deadline rather than sleep past it.
"""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING, TypeVar

from docdoc.ingest.errors import ProviderError

if TYPE_CHECKING:
    from collections.abc import Callable

    from docdoc.ingest.options import Deadline, TransportSettings
    from docdoc.ingest.source import SourceFile

__all__ = ["analyze_with_retries"]

T = TypeVar("T")


def analyze_with_retries(
    analyze: Callable[[SourceFile, TransportSettings, Deadline], T],
    *,
    source: SourceFile,
    transport: TransportSettings,
    parser_id: str,
    sleep: Callable[[float], None] | None = None,
) -> T:
    """Run ``analyze``, retrying transient provider failures.

    ``sleep`` is injectable so a test can assert the *policy* -- how long it
    would have waited, and that it refused to wait past the deadline -- without
    spending that time.

    It defaults to ``None`` rather than to ``time.sleep`` directly: a default
    argument is bound once, at definition, so naming the function there would
    capture the original and make ``monkeypatch.setattr`` on ``time.sleep``
    silently ineffective. Resolving it per call keeps both injection styles
    working.
    """
    sleep = sleep or time.sleep
    deadline = transport.start()
    last: ProviderError | None = None

    for attempt in range(1, transport.max_attempts + 1):
        if deadline.expired:
            raise ProviderError(
                "the overall deadline expired before the parse completed",
                reason="deadline",
                parser_id=parser_id,
                blob_id=source.blob_id,
                attempts=attempt - 1,
            ) from last

        try:
            return analyze(source, transport, deadline)
        except ProviderError as error:
            error.attempts = attempt
            if not error.transient or attempt == transport.max_attempts:
                raise
            last = error
            if not _sleep_before_retry(attempt, transport, deadline, error, sleep):
                raise ProviderError(
                    "the overall deadline left no room for another attempt",
                    reason="deadline",
                    parser_id=parser_id,
                    blob_id=source.blob_id,
                    attempts=attempt,
                ) from error

    raise ProviderError(  # pragma: no cover - loop always returns or raises
        "exhausted every attempt",
        reason="service",
        parser_id=parser_id,
        blob_id=source.blob_id,
        attempts=transport.max_attempts,
    )


def _sleep_before_retry(
    attempt: int,
    transport: TransportSettings,
    deadline: Deadline,
    error: ProviderError,
    sleep: Callable[[float], None],
) -> bool:
    """Wait before the next attempt. False if the deadline forbids it.

    A service-supplied interval is a **floor**, not a suggestion. Jitter may
    extend it and must never shorten it: coming back early to a service that
    has just rate-limited you is how the next 429 is earned, and FR-038 says
    *honour* the interval, which 17 seconds is not when 30 were asked for.

    docdoc's own backoff is jittered in both directions, which is the usual
    defence against a fleet of clients retrying in lockstep. That reasoning
    does not transfer to an interval the server chose.

    A service that asks for longer than the budget allows does not get it:
    the parse fails on the deadline rather than sleeping past it.
    """
    requested = error.retry_after_s
    if requested is not None:
        wait = requested * (1.0 + random.random() * 0.25) if transport.jitter else requested
    else:
        wait = transport.backoff_for(attempt)
        if transport.jitter:
            wait *= 0.5 + random.random()

    if not deadline.allows(wait):
        return False
    sleep(wait)
    return True
