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
- 200 万事件规模下的真实 DuckDB 查询与 `EXPLAIN ANALYZE`
- 真实 HTTP API 上的并发混合负载与数据库瓶颈归因
- 真实 OpenAI-compatible planner 与启发式 planner 的同场景对比
- 当前代码的真实 API 启动
- 直连 API 与 Vite 代理两条实时业务链路
- 无浏览器、无 HTTP 服务的进程内 CLI
- 临时公网隧道的首页、健康检查和完整业务冒烟
- 多租户、RBAC、限流、追踪和 Prometheus 告警的自动化测试与静态校验

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
.\.venv\Scripts\python.exe scripts\benchmark_scale.py
.\.venv\Scripts\python.exe scripts\benchmark_load.py --base-url http://127.0.0.1:8011 --requests 120 --concurrency 12
.\.venv\Scripts\python.exe scripts\benchmark_planners.py
.\.venv\Scripts\python.exe scripts\smoke_test.py --base-url https://puny-bugs-glow.loca.lt
```

## Results

| Check | Result |
|---|---|
| Backend tests | `110 passed` |
| Python coverage | `93%` |
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
| Public temporary demo smoke | passed, 2 Claims, 4 queries |

后端测试存在 1 条第三方弃用警告：Starlette 当前 `TestClient` 对 `httpx` 的兼容提示。该警告不改变测试结果。

## Scale Evidence

机器可读结果：[scale-results.json](scale-results.json)

人类可读报告：[scale-report.md](scale-report.md)

本轮使用仓库脚本确定性生成并实际查询了 **2,000,000** 条事件：

| 指标 | 本轮实测 |
|---|---:|
| 快照数 | 2 |
| 每个快照事件数 | 1,000,000 |
| DuckDB 文件 | 19.26 MiB |
| `demo` / `tenant_b` 事件数 | 1,800,000 / 200,000 |
| 单次调查 SQL 查询数 | 4 |
| DuckDB 总查询耗时 | 353.288 ms |
| DuckDB 平均查询耗时 | 88.322 ms |
| 服务端到端耗时 | 388.935 ms |

`EXPLAIN ANALYZE` 显示目标快照实际进入过滤和聚合阶段约 `116,662` 行。数据规模可以由 `scripts/build_scale_data.py` 重建；耗时受本机 CPU、内存、DuckDB 版本和文件缓存影响。

## Mixed-Load Evidence

机器可读结果：[load-results.json](load-results.json)

人类可读报告：[load-report.md](load-report.md)

本轮对真实运行的 HTTP API 发起 **120** 个混合请求，并发 **12**：

| 指标 | 本轮实测 |
|---|---:|
| 吞吐 | 10.644 req/s |
| 持续时间 | 11.274 s |
| P50 | 155.458 ms |
| P95 | 3,893.734 ms |
| P99 | 4,823.195 ms |
| HTTP/传输错误 | 0 |
| 非预期业务错误 | 0 |
| 错误率 | 0.00% |

DuckDB 执行 **168** 次查询、累计 **113,169.791 ms**、0 错误；SQLite 账本执行 72 次写入和 12 次读取，总计 1,587.413 ms、0 错误。DuckDB 查询耗时占累计请求时间约 **88.83%**，SQLite 占 **1.25%**，因此本轮首要瓶颈判定为 DuckDB。最慢路由是 `investigate`，P95 为 4,823.195 ms。

这是单机、单 API 进程、本地 DuckDB/SQLite 的负载测量，不是生产容量、分布式 trace 或线上 SLA 结论。

## Planner Evidence

机器可读结果：[planner-results.json](planner-results.json)

人类可读报告：[planner-report.md](planner-report.md)

本轮实际调用 DeepSeek 的 OpenAI-compatible `chat/completions` 接口，模型标签为 `api.deepseek.com/deepseek-chat`。输入只包含观察摘要、允许维度和剩余查询预算，不暴露原始事件行。

| Planner | 用例 | 失败率 | 回退率 | P50 | P95 |
|---|---:|---:|---:|---:|---:|
| `heuristic` | 15 | 0.00% | 0.00% | 0.004 ms | 0.013 ms |
| `deepseek-chat` | 15 | 0.00% | 0.00% | 915.992 ms | 1,591.464 ms |

模型结果 `MODEL_SUCCESS=15`。这是 15 个仓库内确定性场景的本轮结果，不是通用模型榜单或生产 SLA；回退路径另由自动化测试覆盖。

## Evaluation Evidence

机器可读结果：[eval/results.json](../../eval/results.json)

人类可读报告：[eval-report.md](eval-report.md)

最近一次评测的基线结果：

| Baseline | Scenario success | Unsafe | Scope violations |
|---|---:|---:|---:|
| `naive_text_to_sql` | `27/63` | `36` | `8` |
| `semantic_without_investigation` | `48/63` | `0` | `0` |
| `metricledger` | `63/63` | `0` | `0` |

本次本机单进程 P95 延迟分别为 `344.33 ms`、`182.08 ms` 和 `351.50 ms`。这些数字受当前机器负载影响，不代表线上 SLA。

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

## Security And Operations

本轮验证了：

- 认证身份覆盖请求自报的租户与过滤范围。
- 跨租户读取与不存在资源不可区分。
- `viewer`、`analyst`、`admin` 的最小角色边界。
- Bearer 令牌只保存哈希匹配结果，不在日志或账本中保存明文。
- 单进程、按主体限流，并在超限时返回 429 和 `Retry-After`。
- W3C `traceparent`、请求 ID、trace ID 和 JSON 访问日志传播。
- Prometheus HTTP、DuckDB、SQLite、认证、限流和分析问题指标。
- 5xx、P95、DuckDB、账本、分析失败、限流和认证异常共 7 条告警表达式。

7 条 PromQL 表达式已使用 `promql-parser` 解析成功；告警引用的 8 个指标名均存在于实时 `/api/metrics` 返回的 15 个指标中。Compose、Prometheus、告警和 GitHub Actions 四份 YAML 均解析成功。

当前限流器是单进程内存实现；SQLite 账本是单机持久化实现。多副本生产环境应替换为共享限流存储和更高并发的审计存储。

## Delivery Evidence

- GitHub Actions 工作流：后端测试、覆盖率、63 场景安全门禁、CLI、前端测试、构建、依赖审计和部署 YAML 校验。
- 多阶段 `Dockerfile`：构建静态前端，以非 root 用户运行 API，并提供健康检查。
- `compose.yaml`：API 数据卷、认证/planner 环境变量、限流配置和 Prometheus。
- 静态校验通过不等于容器已实跑；本轮机器没有 Docker/Podman，因此 `docker compose up --build` 为 `NOT_RUN`。
- 临时公网 Demo `https://puny-bugs-glow.loca.lt` 在验收时已通过远程首页和完整业务冒烟。它依赖当前本机 API 与隧道进程，进程结束后失效，不等同于长期托管部署。
- 长期公网托管需要云主机、域名和 Secret Manager；本轮没有这些托管凭据，因此持久部署为 `NOT_RUN`。

## Guarantee

当前实现可以证明的工程保证是：

> 不伪造数字、不脱离证据、不越权返回、不把失败或空结果冒充业务零值。

不能承诺的边界是：

- 所有自然语言问题都能自动理解。
- 合成评测结果等同于真实企业收益或公开榜单成绩。
- 当前单机参考实现已经满足分布式限流、跨节点租户治理和线上高可用要求。
- 15 个合成 planner 场景的结果可以外推到所有真实问题或所有模型端点。
- 临时公网隧道等同于持久、带 SLA 的公网部署。
