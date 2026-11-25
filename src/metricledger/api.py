from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles

from .models import AnalysisRequest, AnalysisResponse
from .observability import (
    InMemoryRateLimiter,
    RequestMetrics,
    TraceContext,
    reset_trace_context,
    set_trace_context,
)
from .security import ApiIdentity, ApiKeyAuthenticator
from .service import AnalysisService


logger = logging.getLogger("metricledger.http")


def create_app(
    service: AnalysisService,
    *,
    authenticator: ApiKeyAuthenticator | None = None,
    rate_limiter: InMemoryRateLimiter | None = None,
    metrics: RequestMetrics | None = None,
    static_dir: str | Path | None = None,
) -> FastAPI:
    app = FastAPI(
        title="MetricLedger API",
        version="0.1.0",
        description="Trusted, evidence-bound metric investigation.",
    )
    app.state.service = service
    app.state.authenticator = authenticator
    app.state.rate_limiter = rate_limiter
    app.state.metrics = metrics or RequestMetrics()

    @app.middleware("http")
    async def observe_request(request: Request, call_next):
        started = time.perf_counter()
        trace = TraceContext.from_traceparent(
            request.headers.get("traceparent")
        )
        trace_token = set_trace_context(trace)
        provided_request_id = request.headers.get("X-Request-ID", "")
        request_id = (
            provided_request_id[:128]
            if provided_request_id
            else f"req_{uuid4().hex}"
        )
        request.state.request_id = request_id
        request.state.trace_context = trace
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Trace-ID"] = trace.trace_id
            response.headers["traceparent"] = trace.traceparent
            return response
        finally:
            elapsed = time.perf_counter() - started
            route = request.scope.get("route")
            path = getattr(route, "path", request.url.path)
            app.state.metrics.record_request(
                method=request.method,
                path=path,
                status_code=status_code,
                duration_seconds=elapsed,
            )
            logger.info(
                json.dumps(
                    {
                        "event": "http_request",
                        "request_id": request_id,
                        "method": request.method,
                        "path": path,
                        "status": status_code,
                        "trace_id": trace.trace_id,
                        "span_id": trace.span_id,
                        "parent_span_id": trace.parent_span_id,
                        "duration_ms": round(elapsed * 1000, 3),
                    },
                    separators=(",", ":"),
                )
            )
            reset_trace_context(trace_token)

    def authenticated_identity(
        request: Request,
        *,
        roles: tuple[str, ...] = ("viewer",),
    ) -> ApiIdentity | None:
        if authenticator is None:
            return None
        identity = authenticator.authenticate(
            request.headers.get("Authorization")
        )
        if identity is None:
            app.state.metrics.record_auth_failure()
            raise HTTPException(
                status_code=401,
                detail="invalid or missing bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not any(role in identity.roles for role in roles):
            raise HTTPException(
                status_code=403,
                detail=f"requires one of roles: {', '.join(roles)}",
            )
        if rate_limiter is not None:
            decision = rate_limiter.check(
                f"{identity.tenant_id}:{identity.principal_id}"
            )
            if not decision.allowed:
                app.state.metrics.record_rate_limited()
                raise HTTPException(
                    status_code=429,
                    detail="rate limit exceeded",
                    headers={
                        "Retry-After": str(decision.retry_after_seconds)
                    },
                )
        return identity

    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "executions": service.engine.execution_count,
            "snapshots": len(service.snapshots.records),
            "ledger": service.ledger is not None,
        }

    @app.get("/api/metrics")
    def prometheus_metrics() -> Response:
        payload = app.state.metrics.render(
            engine=service.engine,
            ledger=service.ledger,
        )
        return Response(
            content=payload,
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.get("/api/catalog")
    def catalog(request: Request) -> dict:
        authenticated_identity(request)
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
    def analyze(request: Request, body: AnalysisRequest) -> dict:
        identity = authenticated_identity(request, roles=("analyst", "admin"))
        trusted_request = (
            body
            if identity is None
            else body.model_copy(update={"principal": identity.as_principal()})
        )
        response = service.analyze(trusted_request)
        app.state.metrics.record_analysis(response)
        return _response_payload(response)

    @app.get("/api/analyses/{analysis_id}")
    def get_analysis(request: Request, analysis_id: str) -> dict:
        identity = authenticated_identity(request)
        response = service.get(
            analysis_id,
            tenant_id=identity.tenant_id if identity is not None else None,
        )
        if response is None:
            raise HTTPException(status_code=404, detail="analysis not found")
        return _response_payload(response)

    @app.get("/api/eval/latest")
    def latest_evaluation(request: Request) -> dict:
        authenticated_identity(request)
        return _latest_eval_report()

    if static_dir is not None:
        directory = Path(static_dir)
        if directory.is_dir():
            app.mount(
                "/",
                StaticFiles(directory=str(directory), html=True),
                name="web",
            )

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
