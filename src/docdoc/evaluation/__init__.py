"""Golden-set evaluation — how good is it, and did the last change make it worse.

Milestone 3 recorded what the model answered. Milestone 4 made the grounding rate
computable and set no target for it. Milestone 5 did the same for validation. Each
deferred the same question to this layer, and until it shipped every quality claim
in this repository was an assertion.

This layer takes a **golden set** -- documents together with the answers a human
states are correct -- and a **prediction set** -- what the pipeline recorded for
those documents -- and produces one report: every labelled field resolved to
exactly one of six closed outcomes, the five constitutionally required metrics
computed from those outcomes, and the provenance that says what was measured.

What it refuses to do, and why each refusal is load-bearing:

**It does not re-derive anything.** Not the extraction, not the grounding, not the
validation. It reads recorded facts. A stage that quietly recomputed one would be
reporting on a pipeline nobody ran (FR-002).

**It does not ask a model anything** -- including whether a predicted value
*means* the same as the expected one. A model judging its own output is the
failure Principle II forbids for grounding, and it is no more acceptable when the
subject is accuracy (FR-008).

**It does not read ``model_confidence``.** Untrusted upstream by ADR-0004, and
untrusted here (FR-028).

**It does not open a document.** It compares labels against recorded predictions,
so it has no need of one -- which is also why scoring needs no parser, no
credentials, and no network (FR-007).

**It does not normalize, case-fold, trim, round, or coerce** to make a value
match. Any leniency is a declared, versioned comparator recorded next to every
metric it affected (FR-024).

**It does not define a second grounding rate.** Milestone 4's definition and its
recorded counts are reused (FR-033).

**It does not persist anything**, and it does not decide what should happen about
a regression. Whether a build fails is policy configured on top of this output; a
comparison that also decided would bury the decision inside the thing being
measured (FR-049).

**It does not provide a review interface, assignment, workflow, queue, or
storage service.** :class:`Correction` and :func:`promote` are a model and an
explicit act; Principle IX permits that and forbids the MVP becoming a review
platform (FR-054). Where corrections live between being recorded and being
promoted is the caller's decision.

**It does not import :mod:`docdoc.recording`.** Producing a prediction set needs a
provider; scoring one must not. That separation is the ``import-linter`` layers
contract, not a convention -- ``docdoc.recording`` sits *above* this package, so
the import fails the build (FR-003).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from docdoc.evaluation.alignment import EntryAlignment, GroupOutcome
from docdoc.evaluation.compare import (
    ChangedOutcome,
    Comparison,
    Judgement,
    MetricDelta,
    compare,
)
from docdoc.evaluation.corrections import Correction, promote
from docdoc.evaluation.errors import EvaluationError, naming
from docdoc.evaluation.golden import (
    EntryKeySpec,
    GoldenDocument,
    GoldenSet,
    load_golden_set,
)
from docdoc.evaluation.identity import (
    SCORER_ID,
    SCORER_VERSION,
    golden_set_id_for,
    options_hash_for_evaluation,
    prediction_set_id_for,
    report_id_for,
)
from docdoc.evaluation.labels import Expectation, ExpectedLocation, Label
from docdoc.evaluation.location import LocationAgreement
from docdoc.evaluation.metrics import (
    Averaging,
    DatasetMetrics,
    DocumentScore,
    MetricValue,
    OutcomeCounts,
    dataset_metrics,
    document_score,
)
from docdoc.evaluation.observe import log_evaluation, log_refusal
from docdoc.evaluation.outcomes import FieldOutcome, FieldOutcomeKind
from docdoc.evaluation.predictions import (
    DocumentPrediction,
    PredictionSet,
    Stage,
    check_against,
    load_prediction_set,
)
from docdoc.evaluation.redact import redacted_tiers_in
from docdoc.evaluation.report import (
    EvaluationOptions,
    EvaluationProvenance,
    EvaluationReport,
    PartialDeclaration,
    TierSize,
)
from docdoc.evaluation.score import outcome_sort_key, score_document, value_path_index
from docdoc.evaluation.tiers import DocumentOrigin, OriginKind, Tier
from docdoc.evaluation.values import SchemaFacts, schema_facts
from docdoc.validation.result import ValidationCounts

if TYPE_CHECKING:
    from docdoc.grounding.result import GroundingCounts

__all__ = [
    "SCORER_ID",
    "SCORER_VERSION",
    "Averaging",
    "ChangedOutcome",
    "Comparison",
    "Correction",
    "DatasetMetrics",
    "DocumentOrigin",
    "DocumentPrediction",
    "DocumentScore",
    "EntryAlignment",
    "EntryKeySpec",
    "EvaluationError",
    "EvaluationOptions",
    "EvaluationProvenance",
    "EvaluationReport",
    "Expectation",
    "ExpectedLocation",
    "FieldOutcome",
    "FieldOutcomeKind",
    "GoldenDocument",
    "GoldenSet",
    "GroupOutcome",
    "Judgement",
    "Label",
    "LocationAgreement",
    "MetricDelta",
    "MetricValue",
    "OriginKind",
    "OutcomeCounts",
    "PartialDeclaration",
    "PredictionSet",
    "SchemaFacts",
    "Stage",
    "Tier",
    "TierSize",
    "compare",
    "evaluate",
    "load_golden_set",
    "load_prediction_set",
    "promote",
    "schema_facts",
]


def _aggregate_validation(
    predictions: PredictionSet, document_ids: list[str]
) -> ValidationCounts | None:
    """Sum Milestone 5's recorded counts. Never recompute them (FR-034).

    ``None`` when no scored document carried a validation result. A zeroed
    ``ValidationCounts`` would reconcile perfectly and read as "every check
    passed", which is the opposite of what an absence means -- the same reason
    FR-032 refuses to report an empty denominator as ``0.0``.

    Summation is safe here because Milestone 5's own reconciliation is linear:
    ``declared == passed + failed + not_evaluated`` holds for each document, so
    it holds for their sum, and ``ValidationCounts`` re-checks it on construction.
    """
    totals = {
        "declared": 0,
        "evaluated": 0,
        "passed": 0,
        "failed": 0,
        "not_evaluated": 0,
        "errors": 0,
        "warnings": 0,
        "infos": 0,
    }
    seen = False
    for document_id in document_ids:
        prediction = predictions.for_document(document_id)
        if prediction is None or prediction.validation is None:
            continue
        seen = True
        counts = prediction.validation.counts
        for name in totals:
            totals[name] += getattr(counts, name)

    return ValidationCounts(**totals) if seen else None


def _aggregate_grounding(predictions: PredictionSet, document_ids: list[str]) -> GroundingCounts:
    """Sum Milestone 4's recorded counts. Never recompute them (FR-033)."""
    from docdoc.grounding.result import GroundingCounts

    exact = fuzzy = ungrounded = not_applicable = truncated = 0
    for document_id in document_ids:
        prediction = predictions.for_document(document_id)
        if prediction is None or prediction.grounding is None:
            continue
        counts = prediction.grounding.counts
        exact += counts.exact
        fuzzy += counts.fuzzy
        ungrounded += counts.ungrounded
        not_applicable += counts.not_applicable
        truncated += counts.truncated
    return GroundingCounts(
        exact=exact,
        fuzzy=fuzzy,
        ungrounded=ungrounded,
        not_applicable=not_applicable,
        truncated=truncated,
    )


