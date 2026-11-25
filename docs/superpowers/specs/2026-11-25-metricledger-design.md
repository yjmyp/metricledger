# MetricLedger：可信 ChatBI 与指标调查 Agent

## 1. 结论

MetricLedger 是一个面向指标分析的可信分析 Agent。它不追求“自然语言随便问、模型随便写 SQL”，而是把一次分析拆成两套独立门禁：

1. **执行准入**：指标是否明确、权限是否覆盖、快照是否完整、数据是否足够新鲜、质量是否达标。
2. **结论支持**：每条 Claim 是否引用了同一分析上下文内、范围匹配的真实结果。

只有执行准入通过后才能查询业务数据。只有 Claim 通过证据校验后才能进入最终答案。允许诚实地回答“现有证据不足以确定原因”，不允许用流畅解释掩盖证据缺失。

## 2. 目标

- 支持按自然语言或结构化意图发起问数。
- 支持可加指标和比率指标，避免把所有指标都错误地视为可加。
- 支持指标变化的基线对比、维度诊断和有界异常调查。
- 所有查询由语义层和代码编译产生，模型不生成自由 SQL。
- 一次分析只访问固定的不可变数据快照和固定语义版本。
- 模型不直接产出数字；答案中的数字由证据结果确定性渲染。
- 每个结论绑定 Claim 级证据、作用域和限制。
- 对口径歧义、越权、过期数据、质量问题、预算耗尽和技术失败给出明确的产品状态。
- 提供可重复的测试、离线评测、SQL 审计和前端证据展示。

## 3. 非目标

- 不实现多租户、分布式调度、高并发集群或生产级 SLA。
- 不支持任意数据库、任意 SQL 和任意指标公式编辑。
- 不使用模型自动修改语义层或业务数据。
- 不使用多 Agent 角色扮演来制造“智能感”。
- 不在首版支持拖拽 BI、报表编排、通用 RAG 或开放式数据探索。
- 不声称合成演示数据上的结果等同于真实企业收益。
- 不把 Agent Crossbar、Vela、MergeGuard 的代码直接复制进本项目；只吸收可测试的设计原则。

## 4. 成功标准

首版必须同时满足：

- `pytest` 全量通过，测试覆盖准入、编译、快照、权限、质量、证据、调查、API 和评测。
- `npm run build` 通过，前端能展示答案、拒答、证据和 SQL 审计。
- 离线评测至少包含 60 个场景，并同时运行 naive Text-to-SQL、无语义调查和完整系统三个基线。
- 在危险场景集上，完整系统不能输出无证据数字或越权结论。
- 所有答案数字都能追溯到具体 `ResultArtifact`。
- 所有 SQL 都通过 SQLGlot AST 校验、只读校验、表白名单、列白名单、权限条件和查询预算。
- 运行一条命令可以生成数据、执行测试、运行离线评测并启动演示。

## 5. 技术栈

- Python 3.10+
- FastAPI
- Pydantic 2
- DuckDB
- SQLGlot
- SQLite
- React 19、TypeScript、Vite
- pytest、httpx
- Rimraf、ESLint、Vitest

SQLite 是本地单操作员模式的审计账本；项目不把它包装成企业级高并发方案。生产环境可将账本实现替换为 PostgreSQL，但本仓库只交付 SQLite 适配器。

## 6. 核心不变量

以下规则属于不可违反的系统不变量：

1. 用户输入、模型输出、字段值、维度标签和列注释都视为不可信内容。
2. 业务查询只接受经过语义校验的 `AnalysisIntent`，不接受原始 SQL。
3. 正式查询只能访问 Admission Gate 选定的单个不可变快照。
4. 同一分析中的指标版本、语义包哈希、快照、权限版本和时区不得改变。
5. 模型不能创建指标、维度、表、字段、过滤表达式或数值。
6. 一个 Claim 只能引用同一分析上下文中已完成的证据。
7. 证据的指标、时间、过滤器和分组范围必须覆盖 Claim 所需范围。
8. 可加指标可以计算精确算术贡献；比率指标不得把分组变化直接相加为总体贡献。
9. 相关性、算术贡献和因果结论必须使用不同 Claim 类型。
10. 技术错误不能伪装成业务零值。
11. 没有证据时不得生成解释性叙述。
12. 预算不足时优先保留已完成证据，不得补写推测。

## 7. 分析状态

### 7.1 产品状态

`AdmissionStatus`：

- `ADMITTED`：允许执行正式分析。
- `CLARIFY`：指标、时间或作用域存在无法安全推断的歧义。
- `REFUSED`：请求作用域不被当前权限覆盖。
- `UNAVAILABLE`：数据缺失、快照未覆盖、过期或质量门禁失败。

