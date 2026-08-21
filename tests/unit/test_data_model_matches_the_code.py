"""T099 — the data model's field tables describe the models that exist.

`tests/unit/test_documented_api_references_resolve.py` checks the documents that
carry ```python blocks: it resolves imports and hand-listed attribute chains. It
cannot check `data-model.md`, which carries **no python blocks at all** — it
specifies the models as markdown tables, which is exactly the form that test's
regex does not see.

That gap let a real drift through. `data-model.md` listed `group_outcomes` and
`validation_verdicts` as fields of `EvaluationReport`; both lived one level down
on `report.metrics`, so a consumer following the data model wrote
`report.group_outcomes` and got `AttributeError`. Nothing failed, because nothing
was reading the tables.

So the tables are parsed and checked here. The rule is **one-directional**: every
field the data model names must exist on the model. A field the code has and the
document does not is *not* a failure — `EvaluationReport.dataset_size` arrived
with FR-009 after the table was written, and requiring the document to be
exhaustive would turn every additive change into a documentation edit before the
build goes green, which is how a check like this gets deleted.

**A property counts.** The storage is allowed to be normalized where duplicating
it would cost something real: outcomes live in one flat, totally ordered tuple and
`EvaluationReport.group_outcomes` delegates to `metrics`, because storing each
document's outcomes twice would double the bytes FR-043 requires to be identical.
What the data model describes is the surface a reader gets, not the layout.
"""

from __future__ import annotations

import importlib
import pathlib
import re

import pytest

DATA_MODEL = pathlib.Path("specs/006-golden-set-evaluation/data-model.md")

#: `### EVA-9 · `DocumentPrediction`` -> ("EVA-9", "DocumentPrediction").
#:
#: The trailing `[^`]*` is load-bearing: EVA-30's heading is
#: ``EvaluationError(DocdocError)``, and an identifier-only pattern matched it
#: nowhere at all — so the entity vanished from the parse rather than failing
#: loudly. The mapping guard below is what surfaced that, which is the argument
#: for having it.
_HEADING = re.compile(r"^### (EVA-\d+[a-z]?) · `([A-Za-z_][A-Za-z0-9_]*)[^`]*`", re.M)

#: A table row's first cell, which may name more than one field:
#: `| `document_id`, `group_path` | `str` |`
_ROW = re.compile(r"^\|\s*(`[^|]+`)\s*\|", re.M)
_NAME = re.compile(r"`([a-z_][a-z0-9_]*)`")

#: Where each documented entity lives. Hand-maintained, and kept honest by
#: `test_every_documented_entity_is_mapped_or_skipped` below — a new EVA entry
#: with a field table fails until somebody says which model it is, or says why it
#: is not one.
MODELS = {
    "DocumentOrigin": "docdoc.evaluation:DocumentOrigin",
    "GoldenDocument": "docdoc.evaluation:GoldenDocument",
    "EntryKeySpec": "docdoc.evaluation:EntryKeySpec",
    "GoldenSet": "docdoc.evaluation:GoldenSet",
    "ExpectedLocation": "docdoc.evaluation:ExpectedLocation",
    "Label": "docdoc.evaluation:Label",
    "DocumentPrediction": "docdoc.evaluation:DocumentPrediction",
    "PredictionSet": "docdoc.evaluation:PredictionSet",
    "EntryAlignment": "docdoc.evaluation:EntryAlignment",
    "FieldOutcome": "docdoc.evaluation:FieldOutcome",
    "GroupOutcome": "docdoc.evaluation:GroupOutcome",
    "MetricValue": "docdoc.evaluation:MetricValue",
    "EvaluationOptions": "docdoc.evaluation:EvaluationOptions",
    "EvaluationReport": "docdoc.evaluation:EvaluationReport",
    "PartialDeclaration": "docdoc.evaluation:PartialDeclaration",
    "Correction": "docdoc.evaluation:Correction",
}

#: Documented entities that are deliberately not pydantic models, with the reason.
#: Naming them is what stops this map becoming a place to park a real drift.
NOT_MODELS = {
    "Tier": "a StrEnum; its members are checked by tests/unit/test_redaction.py",
    "golden_set_id": "a str, not a type — its formula is EVA-6 and identity.py",
    "FieldOutcomeKind": "a StrEnum; closure is asserted in test_location_agreement.py",
    "Comparator": "a concept, realised as the comparators.py registry",
    "LocationAgreement": "a StrEnum; its three values are asserted directly",
    "MetricDefinition": "realised as definitions.py's METRICS table of MetricSpec",
    "DocumentScore": "prose rather than a table — see the note in this module",
    "DatasetMetrics": "prose rather than a table",
    "EvaluationProvenance": "prose rather than a table; the 17 fields are "
    "enumerated in test_evaluation_refusals.py",
    "report_id": "a str, not a type — its formula is EVA-24 and identity.py",
    "Comparison": "prose rather than a table",
    "EvaluationError": "an exception; its attributes are asserted in the refusal tests",
}