def evaluate(
    golden: GoldenSet,
    predictions: PredictionSet,
    *,
    options: EvaluationOptions | None = None,
    facts: SchemaFacts | None = None,
    repo_revision: str = "unknown",
) -> EvaluationReport:
    """Score a prediction set against a golden set.

    Returns **exactly one** report or raises :class:`EvaluationError`. It never
    returns a partial report without ``report.partial`` being set and naming what
    it omitted (FR-001).

    ``facts`` is what the schemas declare, from :func:`schema_facts`. It is
    optional and affects **no number**: it decides only which comparator version
    each outcome records. Typing the *values* is the loader's job, done once when
    the dataset and the predictions are read, so a caller who omits it here still
    gets a correct report rather than a quietly wrong one.
    """
    started = time.monotonic()
    options = options or EvaluationOptions()
    golden_id = golden.golden_set_id or golden_set_id_for(golden)
    prediction_id = predictions.prediction_set_id or prediction_set_id_for(predictions)

    # `golden_id` rather than `golden.golden_set_id`: a set built in memory carries
    # no identity of its own, and the one computed above is the honest answer to
    # "which dataset?" (FR-060). `naming` here rather than inside `check_against`,
    # which cannot see it.
    try:
        with naming(golden_id):
            check_against(predictions, golden)
    except EvaluationError as error:
        log_refusal(
            reason=str(error),
            golden_set_id=golden_id,
            prediction_set_id=prediction_id,
            document_id=error.document_id,
            expected=error.expected,
            actual=error.actual,
            duration_ms=(time.monotonic() - started) * 1000,
        )
        raise

    # Sorted here rather than trusted from the manifest. `load_golden_set` does
    # sort, but a `GoldenSet` can also be built in memory, and FR-043 requires
    # byte-identical output from the *inputs* rather than from the order somebody
    # happened to write them in: `document_scores`, `group_outcomes`, and the
    # provenance tuples all follow this order, so a manifest with one document
    # appended at the end would otherwise produce a different report for an
    # identical dataset. The lead key is the tier, matching the outcome order of
    # EVA-26, so a public-only report reads as a prefix of a full one.
    dataset = golden_id

    considered = sorted(
        (
            document
            for document in golden.documents
            if options.include_restricted or document.tier is Tier.PUBLIC
        ),
        key=lambda document: (str(document.tier), document.document_id),
    )

    outcomes: list[FieldOutcome] = []
    scores: list[DocumentScore] = []
    group_outcomes: list[GroupOutcome] = []
    value_paths = value_path_index(golden)
    verdicts: dict[object, int] = {}

    # One label for the whole walk. Inside the loop it would be re-entered per
    # document to say the same thing each time.
    with naming(dataset):
        for document in considered:
            labels = golden.labels_for(document.document_id)
            prediction = predictions.for_document(document.document_id)
            document_outcomes, document_groups = score_document(
                document,
                labels,
                prediction,
                field_type_for=(
                    None if facts is None else dict(facts.types_for(document.schema_identity))
                ),
                key_for=golden.key_for,
            )
            outcomes.extend(document_outcomes)
            group_outcomes.extend(document_groups)

            grounding = (
                None
                if prediction is None
                else (prediction.grounding.counts if prediction.grounding else None)
            )
            scores.append(
                document_score(
                    document_id=document.document_id,
                    outcomes=document_outcomes,
                    value_paths=value_paths,
                    grounding=grounding,
                    evaluated=prediction is not None,
                    failed_stage=None if prediction is None else prediction.failed_stage,
                )
            )
            if prediction is not None and prediction.validation is not None:
                verdict = prediction.validation.verdict
                verdicts[verdict] = verdicts.get(verdict, 0) + 1

    outcomes.sort(key=outcome_sort_key)

    metrics = dataset_metrics(
        outcomes=outcomes,
        document_scores=scores,
        value_paths=value_paths,
        grounding=_aggregate_grounding(
            predictions, [document.document_id for document in considered]
        ),
        group_outcomes=group_outcomes,
        verdicts=verdicts,  # type: ignore[arg-type]
        dataset=dataset,
        validation=_aggregate_validation(
            predictions, [document.document_id for document in considered]
        ),
    )

    provenance = _provenance_for(
        golden=golden,
        predictions=predictions,
        considered=considered,
        options=options,
        golden_id=golden_id,
        prediction_id=prediction_id,
        repo_revision=repo_revision,
    )
    provenance.refuse_if_incomplete(documents_considered=len(considered))

    report = EvaluationReport(
        outcomes=tuple(outcomes),
        document_scores=tuple(scores),
        metrics=metrics,
        dataset_size=_dataset_size(golden),
        partial=_partial_for(golden, considered),
        redacted_tiers=redacted_tiers_in(outcomes),
        provenance=provenance,
        report_id=report_id_for(
            golden_set_id=golden_id,
            prediction_set_id=prediction_id,
            options_hash=options_hash_for_evaluation(options),
        ),
    )
    log_evaluation(report, duration_ms=(time.monotonic() - started) * 1000)
    return report


