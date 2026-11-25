# MetricLedger Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a runnable trusted ChatBI and metric-investigation vertical slice with admission gates, semantic DSL compilation, fixed snapshots, Claim-level evidence validation, a React workbench, and reproducible evaluation.

**Architecture:** A modular Python service separates semantic resolution, admission, SQL compilation, DuckDB execution, bounded investigation, evidence validation, and audit persistence. A React frontend consumes one analysis API and renders decisions, Claims, evidence, limitations, and SQL audit without becoming a second source of truth.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic 2, DuckDB, SQLGlot, SQLite, React 19, TypeScript, Vite, pytest.

**Spec:** `docs/superpowers/specs/2026-11-25-metricledger-design.md`

## Global Constraints

- Business queries accept only semantic `AnalysisIntent`; raw user SQL is never accepted.
- Every formal analysis is bound to one immutable snapshot and one semantic bundle hash.
- LLMs cannot create metrics, dimensions, tables, fields, filter expressions, SQL, or numbers.
- Claim support is computed by deterministic validation, never self-reported by a model.
- Additive metrics may use arithmetic contribution; ratio metrics may only use diagnostic claims.
- Technical failures and missing records never become business zero.
- No API key is persisted in files, SQLite, logs, or responses.
- Python tests use real DuckDB and SQLite instances; mocks are limited to external HTTP and planner failure injection.
- Every production behavior starts with a failing test and is committed after the focused test passes.

---

### Task 1: Python Project and Domain Models

**Files:**
- Create: `pyproject.toml`
- Create: `src/metricledger/__init__.py`
- Create: `src/metricledger/models.py`
- Create: `tests/test_models.py`

**Interfaces:**
- Produces: `AdmissionStatus`, `Completeness`, `ClaimSupport`, `MetricKind`, `MetricDefinition`, `SemanticBundle`, `AnalysisIntent`, `AnalysisContext`, `AnalysisIssue`, `Claim`, `ResultArtifact`, `AnalysisResponse`.
- Produces: `canonical_hash(value: object) -> str`.

- [ ] **Step 1: Write failing model tests**

```python
from metricledger.models import AdmissionStatus, ClaimSupport, MetricKind, canonical_hash


def test_enums_have_stable_wire_values():
    assert AdmissionStatus.ADMITTED.value == "ADMITTED"
    assert ClaimSupport.SUPPORTED.value == "SUPPORTED"
    assert MetricKind.RATIO.value == "RATIO"


def test_canonical_hash_is_independent_of_mapping_order():
    assert canonical_hash({"b": 2, "a": 1}) == canonical_hash({"a": 1, "b": 2})
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_models.py -q`

Expected: import failure for `metricledger.models`.

- [ ] **Step 3: Implement project and models**

Create package metadata with minimum versions:

```toml
[project]
name = "metricledger"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
  "duckdb>=1.1,<2",
  "fastapi>=0.115,<1",
  "pydantic>=2.10,<3",
  "sqlglot>=26,<27",
  "uvicorn>=0.32,<1",
]

[project.optional-dependencies]
dev = ["pytest>=8,<9", "httpx>=0.28,<1"]
```

`canonical_hash` serializes JSON with `sort_keys=True`, `separators=(",", ":")`, UTF-8 encoding, then returns a SHA-256 hex digest. Pydantic API models use extra fields forbidden.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/test_models.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/metricledger tests/test_models.py
git commit -m "feat: add MetricLedger domain models"
```

### Task 2: Semantic Bundle and Snapshot Admission

**Files:**
- Create: `src/metricledger/semantic.py`
- Create: `src/metricledger/snapshots.py`
- Create: `src/metricledger/resources/ecommerce_v1.json`
- Create: `src/metricledger/admission.py`
- Create: `tests/test_semantic.py`
- Create: `tests/test_admission.py`

**Interfaces:**
- Produces: `SemanticRegistry.from_mapping(data: dict) -> SemanticRegistry`.
- Produces: `SemanticRegistry.resolve_metric(text: str) -> list[MetricDefinition]`.
- Produces: `SnapshotRepository.from_records(records: list[dict]) -> SnapshotRepository`.
- Produces: `SnapshotRepository.select(as_of: datetime, period: DateRange) -> SnapshotRecord`.
- Produces: `AdmissionService.admit(intent, principal, as_of) -> AdmissionDecision`.

- [ ] **Step 1: Write failing semantic and admission tests**

```python
def test_metric_alias_ambiguity_returns_both_metrics(registry):
    assert [m.metric_id for m in registry.resolve_metric("转化")] == [
        "paid_conversion_rate",
        "cancel_rate",
    ]


