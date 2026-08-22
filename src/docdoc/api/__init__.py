"""The HTTP interface, behind the ``docdoc[api]`` extra.

Single node, synchronous, no queue, no worker, no database. A job is identified
by the run's terminal artifact id -- ADR-0003's ``processing_id`` -- and
``GET /v1/jobs/{id}`` is a lookup in :mod:`docdoc.artifacts`.

That is not a simplification of an asynchronous design; it is what the identity
model permits. A job id that *is* the terminal artifact id cannot be issued
before the run, because that id is not knowable until the stages feeding it have
run. Running inside the request means the id exists by the time there is
anything to hand back, and a run that fails produces no terminal artifact and so
no job -- it produces a typed error, in the same response.

**What it must never import:** :mod:`docdoc.cli`. See that module's note.

**What must never leave it:** a provider's error text, which may quote the
document it choked on. Errors carry docdoc's own message and the stage at fault.

FastAPI lives here and nowhere else. Principle X names it among the things the
domain model must stay free of, and the forbidden-imports contracts enforce that
for every deterministic layer below.
"""

from __future__ import annotations

__all__: list[str] = []
