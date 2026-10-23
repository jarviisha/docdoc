"""`/v1/answers` and `/v1/evaluations*`: a tenant's checks, and scoring them."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from docdoc.api.deps import Caller, _deployment_of, _iso
from docdoc.api.runs import _limit_refusal

_STEP = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def routes() -> APIRouter:
    router = APIRouter()

    @router.get("/answers")
    async def answers(request: Request, principal: Caller) -> Any:
        """How many documents and fields this tenant has checked, per schema."""
        store = _deployment_of(request).corrections()
        if store is None:
            return _not_found()
        return {
            "schemas": [
                {"schema_name": name, "documents": documents, "fields": fields}
                for name, documents, fields in store.answer_summary(tenant_id=principal.tenant_id)
            ]
        }

    @router.post("/evaluations", status_code=202)
    async def start_evaluation(request: Request, schema: str, principal: Caller) -> Any:
        from docdoc.runs.evaluations import EvaluationUnavailable, start
        from docdoc.runs.identity import now

        deployment = _deployment_of(request)
        if deployment.corrections() is None or deployment.evaluations() is None:
            return _not_found()
        try:
            evaluation = start(
                deployment, tenant_id=principal.tenant_id, schema_name=schema, at=now()
            )
        except EvaluationUnavailable as refused:
            if refused.code == "rate_limited":
                return _limit_refusal(refused.verdict)
            if refused.code == "unknown_schema":
                return JSONResponse(
                    status_code=404,
                    content={
                        "error": {
                            "class": "SchemaError",
                            "message": "schema is not configured here",
                        }
                    },
                )
            return JSONResponse(
                status_code=409, content={"error": refused.code, "detail": _DETAIL[refused.code]}
            )
        return JSONResponse(
            status_code=202,
            content={
                "evaluation_id": str(evaluation.evaluation_id),
                "schema_identity": evaluation.schema_identity,
                "documents": len(evaluation.document_ids),
                "status": "running",
            },
        )

    @router.get("/evaluations")
    async def list_evaluations(
        request: Request, principal: Caller, schema: str | None = None, limit: int = 20
    ) -> Any:
        store = _deployment_of(request).evaluations()
        if store is None:
            return _not_found()
        found = store.list(
            tenant_id=principal.tenant_id, schema_name=schema, limit=max(1, min(limit, 100))
        )
        from docdoc.runs.evaluations import settled_safely
        from docdoc.runs.identity import now

        deployment = _deployment_of(request)
        return {
            "evaluations": [
                _summary(e if e.finished else settled_safely(deployment, e, at=now()))
                for e in found
            ]
        }

    @router.get("/evaluations/{evaluation_id}")
    async def read_evaluation(request: Request, evaluation_id: str, principal: Caller) -> Any:
        from docdoc.runs.evaluations import settled
        from docdoc.runs.identity import now

        deployment = _deployment_of(request)
        store = deployment.evaluations()
        if store is None:
            return _not_found()
        try:
            identity = UUID(evaluation_id)
        except ValueError:
            return _not_found()
        evaluation = store.get(identity, tenant_id=principal.tenant_id)
        if evaluation is None:
            return _not_found()
        evaluation = settled(deployment, evaluation, at=now())
        report = evaluation.report
        return {
            **_summary(evaluation),
            "per_field": (
                None if report is None else jsonable_encoder(report.metrics.per_field_path)
            ),
            "documents": _documents(deployment, evaluation),
        }

    return router


_DETAIL = {
    "no_answers": "check some fields of some documents first",
    "evaluation_in_progress": "an evaluation of this schema is still running",
}


def _not_found() -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={"error": {"class": "NotFound", "message": "no such evaluation here"}},
    )


def _summary(evaluation: Any) -> dict[str, Any]:
    report = evaluation.report
    return {
        "evaluation_id": str(evaluation.evaluation_id),
        "schema_identity": evaluation.schema_identity,
        "created_at": _iso(evaluation.created_at),
        "status": "finished" if evaluation.finished else "running",
        "documents": len(evaluation.document_ids),
        "erased_documents": evaluation.erased_documents,
        "metrics": None if report is None else jsonable_encoder(report.metrics.micro),
        "golden_set_id": evaluation.golden.golden_set_id,
    }


def _documents(deployment: Any, evaluation: Any) -> list[dict[str, Any]]:
    """Expected from the stored answers, extracted from the run's own artifact.

    Never from the report, which is redacted.
    """
    from docdoc.runs.answers import prediction_for

    kinds = {}
    if evaluation.report is not None:
        kinds = {(o.document_id, o.field_path): o.kind.value for o in evaluation.report.outcomes}
    artifacts, _ = deployment.stores_for(evaluation.tenant_id)
    queue = deployment.runs()
    documents = []
    for run_id, document_id in zip(evaluation.run_ids, evaluation.document_ids, strict=True):
        run = queue.get(run_id, evaluation.tenant_id)
        extraction = None if run is None else prediction_for(artifacts, run, document_id).extraction
        documents.append(
            {
                "document_id": document_id,
                "run_id": str(run_id),
                "outcomes": [
                    {
                        "field_path": label.field_path,
                        "kind": kinds.get((document_id, label.field_path)),
                        "expected": jsonable_encoder(label.value),
                        "extracted": _extracted(extraction, label.field_path),
                    }
                    for label in evaluation.golden.labels.get(document_id, ())
                ],
            }
        )
    return documents


def _extracted(extraction: Any, field_path: str) -> Any:
    """The value the run read at `field_path` (`line_items[1].amount` too), or ``None``."""
    if extraction is None:
        return None
    node: Any = extraction.values
    for name, index in _STEP.findall(field_path):
        try:
            node = node[int(index)] if index else node[name]
        except (KeyError, IndexError, TypeError):
            return None
    return jsonable_encoder(getattr(node, "value", None))