def test_admission_rejects_snapshot_that_does_not_cover_comparison(admission, intent):
    decision = admission.admit(intent, full_principal, as_of=datetime(2026, 10, 2, tzinfo=TZ))
    assert decision.status is AdmissionStatus.UNAVAILABLE
    assert decision.issue_codes == ["COMPARISON_NOT_COVERED"]


def test_admission_refuses_implicit_scope_narrowing(admission, intent):
    east_only = full_principal.model_copy(update={"allowed_regions": ["east"]})
    decision = admission.admit(intent, east_only, as_of=DEMO_AS_OF)
    assert decision.status is AdmissionStatus.REFUSED
    assert "east,west,north,south" not in decision.effective_scope.regions
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_semantic.py tests/test_admission.py -q`

Expected: modules do not exist.

- [ ] **Step 3: Implement semantic and snapshot rules**

The e-commerce bundle defines `paid_gmv`, `paid_conversion_rate`, and `cancel_rate`, with dimensions `region`, `channel`, `category`. Snapshot records contain `snapshot_id`, `table_name`, `reporting_as_of`, `period_start`, `period_end`, `complete`, and quality metrics.

Admission returns one of:

```python
AdmissionDecision(
    status=AdmissionStatus.ADMITTED,
    issue_codes=[],
    effective_scope=effective_scope,
    snapshot=snapshot,
    context=None,
)
```

Reject requested values outside authorized sets. Do not narrow a request silently. Apply quality `BLOCK` only for metrics whose policy refers to that check.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/test_semantic.py tests/test_admission.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/metricledger/semantic.py src/metricledger/snapshots.py src/metricledger/resources src/metricledger/admission.py tests
git commit -m "feat: add semantic resolution and admission gates"
```

### Task 3: Intent Parsing and Safe SQL Compilation

**Files:**
- Create: `src/metricledger/intent.py`
- Create: `src/metricledger/compiler.py`
- Create: `src/metricledger/guards.py`
- Create: `tests/test_intent.py`
- Create: `tests/test_compiler.py`

**Interfaces:**
- Produces: `RuleBasedIntentParser.parse(question: str, as_of: datetime) -> IntentParse`.
- Produces: `QueryCompiler.compile(intent, metric, snapshot, scope) -> CompiledQuery`.
- `CompiledQuery` contains `sql`, `parameters`, `fingerprint`, and `referenced_tables`.

- [ ] **Step 1: Write failing parser and guard tests**

```python
def test_parser_marks_two_metric_aliases_as_ambiguous():
    parsed = RuleBasedIntentParser(registry).parse(
        "2026-11-17 到 2026-11-23 转化和取消率为什么变化",
        as_of=DEMO_AS_OF,
    )
    assert parsed.status is AdmissionStatus.CLARIFY
    assert parsed.issue_codes == ["AMBIGUOUS_METRIC"]


def test_guard_rejects_multi_statement_sql():
    with pytest.raises(SqlGuardError, match="single statement"):
        validate_select("select 1; drop table x", allowed_tables={"t"})


def test_compiler_binds_times_and_scope_as_parameters(compiler, context):
    compiled = compiler.compile(context.intent, context.metric, context.snapshot, context.effective_scope)
    assert "?" in compiled.sql
    assert "east" not in compiled.sql
    assert compiled.parameters[:2] == ["2026-11-17", "2026-11-23"]
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_intent.py tests/test_compiler.py -q`

Expected: parser/compiler imports fail.

- [ ] **Step 3: Implement parser, compiler, and SQL guards**

The parser recognizes ISO dates, metric aliases, Chinese comparison words, and optional dimension names. A missing or ambiguous metric returns `CLARIFY`.

