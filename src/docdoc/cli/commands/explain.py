"""``docdoc explain ARTIFACT_ID`` — why an identity is the value it is.

ADR-0003 accepted unreadable cache keys on one explicit condition: that a tool
would explain them. This is that tool, and without it the first cache-correctness
incident is unarguable in both directions — nobody can show the reuse was right,
and nobody can show it was wrong.

**It explains identities, not documents.** The output names the stage, the input
identity, the processor and its version, and the *names* of the inputs folded
into the options hash. It carries no payload, no extracted value, no prompt body,
and no credential (FR-025). An explanation that quoted the document would be a
second copy of the document in whatever log the explanation was pasted into.

**A derivation is read, never reconstructed.** It comes from the record a write
left behind, so an identity produced by a run with no store configured has none —
and the honest answer is to say so rather than to recompute what the inputs
*would* have been (FR-023). A reconstruction would be a guess wearing the costume
of a record.

**The folded-input names come from the pipeline, not from the store.** The store
sits directly above the kernel and does not know what a stage folds; the pipeline
does, and the CLI is above both, so this is the layer where the two can be joined
without anybody importing upward (Principle X).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docdoc.cli.render import Rendering

if TYPE_CHECKING:
    import argparse

    from docdoc.artifacts.derivation import DerivationRecord
    from docdoc.cli.config import Settings

__all__ = ["run"]

#: Not an error. An identity with no record is a normal outcome — it is what
#: every identity looks like when the store is off, which is the default.
EXIT_NO_RECORD = 0


def run(args: argparse.Namespace, settings: Settings) -> Rendering:
    """Explain one identity, and with ``--chain`` walk it back to the blob."""
    from docdoc.artifacts.derivation import derivation_chain, derivation_of

    if not settings.has_store:
        return _nothing_to_read(
            args.artifact_id,
            reason="no_store",
            message=(
                "no store is configured, so no derivation was ever recorded. A "
                "derivation is read from the record a write left behind, not "
                "reconstructed. Pass --store DIR or set DOCDOC_STORE_ROOT."
            ),
        )

    store = settings.store()

    if args.chain:
        records = list(derivation_chain(store, args.artifact_id))
    else:
        one = derivation_of(store, args.artifact_id)
        records = [] if one is None else [one]

    if not records:
        return _nothing_to_read(
            args.artifact_id,
            reason="not_in_store",
            message=(
                f"{args.artifact_id} is not in this store, so there is no record "
                "of how it was derived. It was produced elsewhere, produced with "
                "no store, or cleared."
            ),
        )

    links = [_explain(record) for record in records]
    data: dict[str, Any] = {
        "artifact_id": args.artifact_id,
        "derivation": links[0],
        "chain": links if args.chain else None,
        # The end of the walk. `None` here means the chain reached a stage with
        # no input — the parse — and therefore the source blob (FR-024).
        "source_blob_id": links[-1]["input_artifact_id"] if args.chain else None,
    }
    return Rendering(code=0, data=data, lines=_lines(links, chained=bool(args.chain)))


def _explain(record: DerivationRecord) -> dict[str, Any]:
    """One link, with the folded-input names the store could not know.

    ``derivation_of`` leaves ``folded_inputs`` empty because ``docdoc.artifacts``
    sits below ``docdoc.pipeline`` and cannot ask it what a stage folds. Filling
    it here rather than teaching the store about stages keeps the store generic
    over what it holds, which is what lets it sit directly above the kernel.
    """
    from docdoc.pipeline.stages import folded_inputs_for

    link = record.model_dump(mode="json")
    link["folded_inputs"] = list(record.folded_inputs or folded_inputs_for(record.stage))
    return link


def _nothing_to_read(artifact_id: str, *, reason: str, message: str) -> Rendering:
    """Say so plainly, and do not guess (FR-023).

    Exit zero: an unexplainable identity is not a failure of this command. The
    command was asked a question and answered it correctly.
    """
    return Rendering(
        code=EXIT_NO_RECORD,
        data={"artifact_id": artifact_id, "derivation": None, "reason": reason},
        lines=[f"docdoc: {message}"],
    )


def _lines(links: list[dict[str, Any]], *, chained: bool) -> list[str]:
    lines: list[str] = []
    for index, link in enumerate(links):
        if index:
            lines.append("")
        folded = link.get("folded_inputs") or ()
        lines.extend(
            [
                f"artifact   {link['artifact_id']}",
                f"stage      {link['stage']}",
                f"processor  {link['processor_id']}@{link['processor_version']}",
                f"options    {link['options_hash']}",
                f"folded     {', '.join(folded) if folded else '-'}",
                f"input      {link['input_artifact_id'] or '(source blob)'}",
            ]
        )

    if chained and links:
        lines.append("")
        end = links[-1]["input_artifact_id"]
        lines.append(f"chain ends at {end}" if end else "chain ends at the source blob")
    return lines