def _dataset_size(golden: GoldenSet) -> tuple[TierSize, ...]:
    """The dataset's size per tier, for every report to carry (FR-009).

    Read from ``GoldenSet.tier_counts()``, which sums ``declared_label_count`` --
    so a restricted tier contributes its labelled-field count even though its
    labels are not present, which is what that field exists for (EVA-5a).

    **Deliberately outside ``report_id``.** These counts are a function of the
    golden set, so ``golden_set_id`` already covers them: folding them in again
    would move every committed report's identity without any measurement having
    changed, which is exactly the over-sensitivity FR-042's second half forbids.
    """
    return tuple(
        TierSize(tier=tier, documents=documents, labelled_fields=labels)
        for tier, (documents, labels) in sorted(
            golden.tier_counts().items(), key=lambda item: str(item[0])
        )
    )


def _partial_for(golden: GoldenSet, considered: list[GoldenDocument]) -> PartialDeclaration | None:
    """Name what was skipped, in exact integers read from the manifest (EVA-27a)."""
    considered_ids = {document.document_id for document in considered}
    skipped = [d for d in golden.documents if d.document_id not in considered_ids]
    if not skipped:
        return None
    return PartialDeclaration(
        skipped_documents=tuple(sorted(d.document_id for d in skipped)),
        skipped_tiers=tuple(sorted({d.tier for d in skipped}, key=str)),
        covered_documents=len(considered),
        declared_documents=len(golden.documents),
        covered_labels=sum(d.declared_label_count for d in considered),
        declared_labels=sum(d.declared_label_count for d in golden.documents),
    )


