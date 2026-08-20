"""The three identities: the dataset, the prediction set, and the report.

All built on the kernel's ``canonical_json`` and ``content_id_for``, so the
serialization rules of ADR-0002 settle key ordering and float formatting once
rather than being reinvented here.

**The report's identity is ``report_id``, not ``artifact_id``, and the name is
deliberate.** The formula's *shape* is ADR-0003's -- inputs, processor identity,
processor version, options hash -- because the argument for content-addressing is
the same one. Chain membership is not claimed: evaluation consumes the validation
artifact and produces nothing any document-processing stage consumes, so a reader
who went looking for this id in ADR-0003's chain would not find it, and calling it
``artifact_id`` would have invited exactly that search (EVA-24).

``prediction_set_id`` leans on **one validation artifact id per document**, which
is not laziness. ADR-0003 makes the terminal artifact id transitively cover every
result-affecting input of every earlier stage, so recording it records the parser,
the schema, the prompt, the model, the grounding version, and the validator in one
field that cannot drift from them. Re-deriving that set by hand would be a second,
weaker copy of a guarantee the chain already gives.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docdoc.kernel import canonical_json, content_id_for, options_hash_for

if TYPE_CHECKING:
    from docdoc.evaluation.golden import GoldenSet
    from docdoc.evaluation.predictions import PredictionSet
    from docdoc.evaluation.report import EvaluationOptions

__all__ = [
    "SCORER_ID",
    "SCORER_VERSION",
    "golden_set_id_for",
    "options_hash_for_evaluation",
    "prediction_set_id_for",
    "report_id_for",
]

#: The processor id of this stage. Stable; the *version* is what moves when
#: output moves for fixed inputs.
SCORER_ID = "golden-set-scorer"

#: Moves whenever scoring output moves for fixed inputs -- a comparator change, a
#: denominator change, an ordering change. This carries the same review
#: obligation ADR-0008 places on schema hashes and ADR-0003 on processor
#: versions: no system detects a semantic change on its own, so
#: `tests/unit/test_scorer_version_snapshot.py` is a change detector and the
#: classification is made by a human in a commit message.
SCORER_VERSION = "1.0.0"


def _label_payload(label: Any) -> dict[str, Any]:
    """The hashable form of one label.

    Everything that can change a metric is here. ``labeler`` and ``labeled_at``
    are **not**: neither can move a number, and an identity that shifted when
    somebody fixed a typo in an annotator's name would refuse comparisons for no
    reason (EVA-6).
    """
    location = label.location
    return {
        "field_path": label.field_path,
        "expectation": str(label.expectation),
        "value": None if label.value is None else _json_scalar(label.value),
        "location": None
        if location is None
        else {
            "page": location.page,
            "bbox": None
            if location.bbox is None
            else [
                location.bbox.x0,
                location.bbox.y0,
                location.bbox.x1,
                location.bbox.y1,
            ],
        },
    }


def _json_scalar(value: Any) -> Any:
    """Reduce a typed label value to something ``canonical_json`` accepts.

    ``Decimal`` and the date types travel as their canonical strings rather than
    as floats, for the reason Milestone 3 keeps decimals out of ``float``: the
    conversion is lossy and the identity would inherit the loss.
    """
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, float):
        return value
    return str(value)


def golden_set_id_for(golden: GoldenSet) -> str:
    """Identity of a dataset, moving whenever anything metric-relevant moves."""
    payload = {
        "documents": [
            {
                "document_id": document.document_id,
                "blob_sha256": document.blob_sha256,
                "tier": str(document.tier),
                "schema_identity": document.schema_identity,
                "schema_hash": document.schema_hash,
                "declared_label_count": document.declared_label_count,
                "origin": {
                    "kind": str(document.origin.kind),
                    "basis": document.origin.basis,
                    "source": document.origin.source,
                    "generator_id": document.origin.generator_id,
                    "generator_version": document.origin.generator_version,
                },
            }
            for document in sorted(golden.documents, key=lambda d: d.document_id)
        ],
        "labels": {
            document_id: [
                _label_payload(label) for label in sorted(labels, key=lambda item: item.field_path)
            ]
            for document_id, labels in sorted(golden.labels.items())
        },
        "entry_keys": [
            {"group_path": spec.group_path, "key_field": spec.key_field}
            for spec in sorted(golden.entry_keys, key=lambda s: s.group_path)
        ],
    }
    return content_id_for(canonical_json(payload))


def prediction_set_id_for(predictions: PredictionSet) -> str:
    """Identity of a prediction set, one validation artifact id per document."""
    payload = {
        "recorder_id": predictions.recorder_id,
        "recorder_version": predictions.recorder_version,
        "members": {
            document_id: {
                "validation_artifact_id": (
                    None if prediction.validation is None else prediction.validation.artifact_id
                ),
                "failed_stage": (
                    None if prediction.failed_stage is None else str(prediction.failed_stage)
                ),
            }
            for document_id, prediction in sorted(predictions.predictions.items())
        },
    }
    return content_id_for(canonical_json(payload))


def options_hash_for_evaluation(options: EvaluationOptions) -> str:
    """Fold every option that can move a number into one hash."""
    return options_hash_for(
        {
            "metric_definition_version": options.metric_definition_version,
            "comparator_versions": dict(sorted(options.comparator_versions.items())),
            "entry_alignment_version": options.entry_alignment_version,
            "location_rule_version": options.location_rule_version,
            "include_restricted": options.include_restricted,
        }
    )


def report_id_for(
    *,
    golden_set_id: str,
    prediction_set_id: str,
    options_hash: str,
) -> str:
    """``sha256`` over the dataset, the predictions, the scorer, and the options.

    Identical inputs produce an identical id; anything that can move a number
    moves it (FR-042). The shape is ADR-0003's, the name is not -- see the module
    docstring.
    """
    payload = {
        "golden_set_id": golden_set_id,
        "prediction_set_id": prediction_set_id,
        "scorer_id": SCORER_ID,
        "scorer_version": SCORER_VERSION,
        "options_hash": options_hash,
    }
    return content_id_for(canonical_json(payload))
