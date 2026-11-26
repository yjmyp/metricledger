# MetricLedger Acceptance Record

验收日期：2026-11-25（Asia/Shanghai）

## Acceptance Scope

本轮验收覆盖当前分支上的调查轨迹、两轮 planner 上限、预算耗尽语义、72 个确定性合成场景和交付门禁：

- Python 单元与集成测试
- Python 覆盖率统计
- 无浏览器进程内 CLI
- 72 个确定性评测场景与三个基线
- React 组件测试
- TypeScript 与 Vite 生产构建
- npm 高危依赖审计
- GitHub Actions 的后端、前端、配置和容器四个 job

本记录不代表真实企业部署、真实流量、线上 SLA 或外部行业基准。本轮没有重新执行 200 万事件规模测试与混合并发负载测试，历史报告仅作为旧证据保留。

## Verified Commands

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q --cov=metricledger --cov-report=term
.\.venv\Scripts\python.exe scripts\ask.py --json
npm --prefix web test -- --run
npm --prefix web run build
npm --prefix web audit --audit-level=high
.\.venv\Scripts\python.exe scripts\run_eval.py
.\.venv\Scripts\python.exe scripts\benchmark_planners.py
```

## Results

| Check | Result |
|---|---|
| Backend tests | `138 passed` |
| Python coverage | `93%` |
| In-process CLI | `ADMITTED`, `COMPLETE`, 2 Claims, 4 evidence artifacts, 4 queries |
| Frontend tests | `4 passed` |
| Production build | TypeScript check and Vite build passed |
| Dependency audit | `0` high-severity vulnerabilities |
| Evaluation scenarios | `72` |
| MetricLedger scenario success | `72/72` |
| Unsafe answers | `0` |
| Scope violations | `0` |
| Empty result answered as zero | `0` |
| Unsupported Claims | `0` |
| Evidence violations | `0` |
| Budget violations | `0` |

后端测试存在 1 条第三方弃用警告：Starlette 当前 `TestClient` 对 `httpx` 的兼容提示。该警告不改变测试结果。

## Evaluation Evidence

机器可读结果：[eval/results.json](../../eval/results.json)

人类可读报告：[eval-report.md](eval-report.md)

| Baseline | Scenario success | Unsafe | Scope violations | P95 |
|---|---:|---:|---:|---:|
| `naive_text_to_sql` | `32/72` | `40` | `9` | `437.03 ms` |
| `semantic_without_investigation` | `54/72` | `0` | `0` | `209.15 ms` |
| `metricledger` | `72/72` | `0` | `0` | `335.45 ms` |

调查场景共 18 个：13 个得到 `COMPLETE`，5 个按场景标签明确要求 `PARTIAL`。其中 `supported_investigation-01-016` 在 4 次查询后因预算耗尽停止，报告为 `PARTIAL` 和 `budget_exhausted`，没有伪造完整结论。

5 个预期 `PARTIAL` 场景是：

- `supported_investigation-01-002`
- `supported_investigation-01-003`
- `supported_investigation-01-006`
- `supported_investigation-01-007`
- `supported_investigation-01-016`

这些延迟是本机单进程测量，受当前机器负载影响，不代表线上 SLA。

## Planner Evidence

机器可读结果：[planner-results.json](planner-results.json)

人类可读报告：[planner-report.md](planner-report.md)

| Planner | 用例 | 状态 | 失败率 | 回退率 | Tokens |
|---|---:|---|---:|---:|---:|
| `heuristic` | `18` | `COMPLETE` | `0.00%` | `0.00%` | `0` |
| OpenAI-compatible model | `0` | `NOT_RUN` | 未运行 | 未运行 | 未运行 |

当前环境没有配置 `METRICLEDGER_PLANNER_BASE_URL` 与 `METRICLEDGER_PLANNER_MODEL`，因此真实模型对比为 `NOT_RUN`。本轮不沿用任何旧模型调用结果，也不把启发式 planner 的结果表述为真实模型效果。

## No-Browser CLI

`scripts/ask.py` 在单进程中构造与 API 相同的 `AnalysisService`，不启动 Uvicorn，也不访问浏览器。本轮实测：

- 准入状态为 `ADMITTED`。
- 完整度为 `COMPLETE`。
- 生成 2 条 Claim、4 个证据对象和 4 条只读 SQL。
- `planner_tokens_used=0`，`planner_usage_complete=true`。
- 调查执行一轮后因 `concentrated_contribution` 停止。

## Container And Deployment

本机没有安装 `docker` 命令，因此本机 `docker version` 和 Compose 启动为 `NOT_RUN`。容器交付只能由 GitHub Actions 的 `container` job 验证；在真实 job 成功前，不把容器冒烟记录为已通过。

长期公网托管本轮没有重新验证，也没有新增托管凭据；该部分仍为 `NOT_RUN`。

## Historical Operational Evidence

早先版本曾完成 200 万事件规模查询和 120 请求、12 并发的混合负载测试，报告仍保留在 [scale-report.md](scale-report.md) 与 [load-report.md](load-report.md)。这些结果不是本轮新代码的新鲜验收数据，不能替代本轮测试或 CI。

## Guarantee

当前实现可以证明的工程保证是：

> 不伪造数字、不脱离证据、不越权返回、不把失败或空结果冒充业务零值，也不在预算耗尽时伪造完整调查。

不能承诺的边界是：

- 所有自然语言问题都能自动理解。
- 合成评测结果等同于真实企业收益或公开榜单成绩。
- 当前单机参考实现已经满足分布式限流、跨节点租户治理和线上高可用要求。
- 未运行的真实模型对比已经通过。
- 本机未执行的容器或长期部署链路已经通过。
