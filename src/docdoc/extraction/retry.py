"""The retry loop, once, for every adapter.

Transport policy belongs to the layer rather than to each adapter: if every
adapter implemented its own, the second one would drift from the first and the
guarantee "at most N attempts, bounded by a deadline" would become a claim about
whichever adapter you happened to be using.

``TransportSettings`` comes from the ingest layer, which already models exactly
the attempt limit, backoff, jitter, per-attempt timeout, and overall deadline this
needs (research.md R9). Reusing it also means FR-027 -- transport cannot change
identity -- holds by construction, because those knobs live in a type the
identity function has no parameter for.

Three rules, and the third is the one that gets forgotten:

1. Only transient failures are retried. A rejected credential, a malformed
   request, and every refusal fail on the first attempt (FR-025).
2. A service-requested wait is honoured in preference to our own backoff.
3. **The deadline overrides both.** A service asking for a wait longer than the
   remaining budget must fail on the deadline rather than sleep past it.
"""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING, TypeVar

from docdoc.extraction.errors import ModelProviderError

if TYPE_CHECKING:
    from collections.abc import Callable

    from docdoc.extraction.adapter import ModelResponse
    from docdoc.ingest.options import TransportSettings

__all__ = ["call_with_retries"]

T = TypeVar("T")

#: Jitter is multiplicative and bounded, so a retried call cannot wait less than
#: half nor more than the full computed backoff. Full jitter down to zero would
#: let a thundering herd re-converge.
_JITTER_FLOOR = 0.5


def call_with_retries(
    call: Callable[[], ModelResponse],
    *,
    transport: TransportSettings,
    adapter_id: str,
    document_id: str | None = None,
    schema_identity: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[ModelResponse, int]:
    """Run ``call``, retrying transient provider failures. Returns (response, attempts).

    ``sleep`` is injectable so the tests can assert the *policy* -- how long it
    would have waited, and that it refused to wait past the deadline -- without
    spending that time. A retry test that actually sleeps is a slow test that gets
    marked skip.
    """
    deadline = transport.start()
    attempts = 0
    last: ModelProviderError | None = None

    while attempts < transport.max_attempts:
        attempts += 1
        remaining = deadline.remaining_s
        if remaining <= 0:
            raise _deadline_error(
                adapter_id, document_id, schema_identity, attempts - 1, last, "before attempt"
            )
        try:
            return call(), attempts
        except ModelProviderError as exc:
            if not exc.transient:
                # Permanent: re-raise with the attempt count corrected, so a caller
                # reading `attempts` is told the truth rather than the default 1.
                raise _with_attempts(exc, attempts) from exc.__cause__
            last = exc
            if attempts >= transport.max_attempts:
                break
            wait = _wait_for(exc, transport, attempts)
            if wait >= deadline.remaining_s:
                raise _deadline_error(
                    adapter_id, document_id, schema_identity, attempts, last, "during backoff"
                ) from exc
            sleep(wait)

    assert last is not None  # the loop only exits here after a transient failure
    raise _with_attempts(last, attempts)


def _wait_for(exc: ModelProviderError, transport: TransportSettings, attempt: int) -> float:
    """How long to wait before the next attempt.

    A service-requested wait wins over our own backoff -- it knows its own load
    better than an exponential curve does -- and jitter is applied only to our
    own, because jittering a wait the service asked for defeats the point of
    asking.
    """
    if exc.retry_after_s is not None:
        return float(exc.retry_after_s)
    backoff = transport.backoff_for(attempt)
    if not transport.jitter:
        return backoff
    return backoff * random.uniform(_JITTER_FLOOR, 1.0)


def _with_attempts(exc: ModelProviderError, attempts: int) -> ModelProviderError:
    return ModelProviderError(
        str(exc),
        reason=exc.reason,
        adapter_id=exc.adapter_id,
        document_id=exc.document_id,
        schema_identity=exc.schema_identity,
        attempts=attempts,
        retry_after_s=exc.retry_after_s,
        refusal_category=exc.refusal_category,
    )


def _deadline_error(
    adapter_id: str,
    document_id: str | None,
    schema_identity: str | None,
    attempts: int,
    last: ModelProviderError | None,
    when: str,
) -> ModelProviderError:
    cause = f" after {last.reason}" if last is not None else ""
    return ModelProviderError(
        f"the overall deadline expired {when}{cause}; {attempts} attempt(s) made. "
        "The deadline bounds the whole extraction and overrides both our backoff and "
        "any wait the service asked for",
        reason="deadline",
        adapter_id=adapter_id,
        document_id=document_id,
        schema_identity=schema_identity,
        attempts=attempts,
    )