The compiler emits one `SELECT` against the fixed snapshot table. Metric expressions come only from the bundle. All user and scope values are query parameters. Guard checks use SQLGlot to reject non-`SELECT` statements, multiple statements, tables outside the whitelist, columns outside the bundle, and unsupported function nodes.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/test_intent.py tests/test_compiler.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/metricledger/intent.py src/metricledger/compiler.py src/metricledger/guards.py tests/test_intent.py tests/test_compiler.py
git commit -m "feat: compile semantic intents into read-only SQL"
```

### Task 4: DuckDB Snapshot Engine and Quality Checks

**Files:**
- Create: `scripts/build_demo_data.py`
- Create: `src/metricledger/data.py`
- Create: `src/metricledger/quality.py`
- Create: `tests/conftest.py`
- Create: `tests/test_engine.py`
- Create: `tests/test_quality.py`

**Interfaces:**
- Produces: `DuckDbEngine.execute(compiled: CompiledQuery, snapshot_id: str) -> QueryRows`.
- Produces: `QualityService.evaluate(metric, snapshot, period) -> list[QualityCheck]`.
- `QueryRows` contains row dictionaries, elapsed milliseconds, and row count.

- [ ] **Step 1: Write failing real-database tests**

```python
def test_duckdb_engine_executes_against_fixed_snapshot(engine, compiled):
    rows = engine.execute(compiled, "batch_20261123_2300")
    assert len(rows.rows) == 1
    assert rows.rows[0]["metric_value"] == pytest.approx(732400.0)


def test_engine_rejects_unknown_snapshot(engine, compiled):
    with pytest.raises(UnknownSnapshotError):
        engine.execute(compiled, "missing_snapshot")


def test_ratio_quality_blocks_zero_denominator(snapshot_with_zero_sessions, ratio_metric):
    checks = QualityService().evaluate(
        ratio_metric, snapshot_with_zero_sessions, DateRange("2026-11-17", "2026-11-23")
    )
    assert next(c for c in checks if c.code == "POSITIVE_DENOMINATOR").status == "BLOCK"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_engine.py tests/test_quality.py -q`

Expected: engine and quality modules do not exist.

- [ ] **Step 3: Implement deterministic demo data and engine**

Create two immutable tables, `sales_batch_20261116_2300` and `sales_batch_20261123_2300`. Generate daily rows from 2026-10-01 through 2026-11-23 with fixed values. The second snapshot contains a known East-region paid-order decline while sessions remain stable.

The engine opens DuckDB read-only, validates that the snapshot table is registered, executes the compiled statement, and limits returned rows to 200. It distinguishes empty successful queries from exceptions.

Quality checks include snapshot completeness, date coverage, duplicate rate, null rate, and ratio denominator positivity.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/test_engine.py tests/test_quality.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/build_demo_data.py src/metricledger/data.py src/metricledger/quality.py tests/conftest.py tests/test_engine.py tests/test_quality.py
git commit -m "feat: add immutable DuckDB snapshots and quality checks"
```

### Task 5: Evidence Ledger and Claim Validation

**Files:**
- Create: `src/metricledger/evidence.py`
- Create: `src/metricledger/claims.py`
- Create: `tests/test_evidence.py`
- Create: `tests/test_claims.py`

**Interfaces:**
- Produces: `EvidenceStore.add_result(artifact) -> ResultArtifact`.
- Produces: `EvidenceValidator.validate(claim, artifacts) -> ValidatedClaim`.
- Produces: `ClaimRenderer.render(validated_claim) -> str`.

- [ ] **Step 1: Write failing evidence tests**

```python
def test_claim_cannot_cite_artifact_from_another_analysis(artifact, valid_claim):
    foreign = artifact.model_copy(update={"analysis_id": "another"})
    with pytest.raises(EvidenceViolation, match="cross-analysis"):
        EvidenceValidator().validate(valid_claim, {foreign.result_id: foreign})


def test_claim_scope_must_be_covered_by_evidence(valid_claim, artifact):
    scoped = valid_claim.model_copy(update={"scope": valid_claim.scope.model_copy(update={"region": "west"})})
    with pytest.raises(EvidenceViolation, match="scope"):
        EvidenceValidator().validate(scoped, {artifact.result_id: artifact})


def test_renderer_uses_evidence_value_not_template_number(claim, artifact, valid_claim):
    claim = claim.model_copy(update={"template": "GMV 变化 {{evidence:change_pct}}，不是 999%"})
    text = ClaimRenderer().render(EvidenceValidator().validate(claim, {artifact.result_id: artifact}))
    assert "999%" not in text
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_evidence.py tests/test_claims.py -q`