`Completeness`：

- `COMPLETE`：请求中的所有子问题均有结论。
- `PARTIAL`：部分子问题有有效证据，其他子问题因预算或技术问题停止。
- `NONE`：没有可进入答案的有效结论。

技术问题放入结构化 `issues`，不设置独立的 `technical_status`。如果没有可回答结果，产品状态为 `UNAVAILABLE`；如果仍有部分结果，产品状态可为 `ADMITTED` 且完整性为 `PARTIAL`。

### 7.2 Claim 状态

每条 Claim 独立拥有：

- `SUPPORTED`：证据存在且完整覆盖 Claim 范围。
- `PARTIAL`：证据支持方向或局部范围，但不能支持完整 Claim。
- `INCONCLUSIVE`：查询成功，但证据不足以判断。
- `UNSUPPORTED`：证据缺失、过范围、冲突或校验失败。

整个回答不保存一个可覆盖所有 Claim 的全局“正确”布尔值。回答层只展示通过 `SUPPORTED` 或 `PARTIAL` 校验的 Claim。

## 8. 领域模型

### 8.1 SemanticBundle

语义包是可信配置，不是模型生成内容。首版包含：

- `bundle_id`
- `version`
- `timezone`
- `tables`
- `dimensions`
- `metrics`
- `metric_aliases`
- `freshness_policies`
- `quality_policies`
- `canonical_hash`

`canonical_hash` 对去除无关空白后的规范化语义包内容计算 SHA-256。

指标定义包含：

- `metric_id`
- `display_name`
- `kind`：`ADDITIVE` 或 `RATIO`
- `expression`
- `numerator`
- `denominator`
- `time_column`
- `allowed_dimensions`
- `default_filters`
- `freshness_policy_id`
- `quality_policy_id`
- `sensitivity`

### 8.2 AnalysisContext

通过准入后创建不可变上下文：

- `analysis_id`
- `requested_period`
- `comparison_period`
- `reporting_as_of`
- `timezone`
- `snapshot_id`
- `snapshot_manifest_hash`
- `semantic_bundle_id`
- `semantic_bundle_hash`
- `auth_policy_version`
- `effective_scope`
- `auth_scope_hash`
- `budget`

同时保存 `effective_scope` 和哈希。哈希只用于检测漂移，不能代替审计内容。

### 8.3 AnalysisIntent

模型或离线解析器最多只能给出：

- `metric_id`
- `question_kind`：`VALUE`、`CHANGE`、`INVESTIGATE`
- `period`
- `comparison_period`
- `filters`
- `candidate_dimensions`
- `reporting_as_of`

解析结果必须经过语义层、时间范围、权限和预算校验。缺指标、多个指标同时成立且无法消歧、日期不完整且无法使用安全默认值时返回 `CLARIFY`。

### 8.4 ResultArtifact

每次查询保存：

- `result_id`
- `analysis_id`
- `analysis_kind`
- `intent_dsl`
- `compiled_sql`
- `sql_fingerprint`
- `row_count`
- `result_digest`
- `bounded_rows`
- `metric_versions`
- `snapshot_id`
- `scope`
- `quality_checks`
- `created_at`
- `expires_at`

`bounded_rows` 最多 200 行，只保存聚合结果。系统不保存原始敏感明细，不以账本复制业务数据仓库。

### 8.5 Claim

Claim 只描述可验证结论：

- `claim_id`
- `claim_type`：`VALUE`、`CHANGE`、`CONTRIBUTION`、`DIAGNOSTIC`、`QUALITY`、`LIMITATION`
- `template`
- `arguments`
- `evidence_refs`
- `scope`
- `limitations`
- `support`

模型不得填写 `support`。Evidence Validator 根据证据类型、上下文、范围和参数逐项计算。

### 8.6 Evidence Graph

证据图使用 SQLite 关系表保存，不引入图数据库。首版边类型：

- `executed_in`
- `derived_from`
- `supports`
- `contradicts`
- `prevents_answer`

假设（Hypothesis）和叙述（Narrative）没有证据资格，不能成为 `supports` 边的源节点。

## 9. 两阶段快照与准入

### 9.1 Phase 0：Catalog Resolution

只读取快照目录和语义元数据：

1. 解析指标和维度。
2. 校验请求时区和时间语义。
3. 选择 `reporting_as_of` 不晚于请求值的最新完成快照。
4. 校验快照覆盖完整业务周期和对比周期。
5. 校验指标新鲜度策略。
6. 校验数据质量策略。
7. 计算请求作用域和用户授权作用域的交集；请求不完全被授权覆盖时拒绝，不静默缩窄。
8. 固化 `AnalysisContext`。

