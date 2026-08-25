"""Two output forms, and exactly one place that writes to standard output.

The contract in ``contracts/cli.md`` §2 is two sentences long: with ``--json``,
standard output carries exactly one JSON document and nothing else; diagnostics
go to standard error, always, in both forms (FR-027).

That is enforced structurally rather than by discipline. A command does not
print. It returns a :class:`Rendering` carrying both forms, and :func:`emit` --
the only function in the package that touches ``stdout`` -- writes one of them.
A command *cannot* interleave a banner with the JSON document, because it has
nothing to interleave it with.

**Both forms carry the same facts.** The human form is a projection of the
machine form, not a different report. Where they diverge, the machine form is
authoritative: it is the one a caller parses, and a fact that reaches only the
human form is a fact no script can act on.
"""

from __future__ import annotations

import dataclasses
import json
import re
import sys
from typing import TYPE_CHECKING, Any

from docdoc.extraction.value import ExtractedValue

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from typing import TextIO

    from docdoc.extraction.extract import ExtractionResult
    from docdoc.grounding.result import GroundingResult
    from docdoc.validation.result import ValidationResult

#: ``name[3]`` — a repeating-group entry, as grounding addresses one.
_INDEXED = re.compile(r"(?P<name>[^\[\]]+)\[(?P<index>\d+)\]")

__all__ = [
    "Rendering",
    "emit",
    "field_rows",
    "warn",
]


@dataclasses.dataclass(frozen=True)
class Rendering:
    """One command's output, in both forms, plus the exit code it earned.

    The exit code lives here rather than being raised or returned separately
    because it is part of what the command decided, and a caller reading the
    JSON document and a caller reading ``$?`` must never be told different
    things.
    """

    code: int
    data: dict[str, Any]
    lines: Sequence[str] = ()


def emit(rendering: Rendering, *, as_json: bool, stream: TextIO | None = None) -> int:
    """Write one form to standard output and return the exit code.

    The single write. Nothing else in :mod:`docdoc.cli` may touch ``stdout``,
    which is what makes "exactly one JSON document" true by construction rather
    than by every command remembering.
    """
    stream = sys.stdout if stream is None else stream
    if as_json:
        # `default=str` covers the enums and paths that reach here. It is a
        # deliberate blanket: a command that returns something unserialisable
        # should degrade to a readable string rather than crash after the run
        # has already been paid for.
        stream.write(json.dumps(rendering.data, indent=2, default=str, sort_keys=True))
        stream.write("\n")
    else:
        for line in rendering.lines:
            stream.write(f"{line}\n")
    stream.flush()
    return rendering.code


def warn(message: str) -> None:
    """A diagnostic, on standard error, in both output forms.

    Exists so that no command reaches for ``print`` and lands on ``stdout`` by
    reflex. The name is the whole API: if it is not the result, it is a warning,
    and warnings go to the other stream.
    """
    sys.stderr.write(f"docdoc: {message}\n")


def field_rows(
    extraction: ExtractionResult | None,
    grounding: GroundingResult | None,
    validation: ValidationResult | None,
) -> list[dict[str, Any]]:
    """One row per field: its value, its verdict, its page, and its rectangle.

    This is the Definition of Done in tabular form, and the join it performs is
    the reason the three results are separate models in the first place --
    extraction says *what*, grounding says *where*, validation says *whether*.
    Nothing here recomputes any of the three (FR-030); it reads what each
    already decided and puts them on one line.

    Keyed on grounding's outcomes, because grounding is the layer that enumerates
    every field the schema declared, present or absent. Keying on extraction's
    values would silently drop the fields the model never returned, which are
    exactly the ones a reader is looking for.
    """
    if grounding is None:
        return []

    findings = _findings_by_field(validation)

    rows: list[dict[str, Any]] = []
    for path, outcome in sorted(grounding.outcomes.items()):
        value = _value_at(extraction, path)
        located = _location_of(outcome)

        rows.append(
            {
                "field": path,
                "value": None if value is None else value.value,
                "present": False if value is None else value.present,
                "grounding": outcome.status.value,
                "grounding_score": outcome.score,
                # Untrusted and labelled as such wherever it is exposed
                # (ADR-0004). Routing reads `grounding`, never this.
                "model_confidence": None if value is None else value.model_confidence,
                "verdict": _verdict_for(path, findings),
                "page": located["page"],
                "bbox": located["bbox"],
                "findings": findings.get(path, []),
            }
        )
    return rows