def _provenance_for(
    *,
    golden: GoldenSet,
    predictions: PredictionSet,
    considered: list[GoldenDocument],
    options: EvaluationOptions,
    golden_id: str,
    prediction_id: str,
    repo_revision: str,
) -> EvaluationProvenance:
    def _unique(values: list[str]) -> tuple[str, ...]:
        return tuple(sorted({value for value in values if value}))

    prompt_hashes: list[str] = []
    model_ids: list[str] = []
    model_versions: list[str] = []
    grounding_versions: list[str] = []
    validator_versions: list[str] = []
    parser_ids: list[str] = []
    parser_versions: list[str] = []

    for document in considered:
        prediction = predictions.for_document(document.document_id)
        if prediction is None:
            continue
        if prediction.parser_id:
            parser_ids.append(prediction.parser_id)
        if prediction.parser_version:
            parser_versions.append(prediction.parser_version)
        if prediction.extraction is not None:
            extraction = prediction.extraction.provenance
            prompt_hashes.append(extraction.prompt_hash)
            model_ids.append(extraction.model_id)
            model_versions.append(extraction.model_version)
        if prediction.grounding is not None:
            grounding_versions.append(prediction.grounding.provenance.grounding_version)
        if prediction.validation is not None:
            validator_versions.append(prediction.validation.provenance.validator_version)

    return EvaluationProvenance(
        repo_revision=repo_revision,
        golden_set_id=golden_id,
        prediction_set_id=prediction_id,
        schema_identities=_unique([d.schema_identity for d in considered]),
        schema_hashes=_unique([d.schema_hash for d in considered]),
        prompt_hashes=_unique(prompt_hashes),
        model_ids=_unique(model_ids),
        model_versions=_unique(model_versions),
        parser_ids=_unique(parser_ids),
        parser_versions=_unique(parser_versions),
        grounding_versions=_unique(grounding_versions),
        validator_versions=_unique(validator_versions),
        scorer_id=SCORER_ID,
        scorer_version=SCORER_VERSION,
        metric_definition_version=options.metric_definition_version,
        comparator_versions=dict(options.comparator_versions),
        entry_alignment_version=options.entry_alignment_version,
        location_rule_version=options.location_rule_version,
        options=options,
    )
