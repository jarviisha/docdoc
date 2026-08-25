"""``docdoc store clear [--stage STAGE]`` — all of it, or one stage.

Two subsets and no query language (FR-019). That restraint is the design: a
store you can query is a store somebody will write a retention policy against,
and retention is explicitly out of scope for this milestone.

**Clearing one stage is the useful half.** It makes a suspect result reproducible
from scratch without discarding the expensive parses, and it discards nothing
downstream — downstream artifacts stay addressable and their inputs have not
moved, because clearing is a deletion and not an invalidation. Nothing is ever
marked stale in this store; a new identity is how invalidation happens.

**This is the supported recovery path from a failed integrity check.** An
artifact that fails ``content_id`` verification is cleared and recomputed
deliberately, by a person, rather than silently overwritten by the run that found
the fault — which would destroy the only evidence that anything was wrong.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docdoc.cli.render import Rendering

if TYPE_CHECKING:
    import argparse

    from docdoc.cli.config import Settings

__all__ = ["run"]

#: Nothing to clear is not a failure. Refusing with an error would make
#: `docdoc store clear` unusable in a teardown script, which is most of what it
#: is for.
EXIT_NO_STORE = 0


def run(args: argparse.Namespace, settings: Settings) -> Rendering:
    """Clear the store, or one stage of it."""
    # Validated first, and deliberately before the store check. An argument is
    # well-formed or it is not, and that judgement owes nothing to whether a
    # store happens to be configured. With the order reversed, `--stage extrat
    # --no-store` exited 0 reporting only that there was nothing to clear, so the
    # typo went unmentioned — and the reader most likely to have mistyped a stage
    # is the one who also has not set the store yet.
    stage = _stage(args.stage)

    if not settings.has_store:
        message = (
            "no store is configured, so there is nothing to clear. "
            "Pass --store DIR or set DOCDOC_STORE_ROOT."
        )
        return Rendering(
            code=EXIT_NO_STORE,
            data={"cleared": 0, "stage": stage, "reason": "no_store"},
            lines=[f"docdoc: {message}"],
        )

    removed = settings.store().clear(stage=stage)

    scope = f"stage {stage}" if stage else "every stage"
    return Rendering(
        code=0,
        data={"cleared": removed, "stage": stage, "root": str(settings.store_root)},
        lines=[f"cleared {removed} artifact(s) from {scope} in {settings.store_root}"],
    )


def _stage(name: str | None) -> str | None:
    """Validate the stage name against the four that exist.

    Checked here rather than passed through, because ``clear(stage="extarct")``
    removes nothing and reports success — a typo that reads as a completed
    teardown is exactly the kind of silence that makes a later cache incident
    inexplicable.

    Called before the store check for the same reason, which is a correction: the
    argument is judged on its own terms, so the answer to "did I spell that
    right?" does not depend on whether a store was configured.
    """
    if name is None:
        return None

    from docdoc.pipeline.stages import Stage

    valid = tuple(item.value for item in Stage)
    if name not in valid:
        # Raised, not returned: this is a bad invocation, and `main` maps it to
        # exit 64 along with every other one.
        raise ValueError(f"unknown stage {name!r}; expected one of {', '.join(valid)}")
    return name
