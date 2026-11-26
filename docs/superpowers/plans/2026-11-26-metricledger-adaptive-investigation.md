# MetricLedger Adaptive Investigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade MetricLedger from a one-shot diagnostic planner to a bounded two-round investigation agent with controlled evidence feedback, real planner usage accounting, context-drift protection, atomic audit writes, and end-to-end trace visibility.

**Architecture:** `InvestigationLoop` owns a two-round state machine and may invoke a planner at most twice. Planners return immutable `PlannerDecision` values, `ObservationBuilder` exposes only redacted evidence summaries, `ContextIntegrityGuard` verifies the admitted semantic and snapshot context before planning or querying, and the service clears all business answers while retaining SQL audit if drift is detected.

**Tech Stack:** Python 3.10+, Pydantic 2, FastAPI, DuckDB, SQLite, pytest, React 19, TypeScript, Vitest, Prometheus text exposition.

**Spec:** `docs/superpowers/specs/2026-11-26-metricledger-adaptive-investigation-design.md`

## Global Constraints

- A single analysis may call the planner at most two times.
- Each planner round returns at most three candidate dimensions.
- A planner may only choose dimensions from the current remaining allowlist.
- A planner never creates SQL, metric values, filters, Claims, or answer numbers.
- A dimension query pair requires two remaining queries before the pair starts.
- Model Token usage missing from the response is represented as `None`, never `0`.
- If model usage is missing, a second planner round must use the deterministic planner and must not call the model.
- Context drift returns `Completeness.NONE`, clears `answer` and `claims`, and retains SQL audit.
- SQLite immutable writes use `BEGIN IMMEDIATE`; identical retries are idempotent and conflicting payloads raise `ValueError`.
- Existing transaction-safe migration behavior for old ledger databases must remain intact.
- Evaluation must contain at least 72 deterministic scenarios before this plan is considered complete.
- Backend coverage must remain at least 93%.
- Frontend tests, frontend production build, backend tests, evaluation, and all four GitHub Actions jobs must pass.
- This plan must not introduce multi-Agent role-play, unbounded loops, Redis, task queues, or distributed planners.

---

## File Structure

- `src/metricledger/models.py`
  - Owns all wire-safe investigation, observation, planner, model-usage, budget, and response models.
- `src/metricledger/observation.py`
  - Converts executed evidence into redacted, bounded observations. It never receives API keys or emits `bounded_rows`.
- `src/metricledger/planners.py`
  - Defines the planner protocol, deterministic fallback, HTTP planner, sanitization, and structured attempt traces.
- `src/metricledger/context_guard.py`
  - Recomputes the semantic hash and re-reads the snapshot manifest before planner calls and business queries.
- `src/metricledger/investigation.py`
  - Owns the two-round investigation state machine, paired query reservation, stop reasons, and investigation trace.
- `src/metricledger/service.py`
  - Wires admission, headline queries, the guard, investigation, Claim validation, budget usage, and persistence.
- `src/metricledger/ledger.py`
  - Implements atomic immutable writes under SQLite write serialization.
- `src/metricledger/observability.py`
  - Exposes planner, investigation-step, token, and context-drift metrics.
- `tests/test_observation.py`
  - Tests controlled evidence summaries and sensitive-dimension redaction.
- `tests/test_planners.py`
  - Tests planner protocol migration, model usage, sanitization, timeouts, HTTP errors, and invalid output.
- `tests/test_context_guard.py`
  - Tests semantic drift and snapshot drift.
- `tests/test_investigation.py`
  - Tests two rounds, deduplication, query pairing, stop reasons, fallback, and context drift.
- `tests/test_service.py`
  - Tests response integration, wall-clock budget usage, and answer/Claim clearing on drift.
- `tests/test_ledger.py`
  - Tests atomic idempotency and concurrent writes.
- `tests/test_api.py`
  - Tests investigation payload serialization.
- `tests/test_runtime.py`
  - Tests new Prometheus series.
- `web/src/api.ts`
  - Declares investigation trace wire types.
- `web/src/App.tsx`
  - Renders a compact investigation timeline.
- `web/src/App.test.tsx`
  - Tests planner fallback, step status, and drift presentation.
- `eval/scenarios.json`
  - Expands deterministic coverage from 63 to at least 72 scenarios.
- `scripts/benchmark_planners.py`
  - Migrates the benchmark to `PlannerDecision` and reports usage completeness.
- `docs/verification/acceptance.md`
  - Records only freshly verified commands and results.

---

### Task 1: Add Investigation Domain Models and Redacted Observations

**Files:**
- Modify: `src/metricledger/models.py`
- Create: `src/metricledger/observation.py`
- Create: `tests/test_observation.py`
- Modify: `tests/test_models.py`

**Interfaces:**
- Consumes: `AnalysisContext`, `ResultArtifact`, `MetricKind`, `ClaimType`.
- Produces:
  - `EvidenceStatus`
  - `ModelUsage`
  - `PlannerAttemptTrace`
  - `EvidenceObservation`
  - `InvestigationObservation`
  - `InvestigationStepTrace`
  - `InvestigationRoundTrace`
  - `InvestigationTrace`
  - `ObservationBuilder.build(...) -> InvestigationObservation`
  - `ObservationBuilder.step(...) -> InvestigationStepTrace`

- [ ] **Step 1: Write the failing model and observation tests**

```python
# tests/test_models.py
from metricledger.models import BudgetUsage, EvidenceStatus, ModelUsage


def test_unknown_planner_token_usage_is_not_encoded_as_zero():
    usage = BudgetUsage(planner_tokens_used=None, planner_usage_complete=False)

    assert usage.planner_tokens_used is None
    assert usage.planner_usage_complete is False


def test_model_usage_rejects_negative_tokens():
    with pytest.raises(ValueError):
        ModelUsage(prompt_tokens=1, completion_tokens=-1, total_tokens=0)


def test_evidence_status_wire_values_are_stable():
    assert EvidenceStatus.MATERIAL_CHANGE.value == "MATERIAL_CHANGE"
    assert EvidenceStatus.QUERY_FAILED.value == "QUERY_FAILED"
```

```python
# tests/test_observation.py
from datetime import date, datetime
from zoneinfo import ZoneInfo

from metricledger.models import (
    AnalysisContext,
    AnalysisIntent,
    BudgetPolicy,
    DateRange,
    EvidenceStatus,
    MetricKind,
    QuestionKind,
    ResultArtifact,
    Scope,
)
from metricledger.observation import ObservationBuilder


def context(candidate_dimensions, budget=BudgetPolicy(max_queries=6)):
    from metricledger.semantic import SemanticRegistry

    return AnalysisContext(
        analysis_id="an_observation",
        tenant_id="demo",
        intent=AnalysisIntent(
            metric_id="paid_gmv",
            question_kind=QuestionKind.INVESTIGATE,
            period=DateRange(date(2026, 11, 17), date(2026, 11, 23)),
            comparison_period=DateRange(
                date(2026, 11, 10), date(2026, 11, 16)
            ),
            candidate_dimensions=candidate_dimensions,
        ),
        metric_id="paid_gmv",
        semantic_bundle_id="ecommerce",
        semantic_bundle_hash="semantic-hash",
        snapshot_id="snapshot",
        snapshot_table="sales",
        snapshot_manifest_hash="snapshot-hash",
        reporting_as_of=datetime(
            2026, 11, 23, 23, 59, tzinfo=ZoneInfo("Asia/Shanghai")
        ),
        timezone="Asia/Shanghai",
        auth_policy_version="v1",
        effective_scope=Scope(values={"region": ["east"]}),
        auth_scope_hash="auth-hash",
        budget=budget,
    )


def test_observation_builder_emits_bounded_evidence_without_raw_rows():
    builder = ObservationBuilder(MetricKind.ADDITIVE)
    ctx = context(["region"])
    artifact = ResultArtifact(
        result_id="r-region",
        analysis_id=ctx.analysis_id,
        analysis_kind="diagnostic_group",
        intent_dsl=ctx.intent,
        compiled_sql="SELECT secret",
        compiled_parameters=[],
        sql_fingerprint="fp",
        row_count=1,
        result_digest="digest",
        bounded_rows=[{"dimension_value": "east", "metric_value": 10.0}],
        metric_versions={"paid_gmv": "1"},
        snapshot_id="snapshot",
        semantic_bundle_hash="semantic-hash",
        scope=ctx.effective_scope,
    )
    step = builder.step(
        round_number=1,
        dimension_id="region",
        status=EvidenceStatus.MATERIAL_CHANGE,
        result_ids=[artifact.result_id],
        material_change=10.0,
        concentration_share=0.75,
        group_count=1,
        top_group="east",
        issue_codes=[],
    )

    observation = builder.build(
        ctx,
        round_number=1,
        steps=[step],
        completed_dimensions=["region"],
        remaining_queries=4,
        remaining_planner_tokens=1000,
    )
    payload = observation.model_dump(mode="json")

    assert payload["evidence"][0]["top_group"] == "east"
    assert "bounded_rows" not in str(payload)
    assert "SELECT secret" not in str(payload)


def test_sensitive_dimension_omits_top_group():
    builder = ObservationBuilder(MetricKind.ADDITIVE)
    ctx = context(["region"])
    step = builder.step(
        round_number=1,
        dimension_id="region",
        status=EvidenceStatus.MATERIAL_CHANGE,
        result_ids=["r1"],
        material_change=5.0,
        concentration_share=0.4,
        group_count=2,
        top_group="east",
        issue_codes=[],
    )

    observation = builder.build(
        ctx,
        round_number=1,
        steps=[step],
        completed_dimensions=["region"],
        remaining_queries=2,
        remaining_planner_tokens=500,
        sensitive_dimensions={"region"},
    )

    assert observation.evidence[0].top_group is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_models.py tests\test_observation.py -q
```

