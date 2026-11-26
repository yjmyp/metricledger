# MetricLedger

MetricLedger 是一个可运行的“可信 ChatBI + 有界指标调查 Agent”参考实现。它把自然语言问题转换成受限的语义查询，在固定数据快照上完成主指标与分组诊断，并用 Claim 级证据约束每一句结论。

它不承诺自然语言分析在数学意义上永不出错。它保证的是：

- 回答中的业务数字只来自固定快照或确定性计算。
- 每个正式 Claim 都绑定同一分析、同一快照、同一语义版本的证据。
- 越权、未覆盖、快照过期和需要澄清的问题会被显式拒绝或降级。
- 技术失败、查询异常和空结果不会伪装成业务零值。

## 项目能力

- 语义 DSL：业务问题只解析为 `AnalysisIntent`，不接受用户 SQL。
- 准入闸门：校验授权范围、时间覆盖、对比周期和快照质量。
- 只读编译：SQL 由资源包模板生成，时间和范围使用参数绑定。
- 固定快照：DuckDB 只执行白名单快照表的 `SELECT`。
- 有界调查：默认最多 4 次查询，按地区、渠道、品类提出诊断假设。
- Claim 证据：校验跨分析引用、证据范围、指标类型和模板占位符。
- 审计闭环：SQLite 保存分析结果、结果摘要和证据关系，不保存密钥或原始明细行。
- 可复现评测：63 个合成场景、3 个基线、确定性安全指标。
- 多租户与 RBAC：Bearer 令牌只在服务端配置中出现；租户、角色和允许的过滤值由认证身份覆盖请求自报内容。
- 运行治理：按租户主体限流，输出 Prometheus 指标，传播 W3C `traceparent`，并记录带 `request_id`/`trace_id` 的 JSON 访问日志。
- 规模与负载证据：可生成 2 个快照、共 200 万条事件，并运行真实 DuckDB 查询、混合 HTTP 并发负载和 planner 对比。
- 可复现交付：GitHub Actions CI、Dockerfile、Compose、Prometheus 抓取与告警规则。

## 入口不依赖网页

网页只是可选工作台，不是 MetricLedger 的运行前提。核心分析是一个 Python 服务，可以通过三种入口调用：

- 进程内 CLI：不启动服务器、不打开浏览器，直接执行完整的准入、查询、调查和证据校验。
- HTTP API：适合自动化脚本、其他服务和前端集成。
- React 工作台：适合人工查看 Claim、证据、SQL 审计和查询预算。

无需浏览器，直接提问：

```powershell
.\.venv\Scripts\python.exe scripts\ask.py
.\.venv\Scripts\python.exe scripts\ask.py "2026-11-17 到 2026-11-23 支付GMV为什么下降"
.\.venv\Scripts\python.exe scripts\ask.py --json
```

CLI 使用与其他入口相同的 `AnalysisService`，不会绕过任何安全闸门。输出包含准入状态、完整度、答案、Claim 数、证据数和查询预算。

## 快速启动

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
npm --prefix web install
```

终端 1 启动 API：

```powershell
.\.venv\Scripts\python.exe -m scripts.run_api
```

终端 2 启动前端：

```powershell
npm --prefix web run dev
```

如需网页工作台，再打开 [http://127.0.0.1:5173](http://127.0.0.1:5173)；不需要网页时只运行上面的 CLI 即可。

默认 API 地址为 `http://127.0.0.1:8000`，首次启动会生成 `data/metricledger.duckdb` 和 `data/audit.sqlite3`。运行时文件已在 `.gitignore` 中排除。

如需让调查阶段调用 OpenAI-compatible 的假设排序端点，在进程环境中配置：

```powershell
$env:METRICLEDGER_PLANNER_BASE_URL = "https://your-planner.example/v1"
$env:METRICLEDGER_PLANNER_MODEL = "your-model"
$env:METRICLEDGER_PLANNER_API_KEY = "your-api-key"
.\.venv\Scripts\python.exe -m scripts.run_api
```

`BASE_URL` 与 `MODEL` 必须同时配置；只配置其中一项时启动会明确失败。API Key 只从环境变量读取，不作为请求正文、日志或审计账本字段保存。未配置端点时默认使用离线启发式 planner，因此无密钥也能运行全部演示和核心测试。

如需启用多租户认证、RBAC 和单进程限流：

```powershell
$auth = @{
  "replace-with-a-long-random-token" = @{
    principal_id = "demo-analyst"
    tenant_id = "demo"
    roles = @("viewer", "analyst")
    allowed_values = @{
      region = @("east", "west", "north", "south")
      channel = @("app", "web", "mini_program")
      category = @("electronics", "beauty", "home", "fashion")
    }
  }
} | ConvertTo-Json -Depth 8 -Compress

$env:METRICLEDGER_AUTH_JSON = $auth
$env:METRICLEDGER_RATE_LIMIT_PER_MINUTE = "120"
```

认证启用后，目录、分析、结果回读和评测接口都要求 Bearer 令牌；`/api/health` 与 `/api/metrics` 保持可由探针抓取。实际部署应把令牌放入 Secret Manager，而不是提交到仓库或 shell 历史。当前限流器是单进程内存实现，多副本部署需要替换为共享 Redis/API Gateway 策略。

不打开浏览器也可以通过 HTTP 调用 API：

```powershell
$body = @{
  question = "2026-11-17 到 2026-11-23 支付GMV为什么下降"
  as_of = "2026-11-23T23:59:59+08:00"
  principal = @{
    principal_id = "powershell-client"
    allowed_values = @{
      region = @("east", "west", "north", "south")
      channel = @("app", "web", "mini_program")
      category = @("electronics", "beauty", "home", "fashion")
    }
  }
  budget = @{ max_queries = 4 }
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/analyses `
  -ContentType application/json `
  -Body $body
```

