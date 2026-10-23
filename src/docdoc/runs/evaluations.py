"""Evaluations: a tenant's answers re-run under the current configuration, then scored.

`start` stamps the answer set to the newest registered version of the schema
and queues one ordinary extraction run per checked document. `settled` scores
them once every run is terminal, and never again. Scoring is `docdoc.evaluation`'s,
unmodified.

Every read is tenant-scoped in the query, like `corrections`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from docdoc.evaluation import (
    DocumentPrediction,
    EvaluationError,
    EvaluationOptions,
    EvaluationReport,
    GoldenSet,
    PredictionSet,
    Stage,
    evaluate,
    schema_facts,
)
from docdoc.extraction.errors import SchemaError
from docdoc.runs.answers import answer_set, prediction_for
from docdoc.runs.errors import RunError

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from uuid import UUID

__all__ = [
    "Evaluation",
    "EvaluationUnavailable",
    "PostgresEvaluationStore",
    "settled",
    "start",
]


class EvaluationUnavailable(RunError):  # noqa: N818 - the name the routes answer with
    """Why an evaluation cannot start. `verdict` is the limiter's refusal for `rate_limited`."""

    def __init__(
        self,
        code: Literal["no_answers", "evaluation_in_progress", "unknown_schema", "rate_limited"],
        *,
        verdict: Any = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.verdict = verdict


@dataclass(frozen=True)
class Evaluation:
    evaluation_id: UUID
    tenant_id: str
    schema_name: str
    schema_identity: str
    golden: GoldenSet
    run_ids: tuple[UUID, ...]
    #: Parallel to `run_ids`: the document each run re-extracts.
    document_ids: tuple[str, ...]
    report: EvaluationReport | None
    erased_documents: int
    created_at: datetime
    settled_at: datetime | None
    #: Why it could not be scored: an exception class, never a message. Set
    #: once, with `settled_at`; a failed evaluation is finished, without a report.
    failure: str | None = None

    @property
    def finished(self) -> bool:
        return self.report is not None or self.failure is not None


_COLUMNS = (
    "evaluation_id, tenant_id, schema_name, schema_identity, golden, run_ids, "
    "document_ids, report, erased_documents, created_at, settled_at, failure"
)


@dataclass
class PostgresEvaluationStore:
    """The `evaluations` table. ``execute`` is `PostgresRunQueue`'s."""

    execute: Callable[..., Any]

    def create(self, evaluation: Evaluation) -> None:
        """Insert, or refuse when this tenant's schema already has an unfinished one."""
        row = self.execute(
            f"INSERT INTO evaluations ({_COLUMNS}) "
            "VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s, %s, %s, %s) "
            "ON CONFLICT DO NOTHING RETURNING evaluation_id",
            (
                evaluation.evaluation_id,
                evaluation.tenant_id,
                evaluation.schema_name,
                evaluation.schema_identity,
                evaluation.golden.model_dump_json(),
                list(evaluation.run_ids),
                list(evaluation.document_ids),
                None if evaluation.report is None else evaluation.report.model_dump_json(),
                evaluation.erased_documents,
                evaluation.created_at,
                evaluation.settled_at,
                evaluation.failure,
            ),
            fetch="one",
        )
        if row is None:
            raise EvaluationUnavailable("evaluation_in_progress")

    def get(self, evaluation_id: UUID, *, tenant_id: str) -> Evaluation | None:
        row = self.execute(
            f"SELECT {_COLUMNS} FROM evaluations WHERE evaluation_id = %s AND tenant_id = %s",
            (evaluation_id, tenant_id),
            fetch="one",
        )
        return None if row is None else _from_row(row)

    def list(
        self, *, tenant_id: str, schema_name: str | None, limit: int
    ) -> tuple[Evaluation, ...]:
        rows = self.execute(
            f"SELECT {_COLUMNS} FROM evaluations WHERE tenant_id = %(tenant_id)s "
            "AND (%(schema_name)s::text IS NULL OR schema_name = %(schema_name)s) "
            "ORDER BY created_at DESC LIMIT %(limit)s",
            {"tenant_id": tenant_id, "schema_name": schema_name, "limit": limit},
            fetch="all",
        )
        return tuple(_from_row(row) for row in rows or ())

    def unfinished(self, *, tenant_id: str, schema_name: str) -> Evaluation | None:
        row = self.execute(
            f"SELECT {_COLUMNS} FROM evaluations WHERE tenant_id = %s AND schema_name = %s "
            "AND report IS NULL AND failure IS NULL ORDER BY created_at DESC LIMIT 1",
            (tenant_id, schema_name),
            fetch="one",
        )
        return None if row is None else _from_row(row)

    def settle(self, evaluation_id: UUID, report: EvaluationReport, *, at: datetime) -> None:
        """Write the report once.

        Not written when a document was erased since the report was computed: the
        report would carry it. The caller re-reads and scores again.
        """
        self.execute(
            "UPDATE evaluations SET report = %s::jsonb, settled_at = %s "
            "WHERE evaluation_id = %s AND report IS NULL AND failure IS NULL "
            "AND jsonb_array_length(golden->'documents') = %s",
            (report.model_dump_json(), at, evaluation_id, len(report.document_scores)),
        )

    def fail(self, evaluation_id: UUID, failure: str, *, at: datetime) -> None:
        """Record once that this evaluation cannot be scored; it stops holding its schema."""
        self.execute(
            "UPDATE evaluations SET failure = %s, settled_at = %s "
            "WHERE evaluation_id = %s AND report IS NULL AND failure IS NULL",
            (failure, at, evaluation_id),
        )

    def forget_document(self, *, tenant_id: str, blob_id: str) -> int:
        """Remove one document from every evaluation of this tenant's that holds it.

        Every per-document entry goes (outcomes, scores, group outcomes, the
        partial declaration's skipped list); aggregates and `report_id` stay, since
        they describe a computation that happened. Each write is guarded on
        `settled_at` and `erased_documents`, so neither a concurrent settle nor a
        concurrent erase is overwritten; that evaluation is read again.
        """
        touched = 0
        while rows := self.execute(
            f"SELECT {_COLUMNS} FROM evaluations WHERE tenant_id = %s AND %s = ANY(document_ids)",
            (tenant_id, blob_id),
            fetch="all",
        ):
            for row in rows:
                touched += self._strip(_from_row(row), blob_id)
        return touched

    def _strip(self, evaluation: Evaluation, blob_id: str) -> int:
        golden = evaluation.golden.model_copy(
            update={
                "documents": tuple(
                    d for d in evaluation.golden.documents if d.document_id != blob_id
                ),
                "labels": {k: v for k, v in evaluation.golden.labels.items() if k != blob_id},
            }
        )
        report = evaluation.report
        if report is not None:
            partial = report.partial
            if partial is not None:
                partial = partial.model_copy(
                    update={
                        "skipped_documents": tuple(
                            d for d in partial.skipped_documents if d != blob_id
                        )
                    }
                )
            metrics = report.metrics.model_copy(
                update={
                    "group_outcomes": tuple(
                        g for g in report.metrics.group_outcomes if g.document_id != blob_id
                    )
                }
            )
            report = report.model_copy(
                update={
                    "outcomes": tuple(o for o in report.outcomes if o.document_id != blob_id),
                    "document_scores": tuple(
                        s for s in report.document_scores if s.document_id != blob_id
                    ),
                    "metrics": metrics,
                    "partial": partial,
                }
            )
        kept = [
            (run_id, document_id)
            for run_id, document_id in zip(evaluation.run_ids, evaluation.document_ids, strict=True)
            if document_id != blob_id
        ]
        rows = self.execute(
            "UPDATE evaluations SET golden = %s::jsonb, report = %s::jsonb, run_ids = %s, "
            "document_ids = %s, erased_documents = erased_documents + 1 "
            "WHERE evaluation_id = %s AND settled_at IS NOT DISTINCT FROM %s "
            "AND erased_documents = %s RETURNING evaluation_id",
            (
                golden.model_dump_json(),
                None if report is None else report.model_dump_json(),
                [run_id for run_id, _ in kept],
                [document_id for _, document_id in kept],
                evaluation.evaluation_id,
                evaluation.settled_at,
                evaluation.erased_documents,
            ),
            fetch="all",
        )
        return len(rows or ())


def _from_row(row: Any) -> Evaluation:
    report = row["report"]
    return Evaluation(
        evaluation_id=row["evaluation_id"],
        tenant_id=row["tenant_id"],
        schema_name=row["schema_name"],
        schema_identity=row["schema_identity"],
        golden=GoldenSet.model_validate(_decoded(row["golden"])),
        run_ids=tuple(row["run_ids"]),
        document_ids=tuple(row["document_ids"]),
        report=None if report is None else EvaluationReport.model_validate(_decoded(report)),
        erased_documents=row["erased_documents"],
        created_at=row["created_at"],
        settled_at=row["settled_at"],
        failure=row["failure"],
    )


def _decoded(payload: Any) -> Any:
    return json.loads(payload) if isinstance(payload, str | bytes) else payload


def start(deployment: Any, *, tenant_id: str, schema_name: str, at: datetime) -> Evaluation:
    """Queue one re-run per checked document and record the evaluation."""
    from docdoc.extraction.schema import parse_identity
    from docdoc.runs.identity import DEFAULT_RETENTION, deadline, new_evaluation_id, new_run_id
    from docdoc.runs.model import Priority
    from docdoc.runs.queue import Submission

    registry = deployment.registry()
    versions = [parse_identity(identity) for identity in registry.identities()]
    newest = max((version for name, version in versions if name == schema_name), default=None)
    if newest is None:
        raise EvaluationUnavailable("unknown_schema")
    schema = registry.describe(f"{schema_name}@{newest}")

    golden = answer_set(
        deployment.corrections().answers(tenant_id=tenant_id, schema_name=schema_name),
        tenant_id=tenant_id,
        schema=schema,
    )
    if not golden.documents:
        raise EvaluationUnavailable("no_answers")
    store: PostgresEvaluationStore = deployment.evaluations()
    pending = store.unfinished(tenant_id=tenant_id, schema_name=schema_name)
    if pending is not None and not settled_safely(deployment, pending, at=at).finished:
        raise EvaluationUnavailable("evaluation_in_progress")
    # The tenant's limits, as `POST /v1/documents/{blob_id}/runs` applies them.
    # ponytail: one check for N runs; the limiter has no count to check against.
    verdict = deployment.limiter.check(tenant_id=tenant_id, now=at)
    if not verdict.allowed:
        raise EvaluationUnavailable("rate_limited", verdict=verdict)

    queue = deployment.runs()
    document_ids = tuple(document.document_id for document in golden.documents)
    runs = tuple(
        queue.submit(
            Submission(
                tenant_id=tenant_id,
                blob_id=blob_id,
                schema_identity=schema.identity,
                request_id=None,
                idempotency_key=None,
                priority=int(Priority.ORDINARY),
            ),
            run_id=new_run_id(),
            now=at,
            expires_at=deadline(at, DEFAULT_RETENTION),
        )
        for blob_id in document_ids
    )
    evaluation = Evaluation(
        evaluation_id=new_evaluation_id(),
        tenant_id=tenant_id,
        schema_name=schema_name,
        schema_identity=schema.identity,
        golden=golden,
        run_ids=tuple(run.run_id for run in runs),
        document_ids=document_ids,
        report=None,
        erased_documents=0,
        created_at=at,
        settled_at=None,
    )
    for _ in runs:
        deployment.limiter.record_submission(tenant_id=tenant_id, now=at)
    try:
        store.create(evaluation)
    except EvaluationUnavailable:
        # A concurrent start won; its runs are the ones that count.
        for run in runs:
            queue.cancel(run.run_id, tenant_id, now=at)
        raise
    return evaluation


def settled_safely(deployment: Any, evaluation: Evaluation, *, at: datetime) -> Evaluation:
    """`settled`, except a failure leaves the evaluation as it was: unfinished."""
    try:
        return settled(deployment, evaluation, at=at)
    except Exception as error:
        # The class only: a message can quote document content.
        logging.getLogger("docdoc.runs").warning(
            "evaluation_settle_failed",
            extra={"evaluation_id": str(evaluation.evaluation_id), "error": type(error).__name__},
        )
        return evaluation


def settled(deployment: Any, evaluation: Evaluation, *, at: datetime) -> Evaluation:
    """The evaluation with its report, scoring it once every run is terminal."""
    store: PostgresEvaluationStore = deployment.evaluations()
    # Re-read, so a document erased since `evaluation` was fetched is not scored.
    current = store.get(evaluation.evaluation_id, tenant_id=evaluation.tenant_id)
    if current is None or current.finished:
        return current or evaluation

    queue = deployment.runs()
    runs = [queue.get(run_id, current.tenant_id) for run_id in current.run_ids]
    if any(run is not None and not run.is_terminal for run in runs):
        return current

    artifacts, _ = deployment.stores_for(current.tenant_id)
    predictions = PredictionSet(
        predictions={
            document_id: (
                prediction_for(artifacts, run, document_id)
                if run is not None
                # Gone (expired or erased): the document was not processed.
                else DocumentPrediction(
                    document_id=document_id, failed_stage=Stage.PARSE, failure_reason="run_gone"
                )
            )
            for run, document_id in zip(runs, current.document_ids, strict=True)
        }
    )
    try:
        report = evaluate(
            current.golden,
            predictions,
            # Tenant documents are restricted; without this every one is skipped.
            options=EvaluationOptions(include_restricted=True),
            facts=schema_facts([deployment.registry().describe(current.schema_identity)]),
        )
    except (SchemaError, EvaluationError) as error:
        # The schema version was removed or edited since the start, or the
        # library refused the pair: retrying cannot help, and an unfinished row
        # would hold this tenant's schema for ever. The class only.
        store.fail(current.evaluation_id, type(error).__name__, at=at)
    else:
        store.settle(current.evaluation_id, report, at=at)
    return store.get(current.evaluation_id, tenant_id=current.tenant_id) or current