Expected: FAIL because `EvidenceStatus`, `ModelUsage`, and `ObservationBuilder` do not exist.

- [ ] **Step 3: Implement the models**

```python
# src/metricledger/models.py
class EvidenceStatus(WireEnum):
    MATERIAL_CHANGE = "MATERIAL_CHANGE"
    NO_MATERIAL_CHANGE = "NO_MATERIAL_CHANGE"
    NO_COMPARABLE_GROUPS = "NO_COMPARABLE_GROUPS"
    QUERY_FAILED = "QUERY_FAILED"


class ModelUsage(Model):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class PlannerAttemptTrace(Model):
    round_number: int = Field(ge=1, le=2)
    outcome: str
    model_called: bool = False
    fallback_used: bool = False
    fallback_reason: str | None = None
    latency_ms: float = Field(default=0.0, ge=0)
    usage_complete: bool = True
    selected_dimensions: list[str] = Field(default_factory=list)


class EvidenceObservation(Model):
    dimension_id: str
    status: EvidenceStatus
    result_ids: list[str] = Field(default_factory=list)
    material_change: float | None = None
    concentration_share: float | None = Field(default=None, ge=0, le=1)
    group_count: int = Field(default=0, ge=0)
    top_group: str | None = None
    issue_codes: list[str] = Field(default_factory=list)


class InvestigationObservation(Model):
    analysis_id: str
    round_number: int = Field(ge=1, le=2)
    metric_id: str
    metric_kind: MetricKind
    question_kind: QuestionKind
    candidate_dimensions: list[str] = Field(default_factory=list)
    completed_dimensions: list[str] = Field(default_factory=list)
    remaining_queries: int = Field(ge=0)
    remaining_planner_tokens: int | None = Field(default=None, ge=0)
    evidence: list[EvidenceObservation] = Field(default_factory=list)


class InvestigationStepTrace(Model):
    round_number: int = Field(ge=1, le=2)
    dimension_id: str
    status: EvidenceStatus
    result_ids: list[str] = Field(default_factory=list)
    material_change: float | None = None
    concentration_share: float | None = Field(default=None, ge=0, le=1)
    group_count: int = Field(default=0, ge=0)
    top_group: str | None = None
    issue_codes: list[str] = Field(default_factory=list)


class InvestigationRoundTrace(Model):
    round_number: int = Field(ge=1, le=2)
    observation: InvestigationObservation
    planner: PlannerAttemptTrace
    steps: list[InvestigationStepTrace] = Field(default_factory=list)


class InvestigationTrace(Model):
    rounds: list[InvestigationRoundTrace] = Field(default_factory=list)
    completed_dimensions: list[str] = Field(default_factory=list)
    remaining_dimensions: list[str] = Field(default_factory=list)
    stop_reason: str
    planner_fallback_used: bool = False
```

Also change these existing fields:

```python
class BudgetPolicy(Model):
    max_queries: int = Field(default=4, ge=1, le=10)
    timeout_ms: int = Field(default=10_000, ge=100, le=60_000)
    max_planner_tokens: int = Field(default=2_000, ge=0, le=20_000)
    max_planner_rounds: int = Field(default=2, ge=1, le=2)


class BudgetUsage(Model):
    queries_used: int = 0
    planner_tokens_used: int | None = 0
    planner_usage_complete: bool = True
    elapsed_ms: int = 0


class ObservationSummary(Model):
    analysis_id: str
    metric_id: str
    metric_kind: MetricKind
    question_kind: QuestionKind
    candidate_dimensions: list[str] = Field(default_factory=list)
    completed_dimensions: list[str] = Field(default_factory=list)
    round_number: int = Field(default=1, ge=1, le=2)
    remaining_queries: int = Field(default=0, ge=0)
    remaining_planner_tokens: int | None = Field(default=None, ge=0)
    evidence_summaries: list[EvidenceObservation] = Field(default_factory=list)


class AnalysisResponse(Model):
    # Preserve all existing fields and add:
    investigation: InvestigationTrace | None = None


class InvestigationOutcome(Model):
    # Preserve all existing fields, then add:
    planner_tokens_used: int | None = 0
    planner_usage_complete: bool = True
    investigation: InvestigationTrace | None = None
```

- [ ] **Step 4: Implement `ObservationBuilder`**

```python
# src/metricledger/observation.py
from __future__ import annotations

from .models import (
    AnalysisContext,
    EvidenceObservation,
    EvidenceStatus,
    InvestigationObservation,
    InvestigationStepTrace,
    MetricKind,
)


class ObservationBuilder:
    def __init__(self, metric_kind: MetricKind):
        self.metric_kind = metric_kind

    def step(
        self,
        *,
        round_number: int,
        dimension_id: str,
        status: EvidenceStatus,
        result_ids: list[str],
        material_change: float | None,
        concentration_share: float | None,
        group_count: int,
        top_group: str | None,
        issue_codes: list[str],
    ) -> InvestigationStepTrace:
        return InvestigationStepTrace(
            round_number=round_number,
            dimension_id=dimension_id,
            status=status,
            result_ids=list(result_ids),
            material_change=material_change,
            concentration_share=concentration_share,
            group_count=group_count,
            top_group=top_group,
            issue_codes=list(issue_codes),
        )

    def build(
        self,
        context: AnalysisContext,
        *,
        round_number: int,
        steps: list[InvestigationStepTrace],
        completed_dimensions: list[str],
        remaining_queries: int,
        remaining_planner_tokens: int | None,
        sensitive_dimensions: set[str] | None = None,
    ) -> InvestigationObservation:
        sensitive = sensitive_dimensions or set()
        evidence = [
            EvidenceObservation(
                dimension_id=step.dimension_id,
                status=step.status,
                result_ids=list(step.result_ids),
                material_change=step.material_change,
                concentration_share=step.concentration_share,
                group_count=step.group_count,
                top_group=(
                    None
                    if step.dimension_id in sensitive
                    else step.top_group
                ),
                issue_codes=list(step.issue_codes),
            )
            for step in steps
        ]
        return InvestigationObservation(
            analysis_id=context.analysis_id,
            round_number=round_number,
            metric_id=context.metric_id,
            metric_kind=self.metric_kind,
            question_kind=context.intent.question_kind,
            candidate_dimensions=list(
                context.intent.candidate_dimensions
            ),
            completed_dimensions=list(completed_dimensions),
            remaining_queries=remaining_queries,
            remaining_planner_tokens=remaining_planner_tokens,
            evidence=evidence,
        )
```

The builder receives `MetricKind` explicitly because metric kind is registry
metadata, not a field on `AnalysisContext`.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_models.py tests\test_observation.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/metricledger/models.py src/metricledger/observation.py tests/test_models.py tests/test_observation.py
git commit -m "feat: model adaptive investigation evidence"
```

---

### Task 2: Migrate Planners to Immutable Decisions and Real Usage

**Files:**
- Modify: `src/metricledger/models.py`
- Modify: `src/metricledger/planners.py`
- Modify: `tests/test_planners.py`
- Modify: `scripts/benchmark_planners.py`
- Modify: `tests/test_benchmarks.py`

**Interfaces:**
- Consumes: `InvestigationObservation`, `DimensionDefinition`, `Hypothesis`, `ModelUsage`, `PlannerAttemptTrace`.
- Produces:
  - `PlannerDecision`
  - `HypothesisPlanner.propose(observation, allowed_dimensions, *, remaining_queries, round_number) -> PlannerDecision`
  - `HeuristicHypothesisPlanner.propose(...) -> PlannerDecision`
  - `OpenAICompatibleHypothesisPlanner.propose(...) -> PlannerDecision`
  - `OpenAICompatibleHypothesisPlanner.sanitize(...) -> list[Hypothesis]`

- [ ] **Step 1: Replace the failing planner tests**

```python
# tests/test_planners.py
from metricledger.models import (
    EvidenceObservation,
    EvidenceStatus,
    MetricKind,
    ObservationSummary,
    QuestionKind,
)