Expected: evidence modules do not exist.

- [ ] **Step 3: Implement evidence validation and deterministic rendering**

An artifact records `analysis_id`, metric, period, comparison period, filters, snapshot, semantic hash, result digest, bounded rows, SQL, and expiry.

Validation requires:

- Every referenced artifact exists.
- Same `analysis_id`, snapshot, and semantic hash.
- Evidence scope is equal to or broader than Claim scope.
- Claim type is allowed for metric kind.
- Every placeholder exists in a whitelisted evidence argument.
- No literal numeric token is accepted as a Claim data source.

The renderer formats values from evidence only.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/test_evidence.py tests/test_claims.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/metricledger/evidence.py src/metricledger/claims.py tests/test_evidence.py tests/test_claims.py
git commit -m "feat: validate claims against contextual evidence"
```

### Task 6: Bounded Investigation Agent

**Files:**
- Create: `src/metricledger/investigation.py`
- Create: `src/metricledger/planners.py`
- Create: `tests/test_investigation.py`
- Create: `tests/test_planners.py`

**Interfaces:**
- Produces: `HypothesisPlanner.propose(observation, allowed_dimensions, budget) -> list[Hypothesis]`.
- Produces: `InvestigationService.investigate(context) -> InvestigationOutcome`.
- Produces: `HeuristicHypothesisPlanner`.
- Produces: `OpenAICompatibleHypothesisPlanner`.

- [ ] **Step 1: Write failing investigation tests**

```python
def test_additive_investigation_stops_after_concentrated_region(service, context):
    outcome = service.investigate(context)
    assert outcome.stop_reason == "concentrated_contribution"
    assert outcome.queries_used == 2
    assert outcome.top_dimension == "region"


def test_ratio_investigation_never_emits_arithmetic_contribution(service, ratio_context):
    outcome = service.investigate(ratio_context)
    assert all(claim.claim_type is not ClaimType.CONTRIBUTION for claim in outcome.claims)
    assert any(claim.claim_type is ClaimType.DIAGNOSTIC for claim in outcome.claims)


def test_budget_is_reserved_before_query_execution(service, one_query_context):
    outcome = service.investigate(one_query_context)
    assert outcome.queries_used == 1
    assert outcome.completeness is Completeness.PARTIAL
    assert "BUDGET_EXHAUSTED" in outcome.issue_codes
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_investigation.py tests/test_planners.py -q`

Expected: investigation modules do not exist.

- [ ] **Step 3: Implement loop and planners**

The heuristic planner proposes configured dimensions in semantic order. The HTTP planner sends only observation summaries, dimension IDs/display names, existing evidence summaries, and remaining budget to an OpenAI-compatible chat completions endpoint.

Validate planner output as JSON, cap it at three hypotheses, reject unknown dimensions, reject duplicates, and fall back to already queued hypotheses on failure. Never pass raw detail rows.

The service reserves query budget before compiling or executing the next diagnostic. For additive metrics, compute exact group deltas and stop at contribution concentration `>= 0.5`. For ratios, generate numerator/denominator diagnostics and directional group differences.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/test_investigation.py tests/test_planners.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/metricledger/investigation.py src/metricledger/planners.py tests/test_investigation.py tests/test_planners.py
git commit -m "feat: add bounded metric investigation agent"
```

### Task 7: Analysis Orchestrator and SQLite Audit Ledger

**Files:**
- Create: `src/metricledger/service.py`
- Create: `src/metricledger/ledger.py`
- Create: `tests/test_service.py`
- Create: `tests/test_ledger.py`

**Interfaces:**
- Produces: `AnalysisService.analyze(request) -> AnalysisResponse`.
- Produces: `AnalysisService.get(analysis_id) -> AnalysisResponse | None`.
- Produces: `SqliteLedger.save(response) -> None`.

- [ ] **Step 1: Write failing orchestration and persistence tests**

