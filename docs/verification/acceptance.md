# MetricLedger Acceptance Record

验收日期：2026-11-25（Asia/Shanghai）

## Acceptance Scope

本次验收覆盖：

- Python 单元与集成测试
- Python 覆盖率统计
- React 组件测试
- TypeScript 与 Vite 生产构建
- npm 高危依赖审计
- 63 个确定性评测场景
- OpenAI-compatible planner 的环境变量接线与不完整配置拒绝
- 当前代码的真实 API 启动
- 直连 API 与 Vite 代理两条实时业务链路
- 无浏览器、无 HTTP 服务的进程内 CLI

本记录不代表真实企业部署、真实流量、线上 SLA 或外部行业基准。

## Verified Commands

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q --cov=metricledger --cov-report=term
.\.venv\Scripts\python.exe scripts\ask.py --json
npm --prefix web test -- --run
npm --prefix web run build
npm --prefix web audit --audit-level=high
.\.venv\Scripts\python.exe scripts\run_eval.py
.\.venv\Scripts\python.exe scripts\smoke_test.py --base-url http://127.0.0.1:8000
.\.venv\Scripts\python.exe scripts\smoke_test.py --base-url http://127.0.0.1:5173
```

## Results

| Check | Result |
|---|---|
| Backend tests | `79 passed` |
| Python coverage | `92%` |
| In-process CLI | passed, 2 Claims, 4 evidence artifacts, 4 queries |
| Frontend tests | `3 passed` |
| Production build | TypeScript check and Vite build passed |
| Dependency audit | `0` vulnerabilities |
| Evaluation scenarios | `63` |
| MetricLedger scenario success | `63/63` |
| Unsafe answers | `0` |
| Scope violations | `0` |
| Empty result answered as zero | `0` |
| Unsupported Claims | `0` |
| Evidence violations | `0` |
| Budget violations | `0` |
| Direct API smoke | passed, 2 Claims, 4 queries |
| Vite proxy smoke | passed, 2 Claims, 4 queries |

后端测试存在 1 条第三方弃用警告：Starlette 当前 `TestClient` 对 `httpx` 的兼容提示。该警告不改变测试结果。

## Evaluation Evidence

机器可读结果：[eval/results.json](../../eval/results.json)

人类可读报告：[eval-report.md](eval-report.md)

最近一次评测的基线结果：

| Baseline | Scenario success | Unsafe | Scope violations |
|---|---:|---:|---:|
| `naive_text_to_sql` | `27/63` | `36` | `8` |
| `semantic_without_investigation` | `48/63` | `0` | `0` |
| `metricledger` | `63/63` | `0` | `0` |

本次本机单进程 P95 延迟分别为 `398.86 ms`、`225.77 ms` 和 `394.19 ms`。这些数字受当前机器负载影响，不代表线上 SLA。

`metricledger` 的“调查完整率”显示为 `11/15`，表示 15 个支持调查的场景中有 11 个实际得到 `COMPLETE`。其余 4 个场景在评测标签中明确要求 `PARTIAL`，且状态、阻断结论和安全边界均正确，因此仍是成功场景，并不表示评测失败。4 个预期 `PARTIAL` 场景是：`supported_investigation-01-002`、`supported_investigation-01-003`、`supported_investigation-01-006`、`supported_investigation-01-007`。

评测报告的生成时间、提交、工作区状态、环境版本、完整场景分布和本机 P95 延迟由 `scripts/run_eval.py` 写入报告，避免在文档中手工伪造或过期。本次报告在交付提交前生成，因此报告中的 `worktree_dirty: true` 和当时的 HEAD 是预期的工作区快照，不代表推送后的最终提交号。

## Live Smoke Assertions

实时冒烟脚本验证：

- `/api/health` 返回 `ok`，并报告 2 个固定快照。
- `/api/catalog` 只暴露 `paid_gmv`、`paid_conversion_rate`、`cancel_rate`。
- `/api/eval/latest` 返回当前参数化评测报告。
- 示例问题返回 `ADMITTED`。
- 至少产生 2 条 Claim，且每条都有证据引用。
- SQL 审计存在，首条语句为 `SELECT`。
- 渲染文本包含 `732,400.00` 和 `-9.18%`。
- 渲染文本不包含残留的 `{evidence:` 占位符。

## No-Browser CLI

`scripts/ask.py` 在单进程中构造与 API 相同的 `AnalysisService`，不启动 Uvicorn，也不访问浏览器。验收断言包括：

- 准入状态为 `ADMITTED`。
- 完整度为 `COMPLETE`。
- 输出包含 `732,400.00` 和 `-9.18%`。
- 生成 2 条 Claim，且每条 Claim 都有证据引用。
- 使用 4 次查询，所有 SQL 审计语句均为 `SELECT`。

## React Workbench Boundary

React 工作台已通过组件测试、TypeScript 检查、Vite 生产构建和开发服务器 `/api` 代理冒烟。网页是可选的展示入口，不是核心分析运行前提；本轮最终验收没有重新执行 Playwright 人工浏览器检查，因此不把浏览器控制台或截图列为本次新鲜证据。

## Guarantee

当前实现可以证明的工程保证是：

> 不伪造数字、不脱离证据、不越权返回、不把失败或空结果冒充业务零值。

不能承诺的边界是：

- 所有自然语言问题都能自动理解。
- 合成评测结果等同于真实企业收益或公开榜单成绩。
- 当前单机参考实现已经满足多租户、分布式和线上高可用要求。
- 本轮未提供真实第三方 planner 凭据；已验证环境变量接线、HTTP 请求结构与输出清洗，没有声称某个外部模型端点的线上可用性。