def observation(round_number=1, completed=None):
    return ObservationSummary(
        analysis_id="analysis-1",
        metric_id="paid_gmv",
        metric_kind=MetricKind.ADDITIVE,
        question_kind=QuestionKind.INVESTIGATE,
        candidate_dimensions=["region", "channel", "category"],
        completed_dimensions=completed or [],
        round_number=round_number,
        remaining_queries=4,
        remaining_planner_tokens=1000,
        evidence_summaries=[
            EvidenceObservation(
                dimension_id="region",
                status=EvidenceStatus.MATERIAL_CHANGE,
                result_ids=["r1", "r2"],
                material_change=10.0,
                concentration_share=0.3,
                group_count=4,
                top_group="east",
            )
        ],
    )


def test_heuristic_planner_returns_decision_and_zero_usage():
    registry = SemanticRegistry.load_default()

    decision = HeuristicHypothesisPlanner().propose(
        observation(),
        list(registry.bundle.dimensions),
        remaining_queries=4,
        round_number=1,
    )

    assert [item.dimension_id for item in decision.hypotheses] == [
        "region",
        "channel",
        "category",
    ]
    assert decision.usage is not None
    assert decision.usage.total_tokens == 0
    assert decision.attempt.outcome == "HEURISTIC"
    assert decision.attempt.model_called is False