def _value_at(extraction: ExtractionResult | None, path: str) -> Any:
    """Resolve a grounding field path against the extraction's value tree.

    Grounding enumerates repeating groups with indices — ``line_items[0].amount``
    — while ``ExtractionResult.value_at`` splits on dots only and has no notion of
    an index. Using it here silently returned ``None`` for every value inside a
    repeating group, which is most of the interesting ones on an invoice: the
    table read as entirely empty while grounding reported it as exactly matched.

    Walking the indices here rather than teaching ``value_at`` about them is
    deliberate. ``value_at`` is a dotted-path convenience with a documented
    contract and its own tests; this is the join between two layers' addressing
    conventions, and the join belongs to the thing doing the joining.
    """
    if extraction is None:
        return None

    node: Any = extraction.values
    for name, index in _steps(path):
        if not isinstance(node, dict) or name not in node:
            return None
        node = node[name]
        if index is not None:
            if not isinstance(node, (list, tuple)) or index >= len(node):
                return None
            node = node[index]

    return node if isinstance(node, ExtractedValue) else None


def _steps(path: str) -> list[tuple[str, int | None]]:
    """``"line_items[0].amount"`` into ``[("line_items", 0), ("amount", None)]``."""
    steps: list[tuple[str, int | None]] = []
    for part in path.split("."):
        match = _INDEXED.fullmatch(part)
        if match is None:
            steps.append((part, None))
        else:
            steps.append((match.group("name"), int(match.group("index"))))
    return steps


def _location_of(outcome: Any) -> dict[str, Any]:
    """The first page and rectangle a grounded value was read from.

    *First*, not *only*: a value spanning a page break has several, and they are
    all on the outcome. This picks the one to print on a single line, and the
    machine form keeps `pages` intact for anyone who needs the rest.
    """
    page = outcome.pages[0] if outcome.pages else None
    bbox = None
    if outcome.geometry:
        first = outcome.geometry[0]
        page = first.page_index if page is None else page
        bbox = [
            round(first.bbox.x0, 6),
            round(first.bbox.y0, 6),
            round(first.bbox.x1, 6),
            round(first.bbox.y1, 6),
        ]
    return {"page": page, "bbox": bbox}


def _findings_by_field(validation: ValidationResult | None) -> dict[str, list[dict[str, Any]]]:
    if validation is None:
        return {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for finding in validation.findings:
        grouped.setdefault(finding.field_path, []).append(
            {
                "check_id": finding.check_id,
                "reason": finding.reason.value,
                "severity": finding.severity.value,
                "expected": finding.expected,
                "actual": finding.actual,
                "message": finding.message,
            }
        )
    return grouped


def _verdict_for(path: str, findings: dict[str, list[dict[str, Any]]]) -> str:
    """One field's verdict, derived from its findings and nothing else.

    Not a fourth verdict vocabulary: `error`, `warning`, and `info` are the
    severities the validation layer already assigns, and `pass` means it found
    nothing to say. Inventing a per-field verdict here would be validation logic
    in the CLI, which FR-030 forbids.
    """
    at_field = findings.get(path)
    if not at_field:
        return "pass"
    severities = {finding["severity"] for finding in at_field}
    for severity in ("error", "warning", "info"):
        if severity in severities:
            return severity
    return "pass"


def render_rows(rows: Iterable[dict[str, Any]]) -> list[str]:
    """The human form of :func:`field_rows`, aligned into columns."""
    rows = list(rows)
    if not rows:
        return ["(no fields)"]

    width = max(len(str(row["field"])) for row in rows)
    lines = [f"{'FIELD'.ljust(width)}  {'VERDICT':<8} {'GROUNDING':<11} {'PAGE':>4}  VALUE"]
    for row in rows:
        page = "-" if row["page"] is None else str(row["page"])
        value = "-" if row["value"] is None else str(row["value"])
        lines.append(
            f"{str(row['field']).ljust(width)}  {row['verdict']:<8} "
            f"{row['grounding']:<11} {page:>4}  {value}"
        )
    return lines