```python
def test_service_returns_supported_answer_with_audit_sql(service, request):
    response = service.analyze(request)
    assert response.admission.status is AdmissionStatus.ADMITTED
    assert response.claims
    assert all(claim.support is ClaimSupport.SUPPORTED for claim in response.claims)
    assert response.sql_audit[0].sql.startswith("SELECT")


def test_service_rejects_unsafe_scope_without_running_sql(service, east_only_request, engine):
    before = engine.execution_count
    response = service.analyze(east_only_request)
    assert response.admission.status is AdmissionStatus.REFUSED
    assert engine.execution_count == before


def test_ledger_round_trips_response_without_api_key(ledger, response):
    ledger.save(response)
    loaded = ledger.get(response.analysis_id)
    assert loaded.model_dump() == response.model_dump()
    assert "api_key" not in ledger.dump_all_text().lower()
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_service.py tests/test_ledger.py -q`

Expected: orchestrator and ledger imports fail.

- [ ] **Step 3: Implement orchestration and SQLite tables**

Service order is fixed: parse, admit, fix context, execute headline, investigate, validate Claims, summarize issues, persist. `REFUSED`, `CLARIFY`, and `UNAVAILABLE` do not call the DuckDB engine.

SQLite uses WAL mode and three tables: `analyses`, `result_artifacts`, and `graph_edges`. Store API payloads as canonical JSON. Do not persist secrets or full raw business rows.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/test_service.py tests/test_ledger.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/metricledger/service.py src/metricledger/ledger.py tests/test_service.py tests/test_ledger.py
git commit -m "feat: orchestrate analyses and persist audit evidence"
```

### Task 8: FastAPI Application

**Files:**
- Create: `src/metricledger/api.py`
- Create: `tests/test_api.py`
- Create: `scripts/run_api.py`

**Interfaces:**
- Produces: `create_app(service) -> FastAPI`.
- Endpoints: `GET /api/health`, `GET /api/catalog`, `POST /api/analyses`, `GET /api/analyses/{id}`, `GET /api/eval/latest`.

- [ ] **Step 1: Write failing API tests**

```python
def test_analysis_endpoint_returns_claim_and_sql_audit(client, request_payload):
    response = client.post("/api/analyses", json=request_payload)
    assert response.status_code == 200
    body = response.json()
    assert body["admission"]["status"] == "ADMITTED"
    assert body["claims"][0]["evidence_refs"]
    assert body["sql_audit"]


def test_unknown_analysis_returns_404(client):
    assert client.get("/api/analyses/missing").status_code == 404