def test_http_planner_filters_completed_unknown_and_duplicate_dimensions():
    def handler(request: httpx.Request) -> httpx.Response:
        content = json.dumps(
            {
                "hypotheses": [
                    {"dimension_id": "region", "rationale_code": "done", "priority": 0},
                    {"dimension_id": "channel", "rationale_code": "ok", "priority": 0},
                    {"dimension_id": "channel", "rationale_code": "dup", "priority": 1},
                    {"dimension_id": "invented", "rationale_code": "bad", "priority": 2},
                    {"dimension_id": "category", "rationale_code": "ok", "priority": 2},
                ]
            }
        )
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": content}}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                },
            },
        )

    planner = OpenAICompatibleHypothesisPlanner(
        base_url="https://planner.example/v1",
        model="test-model",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    registry = SemanticRegistry.load_default()

    decision = planner.propose(
        observation(round_number=2, completed=["region"]),
        list(registry.bundle.dimensions),
        remaining_queries=4,
        round_number=2,
    )

    assert [item.dimension_id for item in decision.hypotheses] == [
        "channel",
        "category",
    ]
    assert decision.usage == ModelUsage(
        prompt_tokens=100,
        completion_tokens=20,
        total_tokens=120,
    )
    assert decision.attempt.outcome == "MODEL_SUCCESS"
    assert decision.attempt.usage_complete is True


def test_http_planner_marks_missing_usage_incomplete():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "hypotheses": [
                                        {
                                            "dimension_id": "region",
                                            "rationale_code": "ok",
                                            "priority": 0,
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ]
            },
        )

    planner = OpenAICompatibleHypothesisPlanner(
        base_url="https://planner.example/v1",
        model="test-model",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    decision = planner.propose(
        observation(),
        list(SemanticRegistry.load_default().bundle.dimensions),
        remaining_queries=4,
        round_number=1,
    )

    assert decision.usage is None
    assert decision.attempt.usage_complete is False
```

Retain the existing timeout, HTTP-error, invalid-JSON, and all-invalid-fallback tests, changing assertions from `planner.last_trace` to `decision.attempt`.

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_planners.py tests\test_benchmarks.py -q
```

Expected: FAIL because planners still return `list[Hypothesis]` and expose mutable `last_trace`.

- [ ] **Step 3: Add `PlannerDecision` and migrate protocol**

```python
# src/metricledger/models.py
class PlannerDecision(Model):
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    attempt: PlannerAttemptTrace
    usage: ModelUsage | None = None
```

```python
# src/metricledger/planners.py
class HypothesisPlanner(Protocol):
    def propose(
        self,
        observation: ObservationSummary,
        allowed_dimensions: list[DimensionDefinition],
        *,
        remaining_queries: int,
        round_number: int,
    ) -> PlannerDecision:
        """Return a bounded diagnostic order with an immutable trace."""


class HeuristicHypothesisPlanner:
    def propose(
        self,
        observation: ObservationSummary,
        allowed_dimensions: list[DimensionDefinition],
        *,
        remaining_queries: int,
        round_number: int,
    ) -> PlannerDecision:
        candidates = set(observation.candidate_dimensions)
        completed = set(observation.completed_dimensions)
        limit = min(3, max(0, remaining_queries // 2))
        hypotheses = [
            Hypothesis(
                dimension_id=dimension.dimension_id,
                rationale_code="semantic_default",
                priority=min(index, 2),
            )
            for index, dimension in enumerate(
                dimension
                for dimension in allowed_dimensions
                if dimension.dimension_id in candidates
                and dimension.dimension_id not in completed
            )
        ][:limit]
        return PlannerDecision(
            hypotheses=hypotheses,
            attempt=PlannerAttemptTrace(
                round_number=round_number,
                outcome="HEURISTIC",
                model_called=False,
                fallback_used=False,
                usage_complete=True,
                selected_dimensions=[
                    item.dimension_id for item in hypotheses
                ],
            ),
            usage=ModelUsage(),
        )
```

For the HTTP planner:

```python
usage_payload = response.json().get("usage")
usage = ModelUsage.model_validate(usage_payload) if usage_payload else None
try:
    raw = response.json()["choices"][0]["message"]["content"]
    raw_hypotheses = json.loads(_strip_code_fence(raw))["hypotheses"]
    hypotheses = self._sanitize(
        raw_hypotheses,
        observation,
        remaining_queries=remaining_queries,
    )
except (KeyError, TypeError, ValueError, json.JSONDecodeError):
    return self._fallback_decision(
        outcome="FALLBACK_INVALID_OUTPUT",
        reason="invalid model response",
        observation=observation,
        allowed_dimensions=allowed_dimensions,
        remaining_queries=remaining_queries,
        round_number=round_number,
        started=started,
        usage=usage,
    )
if hypotheses:
    return PlannerDecision(
        hypotheses=hypotheses,
        attempt=PlannerAttemptTrace(
            round_number=round_number,
            outcome="MODEL_SUCCESS",
            model_called=True,
            latency_ms=_elapsed_ms(started),
            usage_complete=usage is not None,
            selected_dimensions=[
                item.dimension_id for item in hypotheses
            ],
        ),
        usage=usage,
    )
```

Every fallback must call the heuristic planner for the same observation and return one `PlannerDecision`. Remove `last_trace` and the `PlannerTrace` dataclass.

- [ ] **Step 4: Migrate planner benchmark calls**

```python
decision = planner.propose(
    observation,
    dimensions,
    remaining_queries=6,
    round_number=1,
)
hypotheses = decision.hypotheses
trace = decision.attempt
```

Add benchmark counters:

```python
usage_complete_count = 0
planner_tokens = 0
if decision.usage is not None and decision.attempt.usage_complete:
    usage_complete_count += 1
    planner_tokens += decision.usage.total_tokens
```

- [ ] **Step 5: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_planners.py tests\test_benchmarks.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/metricledger/models.py src/metricledger/planners.py tests/test_planners.py scripts/benchmark_planners.py tests/test_benchmarks.py
git commit -m "feat: return structured planner decisions"
```

---

### Task 3: Add Runtime Context Integrity Guard

**Files:**
- Create: `src/metricledger/context_guard.py`
- Create: `tests/test_context_guard.py`

**Interfaces:**
- Consumes: `SemanticRegistry`, `SnapshotRepository`, `AnalysisContext`, `canonical_hash`.
- Produces:
  - `ContextIntegrityError`
  - `ContextIntegrityGuard.verify(context) -> None`

- [ ] **Step 1: Write failing semantic and snapshot drift tests**

```python
# tests/test_context_guard.py
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from metricledger.admission import AdmissionService
from metricledger.context_guard import ContextIntegrityError, ContextIntegrityGuard
from metricledger.models import AnalysisIntent, DateRange, Principal, QuestionKind
from metricledger.semantic import SemanticRegistry
from metricledger.snapshots import SnapshotRepository
from scripts.build_demo_data import DEMO_SNAPSHOTS


def admitted_context(registry):
    principal = Principal(
        principal_id="guard",
        allowed_values={
            dimension.dimension_id: list(dimension.values)
            for dimension in registry.bundle.dimensions
        },
    )
    decision = AdmissionService(
        registry,
        SnapshotRepository.from_records(DEMO_SNAPSHOTS),
    ).admit(
        AnalysisIntent(
            metric_id="paid_gmv",
            question_kind=QuestionKind.INVESTIGATE,
            period=DateRange(
                start=__import__("datetime").date(2026, 11, 17),
                end=__import__("datetime").date(2026, 11, 23),
            ),
            comparison_period=DateRange(
                start=__import__("datetime").date(2026, 11, 10),
                end=__import__("datetime").date(2026, 11, 16),
            ),
        ),
        principal,
        as_of=datetime(
            2026, 11, 23, 23, 59, tzinfo=ZoneInfo("Asia/Shanghai")
        ),
    )
    assert decision.context is not None
    return decision.context


def test_guard_accepts_unchanged_context():
    registry = SemanticRegistry.load_default()
    guard = ContextIntegrityGuard(
        registry,
        SnapshotRepository.from_records(DEMO_SNAPSHOTS),
    )

    guard.verify(admitted_context(registry))


def test_guard_rejects_semantic_drift():
    registry = SemanticRegistry.load_default()
    context = admitted_context(registry)
    changed_metric = registry.metric("paid_gmv").model_copy(
        update={"display_name": "支付GMV（变更）"}
    )
    registry.metrics["paid_gmv"] = changed_metric
    guard = ContextIntegrityGuard(
        registry,
        SnapshotRepository.from_records(DEMO_SNAPSHOTS),
    )

    with pytest.raises(ContextIntegrityError, match="semantic"):
        guard.verify(context)


def test_guard_rejects_snapshot_manifest_drift():
    registry = SemanticRegistry.load_default()
    context = admitted_context(registry)
    records = [dict(record) for record in DEMO_SNAPSHOTS]
    records[-1]["manifest_hash"] = "changed-manifest"
    guard = ContextIntegrityGuard(
        registry,
        SnapshotRepository.from_records(records),
    )

    with pytest.raises(ContextIntegrityError, match="snapshot"):
        guard.verify(context)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_context_guard.py -q
```

Expected: FAIL because `metricledger.context_guard` does not exist.

- [ ] **Step 3: Implement the guard**

```python
# src/metricledger/context_guard.py
from __future__ import annotations

from .models import AnalysisContext, SemanticBundle, canonical_hash
from .semantic import SemanticRegistry
from .snapshots import SnapshotRepository


class ContextIntegrityError(ValueError):
    def __init__(self, kind: str):
        super().__init__(f"{kind} context drift detected")
        self.kind = kind


class ContextIntegrityGuard:
    def __init__(
        self,
        registry: SemanticRegistry,
        snapshots: SnapshotRepository,
    ):
        self.registry = registry
        self.snapshots = snapshots

    def verify(self, context: AnalysisContext) -> None:
        bundle_payload = SemanticBundle.model_validate(
            self.registry.bundle.model_dump(
                mode="json",
                exclude={"canonical_hash"},
            )
        )
        current_hash = canonical_hash(bundle_payload.model_dump(mode="json"))
        if current_hash != context.semantic_bundle_hash:
            raise ContextIntegrityError("semantic")
        try:
            snapshot = self.snapshots.get(context.snapshot_id)
        except ValueError as exc:
            raise ContextIntegrityError("snapshot") from exc
        if snapshot.manifest_hash != context.snapshot_manifest_hash:
            raise ContextIntegrityError("snapshot")
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_context_guard.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add src/metricledger/context_guard.py tests/test_context_guard.py
git commit -m "feat: guard admitted analysis context"
```

---

### Task 4: Implement the Two-Round Investigation Loop

**Files:**
- Modify: `src/metricledger/investigation.py`
- Modify: `tests/test_investigation.py`

**Interfaces:**
- Consumes:
  - `HypothesisPlanner.propose(..., round_number) -> PlannerDecision`
  - `HeuristicHypothesisPlanner`
  - `ContextIntegrityGuard.verify(context)`
  - `ObservationBuilder.build(...)`
  - `ObservationBuilder.step(...)`
- Produces:
  - `InvestigationOutcome.claims`
  - `InvestigationOutcome.artifacts`
  - `InvestigationOutcome.investigation: InvestigationTrace`
  - Stop reasons from the design document.

- [ ] **Step 1: Add failing two-round and safety tests**

```python
# tests/test_investigation.py
from metricledger.models import Hypothesis, ModelUsage, PlannerAttemptTrace, PlannerDecision
from metricledger.planners import HeuristicHypothesisPlanner


class RecordingPlanner:
    def __init__(self):
        self.calls = []

    def propose(
        self,
        observation,
        allowed_dimensions,
        *,
        remaining_queries,
        round_number,
    ):
        self.calls.append(observation)
        if round_number == 1:
            dimension = "channel"
        else:
            assert observation.evidence
            assert "channel" in observation.completed_dimensions
            dimension = "category"
        return PlannerDecision(
            hypotheses=[
                Hypothesis(
                    dimension_id=dimension,
                    rationale_code="follow_up",
                    priority=0,
                )
            ],
            attempt=PlannerAttemptTrace(
                round_number=round_number,
                outcome="MODEL_SUCCESS",
                model_called=True,
                usage_complete=True,
                selected_dimensions=[dimension],
            ),
            usage=ModelUsage(
                prompt_tokens=10,
                completion_tokens=2,
                total_tokens=12,
            ),
        )


def test_two_round_planner_uses_round_one_evidence_and_never_third_round(
    registry,
    database,
    principal,
):
    planner = RecordingPlanner()
    service = make_service(
        registry,
        database,
        AnalysisIntent(
            metric_id="paid_gmv",
            question_kind=QuestionKind.INVESTIGATE,
            period=CURRENT,
            comparison_period=PREVIOUS,
        ),
        principal,
        BudgetPolicy(max_queries=6),
        planner=planner,
    )

    outcome = service.investigate()

    assert len(planner.calls) == 2
    assert planner.calls[1].round_number == 2
    assert planner.calls[1].evidence
    assert outcome.queries_used == 4
    assert outcome.investigation.stop_reason in {
        "concentrated_contribution",
        "planner_round_limit",
        "no_remaining_candidates",
    }
    assert len(outcome.investigation.rounds) == 2


def test_second_round_filters_completed_dimensions(
    registry,
    database,
    principal,
):
    class RepeatPlanner(RecordingPlanner):
        def propose(self, observation, allowed_dimensions, **kwargs):
            self.calls.append(observation)
            return super().propose(
                observation,
                allowed_dimensions,
                **kwargs,
            ).model_copy(
                update={
                    "hypotheses": [
                        Hypothesis(
                            dimension_id="channel",
                            rationale_code="repeat",
                            priority=0,
                        )
                    ]
                }
            )

    planner = RepeatPlanner()
    service = make_service(
        registry,
        database,
        AnalysisIntent(
            metric_id="paid_gmv",
            question_kind=QuestionKind.INVESTIGATE,
            period=CURRENT,
            comparison_period=PREVIOUS,
            candidate_dimensions=["channel"],
        ),
        principal,
        BudgetPolicy(max_queries=6),
        planner=planner,
    )

    outcome = service.investigate()

    assert outcome.queries_used == 2
    assert outcome.investigation.rounds[1].steps == []
    assert outcome.investigation.stop_reason == "all_candidates_observed"


def test_missing_usage_uses_deterministic_second_round(
    registry,
    database,
    principal,
):
    calls = 0

    class NoUsagePlanner(RecordingPlanner):
        def propose(self, observation, allowed_dimensions, **kwargs):
            nonlocal calls
            calls += 1
            decision = super().propose(
                observation,
                allowed_dimensions,
                **kwargs,
            )
            return decision.model_copy(update={"usage": None}).model_copy(
                update={
                    "attempt": decision.attempt.model_copy(
                        update={"usage_complete": False}
                    )
                }
            )

    service = make_service(
        registry,
        database,
        AnalysisIntent(
            metric_id="paid_gmv",
            question_kind=QuestionKind.INVESTIGATE,
            period=CURRENT,
            comparison_period=PREVIOUS,
        ),
        principal,
        BudgetPolicy(max_queries=6),
        planner=NoUsagePlanner(),
    )

    outcome = service.investigate()

    assert calls == 1
    assert outcome.investigation.rounds[1].planner.outcome == "HEURISTIC"
    assert outcome.investigation.rounds[1].planner.model_called is False
```

Update `make_service` to accept a planner and construct:

```python
guard = ContextIntegrityGuard(
    registry,
    SnapshotRepository.from_records(DEMO_SNAPSHOTS),
)
return InvestigationService(
    registry=registry,
    compiler=QueryCompiler(registry),
    engine=engine,
    planner=planner or HeuristicHypothesisPlanner(),
    context=decision.context,
    guard=guard,
    snapshots=SnapshotRepository.from_records(DEMO_SNAPSHOTS),
)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_investigation.py -q
```

Expected: FAIL because `InvestigationService` still calls one planner and breaks on the first non-concentrated dimension.

- [ ] **Step 3: Implement the state machine**

Use this control flow:

```python
def investigate(self) -> InvestigationOutcome:
    metric = self.registry.metric(self.context.metric_id)
    candidates = self._candidate_dimensions(metric)
    completed: list[str] = []
    all_steps: list[InvestigationStepTrace] = []
    rounds: list[InvestigationRoundTrace] = []
    claims: list[Claim] = []
    top_dimension = None
    planner_tokens_used: int | None = 0
    planner_usage_complete = True
    stop_reason = "no_remaining_candidates"
    completeness = Completeness.NONE

    for round_number in range(1, self.context.budget.max_planner_rounds + 1):
        remaining = [
            dimension
            for dimension in candidates
            if dimension.dimension_id not in completed
        ]
        if not remaining:
            stop_reason = "all_candidates_observed"
            break
        if self._remaining_queries() < 2:
            stop_reason = "budget_exhausted"
            break
        if self._time_exhausted():
            stop_reason = "time_budget_exhausted"
            break
        if planner_usage_complete and (
            planner_tokens_used is not None
            and planner_tokens_used >= self.context.budget.max_planner_tokens
        ):
            stop_reason = "planner_token_budget_exhausted"
            break
        try:
            self.guard.verify(self.context)
        except ContextIntegrityError:
            stop_reason = "context_drift"
            break

        builder = ObservationBuilder(metric.kind)
        observation_summary = builder.build(
            self.context,
            round_number=round_number,
            steps=all_steps,
            completed_dimensions=completed,
            remaining_queries=self._remaining_queries(),
            remaining_planner_tokens=(
                self.context.budget.max_planner_tokens
                - planner_tokens_used
                if planner_usage_complete
                and planner_tokens_used is not None
                else None
            ),
        )
        decision = self._propose(
            observation_summary,
            remaining,
            round_number=round_number,
            planner_usage_complete=planner_usage_complete,
        )
        round_steps: list[InvestigationStepTrace] = []
        round_complete = False
        for hypothesis in decision.hypotheses:
            if hypothesis.dimension_id not in {
                item.dimension_id for item in remaining
            }:
                continue
            if self._remaining_queries() < 2:
                stop_reason = "budget_exhausted"
                break
            try:
                self.guard.verify(self.context)
            except ContextIntegrityError:
                stop_reason = "context_drift"
                break
            result = self._diagnose(dimension_id=hypothesis.dimension_id)
            round_steps.append(result.step)
            all_steps.append(result.step)
            completed.append(hypothesis.dimension_id)
            claims.extend(result.claims)
            if result.top_dimension is not None:
                top_dimension = result.top_dimension
            if result.stop_reason is not None:
                stop_reason = result.stop_reason
                round_complete = True
                break

        if decision.usage is not None and planner_usage_complete:
            planner_tokens_used = (
                (planner_tokens_used or 0) + decision.usage.total_tokens
            )
        elif decision.usage is None:
            planner_usage_complete = False
            planner_tokens_used = None

        rounds.append(
            InvestigationRoundTrace(
                round_number=round_number,
                observation=observation_summary,
                planner=decision.attempt,
                steps=round_steps,
            )
        )
        if round_complete or stop_reason == "context_drift":
            break

    remaining_dimensions = [
        dimension.dimension_id
        for dimension in candidates
        if dimension.dimension_id not in completed
    ]
    if stop_reason == "concentrated_contribution":
        completeness = Completeness.COMPLETE
    elif stop_reason == "context_drift":
        completeness = Completeness.NONE
    elif completed:
        completeness = Completeness.PARTIAL
    if (
        stop_reason == "no_remaining_candidates"
        and len(rounds) == self.context.budget.max_planner_rounds
    ):
        stop_reason = "planner_round_limit"

    trace = InvestigationTrace(
        rounds=rounds,
        completed_dimensions=completed,
        remaining_dimensions=remaining_dimensions,
        stop_reason=stop_reason,
        planner_fallback_used=any(
            round_trace.planner.fallback_used for round_trace in rounds
        ),
    )
    return InvestigationOutcome(
        analysis_id=self.context.analysis_id,
        completeness=completeness,
        stop_reason=stop_reason,
        queries_used=self._queries_used,
        top_dimension=top_dimension,
        claims=claims,
        artifacts=self._artifacts,
        issue_codes=self._issue_codes,
        planner_tokens_used=planner_tokens_used,
        planner_usage_complete=planner_usage_complete,
        investigation=trace,
    )
```

Implement `_diagnose` so a dimension produces one `InvestigationStepTrace`, continues after failure/no-change, and only sets `stop_reason` for:

```python
if metric.kind is MetricKind.RATIO and current and baseline and common_groups:
    status = EvidenceStatus.MATERIAL_CHANGE
    stop_reason = "ratio_diagnostic_complete"
elif metric.kind is MetricKind.ADDITIVE and concentration >= 0.5:
    status = EvidenceStatus.MATERIAL_CHANGE
    stop_reason = "concentrated_contribution"
elif metric.kind is MetricKind.ADDITIVE and total_absolute_delta > 0:
    status = EvidenceStatus.MATERIAL_CHANGE
    stop_reason = None
else:
    status = EvidenceStatus.NO_MATERIAL_CHANGE
    stop_reason = None
```

If current query fails:

```python
step_status = EvidenceStatus.QUERY_FAILED
if "DIAGNOSTIC_QUERY_FAILED" not in self._issue_codes:
    self._issue_codes.append("DIAGNOSTIC_QUERY_FAILED")
claims = []
stop_reason = None
```

Do not execute baseline after current failure.

When usage is incomplete before round 2:

```python
if not planner_usage_complete:
    decision = self.fallback_planner.propose(
        observation_summary,
        remaining_dimensions,
        remaining_queries=self._remaining_queries(),
        round_number=round_number,
    )
```

Use the same planner decision for round 1. Never call the configured planner after `planner_usage_complete` becomes false.

- [ ] **Step 4: Add stop-reason and pair-reservation tests**

```python
def test_failed_current_query_does_not_consume_comparison_budget(
    registry,
    database,
    principal,
    monkeypatch,
):
    service = additive_service(
        registry,
        database,
        principal,
        BudgetPolicy(max_queries=6),
    )
    calls = 0
    original = service.engine.execute

    def fail_current(compiled, snapshot_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise QueryExecutionError("current failed")
        return original(compiled, snapshot_id)

    monkeypatch.setattr(service.engine, "execute", fail_current)

    outcome = service.investigate()

    assert outcome.queries_used < 2
    assert outcome.investigation.rounds[0].steps[0].status is EvidenceStatus.QUERY_FAILED


def test_single_remaining_query_does_not_start_dimension_pair(...):
    service = additive_service(
        registry,
        database,
        principal,
        BudgetPolicy(max_queries=3),
        initial_queries_used=2,
    )

    outcome = service.investigate()

    assert outcome.queries_used == 2
    assert outcome.investigation.stop_reason == "budget_exhausted"
    assert outcome.investigation.rounds[0].steps == []
```

- [ ] **Step 5: Run focused investigation tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_investigation.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/metricledger/investigation.py tests/test_investigation.py
git commit -m "feat: execute bounded two-round investigations"
```

---

### Task 5: Integrate the Guard, Trace, and Budget into the Service

**Files:**
- Modify: `src/metricledger/service.py`
- Modify: `src/metricledger/models.py`
- Modify: `tests/test_service.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Consumes: `ContextIntegrityGuard`, `InvestigationTrace`, `BudgetUsage`, `InvestigationService`.
- Produces:
  - `AnalysisResponse.investigation`
  - Real `BudgetUsage.planner_tokens_used`
  - Real `BudgetUsage.planner_usage_complete`
  - Wall-clock `BudgetUsage.elapsed_ms`
  - Guarded headline and diagnostic queries.

- [ ] **Step 1: Write failing service integration tests**

```python
def test_service_exposes_investigation_trace_and_planner_usage(
    service,
    analysis_request,
):
    response = service.analyze(
        analysis_request.model_copy(
            update={"budget": BudgetPolicy(max_queries=6)}
        )
    )

    assert response.investigation is not None
    assert response.investigation.stop_reason
    assert len(response.investigation.rounds) >= 1
    assert response.budget_usage.planner_usage_complete is True
    assert response.budget_usage.planner_tokens_used == 0
    assert response.budget_usage.elapsed_ms >= 0


def test_drift_clears_answer_and_claims_but_keeps_sql_audit(
    service,
    analysis_request,
    monkeypatch,
):
    from metricledger.context_guard import ContextIntegrityError

    def drifted(self, context):
        raise ContextIntegrityError("snapshot")

    monkeypatch.setattr(
        service.guard,
        "verify",
        drifted.__get__(service.guard, type(service.guard)),
    )

    response = service.analyze(analysis_request)

    assert response.completeness is Completeness.NONE
    assert response.answer is None
    assert response.claims == []
    assert any(issue.code == "CONTEXT_DRIFT" for issue in response.issues)
    assert response.sql_audit
    assert response.investigation.stop_reason == "context_drift"
```

```python
def test_api_serializes_investigation_trace(client, request_payload):
    response = client.post(
        "/api/analyses",
        json={
            **request_payload,
            "budget": {"max_queries": 6},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["investigation"]["rounds"]
    assert body["investigation"]["stop_reason"]
    assert "planner_usage_complete" in body["budget_usage"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_service.py tests\test_api.py -q
```

Expected: FAIL because the service returns no investigation trace and hard-codes planner Token usage to `0`.

- [ ] **Step 3: Implement service integration**

Add to `AnalysisService.__init__`:

```python
self.guard = ContextIntegrityGuard(registry, snapshots)
self.clock = clock or time.monotonic
```

At the start of `analyze`:

```python
started = self.clock()
```

Before every headline or comparison query:

```python
try:
    self.guard.verify(context)
except ContextIntegrityError:
    response = self._context_drift_response(
        context=context,
        decision=decision,
        artifacts=artifacts,
        audits=audits,
    )
    return self._persist(response)
```

Pass guard and clock into `InvestigationService`:

```python
investigation = InvestigationService(
    registry=self.registry,
    compiler=compiler,
    engine=self.engine,
    planner=self.planner,
    context=context,
    initial_artifacts=artifacts,
    initial_queries_used=queries_used,
    guard=self.guard,
    snapshots=self.snapshots,
    started_monotonic=started,
    clock=self.clock,
).investigate()
```

For a completed investigation:

```python
if investigation.investigation.stop_reason == "context_drift":
    return self._persist(
        self._context_drift_response(
            context=context,
            decision=decision,
            artifacts=investigation.artifacts,
            audits=[
                SqlAudit(
                    result_id=artifact.result_id,
                    sql=artifact.compiled_sql,
                    parameters=artifact.compiled_parameters,
                    fingerprint=artifact.sql_fingerprint,
                    row_count=artifact.row_count,
                    snapshot_id=artifact.snapshot_id,
                    elapsed_ms=artifact.elapsed_ms,
                )
                for artifact in investigation.artifacts
            ],
            investigation=investigation.investigation,
        )
    )
```

Set response budget values from the trace:

```python
planner_usage_complete = investigation.planner_usage_complete
planner_tokens_used = investigation.planner_tokens_used
```

`InvestigationOutcome` owns the accumulated planner usage. The service copies
those values directly and never re-derives them from display traces.

Set:

```python
budget_usage=BudgetUsage(
    queries_used=queries_used,
    planner_tokens_used=planner_tokens_used,
    planner_usage_complete=planner_usage_complete,
    elapsed_ms=max(0, round((self.clock() - started) * 1000)),
),
investigation=(
    investigation.investigation
    if context.intent.question_kind is QuestionKind.INVESTIGATE
    else None
),
```

Add `CONTEXT_DRIFT` to `_issues_for_codes`:

```python
"CONTEXT_DRIFT": "语义包或固定快照在分析期间发生变化",
```

- [ ] **Step 4: Run focused integration tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_service.py tests\test_api.py -q
```

Expected: PASS.

- [ ] **Step 5: Run all backend tests to find protocol migration gaps**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Expected: PASS after updating every test or script that still calls the old planner protocol.

- [ ] **Step 6: Commit**

```powershell
git add src/metricledger/models.py src/metricledger/service.py tests/test_service.py tests/test_api.py
git commit -m "feat: expose guarded investigation traces"
```

---

### Task 6: Make SQLite Immutable Writes Atomic and Idempotent

**Files:**
- Modify: `src/metricledger/ledger.py`
- Modify: `tests/test_ledger.py`

**Interfaces:**
- Consumes: `AnalysisResponse`.
- Produces:
  - `SqliteLedger.save(response) -> None`
  - Atomic all-or-nothing analysis, artifact, and edge writes.
  - Idempotent identical writes.

- [ ] **Step 1: Write failing concurrency and idempotency tests**

```python
from concurrent.futures import ThreadPoolExecutor


def test_identical_analysis_write_is_idempotent(ledger, response):
    ledger.save(response)
    ledger.save(response)

    assert ledger.write_count == 2
    assert ledger.write_error_count == 0


def test_concurrent_identical_analysis_writes_are_serialized(ledger, response):
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(ledger.save, response) for _ in range(2)]
        for future in futures:
            future.result()

    assert ledger.get(response.analysis_id) is not None
    assert ledger.write_error_count == 0


def test_concurrent_identical_artifact_writes_create_one_row(
    ledger,
    response,
):
    second = response.model_copy(
        update={
            "analysis_id": "an_second",
            "claims": [],
            "sql_audit": [],
        }
    )
    second = second.model_copy(
        update={"evidence": [response.evidence[0]]}
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(ledger.save, item) for item in (response, second)
        ]
        for future in futures:
            future.result()

    with ledger._connect() as connection:
        count = connection.execute(
            "SELECT count(*) FROM result_artifacts WHERE result_id = ?",
            (response.evidence[0].result_id,),
        ).fetchone()[0]
    assert count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_ledger.py -q
```

Expected: At least one concurrent test raises a unique-constraint or immutable-race error.

- [ ] **Step 3: Implement `BEGIN IMMEDIATE` and conflict resolution**

```python
def _save_impl(self, response: AnalysisResponse) -> None:
    payload = _canonical_json(response.model_dump(mode="json"))
    tenant_id = (
        response.context.tenant_id
        if response.context is not None
        else "local"
    )
    with self._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            self._insert_immutable(
                connection,
                table="analyses",
                key_column="analysis_id",
                key_value=response.analysis_id,
                payload=payload,
                insert_columns=("analysis_id", "tenant_id", "payload"),
                insert_values=(response.analysis_id, tenant_id, payload),
                message="analysis ledger entries are immutable",
            )
            for artifact in response.evidence:
                artifact_payload = _canonical_json(
                    artifact.model_dump(mode="json")
                )
                self._insert_immutable(
                    connection,
                    table="result_artifacts",
                    key_column="result_id",
                    key_value=artifact.result_id,
                    payload=artifact_payload,
                    insert_columns=(
                        "result_id",
                        "analysis_id",
                        "tenant_id",
                        "payload",
                    ),
                    insert_values=(
                        artifact.result_id,
                        response.analysis_id,
                        tenant_id,
                        artifact_payload,
                    ),
                    message="result artifacts are immutable",
                )
            for claim in response.claims:
                for result_id in claim.claim.evidence_refs:
                    connection.execute(
                        """
                        INSERT INTO graph_edges(
                            edge_id, analysis_id, tenant_id,
                            source_id, relation, target_id
                        )
                        VALUES (?, ?, ?, ?, 'SUPPORTED_BY', ?)
                        ON CONFLICT(edge_id) DO NOTHING
                        """,
                        (
                            f"{claim.claim.claim_id}:{result_id}",
                            response.analysis_id,
                            tenant_id,
                            claim.claim.claim_id,
                            result_id,
                        ),
                    )
        except Exception:
            connection.rollback()
            raise
```

Implement `_insert_immutable`:

```python
@staticmethod
def _insert_immutable(
    connection,
    *,
    table: str,
    key_column: str,
    key_value: str,
    payload: str,
    insert_columns: tuple[str, ...],
    insert_values: tuple[str, ...],
    message: str,
) -> None:
    placeholders = ", ".join("?" for _ in insert_columns)
    columns = ", ".join(insert_columns)
    try:
        connection.execute(
            f"INSERT INTO {table}({columns}) VALUES ({placeholders})",
            insert_values,
        )
    except sqlite3.IntegrityError:
        existing = connection.execute(
            f"SELECT payload FROM {table} WHERE {key_column} = ?",
            (key_value,),
        ).fetchone()
        if existing is None or existing[0] != payload:
            raise ValueError(message)
```

The table and column names are private constants supplied only by `_save_impl`; never accept them from request data.

- [ ] **Step 4: Run ledger tests repeatedly**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_ledger.py -q
.\.venv\Scripts\python.exe -m pytest tests\test_ledger.py -q
```

Expected: PASS both times.

- [ ] **Step 5: Commit**

```powershell
git add src/metricledger/ledger.py tests/test_ledger.py
git commit -m "fix: write immutable ledger entries atomically"
```

---

### Task 7: Expose Prometheus Metrics and React Investigation Timeline

**Files:**
- Modify: `src/metricledger/observability.py`
- Modify: `tests/test_runtime.py`
- Modify: `web/src/api.ts`
- Modify: `web/src/App.tsx`
- Modify: `web/src/App.test.tsx`
- Modify: `web/src/styles.css`

**Interfaces:**
- Consumes: `AnalysisResponse.investigation`.
- Produces:
  - `metricledger_planner_calls_total{outcome}`
  - `metricledger_planner_tokens_total`
  - `metricledger_investigation_steps_total{status}`
  - `metricledger_context_drift_total`
  - `InvestigationTimeline` React component.

- [ ] **Step 1: Write failing backend metric tests**

```python
# tests/test_runtime.py
def test_metrics_include_planner_and_investigation_series(response):
    metrics = RequestMetrics()

    metrics.record_analysis(response)
    text = metrics.render()

    assert "metricledger_planner_calls_total" in text
    assert "metricledger_planner_tokens_total" in text
    assert "metricledger_investigation_steps_total" in text
    assert "metricledger_context_drift_total" in text
```

- [ ] **Step 2: Write failing frontend timeline tests**

```tsx
it("renders investigation rounds, step status, fallback, and usage", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({
        analysis_id: "a4",
        admission: { status: "ADMITTED", issue_codes: [] },
        completeness: "PARTIAL",
        answer: null,
        claims: [],
        issues: [],
        context: null,
        budget_usage: {
          queries_used: 4,
          planner_tokens_used: 120,
          planner_usage_complete: true,
          elapsed_ms: 88,
        },
        evidence: [],
        sql_audit: [],
        investigation: {
          stop_reason: "all_candidates_observed",
          completed_dimensions: ["region", "channel"],
          remaining_dimensions: [],
          planner_fallback_used: true,
          rounds: [
            {
              round_number: 1,
              observation: {
                analysis_id: "a4",
                round_number: 1,
                metric_id: "paid_gmv",
                metric_kind: "ADDITIVE",
                question_kind: "INVESTIGATE",
                candidate_dimensions: ["region", "channel"],
                completed_dimensions: [],
                remaining_queries: 4,
                remaining_planner_tokens: 1880,
                evidence: [],
              },
              planner: {
                round_number: 1,
                outcome: "MODEL_SUCCESS",
                model_called: true,
                fallback_used: false,
                fallback_reason: null,
                latency_ms: 42,
                usage_complete: true,
                selected_dimensions: ["region"],
              },
              steps: [
                {
                  round_number: 1,
                  dimension_id: "region",
                  status: "NO_MATERIAL_CHANGE",
                  result_ids: ["r1", "r2"],
                  material_change: 0,
                  concentration_share: null,
                  group_count: 4,
                  top_group: null,
                  issue_codes: [],
                },
              ],
            },
            {
              round_number: 2,
              observation: {
                analysis_id: "a4",
                round_number: 2,
                metric_id: "paid_gmv",
                metric_kind: "ADDITIVE",
                question_kind: "INVESTIGATE",
                candidate_dimensions: ["region", "channel"],
                completed_dimensions: ["region"],
                remaining_queries: 2,
                remaining_planner_tokens: 1880,
                evidence: [],
              },
              planner: {
                round_number: 2,
                outcome: "FALLBACK_TIMEOUT",
                model_called: true,
                fallback_used: true,
                fallback_reason: "timeout",
                latency_ms: 10000,
                usage_complete: false,
                selected_dimensions: ["channel"],
              },
              steps: [],
            },
          ],
        },
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  );

  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "开始调查" }));

  expect(await screen.findByText("调查轨迹")).toBeInTheDocument();
  expect(screen.getByText("第 1 轮")).toBeInTheDocument();
  expect(screen.getByText("第 2 轮")).toBeInTheDocument();
  expect(screen.getByText("FALLBACK_TIMEOUT")).toBeInTheDocument();
  expect(screen.getByText("NO_MATERIAL_CHANGE")).toBeInTheDocument();
  expect(screen.getByText(/Token 120/)).toBeInTheDocument();
});
```

- [ ] **Step 3: Implement metrics**

Add counters:

```python
self._planner_calls: dict[str, int] = defaultdict(int)
self._planner_tokens = 0
self._investigation_steps: dict[str, int] = defaultdict(int)
self._context_drift = 0
```

In `record_analysis`:

```python
investigation = response.investigation
if investigation is not None:
    for round_trace in investigation.rounds:
        self._planner_calls[round_trace.planner.outcome] += 1
        for step in round_trace.steps:
            self._investigation_steps[step.status.value] += 1
    if response.budget_usage.planner_usage_complete:
        self._planner_tokens += response.budget_usage.planner_tokens_used or 0
    if investigation.stop_reason == "context_drift":
        self._context_drift += 1
```

Render exact Prometheus lines with labels escaped through `_escape`.

- [ ] **Step 4: Implement frontend types and timeline**

```ts
// web/src/api.ts
export interface InvestigationStepPayload {
  round_number: number;
  dimension_id: string;
  status:
    | "MATERIAL_CHANGE"
    | "NO_MATERIAL_CHANGE"
    | "NO_COMPARABLE_GROUPS"
    | "QUERY_FAILED";
  result_ids: string[];
  material_change: number | null;
  concentration_share: number | null;
  group_count: number;
  top_group: string | null;
  issue_codes: string[];
}

export interface InvestigationRoundPayload {
  round_number: number;
  planner: {
    round_number: number;
    outcome: string;
    model_called: boolean;
    fallback_used: boolean;
    fallback_reason: string | null;
    latency_ms: number;
    usage_complete: boolean;
    selected_dimensions: string[];
  };
  steps: InvestigationStepPayload[];
  observation: Record<string, unknown>;
}

export interface InvestigationPayload {
  rounds: InvestigationRoundPayload[];
  completed_dimensions: string[];
  remaining_dimensions: string[];
  stop_reason: string;
  planner_fallback_used: boolean;
}
```

Add `investigation?: InvestigationPayload | null` to `AnalysisPayload` and `planner_usage_complete?: boolean` to `budget_usage`.

Add a compact section after `receipt-strip`:

```tsx
{analysis.investigation ? (
  <section className="investigation-card" data-testid="investigation-trace">
    <div className="section-title">
      <div>
        <p className="eyebrow">BOUNDED INVESTIGATION</p>
        <h3>调查轨迹</h3>
      </div>
      <code>{analysis.investigation.stop_reason}</code>
    </div>
    <div className="round-list">
      {analysis.investigation.rounds.map((round) => (
        <article key={round.round_number} className="round-card">
          <header>
            <strong>第 {round.round_number} 轮</strong>
            <span>{round.planner.outcome}</span>
            <small>{round.planner.latency_ms.toFixed(1)} ms</small>
          </header>
          <p>
            选择：
            {round.planner.selected_dimensions.join(" / ") || "无"}
            {round.planner.fallback_used
              ? ` · 已回退：${round.planner.fallback_reason ?? "未知原因"}`
              : ""}
          </p>
          {round.steps.map((step) => (
            <div key={`${round.round_number}-${step.dimension_id}`}>
              <code>{step.dimension_id}</code>
              <span>{step.status}</span>
              <small>{step.result_ids.length} 份证据</small>
            </div>
          ))}
        </article>
      ))}
    </div>
    <p className="round-budget">
      Token {analysis.budget_usage.planner_tokens_used ?? "未知"} ·{" "}
      {analysis.budget_usage.planner_usage_complete
        ? "用量完整"
        : "用量不完整，后续不再调用模型"}
    </p>
  </section>
) : null}
```

Style it as a single horizontal timeline on desktop and stacked cards below 720 px. Reuse the established paper, line, navy, and acid design tokens.

- [ ] **Step 5: Run focused runtime and frontend tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_runtime.py -q
npm --prefix web test -- --run
npm --prefix web run build
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add src/metricledger/observability.py tests/test_runtime.py web/src/api.ts web/src/App.tsx web/src/App.test.tsx web/src/styles.css
git commit -m "feat: surface investigation traces and metrics"
```

---

### Task 8: Expand Evaluation, Run End-to-End Verification, and Update Evidence

**Files:**
- Modify: `eval/scenarios.json`
- Modify: `tests/test_evaluation.py`
- Modify: `scripts/benchmark_planners.py`
- Modify: `scripts/container_smoke.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `docs/verification/acceptance.md`
- Regenerate: `eval/results.json`
- Regenerate: `docs/verification/eval-report.md`
- Regenerate: `docs/verification/planner-results.json`
- Regenerate: `docs/verification/planner-report.md`

- [ ] **Step 1: Add nine deterministic scenarios**

Add one question to each category/group below:

```json
{
  "category": "supported_investigation",
  "principal": "full",
  "as_of": "2026-11-23T23:59:59+08:00",
  "expected_status": "ADMITTED",
  "expected_completeness": "COMPLETE",
  "requires_investigation": true,
  "questions": [
    "2026-11-17 到 2026-11-23 支付GMV下降原因，排查渠道、品类"
  ]
}
```

Because the existing file groups questions by shared settings, append these actual questions:

```text
supported_investigation:
2026-11-17 到 2026-11-23 支付GMV下降原因，排查渠道、品类
2026-11-17 到 2026-11-23 支付GMV下降原因，排查区域、渠道
2026-11-18 到 2026-11-23 订单取消率上升原因，排查区域、品类

supported_value_or_change:
2026-11-17 到 2026-11-23 支付GMV与上一周期相比是多少
2026-11-17 到 2026-11-23 订单取消率相比上一周期变化多少

scope_refusal:
2026-11-18 到 2026-11-23 支付GMV下降原因，排查区域、渠道

uncovered_period:
2026-12-02 到 2026-12-08 订单取消率为什么上升

clarification:
2026-11-17 到 2026-11-23 核心指标为什么变化
```

If one phrasing is rejected by the deterministic parser, replace it with another unique valid question in the same category and record the actual final count from `scripts/run_eval.py`; do not modify expected labels to hide a semantic failure.

- [ ] **Step 2: Update evaluation assertions**

```python
def test_suite_contains_at_least_seventy_two_unique_scenarios(suite):
    assert len(suite.scenarios) >= 72
```

```python
def test_report_reports_adaptive_window():
    assert "max_planner_rounds" in report.to_markdown()
```

Add `max_planner_rounds=2` and `budget_exhausted` stop-reason counts to `EvaluationReport.environment` and markdown.

- [ ] **Step 3: Run the full backend suite with coverage**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q --cov=metricledger --cov-report=term
```

Expected: all tests pass and `TOTAL` coverage is at least 93%. If coverage falls below 93%, add behavior tests for the uncovered adaptive branches, not `# pragma: no cover` exclusions.

- [ ] **Step 4: Run frontend and dependency checks**

Run:

```powershell
npm --prefix web test -- --run
npm --prefix web run build
npm --prefix web audit --audit-level=high
```

Expected: tests and build pass; audit reports zero high-severity vulnerabilities.

- [ ] **Step 5: Regenerate evaluation evidence**

Run:

```powershell
.\.venv\Scripts\python.exe scripts\run_eval.py
```

Expected:

- scenario count is at least 72;
- `metricledger.scenario_success_count == scenario_count`;
- unsafe, scope, zero-as-empty, unsupported Claim, evidence, and budget violations are all zero.

- [ ] **Step 6: Run planner benchmark and optional real-model smoke**

Run:

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_planners.py
```

If `METRICLEDGER_PLANNER_BASE_URL` and `METRICLEDGER_PLANNER_MODEL` are configured, run one full two-round analysis through `scripts\ask.py` or the HTTP API and verify:

```text
investigation.rounds length is 2
round 1 selected at least one dimension
round 2 observation contains round 1 evidence
planner_tokens_used is a non-negative integer
planner_usage_complete is true
```

If credentials are absent, the report and acceptance record must state `NOT_RUN`. Do not claim model quality or latency in that case.

- [ ] **Step 7: Run CLI and container smoke assertions**

Update `scripts/container_smoke.py`:

```python
assert analysis["investigation"]["rounds"]
assert analysis["budget_usage"]["planner_usage_complete"] is True
assert analysis["budget_usage"]["planner_tokens_used"] >= 0
```

Run:

```powershell
.\.venv\Scripts\python.exe scripts\ask.py --json
```

Then run container smoke through the CI workflow after pushing. Local container execution is required only if Docker is available; otherwise record `NOT_RUN` for the local command and rely on the actual GitHub Actions container job.

- [ ] **Step 8: Update CI scenario gate**

```yaml
- name: Enforce scenario count and safety gates
  run: |
    python - <<'PY'
    import json
    from pathlib import Path
    report = json.loads(Path("eval/results.json").read_text(encoding="utf-8"))
    count = report["scenario_count"]
    assert count >= 72, count
    metrics = report["baselines"]["metricledger"]
    assert metrics["scenario_success_count"] == count
    assert metrics["unsafe_answer_count"] == 0
    assert metrics["scope_violation_count"] == 0
    assert metrics["zero_as_empty_count"] == 0
    assert metrics["unsupported_claim_count"] == 0
    assert metrics["evidence_violation_count"] == 0
    assert metrics["budget_violation_count"] == 0
    print(f"scenario and safety gates passed: {count}")
    PY
```

- [ ] **Step 9: Update acceptance evidence only after commands finish**

Record exact:

- commit SHA;
- backend pass count and coverage;
- frontend test count;
- exact scenario count and success count;
- planner real-model status (`RUN` or `NOT_RUN`);
- CI run URL and four job statuses.

Do not preserve stale `110 passed`, `63`, `93%`, or old CI run identifiers from before this implementation unless the fresh commands produce the same values.

- [ ] **Step 10: Commit and push**

```powershell
git add eval/scenarios.json eval/results.json tests/test_evaluation.py scripts/benchmark_planners.py scripts/container_smoke.py .github/workflows/ci.yml README.md docs/verification/acceptance.md docs/verification/eval-report.md docs/verification/planner-results.json docs/verification/planner-report.md
git commit -m "test: verify adaptive investigation end to end"
git push origin feat/metricledger-core
```

- [ ] **Step 11: Wait for CI and fix only evidenced failures**

Open the new GitHub Actions run and verify:

```text
backend: success
frontend: success
config: success
container: success
```

When the run completes, append the exact run URL to `docs/verification/acceptance.md`, commit the documentation-only update, push, and wait for the follow-up run. The final branch must have a clean worktree and all four jobs green.

---

## Self-Review

- [ ] **Spec coverage:** Each design section maps to Tasks 1-8: models/observations to Task 1, planner protocol to Task 2, drift guard to Task 3, two-round state machine to Task 4, service/API and budget to Task 5, atomic ledger to Task 6, metrics/React to Task 7, and 72-scenario verification to Task 8.
- [ ] **Placeholder scan:** The implementation executes concrete snippets, exact commands, and explicit expected outcomes. Any parser-incompatible scenario question must be replaced by an equivalent same-category question and the actual final count recorded.
- [ ] **Type consistency:** `PlannerDecision`, `PlannerAttemptTrace.usage_complete`, `EvidenceStatus`, `InvestigationTrace`, `planner_tokens_used: int | None`, and `max_planner_rounds` use one spelling across models, service, tests, benchmark, metrics, and frontend.
- [ ] **Security coverage:** Planner inputs exclude raw rows, SQL, tenant IDs, and sensitive top groups. Context drift clears answers and Claims while preserving SQL audit. Ledger conflicts roll back the entire transaction.
- [ ] **Global review:** The plan does not exceed two planner rounds, does not introduce distributed infrastructure, and keeps all existing admission, SQL, evidence, and evaluation safety gates.