### 9.2 Phase 1：Formal Analysis

只允许使用已固化上下文：

1. 编译查询 DSL。
2. 执行受控查询。
3. 生成结果和证据。
4. 运行调查循环。
5. 校验 Claim。
6. 渲染答案。

如果执行中检测到语义包或快照变化，分析以结构化 `CONTEXT_DRIFT` 问题停止，不换快照续跑。

## 10. 查询 DSL 与 SQL 安全

查询 DSL 只允许：

- 语义包中存在的 metric、dimension、filter。
- ISO 日期范围。
- 已授权的枚举过滤值。
- 已声明的分组、排序和结果行数上限。

SQL 编译顺序：

1. 从可信语义配置读取指标 SQL 表达式。
2. 使用参数化方式填入时间、枚举过滤器和权限条件。
3. 生成单条 `SELECT`。
4. SQLGlot 解析为 AST。
5. 拒绝多语句、DML、DDL、系统函数、外部读取、未授权表和未授权列。
6. 再次确认 AST 中存在固定快照名和授权过滤条件。
7. 应用行数上限和查询预算。
8. 在只读 DuckDB 连接执行。

编译器产生的 SQL 会写入 `ResultArtifact`，供审计和前端展示。

## 11. 数据质量与时间语义

质量不是全局布尔值。每个指标按自己使用的表、周期和快照定义检查：

- 快照完成状态。
- 周期覆盖。
- 最新事件日期。
- 关键字段非空率。
- 业务主键重复率。
- 比率指标的分母正值率。
- 维度值合法性。

质量检查结果必须映射为：

- `PASS`
- `WARN`：允许回答但加入 limitation。
- `BLOCK`：拒绝正式分析并返回 `UNAVAILABLE`。

真实零值需要快照覆盖成立且查询层明确返回零。查询无记录、快照未覆盖和技术失败不能自动转成零。

## 12. 指标计算与归因边界

### 12.1 可加指标

`paid_gmv` 的每个分组值可以直接相加。调查可以计算：

- 总体变化。
- 分组变化。
- 分组变化占总体变化的算术贡献。

Claim 必须标注 `arithmetic_contribution_only` 和 `not_causal`。

### 12.2 比率指标

`paid_conversion_rate = paid_orders / sessions` 不允许把各分组转化率差值乘权重后直接当总体精确贡献。首版只支持：

- 分子变化诊断。
- 分母变化诊断。
- 分组方向性差异。

Claim 类型为 `DIAGNOSTIC`，不得使用“主要来自”这种可加归因表述。

## 13. 有界调查 Agent

调查只在一个明确循环中发生：

```text
观察总体变化
→ 生成或重排允许的维度假设
→ 执行一个受控维度诊断
→ 验证贡献集中度或比率方向
→ 继续下一个假设或停止
```

### 13.1 Planner 接口

`HypothesisPlanner` 只接收：

- 脱敏后的当前观察。
- 允许的 dimension id 和展示名。
- 已完成证据摘要。
- 剩余预算。

它输出最多 3 个：

- `dimension_id`
- `rationale_code`
- `priority`

Planner 不能输出 SQL、表名、字段名、任意表达式或数字。

### 13.2 默认实现

- `HeuristicHypothesisPlanner`：离线、确定性、用于测试和演示。
- `OpenAICompatibleHypothesisPlanner`：可配置 OpenAI-compatible 端点，JSON 结构输出。
- 模型输出不合法、超时或越界时返回结构化问题并保留已有证据。

### 13.3 停止条件

- 可加指标找到的单一分组贡献绝对值达到总变化的 50%。
- 当前允许维度全部测试完成。
- 达到最大查询数。
- 达到时间或 Token 预算。
- Planner 连续返回非法或重复假设。

预算在执行查询前预留。达到预算的下一步不执行。

## 14. Claim 与答案渲染

Claim 模板仅允许受控占位符：

- 指标值。
- 百分比变化。
- 证据安全引用的维度值。
- 时间和分组描述。

推荐模板示例：

```text
{{metric:paid_gmv.change_pct}} 的下降中，
{{dimension:region.top_contributor}} 的算术贡献为
{{evidence:top_contribution.share_pct}}。
```

渲染器从结构化证据取值。缺失参数、参数类型不符或引用证据不支持模板范围时，Claim 校验失败，答案中不展示该 Claim。

模型可以组织非数字叙述，但叙述必须引用已通过校验的 Claim。首版前端只展示通过校验的 Claim 和 limitation，不把模型叙述作为可选事实源。

## 15. 权限和安全