def test_validation_errors_do_not_echo_authorization_header(client):
    response = client.post(
        "/api/analyses",
        json={},
        headers={"Authorization": "Bearer secret-value"},
    )
    assert response.status_code == 422
    assert "secret-value" not in response.text
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_api.py -q`

Expected: API module does not exist.

- [ ] **Step 3: Implement API**

Use Pydantic request/response models with `extra="forbid"`. Register one exception handler for domain errors. Health returns measured runtime state, not invented uptime. Eval endpoint returns a persisted `NOT_RUN` placeholder when no report exists.

- [ ] **Step 4: Run tests and verify GREEN**

Run: `python -m pytest tests/test_api.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/metricledger/api.py scripts/run_api.py tests/test_api.py
git commit -m "feat: expose MetricLedger analysis API"
```

### Task 9: React Analysis Workbench

**Files:**
- Create: `web/package.json`
- Create: `web/tsconfig.json`
- Create: `web/vite.config.ts`
- Create: `web/index.html`
- Create: `web/src/main.tsx`
- Create: `web/src/App.tsx`
- Create: `web/src/api.ts`
- Create: `web/src/styles.css`
- Create: `web/src/App.test.tsx`

**Interfaces:**
- Consumes: `POST /api/analyses`.
- Displays: admission, completeness, Claims, issues, context, evidence, SQL audit.

- [ ] **Step 1: Write failing frontend test**

```tsx
it("renders a refusal without rendering answer claims", async () => {
  vi.spyOn(global, "fetch").mockResolvedValue(
    new Response(JSON.stringify({
      analysis_id: "a1",
      admission: { status: "REFUSED", issue_codes: ["SCOPE_NOT_AUTHORIZED"] },
      completeness: "NONE",
      answer: null,
      claims: [],
      issues: [{ code: "SCOPE_NOT_AUTHORIZED", message: "请求范围超出权限" }],
      context: {},
      budget_usage: {},
      evidence: [],
      sql_audit: []
    }), { status: 200 })
  );
  render(<App />);
  await userEvent.click(screen.getByRole("button", { name: "开始调查" }));
  expect(await screen.findByText("请求范围超出权限")).toBeInTheDocument();
  expect(screen.queryByTestId("claim-list")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run test and verify RED**

Run: `cd web; npm install; npm test -- --run`

Expected: `App` import or expected UI is missing.

- [ ] **Step 3: Implement workbench**

Build a single analysis surface with example questions. Render user content as text, not HTML. Use status-specific empty states. SQL audit is collapsed by default. Show the fixed snapshot and semantic version in a context strip.

- [ ] **Step 4: Run test and verify GREEN**

Run: `cd web; npm test -- --run; npm run build`

Expected: tests and production build pass.

- [ ] **Step 5: Commit**

```bash
git add web
git commit -m "feat: add React analysis workbench"
```

### Task 10: Evaluation Suite and Baselines

**Files:**
- Create: `src/metricledger/evaluation.py`
- Create: `scripts/run_eval.py`
- Create: `eval/scenarios.json`
- Create: `tests/test_evaluation.py`
- Create: `docs/verification/eval-report.md`

- [ ] **Step 1: Write failing evaluation tests**

```python
def test_suite_contains_at_least_sixty_unique_scenarios(suite):
    assert len(suite.scenarios) >= 60
    assert len({s.scenario_id for s in suite.scenarios}) == len(suite.scenarios)


def test_metricledger_has_no_unsafe_answers_on_forbidden_cases(report):
    assert report.metricledger.unsafe_answer_count == 0
    assert report.metricledger.scope_violation_count == 0


def test_report_does_not_claim_external_benchmark(report):
    text = report.to_markdown()
    assert "行业 SOTA" not in text
    assert "本仓库合成场景" in text
```

- [ ] **Step 2: Run test and verify RED**

Run: `python -m pytest tests/test_evaluation.py -q`

Expected: evaluation module or report is missing.

- [ ] **Step 3: Implement scenarios and three baselines**

Generate unique scenario variants from explicit categories and known anomaly injections. Run:

- `naive_text_to_sql`
- `semantic_without_investigation`
- `metricledger`

Persist JSON and Markdown reports with exact counts, configuration, commit, and measured timings. Use no LLM judge for safety metrics.

- [ ] **Step 4: Run test and verify GREEN**

Run: `python -m pytest tests/test_evaluation.py -q; python scripts/run_eval.py`

Expected: tests pass and report files are generated from the current run.

- [ ] **Step 5: Commit**

```bash
git add src/metricledger/evaluation.py scripts/run_eval.py eval tests/test_evaluation.py docs/verification/eval-report.md
git commit -m "feat: add reproducible evaluation and baselines"
```

### Task 11: Documentation and Delivery Verification

**Files:**
- Create: `README.md`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `Makefile`
- Create: `docs/ARCHITECTURE.md`
- Create: `docs/verification/acceptance.md`

**Interfaces:**
- Produces: reproducible setup, run, test, build, and evaluation commands.

- [ ] **Step 1: Add runtime files and commands**

Provide `make test`, `make eval`, `make web`, `make dev`, and Windows PowerShell equivalents. `.env.example` contains empty values only.

- [ ] **Step 2: Run complete verification**

Run:

```bash
python -m pytest -q
cd web && npm test -- --run && npm run build
cd .. && python scripts/run_eval.py
```

Expected: every command exits zero.

- [ ] **Step 3: Write verified evidence**

Run the application and API smoke test. Record exact test counts, scenario counts, measured values, environment, and known limitations. Do not mix metrics from older projects.

- [ ] **Step 4: Commit**

```bash
git add README.md .gitignore .env.example Makefile docs
git commit -m "docs: add reproducible MetricLedger delivery guide"
```

## Plan Self-Review

- Spec coverage: admission, DSL, guards, snapshots, quality, Claim evidence, ratio semantics, bounded investigation, model boundary, API, frontend, evaluation, and delivery each map to a task.
- Type consistency: `AnalysisIntent`, `AnalysisContext`, `ResultArtifact`, `Claim`, `AdmissionDecision`, and `AnalysisResponse` are produced before consumers use them.
- Placeholder scan: no deferred requirements or unnamed components remain.
- Execution scope: frontend and evaluation are separate tasks but together form one coherent vertical slice.