## 示例问题

```text
2026-11-17 到 2026-11-23 支付GMV为什么下降
```

固定快照 `batch_20261123_2300` 覆盖 `2026-10-01` 至 `2026-11-23`，物理快照含两个租户共 5,184 行，其中 `demo` 租户为 2,592 行。示例主指标为：

- 当前支付 GMV：`732,400.00`
- 上一周期：`806,400.00`
- 变化额：`-74,000.00`
- 变化率：约 `-9.18%`
- East 地区算术贡献：`100%`

## 常用命令

| 操作 | Make | Windows PowerShell |
|---|---|---|
| 进程内提问（无网页） | `make ask` | `.\.venv\Scripts\python.exe scripts\ask.py` |
| 后端测试 | `make test` | `.\.venv\Scripts\python.exe -m pytest -q` |
| 前端测试 | `make test-web` | `npm --prefix web test -- --run` |
| 前端构建 | `make build` | `npm --prefix web run build` |
| 复现评测 | `make eval` | `.\.venv\Scripts\python.exe scripts\run_eval.py` |
| 启动 API | `make api` | `.\.venv\Scripts\python.exe -m scripts.run_api` |
| 启动前端 | `make web` | `npm --prefix web run dev` |
| 实时冒烟 | `make smoke` | `.\.venv\Scripts\python.exe scripts\smoke_test.py` |
| 生成 200 万事件数据 | 无 | `.\.venv\Scripts\python.exe scripts\build_scale_data.py` |
| 真实规模基准 | 无 | `.\.venv\Scripts\python.exe scripts\benchmark_scale.py` |
| 混合并发压测 | 无 | `.\.venv\Scripts\python.exe scripts\benchmark_load.py --base-url http://127.0.0.1:8000` |
| planner 对比 | 无 | `.\.venv\Scripts\python.exe scripts\benchmark_planners.py` |
| 全量验证 | `make verify` | 按下方“验证”逐条执行 |

`make dev` 是 `make api` 的别名，前端需要在另一个终端运行 `make web`。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -q --cov=metricledger --cov-report=term
.\.venv\Scripts\python.exe scripts\ask.py --json
npm --prefix web test -- --run
npm --prefix web run build
npm --prefix web audit --audit-level=high
.\.venv\Scripts\python.exe scripts\run_eval.py
.\.venv\Scripts\python.exe scripts\smoke_test.py
.\.venv\Scripts\python.exe scripts\build_scale_data.py
.\.venv\Scripts\python.exe scripts\benchmark_scale.py
.\.venv\Scripts\python.exe scripts\benchmark_planners.py
```

混合负载需要先启动 API，再执行：

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_load.py `
  --base-url http://127.0.0.1:8000 `
  --requests 120 `
  --concurrency 12
```

最近一次验收结果和限制见 [docs/verification/acceptance.md](docs/verification/acceptance.md)。规模、混合负载和 planner 证据分别见：

- [scale-report.md](docs/verification/scale-report.md)
- [load-report.md](docs/verification/load-report.md)
- [planner-report.md](docs/verification/planner-report.md)

机器可读评测结果见 [eval/results.json](eval/results.json)。

## 可复现部署

仓库提供多阶段 [Dockerfile](Dockerfile) 和 [compose.yaml](compose.yaml)。在安装 Docker/Compose 的环境中：

```powershell
$env:METRICLEDGER_AUTH_JSON = '{"replace-with-a-long-random-token":{"principal_id":"demo-analyst","tenant_id":"demo","roles":["viewer","analyst"],"allowed_values":{"region":["east","west","north","south"],"channel":["app","web","mini_program"],"category":["electronics","beauty","home","fashion"]}}}'
docker compose up --build
```

API 暴露在 `http://127.0.0.1:8000`，Prometheus 暴露在 `http://127.0.0.1:9090`，抓取与告警规则位于 [deploy/prometheus](deploy/prometheus)。

## 架构

请求链路为：

```text
Natural language
  -> semantic intent
  -> admission
  -> fixed context
  -> read-only SQL
  -> fixed snapshot
  -> bounded investigation
  -> evidence validation
  -> rendered claims
  -> audit ledger
```

详细边界、模块职责和失败策略见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 评测口径

评测只覆盖本仓库的 63 个合成场景，不是外部榜单，也不使用 LLM judge 计算安全指标。最近一次复现中，MetricLedger 达到 `63/63` 场景成功、`0` 不安全回答、`0` 越权回答、`0` 空结果冒充零值、`0` 无证据 Claim、`0` 证据违规。延迟是本机单进程测量，不代表线上 SLA。

## 已知限制

- 数据是确定性合成电商数据，不代表真实企业收益。
- 当前解析器只覆盖三个指标、三个维度和明确 ISO 日期。
- `naive_text_to_sql` 是故意不安全的确定性对照组，不是具名商用产品。
- 当前版本是单机参考实现。限流是单进程内存策略；SQLite 账本和多租户隔离适合演示与审计闭环，不等同于生产级高可用方案。
- 真实模型对比本轮使用 `api.deepseek.com/deepseek-chat` 在 15 个确定性场景上运行；结果不是通用模型榜单或生产 SLA，详见 [planner-report.md](docs/verification/planner-report.md)。
- 本轮本机没有 Docker/Podman，也没有长期托管凭据或域名。GitHub Actions 已真实构建并启动精简生产镜像，以非 root 用户完成健康检查与业务冒烟；本机 Compose 启动和持久公网部署仍为 `NOT_RUN`。验收记录见 [acceptance.md](docs/verification/acceptance.md)。
- 自然语言理解仍可能触发澄清；系统只保证不猜测、不越权、不伪造，而不是保证所有问题都能自动回答。
