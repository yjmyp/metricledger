# MetricLedger Architecture

## Design Goal

MetricLedger 的核心目标不是“让模型生成一段看起来合理的分析”，而是把自然语言分析拆成一条可审计、可拒绝、可复现的证据链。LLM 最多参与诊断维度排序；指标、维度、字段、SQL、数值和 Claim 支持状态都不能由模型自由生成。

## Trust Boundary

```text
User question
  |
  v
RuleBasedIntentParser
  |
  v
AdmissionService ---- REFUSED / CLARIFY / UNAVAILABLE
  |
  v
AnalysisContext = intent + metric + snapshot + authorized scope
  |
  v
QueryCompiler -> SQLGlot guard -> DuckDbEngine
  |
  v
Immutable result artifacts
  |
  +----> EvidenceStore
  |
  v
Bounded investigation
  |
  v
EvidenceValidator -> ClaimRenderer
  |
  v
FastAPI response + SQLite audit ledger + React receipt
```

准入失败时不会执行 SQL，也不会产生 Claim。固定快照未覆盖、过期、质量检查阻塞或授权范围不一致时，系统返回明确状态，不把结果补成零。

## Modules

| Module | Responsibility |
|---|---|
| `semantic.py` | 加载版本化语义资源包，解析指标别名和维度 |
| `intent.py` | 将问题解析为结构化 `AnalysisIntent`，识别歧义 |
| `admission.py` | 校验权限、快照、日期覆盖和质量策略 |
| `compiler.py` | 从资源包表达式生成参数化只读 SQL |
| `guards.py` | 用 SQLGlot 拒绝多语句、非 SELECT、越界表和字段 |
| `data.py` | 只读执行白名单快照表，区分空结果与执行失败 |
| `quality.py` | 完整性、覆盖、重复率、空值和比率分母检查 |
| `investigation.py` | 在硬预算内执行有界分组诊断并计算贡献 |
| `planners.py` | 启发式或 OpenAI-compatible 假设排序，输出必须被清洗 |
| `evidence.py` | 保存有作用域、摘要和过期信息的结果证据 |
| `claims.py` | 校验 Claim 引用、同一分析上下文和模板占位符 |
| `service.py` | 按固定顺序编排分析，阻止失败路径进入查询引擎 |
| `ledger.py` | 保存分析、证据和关系，不保存密钥或原始明细 |
| `api.py` | 暴露分析、目录、健康和评测接口 |
| `evaluation.py` | 用确定性场景和基线复现安全指标 |

## Non-Negotiable Invariants

1. 用户不能提交 SQL；业务表达只接受语义请求。
2. 每次分析只绑定一个不可变快照和一个语义包哈希。
3. 所有用户范围和日期都作为 SQL 参数，不拼接进 SQL 字面量。
4. 加法指标的贡献使用确定性算术；比率指标只能产生诊断 Claim。
5. Claim 只能引用同一分析、同一快照、同一语义哈希的结果证据。
6. 模板渲染后不允许残留 `{` 或 `}`，防止占位符泄漏。
7. 拒绝、澄清、不可用和执行失败均不得返回业务零值。
8. 模型规划失败时回退到启发式排序；模型输出不能扩大权限或预算。

## Investigation Model

主指标查询完成后，系统根据指标类型构造诊断路径：

- 加法指标：按语义允许的维度查询分组差分，计算各组对总变化的算术贡献。
- 比率指标：分别诊断分子、分母和方向差异，不生成算术贡献 Claim。
- 预算：默认最多 4 次查询；预算在执行前预留，耗尽时返回 `PARTIAL` 和 `BUDGET_EXHAUSTED`。
- 停止：当已诊断维度形成足够集中的贡献时停止继续遍历，避免无界探索。

LLM 只接收指标观察摘要、允许的维度 ID/名称、已有证据摘要和剩余预算。它不能看到原始明细行，也不能把生成的字段、SQL 或数字写入正式结果。

## Evidence and Claims

每条结果证据记录：

- `analysis_id`
- 指标与时间周期
- 比较周期与过滤作用域
- 快照与语义哈希
- 结果摘要与 SQL 指纹
- 有界结果行
- 过期信息

Claim 验证会拒绝跨分析引用、证据范围不足、指标类型不匹配、未知占位符和残留模板。最终文本由证据值渲染，普通字符串中的数字不能成为 Claim 数据来源。

## Persistence

SQLite 使用 WAL 模式，保存三类数据：

- `analyses`
- `result_artifacts`
- `graph_edges`

持久化内容是规范 JSON 和证据摘要，不包含 API key、Authorization header 或完整原始业务明细。

## Failure Semantics

| Failure | Result |
|---|---|
| 范围超出授权 | `REFUSED`，不执行 SQL |
| 指标别名有歧义 | `CLARIFY`，不猜测 |
| 日期或对比周期未覆盖 | `UNAVAILABLE`，不返回零 |
| 快照过期或不完整 | `UNAVAILABLE` |
| 查询异常 | 显式错误，不转成业务数值 |
| Claim 证据不匹配 | 证据违规，阻止返回 |
| 调查预算耗尽 | `PARTIAL` + 明示原因 |
| 评测文件缺失或损坏 | `NOT_RUN` / `INVALID`，不伪造结果 |

## Security Boundary

- SQL 只读、单语句、白名单表和字段。
- API 请求模型禁止额外字段。
- 模板输出必须来自验证后的证据。
- 运行日志不记录密钥。
- 前端只渲染文本，不把用户内容解释为 HTML。

## Evaluation Boundary

评测使用本仓库 63 个确定性合成场景，将 MetricLedger 与以下基线比较：

- `naive_text_to_sql`：故意绕过权限、把失败或空结果答成零的不安全对照。
- `semantic_without_investigation`：保留语义与证据，但不做分组诊断。
- `metricledger`：完整准入、固定快照、证据和有界调查。

安全指标由代码规则计算，不调用 LLM judge。结果不能外推为公开榜单或真实企业 SLA。