- 请求超过授权作用域时 `REFUSED`。
- 权限条件在 SQL AST 和最终 SQL 双重确认。
- 禁止自由文本进入 SQL 标识符或表达式。
- 维度枚举值只接受语义包白名单。
- 前端对数据值转义，不渲染任意 HTML。
- 模型提示中标记用户内容和数据标签为不可信内容。
- API 不返回完整业务明细。
- 本地审计账本不保存 API Key。

## 16. API

### `GET /api/health`

返回服务状态、语义包版本、快照数量和模型模式。

### `GET /api/catalog`

返回可公开的指标、维度、示例问题和默认快照说明。

### `POST /api/analyses`

请求：

```json
{
  "question": "2026-11-17 到 2026-11-23 为什么支付 GMV 下降？",
  "as_of": "2026-11-23T23:59:59+08:00",
  "principal": {
    "principal_id": "demo-analyst",
    "allowed_regions": ["east", "west", "north", "south"],
    "allowed_channels": ["app", "web", "mini_program"]
  }
}
```

响应必须包含：

- `analysis_id`
- `admission`
- `completeness`
- `answer`
- `claims`
- `issues`
- `context`
- `budget_usage`
- `evidence`
- `sql_audit`

### `GET /api/analyses/{analysis_id}`

从 SQLite 审计账本读取已保存分析。

### `GET /api/eval/latest`

返回最近一次离线评测报告；没有报告时返回 `NOT_RUN`，不伪造数字。

## 17. 前端

React 工作台只做一个分析主页面：

- 顶部问题输入、as-of、示例问题。
- 准入状态和完整性卡片。
- 答案 Claim 列表。
- 关键限制和停止原因。
- 证据图时间线。
- SQL 审计抽屉。
- 快照、语义版本、权限范围和预算信息。

首版不做登录、多页面报表、拖拽图表和复杂管理后台。

## 18. 测试策略

### 18.1 确定性单元与集成测试

至少覆盖：

- 指标解析和歧义。
- 可加指标计算。
- 比率指标禁止算术归因。
- 快照选择和周期覆盖。
- 过期数据。
- 无记录与真实零值区别。
- 越权拒绝和隐式缩窄禁止。
- SQLGlot 多语句、DML、外部读取、表和列白名单。
- 权限条件最终 SQL 确认。
- 质量 `PASS/WARN/BLOCK`。
- 查询失败、预算耗尽和部分证据。
- Claim 证据缺失、跨分析引用、范围不匹配和数值参数错误。
- 维度标签提示注入不进入 HTML。
- 模型 planner 非法输出和重复假设。
- API 序列化和账本持久化。

### 18.2 离线评测

固定评测集由数据生成规则注入已知异常，标签由注入配置产生并人工复核，不使用同一个模型生成答案后再判分。

场景类别至少包括：

- 正常值。
- 正常变化。
- 可加指标单维度归因。
- 多维度诊断。
- 比率指标。
- 指标歧义。
- 越权。
- 快照过期。
- 周期未覆盖。
- 质量阻断。
- 真实零值。
- 空结果。
- 预算耗尽。
- 查询失败。
- 提示注入。
- 相关冒充因果。

报告指标：

- `admission_accuracy`
- `unsafe_answer_rate`
- `false_refusal_rate`
- `numeric_fidelity`
- `evidence_citation_violation_rate`
- `scope_violation_count`
- `average_queries`
- `p95_latency_ms`

### 18.3 基线

同一评测集运行：

1. `naive_text_to_sql`：模型生成 SQL 并直接回答。
2. `semantic_without_investigation`：语义层查询，但不做异常调查和 Claim 证据门禁。
3. `metricledger`：完整系统。

README 只展示本仓库脚本生成的报告，不宣称行业最优。

## 19. 验收门槛

- 单元和集成测试全绿。
- 前端 TypeScript 检查和构建通过。
- 危险场景中不存在无证据数字、跨上下文引用和越权结果。
- 数字保真率必须为 100%，测试发现任何失败都阻断交付。
- 所有答案 Claim 的证据引用违规数为 0。
- 完整系统在 `admission_accuracy`、`unsafe_answer_rate` 和证据违规指标上不差于两个基线。
- 离线环境不配置 API Key 也能完整运行演示和核心测试。
- 配置 OpenAI-compatible 端点时可真实调用 planner；密钥只来自环境变量。
- `README.md` 分别记录已验证数字、未验证边界和复现命令。

## 20. 交付顺序

1. 领域模型、语义包和快照。
2. 查询编译器与安全守卫。
3. 准入、质量和证据校验。
4. 有界调查服务。
5. API 和审计账本。
6. React 工作台。
7. 评测集、三基线和文档。

每一步都先写失败测试，再写实现。项目完成前不以组件数量代替可验证结果。