def _documented() -> dict[str, tuple[str, tuple[str, ...]]]:
    """``{entity: (eva_id, fields)}`` for every heading that is followed by a table.

    A heading with no table before the next heading contributes no fields, which
    is how the prose entries (EVA-19, EVA-20, EVA-21) fall out naturally rather
    than needing to be special-cased.
    """
    text = DATA_MODEL.read_text(encoding="utf-8")
    headings = list(_HEADING.finditer(text))

    found: dict[str, tuple[str, tuple[str, ...]]] = {}
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        section = text[heading.start() : end]

        fields: list[str] = []
        for row in _ROW.findall(section):
            if row.strip("`") in {"Field", "Value"}:
                continue
            fields.extend(_NAME.findall(row))
        found[heading.group(2)] = (heading.group(1), tuple(dict.fromkeys(fields)))
    return found


def _attributes(target: str) -> set[str]:
    """Every name a reader can reach on the model: fields, properties, methods."""
    module_name, type_name = target.split(":")
    obj = getattr(importlib.import_module(module_name), type_name)
    return set(getattr(obj, "model_fields", {})) | {
        name for name in dir(obj) if not name.startswith("_")
    }


def test_the_parser_finds_the_tables_it_is_meant_to() -> None:
    """A checker that parses nothing passes for the wrong reason."""
    documented = _documented()

    assert len(documented) >= 25, f"expected the data model's entities, found {len(documented)}"
    with_fields = {name for name, (_eva, fields) in documented.items() if fields}
    assert len(with_fields) >= 12, f"expected field tables, found {sorted(with_fields)}"
    assert documented["EvaluationReport"][1], "the report's table must be parsed"


@pytest.mark.parametrize("entity", sorted(MODELS))
def test_every_documented_field_exists_on_the_model(entity: str) -> None:
    """The assertion the first convergence pass had no way to make."""
    eva, fields = _documented()[entity]
    available = _attributes(MODELS[entity])

    missing = [field for field in fields if field not in available]
    assert not missing, (
        f"{eva} documents {entity} as carrying {missing}, and the model does not. "
        "A consumer following data-model.md reaches for those and gets an "
        "AttributeError. Either add them — a delegating property counts, where "
        "duplicating the storage would cost something — or the document is "
        "describing a model that no longer exists"
    )


def test_the_report_carries_the_navigation_the_data_model_promises() -> None:
    """EVA-23 spelled out, because it is the entry a reader starts from."""
    from docdoc.evaluation import EvaluationReport

    available = _attributes("docdoc.evaluation:EvaluationReport")
    for name in (
        "outcomes",
        "group_outcomes",
        "document_scores",
        "metrics",
        "validation_verdicts",
        "partial",
        "redacted_tiers",
        "provenance",
        "report_id",
    ):
        assert name in available, f"EvaluationReport has no {name!r}"

    # EVA-19's per-document view, which the flat storage supplies through these
    # rather than by keeping a second copy on each DocumentScore.
    assert hasattr(EvaluationReport, "outcomes_for")
    assert hasattr(EvaluationReport, "groups_for")


def test_every_documented_entity_is_mapped_or_skipped() -> None:
    """Keeps the two maps above honest as the data model grows.

    A new EVA entry fails here until somebody either points it at a model or
    states why it is not one. Without this, the check silently stops covering
    whatever was added last — which is the failure mode of every hand-maintained
    list in this repository, and the reason each of them has a guard like this.
    """
    documented = set(_documented())
    accounted = set(MODELS) | set(NOT_MODELS)

    unaccounted = sorted(documented - accounted)
    assert not unaccounted, (
        f"the data model documents {unaccounted}, which this file neither checks "
        "nor explains. Add it to MODELS, or to NOT_MODELS with the reason"
    )

    stale = sorted(accounted - documented)
    assert not stale, (
        f"{stale} are listed here and appear in no data-model heading; they were "
        "renamed or removed, and this map is now describing nothing"
    )


def test_the_check_can_actually_fail() -> None:
    """Guards the guard.

    The parser is a regex over markdown, which is the kind of thing that quietly
    matches everything or nothing. This confirms it reads real field names and
    that an absent one would be caught.
    """
    fields = _documented()["DocumentPrediction"][1]

    assert "failed_stage" in fields
    assert "failure_reason" in fields
    assert "Field" not in fields, "the table header leaked into the field list"

    available = _attributes("docdoc.evaluation:DocumentPrediction")
    assert "failed_stage" in available
    assert "a_field_that_was_never_written" not in available
