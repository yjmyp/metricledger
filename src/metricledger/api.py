from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException

from .models import AnalysisRequest, AnalysisResponse
from .service import AnalysisService


def create_app(service: AnalysisService) -> FastAPI:
    app = FastAPI(
        title="MetricLedger API",
        version="0.1.0",
        description="Trusted, evidence-bound metric investigation.",
    )
    app.state.service = service

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "executions": service.engine.execution_count,
            "snapshots": len(service.snapshots.records),
            "ledger": service.ledger is not None,
        }

    @app.get("/api/catalog")
    def catalog() -> dict:
        bundle = service.registry.bundle
        return {
            "bundle_id": bundle.bundle_id,
            "version": bundle.version,
            "canonical_hash": bundle.canonical_hash,
            "metrics": [
                {
                    "metric_id": metric.metric_id,
                    "display_name": metric.display_name,
                    "kind": metric.kind.value,
                    "aliases": list(metric.aliases),
                    "allowed_dimensions": list(metric.allowed_dimensions),
                }
                for metric in bundle.metrics
            ],
            "dimensions": [
                {
                    "dimension_id": dimension.dimension_id,
                    "display_name": dimension.display_name,
                    "values": list(dimension.values),
                }
                for dimension in bundle.dimensions
            ],
        }

    @app.post("/api/analyses")
    def analyze(request: AnalysisRequest) -> dict:
        response = service.analyze(request)
        return _response_payload(response)

    @app.get("/api/analyses/{analysis_id}")
    def get_analysis(analysis_id: str) -> dict:
        response = service.get(analysis_id)
        if response is None:
            raise HTTPException(status_code=404, detail="analysis not found")
        return _response_payload(response)

    @app.get("/api/eval/latest")
    def latest_evaluation() -> dict:
        return _latest_eval_report()

    return app


def _response_payload(response: AnalysisResponse) -> dict:
    payload = response.model_dump(mode="json")
    payload["claims"] = [
        {
            **validated.claim.model_dump(mode="json"),
            "rendered_text": validated.rendered_text,
            "evidence_values": validated.evidence_values,
        }
        for validated in response.claims
    ]
    return payload


def _latest_eval_report() -> dict:
    path = Path(__file__).resolve().parents[2] / "eval" / "results.json"
    if not path.is_file():
        return {
            "status": "NOT_RUN",
            "message": "No reproducible evaluation report exists.",
        }
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "status": "INVALID",
            "message": "The evaluation report could not be read.",
        }
