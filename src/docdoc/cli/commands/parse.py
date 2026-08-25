"""``docdoc parse FILE`` — route, parse, and say what came back.

The smallest useful thing docdoc does, and the one that answers the question
Principle V exists to keep inspectable: *did this file go down the native text
path or the recognition path, and on what evidence?* The text-layer verdict is
printed on every run, including the ones where it did not decide the outcome,
because a verdict that only appears when it is obeyed cannot be audited.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from docdoc.cli.render import Rendering

if TYPE_CHECKING:
    import argparse

    from docdoc.cli.config import Settings

__all__ = ["run"]


def run(args: argparse.Namespace, settings: Settings) -> Rendering:
    """Parse one file and report the document it produced."""
    from docdoc.ingest import parse

    document = parse(Path(args.file).read_bytes(), limits=settings.limits())
    provenance = document.provenance

    data: dict[str, Any] = {
        "document_id": document.id,
        "blob_id": document.source.blob_id,
        "parser": {
            "id": provenance.parser_id,
            "version": provenance.parser_version,
            "options_hash": provenance.options_hash,
            "reading_order": provenance.reading_order,
        },
        "text_layer_used": provenance.text_layer_used,
        "text_layer": _verdict(provenance),
        "pages": len(document.pages),
        "tokens": len(document.tokens),
        "blocks": len(document.blocks),
        "characters": len(document.text),
    }

    lines = [
        f"document   {document.id}",
        f"blob       {document.source.blob_id}",
        f"parser     {provenance.parser_id}@{provenance.parser_version}",
        f"text layer {'native' if provenance.text_layer_used else 'recognition'}"
        f"{_verdict_suffix(provenance)}",
        f"pages      {len(document.pages)}",
        f"tokens     {len(document.tokens)}",
        f"characters {len(document.text)}",
    ]
    return Rendering(code=0, data=data, lines=lines)


def _verdict(provenance: Any) -> dict[str, Any] | None:
    """The text-layer record, flattened, or ``None`` where the rule never ran.

    ``None`` and "the rule ran and said no" are different facts and are kept
    that way: the first means no native reader was installed, and a reader who
    cannot tell them apart will go looking for a bad document.
    """
    record = provenance.text_layer
    if record is None:
        return None
    return {
        "rule_id": record.rule_id,
        "usable": record.text_layer_usable,
        "pages_with_text": _text_bearing(record),
        "pages_total": len(record.pages),
        # An override keeps the verdict it did not act on, which is the whole
        # reason the record and the summary bool are separate fields.
        "overridden": record.overridden,
        "overridden_verdict": record.overridden_verdict,
        "rule_not_run": record.rule_not_run,
    }


def _text_bearing(record: Any) -> int:
    return sum(1 for page in record.pages if page.text_bearing)


def _verdict_suffix(provenance: Any) -> str:
    record = provenance.text_layer
    if record is None:
        return "  (no text-layer rule ran)"
    if record.rule_not_run:
        return f"  (rule did not run: {record.rule_not_run})"
    suffix = f"  ({record.rule_id}: {_text_bearing(record)}/{len(record.pages)} pages with text)"
    if record.overridden:
        suffix += f", overridden — the rule said {record.overridden_verdict}"
    return suffix
